"""The `ideafinder` command line."""

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from ideafinder.settings import get_settings

app = typer.Typer(no_args_is_help=True, help="Find and validate SaaS ideas from public evidence.")
db_app = typer.Typer(no_args_is_help=True, help="Create, migrate and inspect the database.")
app.add_typer(db_app, name="db")

QueueOption = Annotated[
    list[str] | None,
    typer.Option("--queue", "-q", help="Queue to serve (repeatable). Default: all queues."),
]


def _configure_logging() -> None:
    from ideafinder.logs import configure

    configure()


def _require_ready() -> None:
    """Stop with a clear message if the database isn't usable yet."""
    from ideafinder.status import get_status

    status = get_status(get_settings())
    if status.problems:
        for problem in status.problems:
            typer.secho(f"✗ {problem}", fg="red", err=True)
        raise typer.Exit(1)


def _resolve_queues(queues: list[str] | None) -> list[str]:
    from ideafinder.worker import QUEUES

    chosen = queues or list(QUEUES)
    unknown = set(chosen) - set(QUEUES)
    if unknown:
        raise typer.BadParameter(f"unknown queue(s): {sorted(unknown)}; choose from {QUEUES}")
    return chosen


def _web_server() -> uvicorn.Server:
    settings = get_settings()
    return uvicorn.Server(
        uvicorn.Config(
            "ideafinder.web.app:app",
            host=settings.web_host,
            port=settings.web_port,
            log_config=None,  # use ours (ideafinder.logs): no line per page load
        )
    )


async def _run_worker(queues: list[str]) -> None:
    from ideafinder.worker import WORKER_OPTIONS
    from ideafinder.worker import app as worker_app

    async with worker_app.open_async():
        logging.getLogger("ideafinder.worker").info(
            "Worker ready (%s). Extraction runs every 10 minutes, the daily run at 06:00.",
            " and ".join(queues),
        )
        await worker_app.run_worker_async(
            queues=queues, install_signal_handlers=False, **WORKER_OPTIONS
        )


async def _up(queues: list[str]) -> None:
    worker = asyncio.create_task(_run_worker(queues))
    try:
        await _web_server().serve()  # returns when you press Ctrl+C
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


@app.command()
def up(queues: QueueOption = None) -> None:
    """Start the web app and the workers together (daily use)."""
    _configure_logging()
    _require_ready()
    chosen = _resolve_queues(queues)
    settings = get_settings()
    typer.echo(f"Web app: http://{settings.web_host}:{settings.web_port}  ·  queues: {chosen}")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_up(chosen))


@app.command()
def web() -> None:
    """Start only the web app."""
    _configure_logging()
    with contextlib.suppress(KeyboardInterrupt):
        _web_server().run()


@app.command()
def worker(queues: QueueOption = None) -> None:
    """Start only the workers (used by scheduled jobs, and on the Pi later)."""
    _configure_logging()
    _require_ready()
    chosen = _resolve_queues(queues)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_worker(chosen))


@app.command()
def doctor() -> None:
    """Check config, database and workers, and say how to fix any problem."""
    from ideafinder.status import get_status

    status = get_status(get_settings())
    db = status.database

    def line(ok: bool, label: str, detail: str = "") -> None:
        typer.secho(
            f"{'✓' if ok else '✗'} {label}" + (f": {detail}" if detail else ""),
            fg="green" if ok else "red",
        )

    line(status.config is not None, "Config", status.config_error or "valid")
    line(db.reachable, "Postgres", db.server_version or db.error or "")
    if db.reachable:
        line(db.pgvector_version is not None, "pgvector", db.pgvector_version or "not installed")
        line(db.up_to_date, "Migrations", f"at {db.current_revision}, latest {db.head_revision}")
    for q in status.queues:
        line(q.alive, f"Worker '{q.queue}'", "running" if q.alive else "not running")
    if status.problems:
        typer.echo("")
        for problem in status.problems:
            typer.echo(f"→ {problem}")
        raise typer.Exit(1)


