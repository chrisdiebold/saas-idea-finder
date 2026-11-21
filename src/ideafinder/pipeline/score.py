"""Scoring (PLAN.md §3 step 7): a 0–100 score per Idea, with the breakdown kept for display.

Each component is 0–1 and the score is their weighted sum × 100, with weights from
config/scoring.yaml. Counts go through `saturate`, so the first few Authors or Communities move
the score a lot and the fiftieth barely does.

- Volume: distinct Authors and Signals from the last 90 days. Each Thread contributes at most
  `thread_cap` Signals, so one long Thread can't carry an Idea alone.
- Spread: distinct Communities.
- Intensity: mean Signal intensity, plus Votes relative to what's normal in each Community
  (agreement replies count as Votes on the Thread's root Item). Votes never count as Authors.
- Payment: Payment Signals, a business Persona (from the Fit check), Competitors that charge.
- Momentum: Signals in the last 4 weeks vs. the 4 before.
- Competitive gap: Complaint Signals relative to the number of Competitors.
- Fit and Solo-buildable: from the Fit check; 0.5 until there is one.
"""

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import ScoringConfig
from ideafinder.db.models import Idea

RECENT_DAYS = 90
MOMENTUM_DAYS = 28
UNKNOWN = 0.5  # for judgements the Fit check hasn't made yet

# Where each count reaches half of its component (see `saturate`).
HALF_AUTHORS = 10
HALF_RECENT_SIGNALS = 10
HALF_COMMUNITIES = 3
HALF_PAYMENT_SIGNALS = 2


def saturate(x: float, half: float) -> float:
    return x / (x + half) if x > 0 else 0.0


@dataclass(frozen=True)
class Evidence:
    """Counts for one Idea, from its Signals and their Items."""

    signals: int
    authors: int  # distinct, all Signals (used for publishing)
    capped_authors: int  # distinct, at most thread_cap Signals per Thread
    capped_recent_signals: int  # last RECENT_DAYS, at most thread_cap per Thread
    communities: int
    community_ids: list[int]
    mean_intensity: float  # 1-5
    vote_score: float | None  # 0-1, None when no Source gives Votes for these Items
    payments: int
    pains_or_requests: int
    complaints: int
    recent: int  # last MOMENTUM_DAYS
    prior: int  # the MOMENTUM_DAYS before that
    competitors: int
    paid_competitors: int


def components(e: Evidence, fit: dict[str, Any] | None) -> dict[str, float]:
    intensity = (e.mean_intensity - 1) / 4
    if e.vote_score is not None:
        intensity = 0.7 * intensity + 0.3 * e.vote_score
    b2b = UNKNOWN if not fit else float(fit["b2b"])
    if e.recent + e.prior == 0:
        momentum = 0.0
    else:
        ratio = (e.recent + 1) / (e.prior + 1)
        momentum = ratio / (1 + ratio)  # 0.5 when flat, 0.75 at 3×
    return {
        "volume": 0.7 * saturate(e.capped_authors, HALF_AUTHORS)
        + 0.3 * saturate(e.capped_recent_signals, HALF_RECENT_SIGNALS),
        "spread": saturate(e.communities, HALF_COMMUNITIES),
        "intensity": min(1.0, max(0.0, intensity)),
        "payment": 0.5 * saturate(e.payments, HALF_PAYMENT_SIGNALS)
        + 0.25 * b2b
        + 0.25 * (1.0 if e.paid_competitors else 0.0),
        "momentum": momentum,
        "competitive_gap": (e.complaints + 1) / (e.complaints + 1 + e.competitors),
        "fit": fit["fit"] if fit else UNKNOWN,
        "solo_buildable": fit["solo_buildable"] if fit else UNKNOWN,
    }


def score(e: Evidence, fit: dict[str, Any] | None, config: ScoringConfig) -> tuple[float, dict]:
    parts = components(e, fit)
    weights = config.weights.model_dump()
    total = 100 * sum(weights[k] * v for k, v in parts.items())
    breakdown = {
        "components": {k: round(v, 3) for k, v in parts.items()},
        "weights": weights,
        "evidence": asdict(e),
    }
    return round(total, 1), breakdown


