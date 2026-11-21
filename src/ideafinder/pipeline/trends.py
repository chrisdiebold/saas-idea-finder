"""Google Trends: search interest for each Idea's search terms, for the Momentum score (M10).

Google's official Trends API is an application-only alpha, so this uses pytrends, which reads
the Trends website (ADR 0004). It's slow and polite on purpose: one request per Idea, spaced
out, a few dozen Ideas a week, and it stops for the day as soon as Google says to slow down.

Only Inbox and Working List Ideas are looked up; the search terms come from their Summary. Data
is weekly, US, last 12 months, scaled 0-100 by Google (relative, not search counts).
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import Idea, IdeaTrend

log = logging.getLogger(__name__)

GEO = "US"
TIMEFRAME = "today 12-m"
REFRESH_EVERY = timedelta(days=7)
MAX_IDEAS_PER_RUN = 30
PAUSE_SECONDS = 12  # between requests; Trends answers 429 quickly when hurried
WINDOW_WEEKS = 4

Week = dict  # {"week": "YYYY-MM-DD", "values": {term: int}, "partial": bool}
Fetcher = Callable[[list[str], str], list[Week]]


class TrendsRefused(Exception):
    """Google asked us to slow down (HTTP 429) or refused; try again another day."""


def fetch_with_pytrends(terms: list[str], geo: str = GEO) -> list[Week]:
    """Weekly interest for up to 5 terms (blocking; run it in a thread)."""
    from pytrends.exceptions import ResponseError, TooManyRequestsError
    from pytrends.request import TrendReq

    client = TrendReq(hl="en-US", tz=0, timeout=(10, 30), retries=0)
    try:
        client.build_payload(terms[:5], timeframe=TIMEFRAME, geo=geo)
        frame = client.interest_over_time()
    except (TooManyRequestsError, ResponseError) as exc:
        raise TrendsRefused(str(exc)) from exc
    if frame.empty:
        return []
    weeks = []
    for when, row in frame.iterrows():
        weeks.append(
            {
                "week": when.date().isoformat(),
                "values": {t: int(row[t]) for t in terms[:5] if t in row},
                "partial": bool(row.get("isPartial", False)),
            }
        )
    return weeks


def trend_ratio(weeks: list[Week]) -> float | None:
    """(Interest over the last 4 complete weeks + 1) / (the 4 before + 1), summed over terms.
    A partial current week is left out. None when there's no interest at all."""
    complete = [w for w in weeks if not w.get("partial")]
    if len(complete) < 2 * WINDOW_WEEKS:
        return None
    totals = [sum(w["values"].values()) for w in complete]
    recent = sum(totals[-WINDOW_WEEKS:])
    prior = sum(totals[-2 * WINDOW_WEEKS : -WINDOW_WEEKS])
    if recent + prior == 0:
        return None
    return round((recent + 1) / (prior + 1), 3)


@dataclass
class TrendsReport:
    refreshed: list[int] = field(default_factory=list)
    no_terms: int = 0
    stopped: str | None = None


def terms_for(idea: Idea) -> list[str]:
    return list((idea.summary or {}).get("search_terms") or [])[:5]


async def refresh_trends(
    session: AsyncSession,
    *,
    fetch: Fetcher = fetch_with_pytrends,
    now: datetime | None = None,
    max_ideas: int = MAX_IDEAS_PER_RUN,
    pause: float = PAUSE_SECONDS,
    idea_ids: list[int] | None = None,
) -> TrendsReport:
    """Look up Ideas whose Trends data is missing, older than a week, or for changed terms."""
    now = now or datetime.now(UTC)
    report = TrendsReport()
    query = (
        select(Idea, IdeaTrend)
        .outerjoin(IdeaTrend, IdeaTrend.idea_id == Idea.id)
        .where(Idea.merged_into_id.is_(None), Idea.state.in_(("inbox", "working")))
        .order_by(IdeaTrend.fetched_at.asc().nullsfirst(), Idea.score.desc().nulls_last())
    )
    if idea_ids:
        query = query.where(Idea.id.in_(idea_ids))
    due = []
    for idea, trend in (await session.execute(query)).all():
        terms = terms_for(idea)
        if not terms:
            report.no_terms += 1
            continue
        fresh = trend and now - trend.fetched_at < REFRESH_EVERY and trend.terms == terms
        if not fresh:
            due.append((idea, trend, terms))

    for n, (idea, trend, terms) in enumerate(due[:max_ideas]):
        if n:
            await asyncio.sleep(pause)
        try:
            weeks = await asyncio.to_thread(fetch, terms, GEO)
        except TrendsRefused as exc:
            report.stopped = f"Google Trends asked to slow down: {exc}"
            log.warning(report.stopped)
            break
        except Exception as exc:  # network trouble: try again next run
            report.stopped = f"Google Trends failed: {type(exc).__name__}: {exc}"
            log.warning(report.stopped)
            break
        if trend is None:
            trend = IdeaTrend(idea_id=idea.id)
            session.add(trend)
        trend.terms, trend.geo, trend.weeks = terms, GEO, weeks
        trend.ratio, trend.fetched_at = trend_ratio(weeks), now
        await session.commit()
        report.refreshed.append(idea.id)
    return report
