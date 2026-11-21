"""Storage for collection: Sources, Communities, Items, cursors and run records."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.collectors.base import RawItem
from ideafinder.config import SourcesConfig
from ideafinder.db.models import CollectorCursor, Community, Item, Run, Source


async def sync_sources(session: AsyncSession, config: SourcesConfig) -> dict[str, int]:
    """Mirror config/sources.yaml into the sources table; returns key -> id."""
    for s in config.sources:
        stmt = insert(Source).values(key=s.key, name=s.name, phase=s.phase, enabled=s.enabled)
        stmt = stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={
                "name": stmt.excluded.name,
                "phase": stmt.excluded.phase,
                "enabled": stmt.excluded.enabled,
            },
        )
        await session.execute(stmt)
    rows = await session.execute(select(Source.key, Source.id))
    return dict(rows.all())


async def ensure_community(
    session: AsyncSession, source_id: int, external_ref: str, name: str, watched: bool
) -> int:
    stmt = insert(Community).values(
        source_id=source_id,
        external_ref=external_ref,
        name=name,
        watch_status="watched" if watched else "suggested",
    )
    # Never downgrade a status you set; only fill in a better name.
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_id", "external_ref"], set_={"name": stmt.excluded.name}
    ).returning(Community.id)
    return (await session.execute(stmt)).scalar_one()


async def get_cursor(session: AsyncSession, source_id: int, stream: str) -> datetime | None:
    return await session.scalar(
        select(CollectorCursor.cursor).where(
            CollectorCursor.source_id == source_id, CollectorCursor.stream == stream
        )
    )


async def set_cursor(session: AsyncSession, source_id: int, stream: str, cursor: datetime) -> None:
    stmt = insert(CollectorCursor).values(source_id=source_id, stream=stream, cursor=cursor)
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_id", "stream"],
        set_={"cursor": stmt.excluded.cursor, "updated_at": func.now()},
    )
    await session.execute(stmt)


@dataclass(frozen=True)
class StoredItem:
    item: RawItem
    community_id: int
    language: str


async def upsert_items(
    session: AsyncSession, source_id: int, items: Sequence[StoredItem]
) -> tuple[int, int]:
    """Insert new Items and refresh changed fields of existing ones. Returns (new, updated)."""
    if not items:
        return 0, 0
    # One row per external_id: the same Item can appear twice in a batch (e.g. two queries).
    unique = {s.item.external_id: s for s in items}
    values = [
        {
            "source_id": source_id,
            "community_id": s.community_id,
            "external_id": s.item.external_id,
            "kind": s.item.kind,
            "thread_external_id": s.item.thread_external_id,
            "parent_external_id": s.item.parent_external_id,
            "url": s.item.url,
            "title": s.item.title,
            "body": s.item.body,
            "author": s.item.author,
            "language": s.language,
            "votes": s.item.votes,
            "extra": s.item.extra,
            "created_at": s.item.created_at,
            "raw": s.item.raw,
        }
        for s in unique.values()
    ]
    stmt = insert(Item).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["source_id", "external_id"],
        set_={
            "title": stmt.excluded.title,
            "body": stmt.excluded.body,
            "votes": stmt.excluded.votes,
            "extra": stmt.excluded.extra,
            "raw": stmt.excluded.raw,
            "fetched_at": func.now(),
        },
    ).returning(literal_column("(xmax = 0)").label("inserted"))  # xmax = 0 means a fresh insert
    inserted = (await session.execute(stmt)).scalars().all()
    new = sum(1 for flag in inserted if flag)
    return new, len(inserted) - new


async def start_run(session: AsyncSession, stage: str, source_id: int) -> int:
    run = Run(stage=stage, source_id=source_id, status="running")
    session.add(run)
    await session.flush()
    return run.id


async def finish_run(
    session: AsyncSession, run_id: int, status: str, stats: dict[str, Any], errors: list[Any]
) -> None:
    await session.execute(
        update(Run)
        .where(Run.id == run_id)
        .values(status=status, stats=stats, errors=errors, finished_at=func.now())
    )
