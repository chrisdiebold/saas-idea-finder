"""The procrastinate job-queue app, its queues, and its tasks.

Two queues keep the work separable (ADR 0003):
- `collect`: collection and everything that doesn't need the local model. Can move to the Pi later.
- `model`: everything that uses the local model or embeddings. Always runs on the Mac.
"""

import functools
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from procrastinate import App, PsycopgConnector
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ideafinder import events
from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import Item, WorkerHeartbeat
from ideafinder.events import (
    describe_collection,
    describe_extraction,
    describe_forming,
    describe_prefilter,
    describe_ranking,
)
from ideafinder.logs import duration
from ideafinder.settings import get_settings

COLLECT_QUEUE = "collect"
MODEL_QUEUE = "model"
QUEUES = (COLLECT_QUEUE, MODEL_QUEUE)

app = App(connector=PsycopgConnector(conninfo=get_settings().database_url))


async def record_heartbeat(queue: str) -> None:
    stmt = insert(WorkerHeartbeat).values(queue=queue)
    stmt = stmt.on_conflict_do_update(index_elements=["queue"], set_={"last_seen": func.now()})
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(stmt)


def reported(
    kind: str, title: str, describe: Callable[[dict], str | None], skip_level: str = "warning"
) -> Callable[[Callable[..., Awaitable[dict]]], Callable[..., Awaitable[dict]]]:
    """Record an event when the job finishes: `describe` turns its result into a sentence (None:
    nothing worth saying). Skips and failures are recorded as warnings and errors."""

    def wrap(job: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        @functools.wraps(job)
        async def run(*args: Any, **kwargs: Any) -> dict:
            started = time.monotonic()
            try:
                result = await job(*args, **kwargs)
            except Exception as exc:
                await events.record(kind, f"{title} failed: {type(exc).__name__}: {exc}", "error")
                raise
            if "skipped" in result:
                # e.g. LM Studio is closed: say so once an hour, not every 10 minutes.
                await events.record(
                    kind, f"{title} skipped: {result['skipped']}", skip_level,
                    quiet_for=timedelta(hours=1),
                )  # fmt: skip
            elif message := describe(result):
                took = duration(time.monotonic() - started)
                await events.record(kind, f"{message} ({took})", details=result)
            return result

        return run

    return wrap


# One heartbeat per queue, every minute, so the status page can tell whether each queue is served.
@app.periodic(cron="* * * * *", periodic_id="heartbeat-collect")
@app.task(queue=COLLECT_QUEUE, name="heartbeat_collect")
async def heartbeat_collect(timestamp: int) -> None:
    await record_heartbeat(COLLECT_QUEUE)


@app.periodic(cron="* * * * *", periodic_id="heartbeat-model")
@app.task(queue=MODEL_QUEUE, name="heartbeat_model")
async def heartbeat_model(timestamp: int) -> None:
    await record_heartbeat(MODEL_QUEUE)


@app.task(queue=COLLECT_QUEUE, name="collect_sources", queueing_lock="collect_sources")
@reported("collect", "Collection", describe_collection)
async def collect_sources(source_keys: list[str] | None = None) -> dict:
    """Collect from all (or the given) Sources. Only one collection is queued at a time."""
    from ideafinder.collectors.runner import collect
    from ideafinder.config import load_config

    settings = get_settings()
    reports = await collect(load_config(settings.config_dir), settings, source_keys)
    await prefilter_items.defer_async()
    return {r.source: r.status for r in reports}


@app.task(queue=COLLECT_QUEUE, name="prefilter_items", queueing_lock="prefilter_items")
@reported("prefilter", "Prefilter", describe_prefilter)
async def prefilter_items() -> dict[str, int]:
    """Decide which new Items go to extraction. Fast and model-free, so it runs on `collect`."""
    from ideafinder.pipeline.prefilter import prefilter_pending

    async with get_sessionmaker()() as session:
        return await prefilter_pending(session)


EXTRACT_THREADS_PER_JOB = 25  # a few minutes of model time, so jobs stay short and resumable


@app.periodic(cron="*/10 * * * *", periodic_id="extract-signals")
@app.task(queue=MODEL_QUEUE, name="extract_signals", queueing_lock="extract_signals")
@reported("extract", "Extraction", describe_extraction)
async def extract_signals(timestamp: int) -> dict:
    """Work through the extraction backlog a little at a time, embed the new Signals, and attach
    them to existing Ideas.

    If LM Studio isn't running, the job fails quietly and the next one retries in 10 minutes.
    """
    from ideafinder.config import load_config
    from ideafinder.llm import LLM, ServerUnreachable
    from ideafinder.pipeline.cluster import attach_pending
    from ideafinder.pipeline.embed import embed_pending
    from ideafinder.pipeline.extract import extract_pending

    settings = get_settings()
    config = load_config(settings.config_dir)
    try:
        async with LLM(config.models) as llm:
            async with get_sessionmaker()() as session:
                report = await extract_pending(session, llm, EXTRACT_THREADS_PER_JOB)
                embedded = await embed_pending(session, llm)
                attached = await attach_pending(session, config.scoring.clustering)
                waiting = await session.scalar(
                    select(func.count())
                    .select_from(Item)
                    .where(Item.prefilter == "keep", Item.extracted_at.is_(None))
                )
    except ServerUnreachable as exc:
        return {"skipped": str(exc)}
    return {
        "items": report.items,
        "signals": report.signals,
        "embedded": embedded,
        "attached": attached.attached,
        "failed_chunks": report.failed_chunks,
        "waiting": waiting or 0,
    }


@app.task(queue=MODEL_QUEUE, name="form_ideas", queueing_lock="form_ideas")
@reported("ideas", "Forming Ideas", describe_forming)
async def form_ideas(timestamp: int) -> dict:
    """Cluster the holding pool into new Incubating Ideas and name them (the daily chain does
    this weekly; this task is for running it on request)."""
    from ideafinder.config import load_config
    from ideafinder.llm import LLM, ServerUnreachable
    from ideafinder.pipeline.cluster import form_ideas as form

    settings = get_settings()
    config = load_config(settings.config_dir)
    try:
        async with LLM(config.models) as llm:
            async with get_sessionmaker()() as session:
                report = await form(session, llm, config.scoring.clustering)
    except ServerUnreachable as exc:
        return {"skipped": str(exc)}
    return {"pool": report.pool, "created": len(report.created), "failed": report.failed}


@app.task(queue=MODEL_QUEUE, name="rank_ideas", queueing_lock="rank_ideas")
@reported("rank", "Ranking", describe_ranking)
async def rank_ideas(timestamp: int) -> dict:
    """Score, check fit, publish, resurface, propose, snapshot (the daily chain does this; this
    task is for "Run ranking now").

    Runs on `model` because the Fit check and Proposed decisions need the local model.
    """
    from ideafinder.config import load_config
    from ideafinder.llm import LLM, ServerUnreachable
    from ideafinder.pipeline.rank import rank

    settings = get_settings()
    config = load_config(settings.config_dir)
    try:
        async with LLM(config.models) as llm:
            async with get_sessionmaker()() as session:
                r = await rank(session, llm, config.scoring)
                new = await events.idea_titles(session, r.publish.published + r.resurfaced)
    except ServerUnreachable as exc:
        return {"skipped": str(exc)}
    return {
        "ideas": new,  # shown as links on the Activity page
        "scored": r.scored,
        "published": len(r.publish.published),
        "outside_fit": len(r.publish.outside_fit) + len(r.refit.outside_fit),
        "resurfaced": len(r.resurfaced) + len(r.refit.resurfaced),
        "proposed": r.propose.proposed,
    }


@app.task(queue=MODEL_QUEUE, name="summarize_idea", queueing_lock="summarize_idea")
@reported("summary", "Summary", lambda r: "Summary written" if r["written"] else None)
async def summarize_idea(idea_id: int) -> dict:
    """Write one Idea's Summary now (e.g. right after you add a Seeded Idea)."""
    from ideafinder.config import load_config
    from ideafinder.db.models import Idea
    from ideafinder.llm import LLM, ServerUnreachable
    from ideafinder.pipeline.score import gather_evidence
    from ideafinder.pipeline.summary import summarize

    settings = get_settings()
    config = load_config(settings.config_dir)
    try:
        async with LLM(config.models) as llm:
            async with get_sessionmaker()() as session:
                idea = await session.get(Idea, idea_id)
                if idea is None:
                    return {"skipped": "no such Idea"}
                e = (await gather_evidence(session, config.scoring, [idea_id])).get(idea_id)
                written = await summarize(session, llm, idea, e)
                await session.commit()
    except ServerUnreachable as exc:
        return {"skipped": str(exc)}
    return {"written": written is not None}


@app.task(queue=MODEL_QUEUE, name="write_lead_openers")
@reported("leads", "Lead openers", lambda r: f"Lead openers: {r['written']:,} written")
async def write_lead_openers(idea_id: int) -> dict:
    """Suggest an opening line for each new Lead of an Idea."""
    from ideafinder.config import load_config
    from ideafinder.llm import LLM, ServerUnreachable
    from ideafinder.validation.leads import write_openers

    config = load_config(get_settings().config_dir)
    try:
        async with LLM(config.models) as llm, get_sessionmaker()() as session:
            return {"written": await write_openers(session, llm, idea_id)}
    except ServerUnreachable as exc:
        return {"skipped": str(exc)}


@app.task(queue=MODEL_QUEUE, name="generate_validation_kit")
@reported("validation", "Validation kit", lambda r: "Validation kit written")
async def generate_validation_kit(idea_id: int) -> dict:
    """Write the Validation kit (run when an Idea reaches Validating, or on request)."""
    from ideafinder.config import load_config
    from ideafinder.llm import LLM, ServerUnreachable
    from ideafinder.validation.kits import generate_kit

    config = load_config(get_settings().config_dir)
    try:
        async with LLM(config.models) as llm, get_sessionmaker()() as session:
            kit = await generate_kit(session, llm, idea_id)
    except ServerUnreachable as exc:
        return {"skipped": str(exc)}
    return {"kit": kit.id}


async def defer(task_name: str, **kwargs) -> None:
    """Queue a job from outside a worker (the web app). Uses its own connection, since the web
    app may share a process with a running worker (`ideafinder up`)."""
    deferrer = app.with_connector(PsycopgConnector(conninfo=get_settings().database_url))
    async with deferrer.open_async():
        await deferrer.configure_task(task_name).defer_async(**kwargs)


@app.periodic(cron="0 6 * * *", periodic_id="daily")
@app.task(queue=MODEL_QUEUE, name="daily", queueing_lock="daily")
@reported(
    "daily", "Daily run", lambda r: None, skip_level="info"
)  # run_daily records its own steps
async def daily(timestamp: int) -> dict:
    """The daily chain, while `ideafinder up` is running. If launchd already ran it today, this
    finds the work done and skips (ideafinder.daily)."""
    from ideafinder.config import load_config
    from ideafinder.daily import run_daily

    settings = get_settings()
    report = await run_daily(settings, load_config(settings.config_dir))
    return {"skipped": report.skipped} if report.skipped else {"status": report.status}


WORKER_OPTIONS = {
    "delete_jobs": "successful",  # keep only failed jobs, so the jobs table doesn't grow forever
}
