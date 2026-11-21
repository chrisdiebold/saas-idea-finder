"""Running collection: every enabled Source at once, each stream resumed from its cursor."""

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import httpx2

from ideafinder.collectors import COLLECTORS
from ideafinder.collectors.base import HttpSource, RawItem, SourceGone, Stream
from ideafinder.collectors.store import (
    StoredItem,
    ensure_community,
    finish_run,
    get_cursor,
    set_cursor,
    start_run,
    sync_sources,
    upsert_items,
)
from ideafinder.collectors.text import detect_language, is_kept_language
from ideafinder.config import Config, SourceConfig
from ideafinder.db.engine import get_sessionmaker
from ideafinder.settings import Settings

log = logging.getLogger(__name__)

BATCH_SIZE = 200
CURSOR_OVERLAP = timedelta(days=1)  # re-check the last day each run, for late edits and votes
HTTP_TIMEOUT = 30.0


@dataclass
class StreamReport:
    fetched: int = 0
    new: int = 0
    updated: int = 0
    skipped_language: int = 0
    error: str | None = None


@dataclass
class SourceReport:
    source: str
    status: str = "succeeded"
    streams: dict[str, StreamReport] = field(default_factory=dict)

    @property
    def totals(self) -> StreamReport:
        t = StreamReport()
        for s in self.streams.values():
            t.fetched += s.fetched
            t.new += s.new
            t.updated += s.updated
            t.skipped_language += s.skipped_language
        return t

    @property
    def errors(self) -> list[str]:
        return [f"{key}: {s.error}" for key, s in self.streams.items() if s.error]


def _since(cursor: datetime | None, backfill_months: int, override: datetime | None) -> datetime:
    if override is not None:
        return override
    if cursor is not None:
        return cursor - CURSOR_OVERLAP
    return datetime.now(UTC) - timedelta(days=30 * backfill_months)


def _detect(items: list[RawItem]) -> list[str]:
    return [detect_language(f"{i.title or ''}\n{i.body}") for i in items]


async def _collect_stream(
    session,
    source: SourceConfig,
    source_id: int,
    stream: Stream,
    since: datetime,
    community_name,
    report: StreamReport,
) -> None:
    community_ids: dict[str, int] = {}
    batch: list[RawItem] = []

    async def flush() -> None:
        languages = await asyncio.to_thread(_detect, batch)  # CPU-bound; keep the loop free
        rows = []
        for item, language in zip(batch, languages, strict=True):
            if not is_kept_language(language):
                report.skipped_language += 1
                continue
            if item.community not in community_ids:
                watched = not source.communities or item.community in source.communities
                community_ids[item.community] = await ensure_community(
                    session, source_id, item.community, community_name(item.community), watched
                )
            rows.append(StoredItem(item, community_ids[item.community], language))
        new, updated = await upsert_items(session, source_id, rows)
        report.new += new
        report.updated += updated
        await session.commit()
        batch.clear()

    async for item in stream.fetch(since):
        report.fetched += 1
        batch.append(item)
        if len(batch) >= BATCH_SIZE:
            await flush()
    if batch:
        await flush()


async def collect_source(
    source: SourceConfig,
    config: Config,
    settings: Settings,
    source_id: int,
    client: httpx2.AsyncClient,
    since_override: datetime | None,
    save_cursors: bool,
) -> SourceReport:
    report = SourceReport(source=source.key)
    collector_cls = COLLECTORS.get(source.key)
    if collector_cls is None:
        report.status = "failed"
        report.streams["setup"] = StreamReport(error=f"no collector for source '{source.key}'")
        return report

    http = HttpSource(client, source.rate_limit_per_minute)
    collector = collector_cls(source, config.sources.queries, http, settings)
    started = datetime.now(UTC)

    async with get_sessionmaker()() as session:
        run_id = await start_run(session, "collect", source_id)
        await session.commit()
        for stream in collector.streams():
            stream_report = report.streams.setdefault(stream.key, StreamReport())
            cursor = await get_cursor(session, source_id, stream.key)
            since = _since(cursor, config.sources.backfill_months, since_override)
            try:
                await _collect_stream(
                    session,
                    source,
                    source_id,
                    stream,
                    since,
                    collector.community_name,
                    stream_report,
                )
            except SourceGone as exc:
                await session.rollback()
                stream_report.error = str(exc)
                log.warning("%s: %s", source.key, exc)
                break
            except Exception as exc:  # one broken stream mustn't stop the others
                await session.rollback()
                stream_report.error = f"{type(exc).__name__}: {exc}"
                log.exception("%s / %s failed", source.key, stream.key)
                continue
            if save_cursors:
                # Everything up to the start of this run has now been seen.
                await set_cursor(session, source_id, stream.key, started)
                await session.commit()
            log.info(
                "%s / %s: %d fetched, %d new",
                source.key,
                stream.key,
                stream_report.fetched,
                stream_report.new,
            )

        failed = sum(1 for s in report.streams.values() if s.error)
        report.status = (
            "succeeded" if failed == 0 else "failed" if failed == len(report.streams) else "partial"
        )
        await finish_run(
            session,
            run_id,
            report.status,
            stats={
                "totals": asdict(report.totals),
                "streams": {k: asdict(v) for k, v in report.streams.items()},
            },
            errors=report.errors,
        )
        await session.commit()
    return report


async def collect(
    config: Config,
    settings: Settings,
    source_keys: list[str] | None = None,
    since_override: datetime | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> list[SourceReport]:
    """Collect from the chosen (default: all enabled) Sources concurrently.

    With `since_override`, cursors are neither used nor saved, so a trial run over a short
    window doesn't stop the real backfill from happening later.
    """
    sources = [s for s in config.sources.enabled() if not source_keys or s.key in source_keys]
    unknown = set(source_keys or []) - {s.key for s in config.sources.enabled()}
    if unknown:
        raise ValueError(f"unknown or disabled sources: {sorted(unknown)}")

    async with get_sessionmaker()() as session:
        ids = await sync_sources(session, config.sources)
        await session.commit()

    async with httpx2.AsyncClient(
        headers={"User-Agent": settings.user_agent},
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        transport=transport,
    ) as client:
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(
                    collect_source(
                        s,
                        config,
                        settings,
                        ids[s.key],
                        client,
                        since_override,
                        save_cursors=since_override is None,
                    )
                )
                for s in sources
            ]
    return [t.result() for t in tasks]
