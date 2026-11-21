"""Your Reviews: moving an Idea to the Working List or the Archive, or restoring it.

Confirming or overriding the pending Proposed decision happens here too. The Inbox UI (M5) calls
these; until then the CLI does (`ideafinder ideas review`).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import ScoringConfig
from ideafinder.db.models import Idea, ProposedDecision
from ideafinder.pipeline.publish import ARCHIVE_REASONS, close_pending_proposals, record
from ideafinder.pipeline.score import gather_evidence

# Reasons you can choose; "outside fit" is only ever set by the Fit check.
CHOOSABLE_REASONS = tuple(r for r in ARCHIVE_REASONS if r != "outside_fit")


class ReviewError(Exception):
    """A Review that doesn't make sense for the Idea's current state."""


async def review(
    session: AsyncSession,
    config: ScoringConfig,
    idea_id: int,
    to: str,
    reason: str | None = None,
    note: str | None = None,
) -> Idea:
    """Apply a Review. `to` is "working", "archive" or "restore"."""
    idea = await session.get(Idea, idea_id)
    if idea is None:
        raise ReviewError(f"there's no Idea {idea_id}")
    if idea.merged_into_id is not None:
        raise ReviewError(f"Idea {idea_id} was merged into Idea {idea.merged_into_id}")

    if to == "working":
        if idea.state != "inbox":
            raise ReviewError(
                f"only Inbox Ideas can move to the Working List (this is {idea.state})"
            )
        if reason:
            raise ReviewError("an archive reason only applies when archiving")
    elif to == "archive":
        if idea.state not in ("inbox", "working"):
            raise ReviewError(
                f"only Inbox or Working List Ideas can be archived (this is {idea.state})"
            )
        if reason not in CHOOSABLE_REASONS:
            raise ReviewError(f"archiving needs a reason: {', '.join(CHOOSABLE_REASONS)}")
        if reason == "dropped_after_research" and idea.state != "working":
            raise ReviewError("'dropped_after_research' is for Ideas leaving the Working List")
    elif to == "restore":
        if idea.state != "archived":
            raise ReviewError(f"only archived Ideas can be restored (this is {idea.state})")
    else:
        raise ReviewError(f"unknown Review '{to}'; use working, archive or restore")

    proposal = await session.scalar(
        select(ProposedDecision)
        .where(ProposedDecision.idea_id == idea_id, ProposedDecision.status == "pending")
        .order_by(ProposedDecision.created_at.desc())
        .limit(1)
    )
    overrode = None
    if proposal is not None and to in ("working", "archive"):
        overrode = proposal.proposal != to
        await close_pending_proposals(session, idea_id, "overridden" if overrode else "confirmed")

    evidence = (await gather_evidence(session, config, [idea_id])).get(idea_id)
    if to == "working":
        idea.state, idea.archive_reason = "working", None
        record(session, idea, "to_working", evidence, overrode_proposal=overrode, note=note)
    elif to == "archive":
        idea.state, idea.archive_reason = "archived", reason
        record(
            session,
            idea,
            "to_archive",
            evidence,
            archive_reason=reason,
            overrode_proposal=overrode,
            note=note,
        )
    else:
        idea.state, idea.archive_reason = "inbox", None
        record(session, idea, "restored", evidence, note=note)
    await session.commit()
    if to == "working":
        await prepare_briefing(session, idea, evidence)
    return idea


async def prepare_briefing(session: AsyncSession, idea: Idea, evidence) -> None:
    """Moving to the Working List is when a Briefing is wanted (PLAN.md §4). With the manual
    provider that means a prompt waiting for you; nothing is sent anywhere."""
    from ideafinder.pipeline import briefing
    from ideafinder.pipeline.fit import get_profile

    if await briefing.latest(session, idea.id) is None:
        await briefing.request(session, idea, await get_profile(session), evidence)
