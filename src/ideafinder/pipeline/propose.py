"""Proposed decisions (PLAN.md §3 step 9): a suggested Review outcome for each Inbox Idea, with a
one-line rationale and a confidence. Never applied without you."""

import logging
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import (
    Competitor,
    FitProfile,
    Idea,
    IdeaSignal,
    ProposedDecision,
    Signal,
    SignalCompetitor,
)
from ideafinder.llm import LLM, ModelUnavailable, ServerUnreachable
from ideafinder.pipeline.fit import profile_as_text

log = logging.getLogger(__name__)

PROPOSABLE_REASONS = (
    "not_interested",
    "too_competitive",
    "low_willingness_to_pay",
    "too_big_to_build",
)
QUOTES = 6
COMPETITORS = 10

SYSTEM = """\
You help a solo founder triage software business ideas. Each idea is a recurring problem for a
kind of person (the Persona), backed by posts collected from the web. Propose ONE outcome:
- "working": worth an hour of research. The founder checks competitors, pricing and buyers in
  a Briefing afterwards, so this doesn't need proof the business works; it needs a specific,
  recurring problem that one developer could plausibly sell a solution to and that fits the
  founder.
- "archive", only when the evidence shows a deal-breaker. The main reason:
  - too_competitive: existing products already solve this problem well and nobody complains
    about them. The product the problem happens IN is not a competitor: a gap in Jira,
    WordPress or Shopify is an opening for an add-on, especially where an app marketplace
    exists.
  - low_willingness_to_pay: the people affected show they won't pay (consumers or hobbyists
    expecting free, complaints about price). Posts rarely mention paying, so "0 about paying"
    alone is not a reason; businesses losing staff time to a workflow problem often pay.
  - too_big_to_build: needs far more than one developer can build in a few months, or needs
    something one developer can't get (a platform's private access, regulation, a big sales team)
  - not_interested: clearly outside what this founder prefers, or not a software problem

rationale: one sentence a busy person can read in five seconds, naming the deciding evidence.
confidence: how sure you are, 0.0–1.0. Use 0.8 or more only when the evidence clearly points
one way. With fewer than 10 Signals, or when the call depends on facts not shown (prices,
competitors, market size), stay at 0.7 or below.
If the proposal is "working", archive_reason must be "none"."""

SCHEMA = {
    "type": "object",
    "properties": {
        "proposal": {"type": "string", "enum": ["working", "archive"]},
        "archive_reason": {"type": "string", "enum": ["none", *PROPOSABLE_REASONS]},
        "rationale": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["proposal", "archive_reason", "rationale", "confidence"],
    "additionalProperties": False,
}


async def render(session: AsyncSession, idea: Idea, profile: FitProfile | None) -> str:
    quotes = (
        await session.execute(
            select(Signal.kind, Signal.quote)
            .join(IdeaSignal, IdeaSignal.signal_id == Signal.id)
            .where(IdeaSignal.idea_id == idea.id, Signal.quote_verified)
            .order_by(Signal.intensity.desc(), IdeaSignal.similarity.desc().nulls_last())
            .limit(QUOTES)
        )
    ).all()
    competitors = (
        await session.scalars(
            select(Competitor.name)
            .join(SignalCompetitor, SignalCompetitor.competitor_id == Competitor.id)
            .join(IdeaSignal, IdeaSignal.signal_id == SignalCompetitor.signal_id)
            .where(IdeaSignal.idea_id == idea.id)
            .distinct()
            .limit(COMPETITORS)
        )
    ).all()
    b = idea.score_breakdown or {}
    e = b.get("evidence", {})
    parts = ", ".join(f"{k} {v:.2f}" for k, v in b.get("components", {}).items())
    lines = [
        f"IDEA: {idea.title}",
        f"Problem: {idea.problem_statement}",
        f"Persona: {idea.persona}",
        f"Score: {idea.score} / 100 ({parts})",
        f"Evidence: {e.get('signals')} Signals from {e.get('authors')} people in "
        f"{e.get('communities')} Communities; {e.get('payments')} about paying, "
        f"{e.get('complaints')} complaints about existing products; "
        f"{e.get('recent')} in the last 4 weeks vs {e.get('prior')} the 4 before.",
        f"Products mentioned: {', '.join(competitors) or 'none'}",
        "Quotes:",
        *[f'- [{kind}] "{quote[:300]}"' for kind, quote in quotes],
    ]
    if idea.fit:
        lines.append(
            f"Fit check: fit {idea.fit['fit']:.2f}, solo-buildable "
            f"{idea.fit['solo_buildable']:.2f}, business buyer: {idea.fit['b2b']}. "
            f"{idea.fit['notes']}"
        )
    lines.append("\nFOUNDER\n" + (profile_as_text(profile) if profile else "No Fit profile yet."))
    return "\n".join(lines)


async def propose(
    session: AsyncSession, llm: LLM, idea: Idea, profile: FitProfile | None, kind: str = "review"
) -> ProposedDecision:
    result = await llm.json(
        "propose_decision", SYSTEM, await render(session, idea, profile), SCHEMA, max_tokens=500
    )
    d = result.data
    archive = d["proposal"] == "archive"
    reason = d["archive_reason"] if archive and d["archive_reason"] != "none" else None
    if archive and reason is None:
        raise ModelUnavailable(f"{result.model} proposed archiving without a reason")
    decision = ProposedDecision(
        idea_id=idea.id,
        kind=kind,
        proposal=d["proposal"],
        archive_reason=reason,
        rationale=d["rationale"].strip(),
        confidence=min(1.0, max(0.0, float(d["confidence"]))),
        model=result.model,
    )
    session.add(decision)
    return decision


@dataclass
class ProposeReport:
    proposed: int = 0
    failed: int = 0


async def propose_missing(
    session: AsyncSession, llm: LLM, profile: FitProfile | None
) -> ProposeReport:
    """Give every Inbox Idea without a pending Proposed decision one."""
    pending = exists().where(
        ProposedDecision.idea_id == Idea.id, ProposedDecision.status == "pending"
    )
    ideas = (
        await session.scalars(
            select(Idea)
            .where(Idea.state == "inbox", Idea.merged_into_id.is_(None), ~pending)
            .order_by(Idea.id)
        )
    ).all()
    report = ProposeReport()
    for idea in ideas:
        try:
            await propose(session, llm, idea, profile)
        except ServerUnreachable:
            raise
        except ModelUnavailable as exc:
            log.warning("couldn't propose a decision for Idea %d: %s", idea.id, exc)
            await session.rollback()
            report.failed += 1
            continue
        await session.commit()
        report.proposed += 1
    return report
