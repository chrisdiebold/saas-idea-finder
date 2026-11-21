"""The Working List (PLAN.md §5): Stages, Time limits, the Active limit and priority.

- Stages: Researching → Validating → Building, plus Paused. Moving forward needs a passed
  Experiment in the current Stage; Building is the last.
- Time limit: default 2 weeks per Stage, editable per Idea. When it runs out you extend it once,
  Pause, or drop the Idea; a second extension isn't offered.
- Active limit: at most N Ideas in Researching, Validating or Building. Moving one in when the
  list is full starts a Head-to-head against the lowest-priority active Idea.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import ValidationRules
from ideafinder.db.models import Experiment, HeadToHead, Idea, WorkingItem

ACTIVE = ("researching", "validating", "building")
NEXT = {"researching": "validating", "validating": "building"}
STAGE_LABELS = {
    "researching": "Researching",
    "validating": "Validating",
    "building": "Building",
    "paused": "Paused",
}


class WorkingListError(Exception):
    """A Working List action that the rules don't allow right now."""


def deadline(item: WorkingItem) -> datetime:
    days = item.time_limit_days * (2 if item.extended_in_stage else 1)
    return item.stage_started_at + timedelta(days=days)


def is_expired(item: WorkingItem, now: datetime | None = None) -> bool:
    return item.stage in ACTIVE and deadline(item) <= (now or datetime.now(UTC))


async def get_item(session: AsyncSession, idea_id: int) -> WorkingItem:
    item = await session.get(WorkingItem, idea_id)
    if item is None:
        raise WorkingListError(f"Idea {idea_id} isn't on the Working List")
    return item


async def active_items(session: AsyncSession) -> list[WorkingItem]:
    rows = await session.scalars(
        select(WorkingItem)
        .join(Idea, Idea.id == WorkingItem.idea_id)
        .where(WorkingItem.stage.in_(ACTIVE), Idea.state == "working")
        .order_by(WorkingItem.priority_rank)
    )
    return list(rows.all())


async def _start_head_to_head_if_full(
    session: AsyncSession, incoming: WorkingItem, rules: ValidationRules
) -> HeadToHead | None:
    others = [i for i in await active_items(session) if i.idea_id != incoming.idea_id]
    if len(others) < rules.active_limit:
        return None
    lowest = max(others, key=lambda i: i.priority_rank)
    h2h = HeadToHead(idea_a=incoming.idea_id, idea_b=lowest.idea_id, trigger="full_list")
    session.add(h2h)
    return h2h


async def join(session: AsyncSession, idea: Idea, rules: ValidationRules) -> WorkingItem:
    """Put an Idea on the Working List in Researching (not committed). If the list is already
    full, a Head-to-head against the lowest-priority active Idea decides which one stays."""
    from ideafinder.validation.experiments import prefill_hypothesis

    lowest_rank = await session.scalar(select(func.max(WorkingItem.priority_rank))) or 0
    item = await session.get(WorkingItem, idea.id)
    now = datetime.now(UTC)
    if item is None:
        item = WorkingItem(idea_id=idea.id, priority_rank=lowest_rank + 1)
        session.add(item)
    item.stage, item.paused_from = "researching", None
    item.stage_started_at, item.extended_in_stage = now, False
    item.time_limit_days = rules.time_limit_days
    await session.flush()
    await prefill_hypothesis(session, idea.id)
    await _start_head_to_head_if_full(session, item, rules)
    return item


async def leave(session: AsyncSession, idea_id: int) -> None:
    """The Idea left the Working List (archived or merged): drop its place and open choices."""
    item = await session.get(WorkingItem, idea_id)
    if item is not None:
        await session.delete(item)
    pending = await session.scalars(
        select(HeadToHead).where(
            HeadToHead.winner.is_(None),
            (HeadToHead.idea_a == idea_id) | (HeadToHead.idea_b == idea_id),
        )
    )
    for h in pending.all():
        await session.delete(h)


async def passed_in_stage(session: AsyncSession, idea_id: int, stage: str) -> bool:
    return bool(
        await session.scalar(
            select(func.count()).where(
                Experiment.idea_id == idea_id,
                Experiment.stage == stage,
                Experiment.outcome == "pass",
            )
        )
    )


async def advance(session: AsyncSession, idea_id: int) -> WorkingItem:
    item = await get_item(session, idea_id)
    if item.stage not in NEXT:
        raise WorkingListError(
            "Building is the last Stage" if item.stage == "building" else "Resume the Idea first"
        )
    if not await passed_in_stage(session, idea_id, item.stage):
        raise WorkingListError(
            f"Moving on from {STAGE_LABELS[item.stage]} needs a passed Experiment in it"
        )
    item.stage = NEXT[item.stage]
    item.stage_started_at, item.extended_in_stage = datetime.now(UTC), False
    await session.commit()
    return item


async def pause(session: AsyncSession, idea_id: int) -> WorkingItem:
    item = await get_item(session, idea_id)
    if item.stage == "paused":
        raise WorkingListError("It's already Paused")
    item.paused_from, item.stage = item.stage, "paused"
    await session.commit()
    return item


async def resume(session: AsyncSession, idea_id: int, rules: ValidationRules) -> WorkingItem:
    """Back to the Stage it was paused in, with a fresh Time limit. Counts against the Active
    limit, so a full list starts a Head-to-head."""
    item = await get_item(session, idea_id)
    if item.stage != "paused":
        raise WorkingListError("Only Paused Ideas can be resumed")
    item.stage, item.paused_from = item.paused_from or "researching", None
    item.stage_started_at, item.extended_in_stage = datetime.now(UTC), False
    await session.flush()
    await _start_head_to_head_if_full(session, item, rules)
    await session.commit()
    return item


async def extend(session: AsyncSession, idea_id: int) -> WorkingItem:
    item = await get_item(session, idea_id)
    if item.extended_in_stage:
        raise WorkingListError("The Time limit was already extended once in this Stage")
    item.extended_in_stage = True
    await session.commit()
    return item


async def set_time_limit(session: AsyncSession, idea_id: int, days: int) -> WorkingItem:
    if not 1 <= days <= 365:
        raise WorkingListError("A Time limit is between 1 and 365 days")
    item = await get_item(session, idea_id)
    item.time_limit_days = days
    await session.commit()
    return item
