"""Validation: Working List rules, Experiments and Kill criteria, Head-to-heads, Leads, kits."""

from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select

from ideafinder.config import load_config
from ideafinder.db.models import (
    Briefing,
    HeadToHead,
    Hypothesis,
    Idea,
    Lead,
    ProposedDecision,
    WorkingItem,
)
from ideafinder.pipeline.review import review
from ideafinder.validation import experiments as exp
from ideafinder.validation import head_to_head as h2h
from ideafinder.validation import leads as leads_mod
from ideafinder.validation import working
from ideafinder.validation.kits import generate_kit
from ideafinder.web.app import app

pytestmark = pytest.mark.db


@pytest.fixture
def config(repo_config_dir):
    return load_config(repo_config_dir).scoring


async def on_list(session, evidence, config, title="Idea", authors=1):
    ids = [await evidence.signal(None, f"{title}-{i}") for i in range(authors)]
    idea = await evidence.idea(ids, state="inbox", title=title)
    await review(session, config, idea.id, "working")
    return idea


async def test_joining_starts_researching_with_a_briefing_prompt(session, evidence, config):
    idea = await on_list(session, evidence, config)
    item = await session.get(WorkingItem, idea.id)
    assert (item.stage, item.time_limit_days, item.priority_rank) == ("researching", 14, 1)
    assert await session.scalar(select(Briefing.status)) == "awaiting_response"


async def test_full_list_starts_a_head_to_head_against_the_lowest_priority(
    session, evidence, config
):
    ideas = [await on_list(session, evidence, config, f"I{i}") for i in range(3)]
    assert await h2h.pending(session) == []
    fourth = await on_list(session, evidence, config, "Fourth")
    [match] = await h2h.pending(session)
    assert (match.idea_a, match.idea_b, match.trigger) == (fourth.id, ideas[2].id, "full_list")

    with pytest.raises(working.WorkingListError, match="Pause or archive"):
        await h2h.decide(session, config, match.id, fourth.id, "kept")
    await h2h.decide(session, config, match.id, fourth.id, "paused")
    ranks = {
        i.idea_id: (i.priority_rank, i.stage)
        for i in (await session.scalars(select(WorkingItem))).all()
    }
    assert ranks[fourth.id][0] < ranks[ideas[2].id][0]  # the winner goes ahead of the loser
    assert ranks[ideas[2].id][1] == "paused"
    assert len(await working.active_items(session)) == 3

    # Resuming into a full list asks again.
    await working.resume(session, ideas[2].id, config.validation)
    assert len(await h2h.pending(session)) == 1


async def test_moving_on_needs_a_passed_experiment(session, evidence, config):
    idea = await on_list(session, evidence, config)
    with pytest.raises(working.WorkingListError, match="Hypothesis"):
        await exp.add_experiment(session, idea.id, "interviews", "Talk to 10 owners")
    await exp.set_hypothesis(session, idea.id, "Owners pay $49", "Fewer than 3 of 10 care")
    e = await exp.add_experiment(session, idea.id, "interviews", "Talk to 10 owners")
    with pytest.raises(working.WorkingListError, match="passed Experiment"):
        await working.advance(session, idea.id)
    await exp.record_outcome(session, e.id, "inconclusive", "4 of 10")
    with pytest.raises(working.WorkingListError, match="passed Experiment"):
        await working.advance(session, idea.id)
    e2 = await exp.add_experiment(session, idea.id, "landing_page", "Run ads")
    await exp.record_outcome(session, e2.id, "pass", "9% signed up")
    item = await working.advance(session, idea.id)
    assert item.stage == "validating" and not item.extended_in_stage
    # The pass was in Researching, so Validating needs its own.
    with pytest.raises(working.WorkingListError, match="passed Experiment"):
        await working.advance(session, idea.id)


async def test_kill_criteria_met_proposes_archiving_and_you_confirm(session, evidence, config):
    idea = await on_list(session, evidence, config)
    await exp.set_hypothesis(session, idea.id, "Owners pay", "Nobody pays")
    e = await exp.add_experiment(session, idea.id, "pre_sale", "Offer $99")
    with pytest.raises(working.WorkingListError, match="can't also"):
        await exp.record_outcome(session, e.id, "pass", "x", met_kill_criteria=True)
    await exp.record_outcome(session, e.id, "fail", "0 of 40 paid", met_kill_criteria=True)
    proposal = await session.scalar(select(ProposedDecision).where(ProposedDecision.kind == "kill"))
    assert (proposal.archive_reason, proposal.status) == ("dropped_after_research", "pending")
    assert "0 of 40 paid" in proposal.rationale
    await session.refresh(idea)
    assert idea.state == "working"  # never applied without you

    await review(session, config, idea.id, "archive", "dropped_after_research")
    await session.refresh(proposal)
    assert proposal.status == "confirmed"
    assert await session.get(WorkingItem, idea.id) is None


async def test_time_limit_extends_once(session, evidence, config):
    idea = await on_list(session, evidence, config)
    item = await session.get(WorkingItem, idea.id)
    item.stage_started_at = datetime.now(UTC) - timedelta(days=15)
    await session.commit()
    assert working.is_expired(item)
    await working.extend(session, idea.id)
    assert not working.is_expired(item)
    with pytest.raises(working.WorkingListError, match="already extended"):
        await working.extend(session, idea.id)


