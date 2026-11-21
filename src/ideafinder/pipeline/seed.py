"""Seeded Ideas: Ideas you add yourself. They go straight to the Working List, skip the Fit check,
and pick up matching Signals from the holding pool (and keep attracting new ones like any Idea)."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import ClusteringRules, ValidationRules
from ideafinder.db.models import Idea, IdeaSignal, Signal
from ideafinder.llm import LLM
from ideafinder.pipeline.cluster import link_related, problem_embedding, refresh_ideas, unassigned
from ideafinder.pipeline.embed import PREFIX
from ideafinder.pipeline.publish import record
from ideafinder.validation.working import join


@dataclass(frozen=True)
class Seeded:
    idea: Idea
    attached: int


async def create_seeded(
    session: AsyncSession,
    llm: LLM,
    rules: ClusteringRules,
    validation: ValidationRules,
    title: str,
    problem_statement: str,
    persona: str,
) -> Seeded:
    title, problem_statement, persona = title.strip(), problem_statement.strip(), persona.strip()
    if not (title and problem_statement and persona):
        raise ValueError("a Seeded Idea needs a title, a problem and a Persona")
    # Same text shape as Signal embeddings, so the seed sits where its Signals would.
    [centre] = await llm.embed([f"{PREFIX}{problem_statement} (Persona: {persona})"])
    idea = Idea(
        title=title,
        problem_statement=problem_statement,
        persona=persona,
        state="working",
        seeded=True,
        centroid=centre,
        problem_embedding=await problem_embedding(llm, problem_statement),
    )
    session.add(idea)
    await session.flush()

    distance = Signal.embedding.cosine_distance(centre)
    matches = (
        await session.execute(
            unassigned()
            .with_only_columns(Signal.id, distance)
            .where(distance <= 1 - rules.attach_similarity)
        )
    ).all()
    for signal_id, d in matches:
        session.add(IdeaSignal(signal_id=signal_id, idea_id=idea.id, similarity=1 - d))
    await session.flush()
    await refresh_ideas(session, [idea.id])
    await link_related(session, idea.id, rules)
    record(session, idea, "to_working", None, note="seeded by you")
    await join(session, idea, validation)
    await session.commit()
    await session.refresh(idea)  # centroid and dates were recomputed in SQL
    return Seeded(idea, len(matches))
