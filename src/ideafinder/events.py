"""App events: what the app did, in a sentence each. Printed in the terminal and kept for the
Activity page.

Jobs record one event when they finish (ideafinder.worker), the daily run one per step
(ideafinder.daily). Heartbeats and other routine bookkeeping aren't events.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import Event, Idea

LEVELS = {"info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
KEEP_DAYS = 90


async def record(
    kind: str,
    message: str,
    level: str = "info",
    details: dict[str, Any] | None = None,
    quiet_for: timedelta | None = None,
) -> None:
    """Print the event and store it. Uses its own session, so it never touches the caller's
    transaction; failing to store it is logged, not raised.

    With `quiet_for`, the same message isn't repeated within that time (a closed LM Studio
    would otherwise say so every 10 minutes)."""
    logger = logging.getLogger(f"ideafinder.{kind}")
    printed = False
    try:
        async with get_sessionmaker()() as session:
            if quiet_for is not None and await _said_recently(session, kind, message, quiet_for):
                return
            logger.log(LEVELS[level], message)
            printed = True
            session.add(Event(kind=kind, level=level, message=message, details=details or {}))
            await session.commit()
    except Exception:
        if not printed:  # the database is the problem: still show the event in the terminal
            logger.log(LEVELS[level], message)
        logging.getLogger(__name__).warning("couldn't store an event", exc_info=True)


async def _said_recently(session: AsyncSession, kind: str, message: str, within: timedelta) -> bool:
    since = datetime.now(UTC) - within
    query = exists().where(Event.kind == kind, Event.message == message, Event.at > since)
    return bool(await session.scalar(select(query)))


async def prune(session: AsyncSession, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=KEEP_DAYS)
    result = await session.execute(delete(Event).where(Event.at < cutoff))
    return result.rowcount or 0


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


# --- sentences for job results (the worker and the daily run say things the same way) ---


def describe_extraction(r: dict) -> str | None:
    if not (r["items"] or r["failed_chunks"] or r["embedded"]):
        return None  # nothing was waiting
    text = f"Extraction: {plural(r['items'], 'Item')} → {plural(r['signals'], 'Signal')}"
    if r["failed_chunks"]:
        text += f" ({plural(r['failed_chunks'], 'chunk')} failed, will retry)"
    text += f"; {r['embedded']:,} embedded, {r['attached']:,} joined existing Ideas"
    return text + f". {r['waiting']:,} Items still waiting"


def describe_collection(r: dict) -> str:
    failed = sorted(k for k, v in r.items() if v == "failed")
    text = f"Collection: {plural(len(r) - len(failed), 'Source')} collected"
    return text + (f"; failed: {', '.join(failed)}" if failed else "")


def describe_prefilter(counts: dict) -> str | None:
    if not counts:
        return None
    outcomes: dict[str, int] = {}
    for key, n in counts.items():
        outcome = key.split(":")[0]
        outcomes[outcome] = outcomes.get(outcome, 0) + n
    parts = [f"{outcomes.pop('keep', 0):,} kept for extraction"]
    parts += [f"{n:,} {outcome}" for outcome, n in sorted(outcomes.items())]
    return "Prefilter: " + ", ".join(parts)


def describe_forming(r: dict) -> str:
    return f"Forming Ideas: {r['pool']:,} pooled Signals → {plural(r['created'], 'new Idea')}"


def describe_ranking(r: dict) -> str:
    text = f"Ranking: {plural(r['scored'], 'Idea')} scored, {r['published']:,} new in the Inbox"
    extras = [
        f"{r['resurfaced']:,} resurfaced" if r["resurfaced"] else "",
        f"{r['outside_fit']:,} archived as outside your fit" if r["outside_fit"] else "",
        f"{plural(r['proposed'], 'proposal')} written" if r["proposed"] else "",
    ]
    return ", ".join([text, *[e for e in extras if e]])


async def idea_titles(session: AsyncSession, idea_ids: list[int]) -> list[list]:
    """[[id, title], ...] for event details; the Activity page links them."""
    if not idea_ids:
        return []
    rows = await session.execute(select(Idea.id, Idea.title).where(Idea.id.in_(idea_ids)))
    return [[i, t] for i, t in rows.all()]