@app.command()
def collect(
    sources: Annotated[
        list[str] | None,
        typer.Option("--source", "-s", help="Source key to collect (repeatable). Default: all."),
    ] = None,
    since_days: Annotated[
        int | None,
        typer.Option(help="Trial run over the last N days. Cursors are not used or saved."),
    ] = None,
) -> None:
    """Collect new Items from Sources now (first run backfills 12 months)."""
    from datetime import UTC, datetime, timedelta

    from ideafinder.collectors.runner import collect as run_collect
    from ideafinder.config import load_config

    _configure_logging()
    _require_ready()
    settings = get_settings()
    since = datetime.now(UTC) - timedelta(days=since_days) if since_days else None
    try:
        reports = asyncio.run(
            run_collect(load_config(settings.config_dir), settings, sources, since)
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    typer.echo("")
    for r in reports:
        t = r.totals
        colour = {"succeeded": "green", "partial": "yellow"}.get(r.status, "red")
        typer.secho(
            f"{r.source:<16} {r.status:<10} {t.fetched:>6} fetched  {t.new:>6} new  "
            f"{t.updated:>6} updated  {t.skipped_language:>5} non-English",
            fg=colour,
        )
        for error in r.errors:
            typer.secho(f"    {error}", fg=colour)
    if any(r.status == "failed" for r in reports):
        raise typer.Exit(1)


@app.command()
def prefilter(
    redo: Annotated[
        bool, typer.Option(help="Re-decide Items not yet extracted (after changing the rules).")
    ] = False,
) -> None:
    """Decide which newly collected Items go to extraction (no model needed)."""
    from ideafinder.db.engine import get_sessionmaker
    from ideafinder.pipeline.prefilter import prefilter_pending, reset_unextracted

    _require_ready()

    async def run() -> dict[str, int]:
        async with get_sessionmaker()() as session:
            if redo:
                await reset_unextracted(session)
            return await prefilter_pending(session)

    counts = asyncio.run(run())
    if not counts:
        typer.echo("Nothing new to prefilter.")
    for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        typer.echo(f"{key:<24}{n:>8}")


@app.command()
def extract(
    threads: Annotated[int, typer.Option(help="How many Threads to process.")] = 20,
    model: Annotated[str | None, typer.Option(help="Override the configured model.")] = None,
    source: Annotated[
        list[str] | None,
        typer.Option("--source", "-s", help="Only these Source keys (repeatable)."),
    ] = None,
    skip_source: Annotated[
        list[str] | None,
        typer.Option("--skip-source", help="Every Source except these (repeatable)."),
    ] = None,
) -> None:
    """Extract Signals from kept Items with the local model (LM Studio must be running)."""
    from ideafinder.config import load_config
    from ideafinder.db.engine import get_sessionmaker
    from ideafinder.llm import LLM, ModelUnavailable
    from ideafinder.pipeline.extract import extract_pending

    _configure_logging()
    _require_ready()
    settings = get_settings()

    config = load_config(settings.config_dir)
    chosen = list(source or [s.key for s in config.sources.sources])
    chosen = [k for k in chosen if k not in (skip_source or [])]
    wanted = chosen if (source or skip_source) else None

    async def run():
        async with LLM(config.models) as llm:
            async with get_sessionmaker()() as session:
                return await extract_pending(session, llm, threads, model, wanted)

    try:
        r = asyncio.run(run())
    except ModelUnavailable as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    rate = f", {r.output_tokens / r.seconds:.0f} output tokens/s" if r.seconds else ""
    typer.echo(
        f"{r.chunks} chunks · {r.items} Items · {r.signals} Signals {r.by_kind} · "
        f"{r.failed_chunks} failed · {r.seconds:.0f}s{rate}"
    )


@app.command()
def cluster(
    form: Annotated[
        bool, typer.Option(help="Also cluster the holding pool into new Ideas (normally weekly).")
    ] = True,
) -> None:
    """Embed new Signals, attach them to Ideas, and form new Ideas from the pool."""
    from ideafinder.config import load_config
    from ideafinder.db.engine import get_sessionmaker
    from ideafinder.llm import LLM, ModelUnavailable
    from ideafinder.pipeline.cluster import attach_pending, form_ideas
    from ideafinder.pipeline.embed import embed_pending

    _configure_logging()
    _require_ready()
    config = load_config(get_settings().config_dir)
    rules = config.scoring.clustering

    async def run():
        async with LLM(config.models) as llm:
            async with get_sessionmaker()() as session:
                embedded = await embed_pending(session, llm)
                attached = await attach_pending(session, rules)
                formed = await form_ideas(session, llm, rules) if form else None
                if formed and formed.created:
                    # Pooled Signals may fit the new Ideas better than the HDBSCAN groups did.
                    attached_after = await attach_pending(session, rules)
                    attached.attached += attached_after.attached
                return embedded, attached, formed

    try:
        embedded, attached, formed = asyncio.run(run())
    except ModelUnavailable as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Embedded {embedded} Signals · attached {attached.attached} to existing Ideas")
    if formed:
        typer.echo(
            f"Pool of {formed.pool} Signals → {formed.groups} groups → "
            f"{len(formed.created)} new Ideas ({formed.failed} couldn't be named)"
        )


ideas_app = typer.Typer(no_args_is_help=True, help="Look at Ideas, and Merge or Split them.")
app.add_typer(ideas_app, name="ideas")


def _ideas_session(fn):
    from ideafinder.db.engine import get_sessionmaker

    async def run():
        async with get_sessionmaker()() as session:
            return await fn(session)

    return asyncio.run(run())


@ideas_app.command("list")
def ideas_list(
    state: Annotated[
        str | None, typer.Option(help="incubating / inbox / working / archived")
    ] = None,
    limit: Annotated[int, typer.Option(help="How many to show.")] = 50,
) -> None:
    """Ideas with the highest score first."""
    from ideafinder.pipeline.cluster import list_ideas

    _require_ready()
    rows = _ideas_session(lambda s: list_ideas(s, state, limit))
    if not rows:
        typer.echo("No Ideas yet. Run: ideafinder cluster")
        return
    typer.echo(
        f"{'id':>5} {'score':>5}  {'authors':>7} {'signals':>7} {'comms':>5}  {'state':<11} title"
    )
    for r in rows:
        score = f"{r.idea.score:.0f}" if r.idea.score is not None else "-"
        typer.echo(
            f"{r.idea.id:>5} {score:>5}  {r.authors:>7} {r.signals:>7} {r.communities:>5}  "
            f"{r.idea.state:<11} {r.idea.title}"
        )


@ideas_app.command("show")
def ideas_show(idea_id: Annotated[int, typer.Argument(help="Idea id.")]) -> None:
    """An Idea's problem, Persona, Related Ideas and Signals."""
    from ideafinder.pipeline.cluster import idea_detail

    _require_ready()
    detail = _ideas_session(lambda s: idea_detail(s, idea_id))
    if detail is None:
        typer.secho(f"✗ There's no Idea {idea_id}", fg="red", err=True)
        raise typer.Exit(1)
    idea = detail.idea
    typer.secho(f"#{idea.id} {idea.title}", bold=True)
    if idea.merged_into_id:
        typer.echo(f"Merged into #{idea.merged_into_id}")
    typer.echo(f"Problem: {idea.problem_statement}")
    typer.echo(f"Persona: {idea.persona}")
    seen = f"{idea.first_seen:%Y-%m-%d} – {idea.last_seen:%Y-%m-%d}" if idea.first_seen else "-"
    typer.echo(f"State:   {idea.state} · seen {seen}")
    if idea.state == "archived":
        typer.echo(f"Archived as: {idea.archive_reason}")
    if idea.score is not None:
        parts = (idea.score_breakdown or {}).get("components", {})
        typer.echo(
            f"Score:   {idea.score:.0f} / 100 · "
            + ", ".join(f"{k} {v:.2f}" for k, v in parts.items())
        )
    if idea.fit:
        broken = idea.fit["deal_breakers"]
        typer.echo(
            f"Fit:     {idea.fit['fit']:.2f} · solo-buildable {idea.fit['solo_buildable']:.2f} · "
            f"business buyer {idea.fit['b2b']} · {idea.fit['notes']}"
            + (f" · BREAKS: {'; '.join(broken)}" if broken else "")
        )
    if detail.proposal:
        p = detail.proposal
        outcome = f"archive ({p.archive_reason})" if p.proposal == "archive" else "Working List"
        typer.echo(f"Proposed: {outcome} · confidence {p.confidence:.2f} · {p.rationale}")
    for r in detail.reviews:
        extra = " · ".join(x for x in (r.archive_reason, r.note) if x)
        typer.echo(
            f"History: {r.created_at:%Y-%m-%d} {r.action}" + (f" · {extra}" if extra else "")
        )
    for other, similarity in detail.related:
        typer.echo(f"Related: #{other.id} {other.title} ({other.persona}) · {similarity:.2f}")
    typer.echo(f"\n{len(detail.signals)} Signals:")
    for s in detail.signals:
        sim = f"{s.similarity:.2f}" if s.similarity is not None else "  - "
        typer.echo(f"  {s.signal.id:>7} {sim} [{s.signal.kind}] {s.signal.problem_statement}")
        typer.echo(f'          {s.source} · {s.author or "?"} · "{s.signal.quote[:140]}"')


@ideas_app.command("merge")
def ideas_merge(
    source: Annotated[int, typer.Argument(help="Idea to merge away.")],
    target: Annotated[int, typer.Argument(help="Idea that keeps its identity.")],
) -> None:
    """Merge one Idea into another. The target keeps its id and the more advanced state."""
    from ideafinder.config import load_config
    from ideafinder.pipeline.cluster import ClusterError, merge

    _require_ready()
    rules = load_config(get_settings().config_dir).scoring.clustering
    try:
        idea = _ideas_session(lambda s: merge(s, source, target, rules))
    except ClusterError as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Merged #{source} into #{idea.id} {idea.title} ({idea.state})")


@ideas_app.command("split")
def ideas_split(
    idea_id: Annotated[int, typer.Argument(help="Idea to split.")],
    signal_ids: Annotated[list[int], typer.Argument(help="Signal ids to move to a new Idea.")],
) -> None:
    """Move some Signals into a new Incubating Idea (see ids with: ideafinder ideas show ID)."""
    from ideafinder.config import load_config
    from ideafinder.db.engine import get_sessionmaker
    from ideafinder.llm import LLM, ModelUnavailable
    from ideafinder.pipeline.cluster import ClusterError, split

    _require_ready()
    config = load_config(get_settings().config_dir)

    async def run():
        async with LLM(config.models) as llm:
            async with get_sessionmaker()() as session:
                return await split(session, llm, idea_id, signal_ids, config.scoring.clustering)

    try:
        idea = asyncio.run(run())
    except (ClusterError, ModelUnavailable) as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Split {len(signal_ids)} Signals off #{idea_id} into #{idea.id} {idea.title}")


@ideas_app.command("review")
def ideas_review(
    idea_id: Annotated[int, typer.Argument(help="Idea id.")],
    to: Annotated[str, typer.Argument(help="working, archive or restore")],
    reason: Annotated[
        str | None,
        typer.Option(
            help="Archive reason: not_interested, too_competitive, low_willingness_to_pay, "
            "too_big_to_build, dropped_after_research"
        ),
    ] = None,
    note: Annotated[str | None, typer.Option(help="Why, in your words.")] = None,
) -> None:
    """Move an Idea to the Working List or the Archive, or restore it (until the Inbox UI in M5)."""
    from ideafinder.config import load_config
    from ideafinder.pipeline.review import ReviewError, review

    _require_ready()
    config = load_config(get_settings().config_dir).scoring
    try:
        idea = _ideas_session(lambda s: review(s, config, idea_id, to, reason, note))
    except ReviewError as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    where = f"archived ({idea.archive_reason})" if idea.state == "archived" else idea.state
    typer.echo(f"#{idea.id} {idea.title} → {where}")


@app.command()
def rank() -> None:
    """Score Ideas, check fit, publish to the Inbox, resurface, and propose decisions."""
    from ideafinder.config import load_config
    from ideafinder.db.engine import get_sessionmaker
    from ideafinder.llm import LLM, ModelUnavailable
    from ideafinder.pipeline.rank import rank as run_rank

    _configure_logging()
    _require_ready()
    config = load_config(get_settings().config_dir)

    async def run():
        async with LLM(config.models) as llm:
            async with get_sessionmaker()() as session:
                return await run_rank(session, llm, config.scoring)

    try:
        r = asyncio.run(run())
    except ModelUnavailable as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    if not r.has_profile:
        typer.secho(
            "! No Fit profile yet, so nothing is checked for fit. See: ideafinder fit load --help",
            fg="yellow",
        )
    typer.echo(f"Scored {r.scored} Ideas · {r.snapshots} weekly snapshots taken")
    typer.echo(
        f"Published {len(r.publish.published)} to the Inbox · "
        f"{len(r.publish.outside_fit) + len(r.refit.outside_fit)} archived as outside fit · "
        f"{len(r.resurfaced) + len(r.refit.resurfaced)} resurfaced"
    )
    typer.echo(
        f"Proposed decisions: {r.propose.proposed} new"
        + (f" ({r.propose.failed} failed)" if r.propose.failed else "")
        + (
            f" · fit checks failed: {r.publish.fit_failed + r.refit.failed}"
            if r.publish.fit_failed + r.refit.failed
            else ""
        )
    )


fit_app = typer.Typer(no_args_is_help=True, help="Your Fit profile (what Ideas you'd build).")
app.add_typer(fit_app, name="fit")


@fit_app.command("show")
def fit_show() -> None:
    """Show the current Fit profile."""
    from ideafinder.pipeline.fit import get_profile, profile_as_text

    _require_ready()
    profile = _ideas_session(get_profile)
    if profile is None:
        typer.echo("No Fit profile yet. Start from config/fit_profile.example.yaml")
        return
    typer.echo(f"Version {profile.version}, updated {profile.updated_at:%Y-%m-%d}")
    typer.echo(profile_as_text(profile))
    typer.echo("Deal-breakers: " + ("; ".join(profile.deal_breakers) or "none"))


@fit_app.command("load")
def fit_load(
    path: Annotated[Path, typer.Argument(help="YAML file, like config/fit_profile.example.yaml")],
) -> None:
    """Load the Fit profile from a YAML file. Ideas are re-checked on the next `rank`."""
    from pydantic import ValidationError

    from ideafinder.pipeline.fit import read_profile_file, save_profile

    _require_ready()
    try:
        data = read_profile_file(path)
    except (OSError, ValidationError) as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    profile = _ideas_session(lambda s: save_profile(s, data))
    typer.echo(f"Fit profile saved (version {profile.version}).")


census_app = typer.Typer(no_args_is_help=True, help="US business counts for market math.")
app.add_typer(census_app, name="census")


@census_app.command("load")
def census_load(
    year: Annotated[int, typer.Option(help="County Business Patterns year.")] = 2023,
) -> None:
    """Download County Business Patterns (no API key needed) and store counts per industry."""
    from ideafinder.pipeline.census import download, parse, store

    _require_ready()

    async def run() -> int:
        us_file, names = await download(year)
        rows = parse(us_file, names)
        return await _ideas_session_async(lambda s: store(s, rows, year))

    typer.echo(f"Stored US business counts for {asyncio.run(run()):,} industries ({year}).")


async def _ideas_session_async(fn):
    from ideafinder.db.engine import get_sessionmaker

    async with get_sessionmaker()() as session:
        return await fn(session)


brief_app = typer.Typer(
    no_args_is_help=True, help="Briefings by hand: export the prompt, import the reply."
)
app.add_typer(brief_app, name="brief")


@brief_app.command("export")
def brief_export(
    idea_id: Annotated[int, typer.Argument(help="Idea id.")],
    out: Annotated[
        Path | None, typer.Option(help="Write to this file instead of printing.")
    ] = None,
) -> None:
    """Prepare the Briefing prompt for an Idea (maps its industry first if needed) and print it."""
    from ideafinder.config import load_config
    from ideafinder.db.models import Idea
    from ideafinder.llm import LLM, ModelUnavailable
    from ideafinder.pipeline import briefing
    from ideafinder.pipeline.fit import get_profile
    from ideafinder.pipeline.industry import latest_year, map_industry
    from ideafinder.pipeline.score import gather_evidence

    _require_ready()
    config = load_config(get_settings().config_dir)

    async def run(session):
        idea = await session.get(Idea, idea_id)
        if idea is None:
            raise typer.BadParameter(f"there's no Idea {idea_id}")
        if idea.industry_codes is None and await latest_year(session):
            try:
                async with LLM(config.models) as llm:
                    await map_industry(session, llm, idea)
                await session.commit()
            except ModelUnavailable as exc:
                typer.secho(f"! Couldn't map the industry ({exc}); continuing without.", err=True)
                await session.rollback()
        e = (await gather_evidence(session, config.scoring, [idea_id])).get(idea_id)
        return await briefing.request(session, idea, await get_profile(session), e)

    b = _ideas_session(run)
    if out:
        out.write_text(b.prompt_text)
        typer.echo(f"Wrote the prompt for idea-{idea_id} (version {b.version}) to {out}")
    else:
        typer.echo(b.prompt_text)


@brief_app.command("import")
def brief_import(
    idea_id: Annotated[int, typer.Argument(help="Idea id.")],
    path: Annotated[Path, typer.Argument(help="File with the reply (Markdown + frontmatter).")],
) -> None:
    """Check a Briefing reply and store it."""
    from ideafinder.pipeline import briefing

    _require_ready()
    raw = path.read_text()
    try:
        stored, parsed = _ideas_session(lambda s: briefing.accept_reply(s, idea_id, raw))
    except briefing.BriefingError as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    for w in parsed.warnings:
        typer.secho(f"! {w}", fg="yellow")
    if stored is None:
        for e in parsed.errors:
            typer.secho(f"✗ {e}", fg="red", err=True)
        raise typer.Exit(1)
    fm = parsed.frontmatter
    typer.echo(
        f"Stored Briefing version {stored.version} for idea-{idea_id}: "
        f"{fm.verdict} (confidence {fm.confidence}/5)."
    )


@app.command()
def daily(
    force: Annotated[bool, typer.Option(help="Run even if today's run already happened.")] = False,
) -> None:
    """The daily chain: collect, prefilter, extract, cluster, rank. launchd runs this at 06:00."""
    from ideafinder.config import load_config
    from ideafinder.daily import run_daily

    _configure_logging()
    _require_ready()
    settings = get_settings()
    report = asyncio.run(run_daily(settings, load_config(settings.config_dir), force=force))
    if report.skipped:
        typer.echo(f"Skipped: {report.skipped}.")
        return
    for step, result in report.steps.items():
        typer.echo(f"{step:<10} {result}")
    for e in report.errors:
        typer.secho(f"! {e}", fg="yellow")
    typer.echo(f"Daily run {report.status}.")
    if report.status == "failed":
        raise typer.Exit(1)


@app.command()
def backup(
    list_: Annotated[bool, typer.Option("--list", help="Show existing backups instead.")] = False,
) -> None:
    """Back up the database (pg_dump), keeping 14 days. launchd runs this at 02:30."""
    from ideafinder.backup import BackupError, list_backups
    from ideafinder.backup import backup as make_backup

    settings = get_settings()
    if list_:
        for b in list_backups(settings.backup_dir):
            typer.echo(f"{b.taken_at:%Y-%m-%d %H:%M}  {b.size / 1048576:7.1f} MB  {b.path}")
        return
    try:
        made, removed = make_backup(settings)
    except BackupError as exc:
        typer.secho(f"✗ {exc}", fg="red", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Backed up to {made.path} ({made.size / 1048576:.1f} MB).")
    if removed:
        typer.echo(f"Removed {len(removed)} backup(s) older than {settings.backup_keep_days} days.")


schedule_app = typer.Typer(
    no_args_is_help=True, help="The launchd schedule: daily run at 06:00, backup at 02:30."
)
app.add_typer(schedule_app, name="schedule")


@schedule_app.command("show")
def schedule_show() -> None:
    """Print the launchd jobs that `install` would set up, without installing anything."""
    import plistlib

    from ideafinder.schedule import JOBS, is_loaded, plist_for, plist_path

    settings = get_settings()
    for job in JOBS:
        state = "installed" if plist_path(job).exists() and is_loaded(job) else "not installed"
        typer.secho(f"# {plist_path(job)} ({state}): {job.what}", bold=True)
        typer.echo(plistlib.dumps(plist_for(job, settings)).decode())


@schedule_app.command("install")
def schedule_install() -> None:
    """Install (or update) the launchd jobs in ~/Library/LaunchAgents."""
    from ideafinder.schedule import JOBS, install

    for path, job in zip(install(get_settings()), JOBS, strict=True):
        typer.echo(f"Installed {path.name}: {job.what} at {job.hour:02d}:{job.minute:02d}")
    typer.echo(f"Logs go to {get_settings().log_dir}.")


@schedule_app.command("uninstall")
def schedule_uninstall() -> None:
    """Remove the launchd jobs."""
    from ideafinder.schedule import uninstall

    removed = uninstall()
    typer.echo("\n".join(f"Removed {p.name}" for p in removed) or "Nothing was installed.")


@app.command()
def trends(
    idea: Annotated[list[int] | None, typer.Option(help="Only these Idea ids.")] = None,
    max_ideas: Annotated[int, typer.Option(help="At most this many lookups.")] = 30,
) -> None:
    """Look up Google search interest for Inbox and Working List Ideas (the daily run does this)."""
    from ideafinder.pipeline.trends import refresh_trends

    _configure_logging()
    _require_ready()
    r = _ideas_session(lambda s: refresh_trends(s, max_ideas=max_ideas, idea_ids=idea))
    typer.echo(
        f"Refreshed {len(r.refreshed)} Ideas; {r.no_terms} have no search terms yet "
        "(they come with the Summary)."
    )
    if r.stopped:
        typer.secho(f"! {r.stopped}", fg="yellow")


eval_app = typer.Typer(no_args_is_help=True, help="The extraction test set (pick the local model).")
app.add_typer(eval_app, name="eval")


def _eval_session(fn):
    from ideafinder.db.engine import get_sessionmaker

    async def run():
        async with get_sessionmaker()() as session:
            return await fn(session)

    return asyncio.run(run())


@eval_app.command("sample")
def eval_sample() -> None:
    """Draw ~200 test Items across Sources (keeps any already-labelled ones)."""
    from ideafinder.pipeline.evaluation import sample

    _require_ready()
    for (source, kind), n in _eval_session(sample).items():
        typer.echo(f"{source:<16}{kind:<10}{n:>4}")


@eval_app.command("export")
def eval_export(path: Annotated[Path, typer.Argument(help="JSON Lines file to write.")]) -> None:
    """Write the test Items and any labels to a JSON Lines file."""
    from ideafinder.pipeline.evaluation import export_items

    n = _eval_session(lambda s: export_items(s, path))
    typer.echo(f"Wrote {n} test Items to {path}")


@eval_app.command("import")
def eval_import(
    path: Annotated[Path, typer.Argument(help="JSON Lines file with labels.")],
    by: Annotated[str, typer.Option(help="Who labelled them: claude or you.")] = "you",
) -> None:
    """Load labels for the test Items."""
    from ideafinder.pipeline.evaluation import import_labels

    n = _eval_session(lambda s: import_labels(s, path, by))
    typer.echo(f"Imported {n} labels")


@eval_app.command("run")
def eval_run(model: Annotated[str, typer.Argument(help="LM Studio model id.")]) -> None:
    """Run one model over every labelled test Item."""
    from ideafinder.config import load_config
    from ideafinder.db.engine import get_sessionmaker
    from ideafinder.llm import LLM
    from ideafinder.pipeline.evaluation import run_model, run_name

    _configure_logging()
    settings = get_settings()

    async def run():
        async with LLM(load_config(settings.config_dir).models) as llm:
            async with get_sessionmaker()() as session:
                return await run_model(session, llm, model, run_name(model))

    typer.echo(f"Scored {asyncio.run(run())} Items. See: ideafinder eval report")


@eval_app.command("report")
def eval_report() -> None:
    """Compare models on the test set."""
    from ideafinder.pipeline.evaluation import report

    scores = _eval_session(report)
    if not scores:
        typer.echo("No runs yet. Label the test set, then: ideafinder eval run <model>")
        return
    typer.echo(
        f"{'run':<36}{'items':>6}{'precision':>11}{'recall':>8}{'F1':>6}"
        f"{'kinds':>7}{'s/item':>8}{'tok/s':>7}"
    )
    for s in scores:
        tps = f"{s.output_tokens_per_second:.0f}" if s.output_tokens_per_second else "-"
        typer.echo(
            f"{s.run:<36}{s.items:>6}{s.precision:>11.0%}{s.recall:>8.0%}{s.f1:>6.2f}"
            f"{s.kind_agreement:>7.0%}{s.median_seconds:>8.1f}{tps:>7}"
        )


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Create the database if needed and apply all migrations."""
    from ideafinder.db import admin

    settings = get_settings()
    if admin.create_database_if_missing(settings):
        typer.echo("Created the database.")
    admin.upgrade(settings)
    typer.secho("Database is up to date.", fg="green")


@db_app.command("status")
def db_status() -> None:
    """Show the database version, pgvector and migration state."""
    from ideafinder.db import admin

    s = admin.status(get_settings())
    if not s.reachable:
        typer.secho(f"Not reachable: {s.error}", fg="red")
        raise typer.Exit(1)
    typer.echo(f"Postgres {s.server_version}, pgvector {s.pgvector_version or 'not installed'}")
    typer.echo(f"Migration {s.current_revision or 'none'} (latest {s.head_revision})")


@db_app.command("revision")
def db_revision(
    message: Annotated[str, typer.Option("--message", "-m", help="What the migration does.")],
    empty: Annotated[bool, typer.Option(help="Don't autogenerate from the models.")] = False,
) -> None:
    """Create a new migration (autogenerated from the models by default)."""
    from ideafinder.db import admin

    admin.new_revision(get_settings(), message, autogenerate=not empty)
