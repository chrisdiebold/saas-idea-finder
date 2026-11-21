"""Clustering: grouping logic, then attach / form / Related / Merge / Split against Postgres."""

import numpy as np
import pytest
from sqlalchemy import select

from ideafinder.config import ClusteringRules
from ideafinder.db.models import Idea, IdeaRelation, IdeaSignal
from ideafinder.pipeline.cluster import (
    NAMING_SAMPLE,
    ClusterError,
    PoolSignal,
    attach_pending,
    find_groups,
    form_ideas,
    list_ideas,
    merge,
    render_for_naming,
    split,
)
from tests.fixtures import direction, near

RULES = ClusteringRules(
    attach_similarity=0.8, related_similarity=0.85, min_cluster_size=3, min_authors=2
)


def pool_signal(i: int, vector: np.ndarray, author: str) -> PoolSignal:
    return PoolSignal(i, author, f"problem {i}", "persona", "pain", vector)


# --- pure logic -----------------------------------------------------------------------------


def test_tight_groups_with_several_authors_become_ideas():
    a, b = direction(1), direction(2)
    pool = [pool_signal(i, near(a), f"author{i}") for i in range(5)]  # 5 people, one problem
    pool += [pool_signal(10 + i, near(b), "same_person") for i in range(5)]  # one person, 5 times
    pool += [pool_signal(20 + i, direction(100 + i), f"loner{i}") for i in range(6)]  # noise
    groups = find_groups(pool, RULES)
    assert [sorted(m.id for m in g.members) for g in groups] == [[0, 1, 2, 3, 4]]
    assert all(s >= RULES.attach_similarity for s in groups[0].similarities)


def test_too_small_pool_forms_nothing():
    assert find_groups([pool_signal(1, direction(1), "x")], RULES) == []


def test_naming_prompt_leads_with_the_most_central_signals():
    members = [pool_signal(i, direction(i), f"a{i}") for i in range(NAMING_SAMPLE + 5)]
    sims = [i / 100 for i in range(len(members))]
    prompt = render_for_naming(members, sims)
    lines = prompt.splitlines()
    assert lines[0] == f"{len(members)} Signals from {len(members)} different people:"
    assert len(lines) == NAMING_SAMPLE + 1
    assert f"problem {len(members) - 1}" in lines[1]


# --- against Postgres -----------------------------------------------------------------------


async def members(session, idea_id: int) -> set[int]:
    rows = await session.scalars(select(IdeaSignal.signal_id).where(IdeaSignal.idea_id == idea_id))
    return set(rows.all())


@pytest.mark.db
async def test_pool_forms_named_ideas_and_new_signals_attach(session, llm, fake, evidence):
    a = direction(1)
    group = [await evidence.signal(near(a), f"author{i}") for i in range(4)]
    for _ in range(3):  # one person repeating themselves isn't a pattern
        await evidence.signal(near(direction(2)), "same_person")
    loner = await evidence.signal(direction(50), "loner")
    market = await evidence.signal(near(a), "launcher", kind="market")
    fake.name(
        "Crews re-texted after rain", "Rescheduling crews by text", "landscaper", direction(9)
    )

    assert (await attach_pending(session, RULES)).pooled == 8  # no Ideas yet; market excluded
    report = await form_ideas(session, llm, RULES)
    assert len(report.created) == 1
    idea = await session.get(Idea, report.created[0])
    assert (idea.title, idea.persona, idea.state) == (
        "Crews re-texted after rain",
        "landscaper",
        "incubating",
    )
    assert await members(session, idea.id) == set(group)
    assert "4 Signals from 4 different people" in fake.prompts["name_idea"][0]
    assert idea.first_seen < idea.last_seen

    late = await evidence.signal(near(a), "newcomer")
    far = await evidence.signal(direction(60), "elsewhere")
    report = await attach_pending(session, RULES)
    assert (report.attached, report.pooled) == (1, 5)  # same_person, loner and far stay pooled
    assert late in await members(session, idea.id)
    assert loner not in await members(session, idea.id) and far not in await members(
        session, idea.id
    )
    assert market not in await members(session, idea.id)

    [row] = await list_ideas(session)
    assert (row.signals, row.authors, row.communities) == (5, 5, 1)


@pytest.mark.db
async def test_same_problem_for_other_persona_is_related_not_merged(session, llm, fake, evidence):
    problem = direction(30)
    for i in range(3):
        await evidence.signal(near(direction(1)), f"landscaper{i}")
    for i in range(3):
        await evidence.signal(near(direction(2)), f"plumber{i}")
    fake.name("Landscapers rescheduling", "Rescheduling crews", "landscaper", near(problem, 0.01))
    fake.name("Plumbers rescheduling", "Rescheduling jobs", "plumber", near(problem, 0.01))

    report = await form_ideas(session, llm, RULES)
    assert len(report.created) == 2
    relations = (await session.scalars(select(IdeaRelation))).all()
    assert [(r.idea_id, r.related_idea_id) for r in relations] == [tuple(sorted(report.created))]


@pytest.mark.db
async def test_merge_keeps_target_identity_and_more_advanced_state(session, llm, fake, evidence):
    for i in range(3):
        await evidence.signal(near(direction(1)), f"a{i}")
    for i in range(3):
        await evidence.signal(near(direction(2)), f"b{i}")
    fake.name("One", "Problem one", "p1", direction(31))
    fake.name("Two", "Problem two", "p2", direction(32))
    first, second = (await form_ideas(session, llm, RULES)).created
    (await session.get(Idea, first)).state = "inbox"
    await session.commit()

    target = await merge(session, first, second, RULES)
    assert (target.id, target.title, target.state) == (second, "Two", "inbox")
    assert len(await members(session, second)) == 6
    assert (await session.get(Idea, first)).merged_into_id == second
    assert [r.idea.id for r in await list_ideas(session)] == [second]
    with pytest.raises(ClusterError, match="was merged into"):
        await merge(session, first, second, RULES)


@pytest.mark.db
async def test_split_moves_signals_to_a_new_incubating_idea(session, llm, fake, evidence):
    ids = [await evidence.signal(near(direction(1)), f"a{i}") for i in range(5)]
    for _ in range(3):  # HDBSCAN needs more than one group in the pool; this one has one Author
        await evidence.signal(near(direction(2)), "same_person")
    fake.name("Original", "Problem", "persona", direction(31))
    [original] = (await form_ideas(session, llm, RULES)).created
    (await session.get(Idea, original)).state = "working"
    await session.commit()

    fake.name("Split off", "Narrower problem", "persona", direction(33))
    new = await split(session, llm, original, ids[:2], RULES)
    assert new.state == "incubating" and new.title == "Split off"
    assert await members(session, new.id) == set(ids[:2])
    assert await members(session, original) == set(ids[2:])
    assert (await session.get(Idea, original)).state == "working"

    with pytest.raises(ClusterError, match="every Signal"):
        await split(session, llm, new.id, ids[:2], RULES)
    with pytest.raises(ClusterError, match="aren't in Idea"):
        await split(session, llm, original, [ids[0]], RULES)
