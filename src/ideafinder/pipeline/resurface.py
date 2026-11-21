"""Resurfacing (PLAN.md §3 step 10) and re-checking fit when the Fit profile changes.

An archived Idea returns to the Inbox as Resurfaced when:
- it was archived as too competitive / low willingness to pay / too big to build, and its evidence
  has changed a lot since: momentum_multiplier × the Signals of the 4 weeks before archiving, or
  a Community it hadn't been seen in (config/scoring.yaml `resurface`);
- or it was archived as "outside fit" and no longer breaks a Deal-breaker after a profile change.
"Not interested" and "dropped after research" are permanent.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import ResurfaceRule
from ideafinder.db.models import FitProfile, Idea, IdeaReview
from ideafinder.llm import LLM, ModelUnavailable, ServerUnreachable
from ideafinder.pipeline import fit
from ideafinder.pipeline.publish import EVIDENCE_REASONS, archive_outside_fit, record
from ideafinder.pipeline.score import Evidence

log = logging.getLogger(__name__)


def resurface(session: AsyncSession, idea: Idea, evidence: Evidence | None, why: str) -> None:
    idea.state = "inbox"
    idea.archive_reason = None
    idea.resurfaced_at = datetime.now(UTC)
    record(session, idea, "resurfaced", evidence, note=why)


def evidence_changed(e: Evidence, archived: IdeaReview, rules: ResurfaceRule) -> str | None:
    """Why the evidence now justifies another look, or None."""
    before = archived.recent_signals_at_action or 0
    if e.recent >= rules.momentum_multiplier * max(1, before):
        return f"{e.recent} Signals in the last 4 weeks (vs {before} when archived)"
    if rules.on_new_community:
        new = set(e.community_ids) - set(archived.community_ids_at_action or [])
        if new:
            return f"seen in {len(new)} new Communit{'y' if len(new) == 1 else 'ies'}"
    return None


async def resurface_on_evidence(
    session: AsyncSession, rules: ResurfaceRule, evidence: dict[int, Evidence]
) -> list[int]:
    ideas = (
        await session.scalars(
            select(Idea).where(
                Idea.state == "archived",
                Idea.archive_reason.in_(EVIDENCE_REASONS),
                Idea.merged_into_id.is_(None),
            )
        )
    ).all()
    resurfaced = []
    for idea in ideas:
        e = evidence.get(idea.id)
        archived = await session.scalar(
            select(IdeaReview)
            .where(IdeaReview.idea_id == idea.id, IdeaReview.action == "to_archive")
            .order_by(IdeaReview.created_at.desc(), IdeaReview.id.desc())
            .limit(1)
        )
        if e is None or archived is None:
            continue
        if why := evidence_changed(e, archived, rules):
            resurface(session, idea, e, why)
            resurfaced.append(idea.id)
    await session.commit()
    return resurfaced


@dataclass
class RefitReport:
    checked: int = 0
    resurfaced: list[int] = field(default_factory=list)
    outside_fit: list[int] = field(default_factory=list)
    failed: int = 0


async def refit(
    session: AsyncSession, llm: LLM, profile: FitProfile | None, evidence: dict[int, Evidence]
) -> RefitReport:
    """After a Fit profile change, check fit again for Inbox, Working List and "outside fit" Ideas.

    Incubating Ideas are checked when they're about to be published, so they're skipped here.
    An Inbox Idea that now breaks a Deal-breaker is archived as "outside fit"; a Working List Idea
    only gets its fit updated, since you've already chosen to pursue it.
    """
    report = RefitReport()
    if profile is None:
        return report
    ideas = (
        await session.scalars(
            select(Idea)
            .where(
                Idea.merged_into_id.is_(None),
                Idea.seeded.is_(False),
                (Idea.state.in_(("inbox", "working")))
                | ((Idea.state == "archived") & (Idea.archive_reason == "outside_fit")),
            )
            .order_by(Idea.id)
        )
    ).all()
    for idea in ideas:
        if not fit.needs_check(idea, profile):
            continue
        try:
            result = await fit.check(session, llm, idea, profile)
        except ServerUnreachable:
            raise
        except ModelUnavailable as exc:
            log.warning("Fit check failed for Idea %d: %s", idea.id, exc)
            await session.rollback()
            report.failed += 1
            continue
        report.checked += 1
        e = evidence.get(idea.id)
        if idea.state == "archived" and not result["deal_breakers"]:
            resurface(session, idea, e, "no longer outside fit after a Fit profile change")
            report.resurfaced.append(idea.id)
        elif idea.state == "inbox" and result["deal_breakers"]:
            await archive_outside_fit(session, idea, e)
            report.outside_fit.append(idea.id)
        await session.commit()
    return report
