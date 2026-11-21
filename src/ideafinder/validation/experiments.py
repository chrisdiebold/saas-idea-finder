"""Hypotheses, Experiments and Outcomes, and the Kill-criteria Proposed decision."""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import Briefing, Experiment, Hypothesis, ProposedDecision
from ideafinder.validation.working import ACTIVE, WorkingListError, get_item

TYPES = {
    "interviews": "Interviews",
    "landing_page": "Landing page",
    "pre_sale": "Pre-sale offer",
    "other": "Other",
}
OUTCOMES = {"pass": "Passed", "fail": "Failed", "inconclusive": "Inconclusive"}


async def current_hypothesis(session: AsyncSession, idea_id: int) -> Hypothesis | None:
    return await session.scalar(
        select(Hypothesis)
        .where(Hypothesis.idea_id == idea_id, Hypothesis.current)
        .order_by(Hypothesis.id.desc())
        .limit(1)
    )


async def set_hypothesis(
    session: AsyncSession, idea_id: int, statement: str, kill_criteria: str, source: str = "you"
) -> Hypothesis:
    statement, kill_criteria = statement.strip(), kill_criteria.strip()
    if not statement:
        raise WorkingListError("Write who would pay how much to have the problem solved")
    if not kill_criteria:
        raise WorkingListError("Set the Kill criteria before testing starts")
    await session.execute(
        update(Hypothesis).where(Hypothesis.idea_id == idea_id).values(current=False)
    )
    h = Hypothesis(idea_id=idea_id, statement=statement, kill_criteria=kill_criteria, source=source)
    session.add(h)
    await session.flush()
    return h


async def prefill_hypothesis(session: AsyncSession, idea_id: int) -> Hypothesis | None:
    """Use the Briefing's top Assumption if there's no Hypothesis yet (not committed). Yours
    are never overwritten."""
    if await current_hypothesis(session, idea_id) is not None:
        return None
    done = await session.scalar(
        select(Briefing)
        .where(Briefing.idea_id == idea_id, Briefing.status == "complete")
        .order_by(Briefing.version.desc())
        .limit(1)
    )
    if done is None:
        return None
    for a in done.frontmatter.get("assumptions", []):
        if a.get("hypothesis"):
            return await set_hypothesis(
                session,
                idea_id,
                a["hypothesis"],
                a.get("kill_criteria") or f"Evidence shows this is false: {a['claim']}",
                source="briefing",
            )
    return None


async def add_experiment(session: AsyncSession, idea_id: int, type_: str, plan: str) -> Experiment:
    item = await get_item(session, idea_id)
    if item.stage not in ACTIVE:
        raise WorkingListError("Resume the Idea before starting an Experiment")
    if type_ not in TYPES:
        raise WorkingListError(f"Choose a type: {', '.join(TYPES.values())}")
    if not plan.strip():
        raise WorkingListError("Describe the plan: what you'll do and what would count as a pass")
    hypothesis = await current_hypothesis(session, idea_id)
    if hypothesis is None:
        raise WorkingListError("Write the Hypothesis and Kill criteria first")
    experiment = Experiment(
        idea_id=idea_id,
        hypothesis_id=hypothesis.id,
        stage=item.stage,
        type=type_,
        plan_md=plan.strip(),
    )
    session.add(experiment)
    await session.commit()
    return experiment


async def record_outcome(
    session: AsyncSession,
    experiment_id: int,
    outcome: str,
    evidence: str,
    met_kill_criteria: bool = False,
) -> Experiment:
    """Close an Experiment. If it met the Kill criteria, propose archiving the Idea as
    "dropped after research" with the Experiment as evidence; you confirm it."""
    experiment = await session.get(Experiment, experiment_id)
    if experiment is None:
        raise WorkingListError(f"There's no Experiment {experiment_id}")
    if outcome not in OUTCOMES:
        raise WorkingListError("The Outcome is pass, fail or inconclusive")
    if not evidence.strip():
        raise WorkingListError("Note the evidence behind the Outcome")
    if met_kill_criteria and outcome == "pass":
        raise WorkingListError("A passed Experiment can't also meet the Kill criteria")
    experiment.outcome = outcome
    experiment.evidence_md = evidence.strip()
    experiment.met_kill_criteria = met_kill_criteria
    experiment.ended_at = datetime.now(UTC)
    if met_kill_criteria:
        session.add(
            ProposedDecision(
                idea_id=experiment.idea_id,
                kind="kill",
                proposal="archive",
                archive_reason="dropped_after_research",
                rationale=f"{TYPES[experiment.type]} met the Kill criteria: "
                f"{experiment.evidence_md[:280]}",
                confidence=1.0,
                model="kill criteria",
            )
        )
    await session.commit()
    return experiment