async def test_hypothesis_prefilled_from_briefing_but_yours_kept(session, evidence, config):
    idea = await on_list(session, evidence, config)
    b = await session.scalar(select(Briefing))
    b.status = "complete"
    b.frontmatter = {
        "assumptions": [
            {"claim": "c0", "risk": 5, "evidence": "none"},
            {"claim": "c1", "risk": 4, "evidence": "weak", "hypothesis": "Owners pay $49",
             "kill_criteria": "Under 3 of 10"},
        ]
    }  # fmt: skip
    await session.commit()
    h = await exp.prefill_hypothesis(session, idea.id)
    assert (h.statement, h.kill_criteria, h.source) == (
        "Owners pay $49",
        "Under 3 of 10",
        "briefing",
    )
    await exp.set_hypothesis(session, idea.id, "Mine", "My kill")
    await session.commit()
    assert await exp.prefill_hypothesis(session, idea.id) is None
    current = await exp.current_hypothesis(session, idea.id)
    assert current.statement == "Mine"
    assert len((await session.scalars(select(Hypothesis))).all()) == 2


async def test_periodic_head_to_head_only_reorders(session, evidence, config):
    a = await on_list(session, evidence, config, "A")
    b = await on_list(session, evidence, config, "B")
    match = await h2h.ask_periodic(session)
    assert (match.trigger, {match.idea_a, match.idea_b}) == ("periodic", {a.id, b.id})
    assert await h2h.ask_periodic(session) is None  # one at a time, at most weekly
    await h2h.decide(session, config, match.id, b.id, "paused")  # loser action ignored
    items = await working.active_items(session)
    assert [i.idea_id for i in items] == [b.id, a.id]
    assert {i.stage for i in items} == {"researching"}


async def test_leads_one_per_author_and_talked_notes_become_evidence(
    session, llm, fake, evidence, config
):
    ids = [await evidence.signal(None, "ann", intensity=5), await evidence.signal(None, "ann")]
    ids.append(await evidence.signal(None, "bob", intensity=2))
    ids.append(await evidence.signal(None, None, intensity=5))  # no name: not a Lead
    idea = await evidence.idea(ids, state="inbox")
    await review(session, config, idea.id, "working")
    assert await leads_mod.find_leads(session, idea.id) == 2
    assert await leads_mod.find_leads(session, idea.id) == 0
    leads = (await session.scalars(select(Lead).order_by(Lead.id))).all()
    assert [lead.author for lead in leads] == ["ann", "bob"]
    assert leads[0].signal_id == ids[0]  # her strongest Signal

    fake.answer("lead_opener", opener="How did you handle it last time?")
    fake.answer("lead_opener", opener="What did that cost you?")
    assert await leads_mod.write_openers(session, llm, idea.id) == 2
    assert "What ann wrote" in fake.prompts["lead_opener"][0]

    await exp.set_hypothesis(session, idea.id, "h", "k")
    e = await exp.add_experiment(session, idea.id, "interviews", "Talk to them")
    lead, attached = await leads_mod.update_lead(session, leads[0].id, "talked", "Pays $30/mo")
    assert attached.id == e.id and lead.experiment_id == e.id


async def test_validation_kit_keeps_only_real_leads(session, llm, fake, evidence, config):
    idea = await on_list(session, evidence, config, authors=2)
    await leads_mod.find_leads(session, idea.id)
    lead_ids = (await session.scalars(select(Lead.id))).all()
    fake.answer(
        "validation_kit",
        interview_script=["When did this last happen?"],
        lead_messages=[{"lead_id": lead_ids[0], "message": "Hi"}, {"lead_id": 999, "message": "?"}],
        landing_page={
            "headline": "H",
            "subheadline": "S",
            "bullets": ["b"],
            "call_to_action": "Join",
        },
        offer={"kind": "waitlist", "text": "Join the waitlist"},
    )
    kit = await generate_kit(session, llm, idea.id)
    assert kit.content["lead_messages"] == {str(lead_ids[0]): "Hi"}
    assert f"lead_id {lead_ids[0]}" in fake.prompts["validation_kit"][0]


async def test_merge_carries_the_working_place(session, evidence, config, repo_config_dir):
    from ideafinder.pipeline.cluster import merge

    rules = load_config(repo_config_dir).scoring.clustering
    source = await on_list(session, evidence, config, "S")
    target = await evidence.idea([await evidence.signal(None, "t")], state="inbox")
    target_id, source_id = target.id, source.id
    await merge(session, source_id, target_id, rules)
    session.expire_all()
    assert (await session.get(Idea, target_id)).state == "working"
    assert await session.get(WorkingItem, target_id) is not None
    assert await session.get(WorkingItem, source_id) is None


async def test_pages(session, evidence, config):
    a = await on_list(session, evidence, config, "Alpha")
    b = await on_list(session, evidence, config, "Beta")
    match = await h2h.ask_periodic(session)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://t"
    ) as c:
        page = (await c.get("/working")).text
        assert "2 of 3 active" in page and "Which comes first?" in page and "Alpha" in page
        assert "Beta" in (await c.get(f"/head-to-head/{match.id}")).text
        r = await c.post(f"/head-to-head/{match.id}", data={"winner": b.id})
        assert r.status_code == 303
        v = (await c.get(f"/ideas/{a.id}/validation")).text
        assert "Researching" in v and "Move on to Validating" in v and "disabled" in v
        r = await c.post(
            f"/ideas/{a.id}/hypothesis", data={"statement": "Owners pay", "kill_criteria": "No"}
        )
        assert "Hypothesis+saved" in r.headers["location"]
        r = await c.post(
            f"/ideas/{a.id}/experiments", data={"type": "interviews", "plan": "10 calls"}
        )
        assert "Experiment+started" in r.headers["location"]
        assert "Record the Outcome" in (await c.get(f"/ideas/{a.id}/validation")).text
        r = await c.post(f"/ideas/{a.id}/stage", data={"action": "pause"})
        assert "Paused" in httpx2.URL(r.headers["location"]).params["msg"]
        assert (await session.scalar(select(HeadToHead.winner))) == b.id
