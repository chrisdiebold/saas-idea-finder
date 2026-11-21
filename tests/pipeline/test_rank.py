"""Ranking: scoring maths, then fit / publish / propose / review / resurface against Postgres."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ideafinder.config import PublishThreshold, ResurfaceRule, load_config
from ideafinder.db.models import (
    FitProfile,
    Idea,
    IdeaReview,
    IdeaSignal,
    IdeaSnapshot,
    ProposedDecision,
    Signal,
)
from ideafinder.pipeline.cluster import merge
from ideafinder.pipeline.fit import FitProfileFile, clean, save_profile
from ideafinder.pipeline.publish import meets_threshold
from ideafinder.pipeline.rank import rank
from ideafinder.pipeline.resurface import evidence_changed
from ideafinder.pipeline.review import ReviewError, review
from ideafinder.pipeline.score import Evidence, components, gather_evidence, saturate, score

NOW = datetime(2026, 11, 21, tzinfo=UTC)


@pytest.fixture
def config(repo_config_dir):
    return load_config(repo_config_dir).scoring


def evidence_of(**overrides) -> Evidence:
    base = dict(
        signals=10,
        authors=8,
        capped_authors=8,
        capped_recent_signals=4,
        communities=3,
        community_ids=[1, 2, 3],
        mean_intensity=3.0,
        vote_score=None,
        payments=1,
        pains_or_requests=6,
        complaints=2,
        recent=3,
        prior=3,
        competitors=1,
        paid_competitors=0,
    )
    return Evidence(**(base | overrides))


# --- pure logic -----------------------------------------------------------------------------


def test_saturation_gives_diminishing_returns():
    assert saturate(0, 10) == 0 and saturate(10, 10) == 0.5
    assert saturate(20, 10) - saturate(10, 10) < saturate(10, 10) - saturate(0, 10)


def test_momentum_is_half_when_flat_and_zero_when_quiet():
    assert components(evidence_of(recent=3, prior=3), None)["momentum"] == 0.5
    assert components(evidence_of(recent=11, prior=3), None)["momentum"] == 0.75
    assert components(evidence_of(recent=0, prior=0), None)["momentum"] == 0.0


def test_unknown_fit_is_neutral_and_known_fit_is_used():
    assert components(evidence_of(), None)["fit"] == 0.5
    fit = {"fit": 0.9, "b2b": True, "solo_buildable": 0.2, "deal_breakers": [], "notes": ""}
    parts = components(evidence_of(), fit)
    assert (parts["fit"], parts["solo_buildable"]) == (0.9, 0.2)


def test_few_competitors_means_a_bigger_gap():
    open_field = components(evidence_of(complaints=0, competitors=0), None)["competitive_gap"]
    crowded = components(evidence_of(complaints=0, competitors=9), None)["competitive_gap"]
    assert open_field == 1.0 and crowded == 0.1


def test_score_is_0_to_100_with_a_breakdown(config):
    total, breakdown = score(evidence_of(), None, config)
    assert 0 < total < 100
    assert set(breakdown) == {"components", "weights", "evidence"}
    assert breakdown["evidence"]["authors"] == 8


def test_publish_threshold():
    rules = PublishThreshold(min_authors=5, min_communities=2, require_pain_or_request=True)
    assert meets_threshold(evidence_of(authors=5, communities=2), rules)
    assert not meets_threshold(evidence_of(authors=4), rules)
    assert not meets_threshold(evidence_of(communities=1), rules)
    assert not meets_threshold(evidence_of(pains_or_requests=0), rules)


def test_fit_check_ignores_invented_deal_breakers_and_clamps():
    profile = FitProfile(deal_breakers=["needs custom hardware"])
    out = clean(
        {
            "deal_breakers": ["Needs custom hardware", "something made up"],
            "fit": 1.7,
            "b2b": True,
            "solo_buildable": -1,
            "notes": " ok ",
        },
        profile,
    )
    assert out == {
        "deal_breakers": ["needs custom hardware"],
        "fit": 1.0,
        "b2b": True,
        "solo_buildable": 0.0,
        "notes": "ok",
    }


def test_resurface_on_momentum_or_new_community():
    rules = ResurfaceRule(momentum_multiplier=3.0, on_new_community=True)
    archived = IdeaReview(recent_signals_at_action=2, community_ids_at_action=[1, 2, 3])
    assert evidence_changed(evidence_of(recent=5), archived, rules) is None
    assert "6 Signals" in evidence_changed(evidence_of(recent=6), archived, rules)
    assert "1 new Community" in evidence_changed(
        evidence_of(recent=0, community_ids=[1, 2, 3, 4]), archived, rules
    )


# --- against Postgres -----------------------------------------------------------------------

FIT_OK = dict(deal_breakers=[], fit=0.8, b2b=True, solo_buildable=0.7, notes="good fit")
FIT_BREAKS = dict(
    deal_breakers=["needs custom hardware"], fit=0.1, b2b=True, solo_buildable=0.1, notes="no"
)
PROPOSE_WORKING = dict(
    proposal="working", archive_reason="none", rationale="Many people pay already.", confidence=0.9
)


async def publishable(evidence, n_authors=5, tag="a") -> list[int]:
    """Signals from n distinct Authors across two Communities, with Pain among them."""
    return [
        await evidence.signal(None, f"{tag}{i}", community=f"c{i % 2}") for i in range(n_authors)
    ]


async def profile(session, **fields) -> FitProfile:
    data = FitProfileFile(deal_breakers=["needs custom hardware"], skills=["python"])
    return await save_profile(session, data.model_copy(update=fields))


@pytest.mark.db
async def test_rank_publishes_fitting_ideas_and_archives_deal_breakers(
    session, llm, fake, evidence, config
):
    await profile(session)
    good = await evidence.idea(await publishable(evidence, tag="g"), title="Good")
    bad = await evidence.idea(await publishable(evidence, tag="b"), title="Hardware")
    small = await evidence.idea(await publishable(evidence, n_authors=4, tag="s"), title="Small")
    fake.answer("fit_check", **FIT_OK)
    fake.answer("fit_check", **FIT_BREAKS)
    fake.answer("propose_decision", **PROPOSE_WORKING)

    report = await rank(session, llm, config, now=NOW)

    assert (report.scored, report.publish.published, report.publish.outside_fit) == (
        3,
        [good.id],
        [bad.id],
    )
    await session.refresh(good), await session.refresh(bad), await session.refresh(small)
    assert (good.state, bad.state, small.state) == ("inbox", "archived", "incubating")
    assert bad.archive_reason == "outside_fit"
    assert good.fit["fit"] == 0.8 and good.score is not None and small.score is not None
    proposals = (await session.scalars(select(ProposedDecision))).all()
    assert [(p.idea_id, p.proposal, p.status) for p in proposals] == [
        (good.id, "working", "pending")
    ]
    assert "needs custom hardware" in fake.prompts["fit_check"][0]
    actions = (await session.scalars(select(IdeaReview.action).order_by(IdeaReview.id))).all()
    assert actions == ["published", "auto_outside_fit"]
    assert report.snapshots == 3
    assert len((await session.scalars(select(IdeaSnapshot))).all()) == 3

    # A second run the same day changes nothing and calls no model.
    again = await rank(session, llm, config, now=NOW)
    assert (again.publish.published, again.propose.proposed, again.snapshots) == ([], 0, 0)


@pytest.mark.db
async def test_without_a_profile_ideas_publish_unchecked(session, llm, fake, evidence, config):
    idea = await evidence.idea(await publishable(evidence))
    fake.answer("propose_decision", **PROPOSE_WORKING)
    report = await rank(session, llm, config, now=NOW)
    assert not report.has_profile and report.publish.published == [idea.id]
    assert "No Fit profile yet" in fake.prompts["propose_decision"][0]


@pytest.mark.db
async def test_review_records_override_and_archive_resurfaces_on_new_evidence(
    session, llm, fake, evidence, config
):
    idea = await evidence.idea(await publishable(evidence))
    fake.answer("propose_decision", **PROPOSE_WORKING)
    await rank(session, llm, config, now=NOW)

    with pytest.raises(ReviewError, match="needs a reason"):
        await review(session, config, idea.id, "archive")
    await review(session, config, idea.id, "archive", reason="too_competitive")
    proposal = await session.scalar(select(ProposedDecision))
    archived = await session.scalar(select(IdeaReview).where(IdeaReview.action == "to_archive"))
    assert proposal.status == "overridden" and archived.overrode_proposal is True
    assert archived.community_ids_at_action == sorted(evidence.communities.values())

    # Nothing new: stays archived.
    await rank(session, llm, config, now=NOW)
    await session.refresh(idea)
    assert idea.state == "archived"

    # Evidence from a Community it hadn't been seen in brings it back, with a fresh proposal.
    sid = await evidence.signal(None, "newcomer", community="c-new")
    session.add(IdeaSignal(signal_id=sid, idea_id=idea.id, similarity=0.9))
    await session.commit()
    fake.answer("propose_decision", **PROPOSE_WORKING)
    report = await rank(session, llm, config, now=NOW)
    await session.refresh(idea)
    assert report.resurfaced == [idea.id]
    assert (idea.state, idea.archive_reason) == ("inbox", None) and idea.resurfaced_at
    assert report.propose.proposed == 1


@pytest.mark.db
async def test_not_interested_never_resurfaces(session, llm, fake, evidence, config):
    idea = await evidence.idea(await publishable(evidence), state="inbox")
    await review(session, config, idea.id, "archive", reason="not_interested")
    sid = await evidence.signal(None, "newcomer", community="c-new")
    session.add(IdeaSignal(signal_id=sid, idea_id=idea.id, similarity=0.9))
    await session.commit()
    report = await rank(session, llm, config, now=NOW)
    assert report.resurfaced == []


@pytest.mark.db
async def test_profile_change_resurfaces_outside_fit(session, llm, fake, evidence, config):
    await profile(session)
    idea = await evidence.idea(await publishable(evidence))
    fake.answer("fit_check", **FIT_BREAKS)
    await rank(session, llm, config, now=NOW)
    await session.refresh(idea)
    assert idea.archive_reason == "outside_fit"

    # Same profile again: no version change, no new check.
    await profile(session)
    assert (await rank(session, llm, config, now=NOW)).refit.checked == 0

    await profile(session, deal_breakers=[])
    fake.answer("fit_check", **FIT_OK)
    fake.answer("propose_decision", **PROPOSE_WORKING)
    report = await rank(session, llm, config, now=NOW)
    await session.refresh(idea)
    assert report.refit.resurfaced == [idea.id] and idea.state == "inbox"


@pytest.mark.db
async def test_thread_cap_limits_volume_but_not_publishing(session, evidence, config):
    ids = [await evidence.signal(None, f"t{i}", thread="T1") for i in range(8)]
    idea = await evidence.idea(ids)
    e = (await gather_evidence(session, config, [idea.id], now=NOW))[idea.id]
    assert (e.authors, e.capped_authors) == (8, config.thread_cap)


@pytest.mark.db
async def test_agreement_replies_count_as_votes_on_the_root(session, evidence, config):
    root = await evidence.item("op", thread="T9")
    root.external_id = "T9"  # the Thread's root Item
    await session.commit()
    for i in range(3):
        await evidence.item(f"plus{i}", thread="T9", prefilter="vote")
    await evidence.item("other", votes=4)  # typical Votes in this Community
    sig = Signal(
        item_id=root.id,
        kind="pain",
        problem_statement="p",
        persona="p",
        intensity=3,
        quote="q",
        quote_verified=True,
        embedding=[0.1] * 768,
        model="t",
        prompt_version="t",
    )
    session.add(sig)
    await session.commit()
    idea = await evidence.idea([sig.id])
    e = (await gather_evidence(session, config, [idea.id], now=NOW))[idea.id]
    assert e.vote_score == pytest.approx(3 / 4)
    assert e.authors == 1  # Votes never count as Authors


@pytest.mark.db
async def test_momentum_counts_recent_weeks(session, evidence, config):
    ids = [
        await evidence.signal(None, f"r{i}", created_at=NOW - timedelta(days=3)) for i in range(3)
    ]
    ids.append(await evidence.signal(None, "old", created_at=NOW - timedelta(days=40)))
    idea = await evidence.idea(ids)
    e = (await gather_evidence(session, config, [idea.id], now=NOW))[idea.id]
    assert (e.recent, e.prior) == (3, 1)


@pytest.mark.db
async def test_merge_takes_the_more_advanced_state(session, evidence, config, repo_config_dir):
    rules = load_config(repo_config_dir).scoring.clustering
    a = await evidence.idea([await evidence.signal(None, "x")], state="working")
    b = await evidence.idea([await evidence.signal(None, "y")], state="incubating")
    session.add(
        ProposedDecision(
            idea_id=a.id,
            kind="review",
            proposal="working",
            rationale="r",
            confidence=0.5,
            model="m",
        )
    )
    await session.commit()
    target = await merge(session, a.id, b.id, rules)
    assert target.state == "working"
    assert (await session.scalar(select(ProposedDecision))).status == "superseded"
    assert (await session.get(Idea, a.id)).merged_into_id == b.id
