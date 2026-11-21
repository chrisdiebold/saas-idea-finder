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
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("alembic").setLevel(logging.WARNING)  # its plugin setup is noisy at INFO


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
        uvicorn.Config("ideafinder.web.app:app", host=settings.web_host, port=settings.web_port)
    )


async def _run_worker(queues: list[str]) -> None:
    from ideafinder.worker import WORKER_OPTIONS
    from ideafinder.worker import app as worker_app

    async with worker_app.open_async():
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
) -> None:
    """Extract Signals from kept Items with the local model (LM Studio must be running)."""
    from ideafinder.config import load_config
    from ideafinder.db.engine import get_sessionmaker
    from ideafinder.llm import LLM, ModelUnavailable
    from ideafinder.pipeline.extract import extract_pending

    _configure_logging()
    _require_ready()
    settings = get_settings()

    async def run():
        async with LLM(load_config(settings.config_dir).models) as llm:
            async with get_sessionmaker()() as session:
                return await extract_pending(session, llm, threads, model)

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
