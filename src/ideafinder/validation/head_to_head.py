"""Head-to-heads: which of two Ideas deserves your time next. Priority on the Working List comes
only from these (no drag-to-reorder).

- full_list: an Idea moved in while the Active limit was reached. The loser is Paused or
  archived, your choice.
- periodic: the app asks now and then between two active Ideas next to each other in priority,
  so the order stays yours. Only the order changes.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import ScoringConfig
from ideafinder.db.models import HeadToHead, WorkingItem
from ideafinder.validation.working import WorkingListError, active_items, pause

PERIODIC_EVERY_DAYS = 7


async def pending(session: AsyncSession) -> list[HeadToHead]:
    rows = await session.scalars(
        select(HeadToHead).where(HeadToHead.winner.is_(None)).order_by(HeadToHead.created_at)
    )
    return list(rows.all())


async def _rank_ahead(session: AsyncSession, winner_id: int, loser_id: int) -> None:
    """Put the winner directly ahead of the loser if it was behind; others keep their order."""
    items = list(
        (await session.scalars(select(WorkingItem).order_by(WorkingItem.priority_rank))).all()
    )
    order = [i.idea_id for i in items]
    if order.index(winner_id) < order.index(loser_id):
        return
    order.remove(winner_id)
    order.insert(order.index(loser_id), winner_id)
    by_id = {i.idea_id: i for i in items}
    for rank, idea_id in enumerate(order, start=1):
        by_id[idea_id].priority_rank = rank


async def decide(
    session: AsyncSession,
    config: ScoringConfig,
    h2h_id: int,
    winner_id: int,
    loser_action: str = "kept",
    reason: str | None = None,
) -> HeadToHead:
    from ideafinder.pipeline.review import review

    h2h = await session.get(HeadToHead, h2h_id)
    if h2h is None or h2h.winner is not None:
        raise WorkingListError("That Head-to-head was already decided")
    if winner_id not in (h2h.idea_a, h2h.idea_b):
        raise WorkingListError("The winner must be one of the two Ideas")
    loser_id = h2h.idea_b if winner_id == h2h.idea_a else h2h.idea_a
    if h2h.trigger == "full_list" and loser_action not in ("paused", "archived"):
        raise WorkingListError("The list is full: Pause or archive the other Idea")
    if h2h.trigger == "periodic":
        loser_action = "kept"
    await _rank_ahead(session, winner_id, loser_id)
    h2h.winner, h2h.loser_action, h2h.decided_at = winner_id, loser_action, datetime.now(UTC)
    await session.commit()
    if loser_action == "paused":
        await pause(session, loser_id)
    elif loser_action == "archived":
        await review(session, config, loser_id, "archive", reason or "dropped_after_research")
    return h2h


async def ask_periodic(session: AsyncSession, now: datetime | None = None) -> HeadToHead | None:
    """At most once a week, ask about the adjacent pair of active Ideas compared longest ago."""
    now = now or datetime.now(UTC)
    recent = await session.scalar(
        select(HeadToHead.id).where(
            HeadToHead.trigger == "periodic",
            HeadToHead.created_at >= now - timedelta(days=PERIODIC_EVERY_DAYS),
        )
    )
    if recent or await pending(session):
        return None
    active = await active_items(session)
    if len(active) < 2:
        return None
    decided = (
        await session.scalars(select(HeadToHead).where(HeadToHead.winner.is_not(None)))
    ).all()

    def last_compared(a: int, b: int) -> datetime:
        times = [h.decided_at for h in decided if {h.idea_a, h.idea_b} == {a, b}]
        return max(times) if times else datetime.min.replace(tzinfo=UTC)

    pairs = [(x.idea_id, y.idea_id) for x, y in zip(active, active[1:], strict=False)]
    a, b = min(pairs, key=lambda p: last_compared(*p))
    h2h = HeadToHead(idea_a=a, idea_b=b, trigger="periodic")
    session.add(h2h)
    await session.commit()
    return h2h
