"""Summaries (which quotes, when to rewrite) and Seeded Ideas."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from ideafinder.config import ClusteringRules, StaleRule, ValidationRules
from ideafinder.db.models import Idea, IdeaReview, IdeaSignal, Signal
from ideafinder.pipeline.seed import create_seeded
from ideafinder.pipeline.summary import needs_summary, summarize
from tests.fixtures import direction, near
from tests.pipeline.test_rank import evidence_of

VALIDATION = ValidationRules(time_limit_days=14, active_limit=3)
RULE = StaleRule(signal_growth_pct=50, on_new_community=True)
CLUSTER = ClusteringRules(
    attach_similarity=0.8, related_similarity=0.85, min_cluster_size=3, min_authors=2
)


def idea_with_summary(**fields) -> Idea:
    return Idea(state="inbox", seeded=False, **fields)


def test_summary_written_once_published_and_rewritten_on_big_changes():
    assert not needs_summary(Idea(state="incubating", seeded=False), evidence_of(), RULE)
    assert needs_summary(idea_with_summary(summary=None), evidence_of(), RULE)
    done = idea_with_summary(
        summary={"x": 1}, summary_signal_count=10, summary_community_ids=[1, 2, 3]
    )
    assert not needs_summary(done, evidence_of(signals=14), RULE)
    assert needs_summary(done, evidence_of(signals=15), RULE)  # +50%
    assert needs_summary(done, evidence_of(signals=10, community_ids=[1, 2, 3, 9]), RULE)


@pytest.mark.db
async def test_summary_quotes_come_only_from_verified_evidence(session, llm, fake, evidence):
    ids = [await evidence.signal(None, f"a{i}") for i in range(3)]
    await session.execute(update(Signal).where(Signal.id == ids[1]).values(quote_verified=False))
    await session.commit()
    idea = await evidence.idea(ids, state="inbox")
    fake.answer(
        "summary",
        problem="Crews get re-texted.",
        persona="landscapers",
        quotes=[2, 99, 1, 2],  # out of range and repeated numbers are ignored
        payment_note="Some pay $50.",
        verdict="Worth a look.",
    )
    summary = await summarize(session, llm, idea, evidence_of(signals=3))
    assert [q["author"] for q in summary["quotes"]] == ["a2", "a0"]
    assert "2. [pain]" in fake.prompts["summary"][0] and "a1" not in str(summary)
    assert idea.summary_signal_count == 3


@pytest.mark.db
async def test_seeded_idea_goes_to_working_list_with_matching_pool_signals(
    session, llm, fake, evidence
):
    topic = direction(5)
    near_ids = [await evidence.signal(near(topic), f"p{i}") for i in range(3)]
    far = await evidence.signal(direction(77), "elsewhere")
    taken = await evidence.signal(near(topic), "owned")
    await evidence.idea([taken])  # already in another Idea: not taken over
    fake.vectors["clustering: Crews re-texted after rain (Persona: landscaper)"] = topic

    seeded = await create_seeded(
        session, llm, CLUSTER, VALIDATION, "Rain delays", "Crews re-texted after rain", "landscaper"
    )
    idea = seeded.idea
    assert (idea.state, idea.seeded, seeded.attached) == ("working", True, 3)
    members = set(
        (
            await session.scalars(select(IdeaSignal.signal_id).where(IdeaSignal.idea_id == idea.id))
        ).all()
    )
    assert members == set(near_ids) and far not in members
    review = await session.scalar(select(IdeaReview).where(IdeaReview.idea_id == idea.id))
    assert review.action == "to_working" and review.note == "seeded by you"
    assert idea.first_seen is not None and idea.first_seen < datetime.now(UTC)

    with pytest.raises(ValueError, match="needs a title"):
        await create_seeded(session, llm, CLUSTER, VALIDATION, " ", "x", "y")
