"""The review screens against a real database: pages render, and forms do what they say."""

import httpx2
import pytest
from sqlalchemy import select

from ideafinder.db.models import FitProfile, Idea, IdeaReview, ProposedDecision
from ideafinder.web.app import app

pytestmark = pytest.mark.db


@pytest.fixture
async def client(session):
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def inbox_idea(session, evidence, title, confidence, proposal="working", reason=None):
    ids = [await evidence.signal(None, f"{title}{i}", community=f"c{i % 2}") for i in range(5)]
    idea = await evidence.idea(
        ids,
        state="inbox",
        title=title,
        score=60.0,
        score_breakdown={
            "components": {"volume": 0.5, "spread": 0.4},
            "evidence": {"authors": 5, "communities": 2},
        },
        summary={
            "problem": f"{title} problem.",
            "persona": "plumbers",
            "quotes": [
                {"text": "We lose a day a week", "author": "ann", "source": "HN", "url": "u"}
            ],
            "payment_note": "They pay $30 now.",
            "verdict": "Promising.",
        },
    )
    session.add(
        ProposedDecision(
            idea_id=idea.id,
            kind="review",
            proposal=proposal,
            archive_reason=reason,
            rationale=f"Because {title}.",
            confidence=confidence,
            model="m",
        )
    )
    await session.commit()
    return idea


async def test_inbox_groups_clear_calls_and_shows_evidence(client, session, evidence):
    sure = await inbox_idea(session, evidence, "Sure", 0.9)
    unsure = await inbox_idea(session, evidence, "Unsure", 0.5, "archive", "too_competitive")
    page = (await client.get("/inbox")).text
    clear, judge = page.split("Needs your judgement")
    assert "Sure" in clear and f'value="{sure.id}"' in clear
    assert "Unsure" in judge and "We lose a day a week" in judge
    assert "Archive as too competitive" in judge and "50% sure" in judge
    assert "<kbd>y</kbd>" in judge
    assert (await client.get("/")).headers["location"] == "/inbox"
    assert '>Inbox<span class="count">2<' in page
    del unsure


async def test_confirm_all_applies_only_ticked_proposals(client, session, evidence):
    a = await inbox_idea(session, evidence, "A", 0.9)
    b = await inbox_idea(session, evidence, "B", 0.95, "archive", "low_willingness_to_pay")
    c = await inbox_idea(session, evidence, "C", 0.9)
    r = await client.post("/inbox/confirm", data={"ids": [a.id, b.id]})
    assert r.status_code == 303 and "Confirmed+2+proposals" in r.headers["location"]
    for idea in (a, b, c):
        await session.refresh(idea)
    assert (a.state, b.state, b.archive_reason, c.state) == (
        "working",
        "archived",
        "low_willingness_to_pay",
        "inbox",
    )


async def test_review_form_and_errors_come_back_as_messages(client, session, evidence):
    idea = await inbox_idea(session, evidence, "X", 0.5)
    r = await client.post(
        f"/ideas/{idea.id}/review", data={"to": "archive", "reason": "", "back": "/inbox"}
    )
    assert r.headers["location"].startswith("/inbox?error=")
    r = await client.post(
        f"/ideas/{idea.id}/review",
        data={"to": "archive", "reason": "too_big_to_build", "back": "/inbox"},
    )
    assert "Archived" in httpx2.URL(r.headers["location"]).params["msg"]
    review = await session.scalar(select(IdeaReview).where(IdeaReview.action == "to_archive"))
    assert review.overrode_proposal is True  # the proposal was "working"
    archive = (await client.get("/archive?reason=too_big_to_build")).text
    assert "X" in archive and "Restore" in archive


async def test_idea_page_shows_summary_score_and_evidence(client, session, evidence):
    idea = await inbox_idea(session, evidence, "Detail", 0.5)
    page = (await client.get(f"/ideas/{idea.id}")).text
    assert "Promising." in page and "Score 60 of 100" in page
    assert page.count('name="signal_ids"') == 5
    assert "Split ticked Signals into a new Idea" in page
    r = await client.post(f"/ideas/{idea.id}/notes", data={"notes": "Call Ann"})
    assert r.status_code == 303
    await session.refresh(idea)
    assert idea.notes_md == "Call Ann"
    missing = await client.get("/ideas/999999")
    assert missing.status_code == 404 and "no Idea #999999" in missing.text


async def test_merge_from_the_page(client, session, evidence):
    a = await inbox_idea(session, evidence, "Dup", 0.5)
    b = await inbox_idea(session, evidence, "Keep", 0.5)
    a_id, b_id = a.id, b.id
    r = await client.post(f"/ideas/{a_id}/merge", data={"into": f"{b_id} Keep", "back": "/inbox"})
    assert r.headers["location"].startswith(f"/ideas/{b_id}")
    session.expire_all()  # the app changed it through its own session
    assert (await session.get(Idea, a_id)).merged_into_id == b_id


async def test_settings_save_and_validation(client, session):
    page = (await client.get("/settings")).text
    assert "You haven't set one yet" in page
    r = await client.post(
        "/settings",
        data={"deal_breakers": "needs hardware\n\n", "price_min_usd": "$20", "skills": "python"},
    )
    assert "version+1" in r.headers["location"]
    profile = await session.get(FitProfile, 1)
    assert profile.deal_breakers == ["needs hardware"] and profile.price_min_usd == 20
    bad = await client.post("/settings", data={"price_max_usd": "lots"})
    assert "whole numbers" in bad.text


async def test_other_pages_render(client, session, evidence):
    await inbox_idea(session, evidence, "W", 0.5)
    for path in ("/working", "/archive", "/communities", "/ideas/new", "/status", "/eval"):
        assert (await client.get(path)).status_code == 200, path
    assert "Watch list" in (await client.get("/communities")).text
