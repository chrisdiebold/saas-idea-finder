"""App events: the sentences, the terminal format, storing them, and the Activity page."""

import logging
from datetime import timedelta

import httpx2
import pytest
from sqlalchemy import select

from ideafinder import events
from ideafinder.db.models import Event
from ideafinder.logs import _Formatter, duration
from ideafinder.web.app import app
from ideafinder.worker import reported


def test_sentences_say_what_happened():
    assert events.describe_extraction(
        {"items": 120, "signals": 41, "failed_chunks": 2, "embedded": 41, "attached": 12,
         "waiting": 48210}
    ) == (
        "Extraction: 120 Items → 41 Signals (2 chunks failed, will retry); 41 embedded,"
        " 12 joined existing Ideas. 48,210 Items still waiting"
    )  # fmt: skip
    idle = {"items": 0, "signals": 0, "failed_chunks": 0, "embedded": 0, "attached": 0}
    assert events.describe_extraction(idle | {"waiting": 0}) is None
    assert events.describe_collection({"github": "succeeded", "producthunt": "failed"}) == (
        "Collection: 1 Source collected; failed: producthunt"
    )
    assert events.describe_prefilter({"keep:request": 3, "keep:pain": 2, "drop:short": 9}) == (
        "Prefilter: 5 kept for extraction, 9 drop"
    )
    assert (
        events.describe_ranking(
            {"scored": 445, "published": 3, "resurfaced": 1, "outside_fit": 0, "proposed": 4}
        )
        == "Ranking: 445 Ideas scored, 3 new in the Inbox, 1 resurfaced, 4 proposals written"
    )


def test_terminal_lines_are_short_and_problems_say_where_from():
    fmt = _Formatter()
    info = logging.LogRecord("ideafinder.extract", logging.INFO, "", 0, "Extraction: x", None, None)
    warn = logging.LogRecord("ideafinder.daily", logging.WARNING, "", 0, "Skipped", None, None)
    assert fmt.format(info)[8:] == "  Extraction: x"
    assert fmt.format(warn)[8:] == "  WARNING  ideafinder.daily: Skipped"
    assert [duration(s) for s in (42, 134, 4000)] == ["42s", "2m 14s", "1h 06m"]


@pytest.mark.db
async def test_jobs_record_results_skips_and_failures(session):
    @reported("extract", "Extraction", lambda r: f"did {r['n']}")
    async def works() -> dict:
        return {"n": 3}

    @reported("extract", "Extraction", lambda r: "unused")
    async def lm_studio_closed() -> dict:
        return {"skipped": "LM Studio isn't running"}

    @reported("rank", "Ranking", lambda r: "unused")
    async def breaks() -> dict:
        raise RuntimeError("boom")

    await works()
    await lm_studio_closed()
    await lm_studio_closed()  # said once an hour, not every 10 minutes
    with pytest.raises(RuntimeError):
        await breaks()
    rows = (await session.execute(select(Event.level, Event.message).order_by(Event.id))).all()
    assert [r.level for r in rows] == ["info", "warning", "error"]
    assert rows[0].message.startswith("did 3 (")
    assert rows[1].message == "Extraction skipped: LM Studio isn't running"
    assert rows[2].message == "Ranking failed: RuntimeError: boom"

    await events.record("extract", rows[1].message, "warning", quiet_for=timedelta(0))
    assert len((await session.scalars(select(Event))).all()) == 4


@pytest.mark.db
async def test_activity_page_lists_events_and_filters_problems(session, evidence):
    idea = await evidence.idea([], state="inbox", title="Crew rota app")
    await events.record(
        "rank", "Ranking: 1 new in the Inbox", details={"ideas": [[idea.id, idea.title]]}
    )
    await events.record("collect", "Collection: failed: producthunt", "warning")
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://t"
    ) as c:
        page = (await c.get("/activity")).text
        problems = (await c.get("/activity?show=problems")).text
    assert "Ranking: 1 new in the Inbox" in page and f'href="/ideas/{idea.id}"' in page
    assert "failed: producthunt" in problems and "Ranking:" not in problems
