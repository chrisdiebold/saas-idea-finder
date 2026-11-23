"""The daily chain: collect → prefilter → extract → embed and attach → form Ideas (weekly) → rank
→ Google Trends.

Started by launchd every morning (macOS runs a missed schedule when the Mac wakes), and also by
the worker's own daily job while `ideafinder up` is running. Whichever starts first does the
work: a database lock stops two runs overlapping, and a run that finds today's work already done
skips. Each step's outcome is recorded in `runs` (stage "daily") for the Health page.

Steps that need the local model are skipped, not failed, when LM Studio isn't running; the next
run picks the backlog up. Extraction gets a time budget so one run can't go on for hours.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text

from ideafinder import events
from ideafinder.collectors.store import finish_run, start_run
from ideafinder.config import Config
from ideafinder.db.engine import get_engine, get_sessionmaker
from ideafinder.db.models import Item, Run
from ideafinder.logs import duration
from ideafinder.settings import Settings

log = logging.getLogger(__name__)

LOCK_KEY = 7_316_001  # pg advisory lock id for the daily chain
ALREADY_DONE_WITHIN = timedelta(hours=20)
FORM_IDEAS_EVERY = timedelta(days=7)
EXTRACT_THREADS_PER_BATCH = 25


@dataclass
class DailyReport:
    skipped: str | None = None
    status: str = "succeeded"  # succeeded / partial / failed
    steps: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


async def _last_finished(session, stage: str, statuses=("succeeded", "partial")) -> datetime | None:
    return await session.scalar(
        select(Run.finished_at)
        .where(Run.stage == stage, Run.status.in_(statuses))
        .order_by(Run.finished_at.desc())
        .limit(1)
    )


async def _waiting(session) -> int:
    query = select(func.count()).select_from(Item)
    query = query.where(Item.prefilter == "keep", Item.extracted_at.is_(None))
    return await session.scalar(query) or 0


async def run_daily(
    settings: Settings,
    config: Config,
    *,
    force: bool = False,
    now: datetime | None = None,
    collect_transport=None,
    llm_transport=None,
    trends_fetch=None,
) -> DailyReport:
    from ideafinder.collectors.runner import collect
    from ideafinder.llm import LLM, ServerUnreachable
    from ideafinder.pipeline.cluster import attach_pending, form_ideas
    from ideafinder.pipeline.embed import embed_pending
    from ideafinder.pipeline.extract import extract_pending
    from ideafinder.pipeline.prefilter import prefilter_pending
    from ideafinder.pipeline.rank import rank
    from ideafinder.pipeline.trends import fetch_with_pytrends, refresh_trends

    trends_fetch = trends_fetch or fetch_with_pytrends

    now = now or datetime.now(UTC)
    report = DailyReport()
    async with get_engine().connect() as lock_conn:
        got = await lock_conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
        if not got:
            report.skipped = "another daily run is in progress"
            return report
        try:
            async with get_sessionmaker()() as session:
                last = await _last_finished(session, "daily")
                if not force and last and now - last < ALREADY_DONE_WITHIN:
                    report.skipped = f"already ran at {last:%Y-%m-%d %H:%M} UTC"
                    return report
                run_id = await start_run(session, "daily", None)
                await session.commit()
                started = time.monotonic()
                await events.record("daily", "Daily run started")

                try:
                    # 1. Collect. Each Source records its own run; failures don't stop the others.
                    try:
                        reports = await collect(config, settings, transport=collect_transport)
                        report.steps["collect"] = {r.source: r.status for r in reports}
                        failed = [r.source for r in reports if r.status == "failed"]
                        if failed:
                            report.errors.append(f"collection failed for: {', '.join(failed)}")
                        await events.record(
                            "collect",
                            events.describe_collection(report.steps["collect"]),
                            "warning" if failed else "info",
                        )
                    except Exception as exc:  # keep going: yesterday's Items still need processing
                        log.exception("collection failed")
                        report.errors.append(f"collection: {exc}")
                        await events.record("collect", f"Collection failed: {exc}", "error")

                    # 2. Prefilter (no model).
                    report.steps["prefilter"] = await prefilter_pending(session)
                    if said := events.describe_prefilter(report.steps["prefilter"]):
                        await events.record("prefilter", said)

                    # 3. Everything that needs LM Studio.
                    try:
                        async with LLM(config.models, transport=llm_transport) as llm:
                            budget_ends = time.monotonic() + settings.daily_extract_minutes * 60
                            extracted = {"chunks": 0, "items": 0, "signals": 0, "failed_chunks": 0}
                            while time.monotonic() < budget_ends:
                                r = await extract_pending(session, llm, EXTRACT_THREADS_PER_BATCH)
                                extracted["chunks"] += r.chunks
                                extracted["items"] += r.items
                                extracted["signals"] += r.signals
                                extracted["failed_chunks"] += r.failed_chunks
                                if r.chunks + r.failed_chunks == 0:
                                    break
                            report.steps["extract"] = extracted
                            report.steps["embedded"] = await embed_pending(session, llm)
                            rules = config.scoring.clustering
                            report.steps["attached"] = (
                                await attach_pending(session, rules)
                            ).attached
                            said = events.describe_extraction(
                                {
                                    **extracted,
                                    "embedded": report.steps["embedded"],
                                    "attached": report.steps["attached"],
                                    "waiting": await _waiting(session),
                                }
                            )
                            if said:
                                await events.record("extract", said)
                            last_form = await _last_finished(session, "form_ideas", ("succeeded",))
                            if force or last_form is None or now - last_form >= FORM_IDEAS_EVERY:
                                formed = await form_ideas(session, llm, rules)
                                report.steps["formed"] = len(formed.created)
                                await events.record(
                                    "ideas",
                                    events.describe_forming(
                                        {"pool": formed.pool, "created": len(formed.created)}
                                    ),
                                )
                                if formed.created:
                                    await attach_pending(session, rules)
                                form_run = await start_run(session, "form_ideas", None)
                                await finish_run(
                                    session, form_run, "succeeded", {"created": formed.created}, []
                                )
                                await session.commit()
                            ranked = await rank(session, llm, config.scoring, now=now)
                            report.steps["rank"] = {
                                "scored": ranked.scored,
                                "published": len(ranked.publish.published),
                                "resurfaced": len(ranked.resurfaced) + len(ranked.refit.resurfaced),
                                "outside_fit": len(ranked.publish.outside_fit)
                                + len(ranked.refit.outside_fit),
                                "proposed": ranked.propose.proposed,
                            }
                            await events.record(
                                "rank",
                                events.describe_ranking(report.steps["rank"]),
                                details={
                                    "ideas": await events.idea_titles(
                                        session, ranked.publish.published + ranked.resurfaced
                                    )
                                },
                            )
                    except ServerUnreachable as exc:
                        await session.rollback()
                        report.errors.append(f"model steps skipped: {exc}")
                        await events.record(
                            "daily", f"Model steps skipped: {exc}. They'll catch up next run.",
                            "warning",
                        )  # fmt: skip

                    # 4. Google Trends for Inbox and Working List Ideas (weekly per Idea; no
                    # model needed). Feeds tomorrow's Momentum.
                    trends = await refresh_trends(session, fetch=trends_fetch, now=now)
                    report.steps["trends"] = {
                        "refreshed": len(trends.refreshed),
                        "without_terms": trends.no_terms,
                    }
                    if trends.stopped:
                        report.steps["trends"]["stopped"] = trends.stopped
                        await events.record("trends", trends.stopped, "warning")
                    elif trends.refreshed:
                        await events.record(
                            "trends",
                            f"Google Trends: {events.plural(len(trends.refreshed), 'Idea')} "
                            "refreshed",
                        )
                    await events.prune(session)
                except Exception as exc:
                    await session.rollback()
                    report.status = "failed"
                    report.errors.append(f"{type(exc).__name__}: {exc}")
                    await finish_run(session, run_id, "failed", report.steps, report.errors)
                    await session.commit()
                    await events.record("daily", f"Daily run failed: {exc}", "error")
                    raise

                report.status = "succeeded" if not report.errors else "partial"
                await finish_run(session, run_id, report.status, report.steps, report.errors)
                await session.commit()
                took = duration(time.monotonic() - started)
                if report.status == "succeeded":
                    await events.record("daily", f"Daily run finished ({took})")
                else:
                    await events.record(
                        "daily",
                        f"Daily run finished with problems ({took}): {'; '.join(report.errors)}",
                        "warning",
                    )
                return report
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
