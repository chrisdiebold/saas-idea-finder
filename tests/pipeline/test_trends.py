"""Google Trends: the ratio, which Ideas get looked up, and how it moves Momentum."""

from datetime import UTC, date, datetime, timedelta

import httpx2
import pytest

from ideafinder.db.models import IdeaTrend
from ideafinder.pipeline.score import components
from ideafinder.pipeline.trends import TrendsRefused, refresh_trends, trend_ratio
from ideafinder.web.app import app
from tests.pipeline.test_rank import evidence_of

NOW = datetime(2026, 11, 21, tzinfo=UTC)


def weeks(values: list[int], partial_last: bool = False) -> list[dict]:
    start = date(2026, 1, 4)
    out = [
        {"week": (start + timedelta(weeks=i)).isoformat(), "values": {"t": v}, "partial": False}
        for i, v in enumerate(values)
    ]
    if partial_last:
        out[-1]["partial"] = True
    return out


def test_ratio_compares_complete_weeks():
    assert trend_ratio(weeks([10] * 4 + [30] * 4)) == pytest.approx(121 / 41, rel=1e-3)
    # The partial current week is ignored.
    assert trend_ratio(weeks([10] * 4 + [30] * 4 + [0], partial_last=True)) == pytest.approx(
        121 / 41, rel=1e-3
    )
    assert trend_ratio(weeks([0] * 8)) is None
    assert trend_ratio(weeks([5] * 5)) is None  # not enough weeks


def test_rising_searches_lift_momentum_by_half():
    flat = components(evidence_of(recent=3, prior=3), None)["momentum"]
    rising = components(evidence_of(recent=3, prior=3, trend_ratio=3.0), None)["momentum"]
    assert flat == 0.5 and rising == pytest.approx(0.5 * 0.5 + 0.5 * 0.75)


@pytest.mark.db
async def test_only_published_ideas_with_terms_are_looked_up_once_a_week(session, evidence):
    summary = {"search_terms": ["crew scheduling app", "rain delay texts"]}
    inbox = await evidence.idea([], state="inbox", summary=summary)
    await evidence.idea([], state="incubating", summary=summary)  # not published: skipped
    await evidence.idea([], state="inbox")  # no Summary yet: no terms
    calls = []

    def fetch(terms, geo):
        calls.append((terms, geo))
        return weeks([10] * 4 + [20] * 4)

    report = await refresh_trends(session, fetch=fetch, now=NOW, pause=0)
    assert report.refreshed == [inbox.id] and report.no_terms == 1
    assert calls == [(["crew scheduling app", "rain delay texts"], "US")]
    trend = await session.get(IdeaTrend, inbox.id)
    assert trend.ratio == pytest.approx(81 / 41, rel=1e-3)

    # A day later nothing is due; after a week, or when the terms change, it is again.
    assert (await refresh_trends(session, fetch=fetch, now=NOW + timedelta(days=1))).refreshed == []
    inbox.summary = {"search_terms": ["crew rota app"]}
    await session.commit()
    again = await refresh_trends(session, fetch=fetch, now=NOW + timedelta(days=1), pause=0)
    assert again.refreshed == [inbox.id] and calls[-1][0] == ["crew rota app"]


@pytest.mark.db
async def test_google_saying_slow_down_stops_the_run(session, evidence):
    for i in range(3):
        await evidence.idea([], state="inbox", title=f"I{i}", summary={"search_terms": ["x"]})

    def refuse(terms, geo):
        raise TrendsRefused("429")

    report = await refresh_trends(session, fetch=refuse, now=NOW, pause=0)
    assert report.refreshed == [] and "slow down" in report.stopped


@pytest.mark.db
async def test_idea_page_shows_google_searches(session, evidence):
    idea = await evidence.idea(
        [await evidence.signal(None, "a")], state="inbox", summary=None, title="Rota"
    )
    session.add(
        IdeaTrend(idea_id=idea.id, terms=["crew rota app"], geo="US",
                  weeks=weeks([10] * 4 + [20] * 4), ratio=2.0, fetched_at=NOW)
    )  # fmt: skip
    await session.commit()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://t"
    ) as c:
        page = (await c.get(f"/ideas/{idea.id}")).text
    assert "Google searches" in page and "crew rota app" in page and "+100% on the 4 before" in page
