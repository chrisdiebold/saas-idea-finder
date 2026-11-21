"""Publishing (PLAN.md §3 step 8) and the shared bookkeeping for state changes.

An Incubating Idea reaches the Inbox once it has enough distinct Authors across enough
Communities, with at least one Pain or Request Signal (config/scoring.yaml `publish`). It is
Fit-checked first: breaking a Deal-breaker archives it as "outside fit" instead, with no
Proposed decision. Seeded Ideas are exempt from the Fit check.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import PublishThreshold
from ideafinder.db.models import FitProfile, Idea, IdeaReview, ProposedDecision
from ideafinder.llm import LLM, ModelUnavailable, ServerUnreachable
from ideafinder.pipeline import fit
from ideafinder.pipeline.score import Evidence

log = logging.getLogger(__name__)

ARCHIVE_REASONS = (
    "not_interested",  # permanent
    "too_competitive",  # these three resurface on new evidence
    "low_willingness_to_pay",
    "too_big_to_build",
    "dropped_after_research",  # permanent; for Ideas leaving the Working List
    "outside_fit",  # set automatically; resurfaces when the Fit profile changes
)
EVIDENCE_REASONS = ("too_competitive", "low_willingness_to_pay", "too_big_to_build")


def record(
    session: AsyncSession,
    idea: Idea,
    action: str,
    evidence: Evidence | None,
    *,
    archive_reason: str | None = None,
    overrode_proposal: bool | None = None,
    note: str | None = None,
) -> None:
    """Log a state change with the evidence at the time (not committed)."""
    session.add(
        IdeaReview(
            idea_id=idea.id,
            action=action,
            archive_reason=archive_reason,
            overrode_proposal=overrode_proposal,
            note=note,
            score_at_action=idea.score,
            signal_count_at_action=evidence.signals if evidence else None,
            recent_signals_at_action=evidence.recent if evidence else None,
            community_ids_at_action=evidence.community_ids if evidence else [],
        )
    )


async def close_pending_proposals(session: AsyncSession, idea_id: int, status: str) -> None:
    await session.execute(
        update(ProposedDecision)
        .where(ProposedDecision.idea_id == idea_id, ProposedDecision.status == "pending")
        .values(status=status, resolved_at=datetime.now(UTC))
    )


async def archive_outside_fit(session: AsyncSession, idea: Idea, evidence: Evidence | None) -> None:
    idea.state = "archived"
    idea.archive_reason = "outside_fit"
    await close_pending_proposals(session, idea.id, "superseded")
    record(
        session,
        idea,
        "auto_outside_fit",
        evidence,
        archive_reason="outside_fit",
        note="breaks: " + "; ".join(idea.fit["deal_breakers"]),
    )


def meets_threshold(e: Evidence, rules: PublishThreshold) -> bool:
    return (
        e.authors >= rules.min_authors
        and e.communities >= rules.min_communities
        and (e.pains_or_requests > 0 or not rules.require_pain_or_request)
    )


@dataclass
class PublishReport:
    published: list[int]
    outside_fit: list[int]
    fit_failed: int = 0


async def publish_ready(
    session: AsyncSession,
    llm: LLM,
    rules: PublishThreshold,
    evidence: dict[int, Evidence],
    profile: FitProfile | None,
) -> PublishReport:
    report = PublishReport([], [])
    ready = [i for i, e in evidence.items() if meets_threshold(e, rules)]
    if not ready:
        return report
    ideas = (
        await session.scalars(
            select(Idea).where(Idea.id.in_(ready), Idea.state == "incubating").order_by(Idea.id)
        )
    ).all()
    for idea in ideas:
        if fit.needs_check(idea, profile):
            try:
                await fit.check(session, llm, idea, profile)
            except ServerUnreachable:
                raise
            except ModelUnavailable as exc:
                # Don't publish without knowing about Deal-breakers; try again next run.
                log.warning("Fit check failed for Idea %d: %s", idea.id, exc)
                await session.rollback()
                report.fit_failed += 1
                continue
        e = evidence[idea.id]
        if idea.fit and idea.fit["deal_breakers"]:
            await archive_outside_fit(session, idea, e)
            report.outside_fit.append(idea.id)
        else:
            idea.state = "inbox"
            idea.published_at = datetime.now(UTC)
            record(session, idea, "published", e)
            report.published.append(idea.id)
        await session.commit()
    return report
