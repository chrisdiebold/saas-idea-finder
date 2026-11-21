"""Summaries (PLAN.md §3 step 11): a short analysis of each Idea that reaches the Inbox.

Written when an Idea is published (or seeded) and rewritten when its evidence changes
significantly: the same rule that marks Briefings Stale (config/scoring.yaml `stale`).

The model picks the strongest quotes by number from verified quotes only, so a Summary never
contains words nobody wrote.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import StaleRule
from ideafinder.db.models import Idea, IdeaSignal, Item, Signal, Source
from ideafinder.llm import LLM, ModelUnavailable, ServerUnreachable
from ideafinder.pipeline.score import Evidence

log = logging.getLogger(__name__)

CANDIDATE_QUOTES = 12
PICKED_QUOTES = 3
PUBLISHED_STATES = ("inbox", "working", "archived")

SYSTEM = f"""\
You summarise evidence about a software problem for a solo founder deciding whether to pursue it.
The Idea is a recurring problem for one kind of person (the Persona). Write plainly and briefly,
using only the evidence given.

- problem: two sentences at most: what goes wrong, how often, and what it costs people.
- persona: who has it, as specifically as the evidence allows.
- quotes: the numbers of the {PICKED_QUOTES} quotes that best show the problem is real and painful
  (prefer different people and specifics over general grumbling).
- payment_note: one sentence on willingness to pay: what people pay now, say they'd pay, or that
  there's no sign of it.
- verdict: one sentence a busy person can act on.
- search_terms: 2 or 3 short phrases (2–4 words) this Persona would type into Google when the
  problem bites, in their words, e.g. "crew scheduling app", not product names."""

SCHEMA = {
    "type": "object",
    "properties": {
        "problem": {"type": "string"},
        "persona": {"type": "string"},
        "quotes": {"type": "array", "items": {"type": "integer"}},
        "payment_note": {"type": "string"},
        "verdict": {"type": "string"},
        "search_terms": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["problem", "persona", "quotes", "payment_note", "verdict", "search_terms"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Quote:
    kind: str
    text: str
    author: str | None
    source: str
    url: str
    workaround: str | None


async def candidate_quotes(session: AsyncSession, idea_id: int) -> list[Quote]:
    rows = (
        await session.execute(
            select(Signal, Item.author, Item.url, Source.name)
            .join(IdeaSignal, IdeaSignal.signal_id == Signal.id)
            .join(Item, Item.id == Signal.item_id)
            .join(Source, Source.id == Item.source_id)
            .where(IdeaSignal.idea_id == idea_id, Signal.quote_verified)
            .order_by(Signal.intensity.desc(), IdeaSignal.similarity.desc().nulls_last())
            .limit(CANDIDATE_QUOTES * 3)
        )
    ).all()
    quotes, seen_authors = [], set()
    # One quote per Author first, so the Summary shows several people.
    for signal, author, url, source in rows:
        key = author or url
        if key in seen_authors or not signal.quote:
            continue
        seen_authors.add(key)
        quotes.append(Quote(signal.kind, signal.quote, author, source, url, signal.workaround))
        if len(quotes) == CANDIDATE_QUOTES:
            break
    return quotes


def render(idea: Idea, quotes: list[Quote], e: Evidence | None) -> str:
    lines = [
        f"IDEA: {idea.title}",
        f"Problem as named: {idea.problem_statement}",
        f"Persona as named: {idea.persona}",
    ]
    if e:
        lines.append(
            f"Evidence: {e.signals} Signals from {e.authors} people in {e.communities} "
            f"Communities; {e.payments} about paying; {e.complaints} complaints about products."
        )
    lines.append("Quotes:")
    for n, q in enumerate(quotes, start=1):
        extra = f" (workaround: {q.workaround})" if q.workaround else ""
        lines.append(f'{n}. [{q.kind}] "{q.text[:400]}"{extra}')
    return "\n".join(lines)


async def summarize(session: AsyncSession, llm: LLM, idea: Idea, e: Evidence | None) -> dict | None:
    """Write the Summary onto the Idea (not committed). None if there are no verified quotes."""
    quotes = await candidate_quotes(session, idea.id)
    if not quotes:
        return None
    result = await llm.json("summary", SYSTEM, render(idea, quotes, e), SCHEMA, max_tokens=700)
    d = result.data
    picked = []
    for n in d["quotes"]:
        if isinstance(n, int) and 1 <= n <= len(quotes) and quotes[n - 1] not in picked:
            picked.append(quotes[n - 1])
    picked = (picked or quotes)[:PICKED_QUOTES]
    idea.summary = {
        "problem": d["problem"].strip(),
        "persona": d["persona"].strip(),
        "quotes": [
            {"text": q.text, "author": q.author, "source": q.source, "url": q.url} for q in picked
        ],
        "payment_note": d["payment_note"].strip(),
        "verdict": d["verdict"].strip(),
        "search_terms": [
            t.strip().lower() for t in d.get("search_terms", [])[:3] if t and t.strip()
        ],
    }
    idea.summary_updated_at = datetime.now(UTC)
    idea.summary_signal_count = e.signals if e else None
    idea.summary_community_ids = e.community_ids if e else []
    return idea.summary


def needs_summary(idea: Idea, e: Evidence | None, rule: StaleRule) -> bool:
    if idea.state not in PUBLISHED_STATES and not idea.seeded:
        return False
    if idea.summary is None:
        return e is not None
    if e is None:
        return False
    before = idea.summary_signal_count or 0
    if e.signals >= before * (1 + rule.signal_growth_pct / 100) and e.signals > before:
        return True
    return rule.on_new_community and bool(
        set(e.community_ids) - set(idea.summary_community_ids or [])
    )


@dataclass
class SummaryReport:
    written: int = 0
    failed: int = 0


async def summarize_pending(
    session: AsyncSession, llm: LLM, rule: StaleRule, evidence: dict[int, Evidence]
) -> SummaryReport:
    ideas = (
        await session.scalars(
            select(Idea)
            .where(
                Idea.merged_into_id.is_(None),
                or_(Idea.state.in_(PUBLISHED_STATES), Idea.seeded),
            )
            .order_by(Idea.id)
        )
    ).all()
    report = SummaryReport()
    for idea in ideas:
        e = evidence.get(idea.id)
        if not needs_summary(idea, e, rule):
            continue
        try:
            written = await summarize(session, llm, idea, e)
        except ServerUnreachable:
            raise
        except ModelUnavailable as exc:
            log.warning("couldn't summarise Idea %d: %s", idea.id, exc)
            await session.rollback()
            report.failed += 1
            continue
        await session.commit()
        report.written += written is not None
    return report