EVIDENCE_SQL = """
WITH members AS (
    SELECT x.idea_id, s.kind, s.intensity, i.id AS item_id, i.author, i.community_id,
           i.source_id, i.thread_external_id, i.external_id, i.created_at, i.votes,
           row_number() OVER (
               PARTITION BY x.idea_id, i.source_id, coalesce(i.thread_external_id, i.id::text)
               ORDER BY i.created_at, i.id
           ) AS nth
    FROM idea_signals x
    JOIN ideas d ON d.id = x.idea_id AND d.merged_into_id IS NULL
    JOIN signals s ON s.id = x.signal_id
    JOIN items i ON i.id = s.item_id
    WHERE (CAST(:ids AS integer[]) IS NULL OR x.idea_id = ANY(:ids))
),
agreements AS (
    SELECT source_id, thread_external_id, count(*) AS n
    FROM items WHERE prefilter = 'vote' AND thread_external_id IS NOT NULL
    GROUP BY 1, 2
),
typical_votes AS (
    SELECT community_id, percentile_cont(0.9) WITHIN GROUP (ORDER BY votes) AS p90
    FROM items
    WHERE votes IS NOT NULL AND community_id IN (SELECT community_id FROM members)
    GROUP BY 1
),
scored AS (
    SELECT m.*,
           CASE WHEN m.votes IS NULL AND a.n IS NULL THEN NULL
                WHEN coalesce(t.p90, 0) <= 0 THEN NULL
                ELSE least(1.0, (coalesce(m.votes, 0) + coalesce(a.n, 0)) / t.p90)
           END AS vote_score,
           coalesce(m.author, 'item:' || m.item_id) AS who
    FROM members m
    LEFT JOIN agreements a
      ON a.source_id = m.source_id AND a.thread_external_id = m.thread_external_id
     AND m.external_id = m.thread_external_id  -- Votes belong to the Thread's root Item
    LEFT JOIN typical_votes t ON t.community_id = m.community_id
),
competition AS (
    SELECT x.idea_id, count(DISTINCT c.id) AS competitors,
           count(DISTINCT c.id) FILTER (WHERE c.pricing IS NOT NULL) AS paid_competitors
    FROM idea_signals x
    JOIN signal_competitors sc ON sc.signal_id = x.signal_id
    JOIN competitors c ON c.id = sc.competitor_id
    WHERE (CAST(:ids AS integer[]) IS NULL OR x.idea_id = ANY(:ids))
    GROUP BY 1
)
SELECT s.idea_id,
       count(*) AS signals,
       count(DISTINCT s.who) AS authors,
       count(DISTINCT s.who) FILTER (WHERE s.nth <= :cap) AS capped_authors,
       count(*) FILTER (WHERE s.nth <= :cap AND s.created_at >= :recent_since)
           AS capped_recent_signals,
       count(DISTINCT s.community_id) AS communities,
       array_agg(DISTINCT s.community_id) AS community_ids,
       avg(s.intensity)::float AS mean_intensity,
       avg(s.vote_score)::float AS vote_score,
       count(*) FILTER (WHERE s.kind = 'payment') AS payments,
       count(*) FILTER (WHERE s.kind IN ('pain', 'request')) AS pains_or_requests,
       count(*) FILTER (WHERE s.kind = 'complaint') AS complaints,
       count(*) FILTER (WHERE s.created_at >= :momentum_since) AS recent,
       count(*) FILTER (WHERE s.created_at >= :prior_since AND s.created_at < :momentum_since)
           AS prior,
       coalesce(max(c.competitors), 0) AS competitors,
       coalesce(max(c.paid_competitors), 0) AS paid_competitors
FROM scored s
LEFT JOIN competition c ON c.idea_id = s.idea_id
GROUP BY s.idea_id
"""


async def gather_evidence(
    session: AsyncSession,
    config: ScoringConfig,
    idea_ids: list[int] | None = None,
    now: datetime | None = None,
) -> dict[int, Evidence]:
    now = now or datetime.now(UTC)
    rows = await session.execute(
        text(EVIDENCE_SQL),
        {
            "ids": idea_ids,
            "cap": config.thread_cap,
            "recent_since": now - timedelta(days=RECENT_DAYS),
            "momentum_since": now - timedelta(days=MOMENTUM_DAYS),
            "prior_since": now - timedelta(days=2 * MOMENTUM_DAYS),
        },
    )
    out = {}
    for row in rows.mappings():
        values = dict(row)
        idea_id = values.pop("idea_id")
        values["community_ids"] = sorted(values["community_ids"])
        out[idea_id] = Evidence(**values)
    return out


async def score_ideas(
    session: AsyncSession,
    config: ScoringConfig,
    idea_ids: list[int] | None = None,
    now: datetime | None = None,
) -> dict[int, Evidence]:
    """Recompute the score of every live Idea (or the given ones). Returns their evidence."""
    now = now or datetime.now(UTC)
    evidence = await gather_evidence(session, config, idea_ids, now)
    for idea_id, e in evidence.items():
        idea = await session.get(Idea, idea_id)
        idea.score, idea.score_breakdown = score(e, idea.fit, config)
        idea.scored_at = now
    await session.commit()
    return evidence


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def snapshot_week(session: AsyncSession, now: datetime | None = None) -> int:
    """Record each live Idea's counts and score once per week (the first run of the week wins)."""
    week = week_start((now or datetime.now(UTC)).date())
    result = await session.execute(
        text(
            """
            INSERT INTO idea_snapshots (idea_id, week, signal_count, author_count,
                                        community_count, score)
            SELECT d.id, :week, count(*),
                   count(DISTINCT coalesce(i.author, 'item:' || i.id)),
                   count(DISTINCT i.community_id), d.score
            FROM ideas d
            JOIN idea_signals x ON x.idea_id = d.id
            JOIN signals s ON s.id = x.signal_id
            JOIN items i ON i.id = s.item_id
            WHERE d.merged_into_id IS NULL
            GROUP BY d.id
            ON CONFLICT DO NOTHING
            """
        ),
        {"week": week},
    )
    await session.commit()
    return result.rowcount
