"""Briefings (PLAN.md §4): build the prompt, check and store the reply, and mark Briefings Stale.

Until an API key is set up, the provider is `manual`: the app shows one self-contained prompt,
you paste it into claude.ai (with web search on) and paste the reply back. The reply is Markdown
with YAML frontmatter; it's checked strictly (so a paste can't land on the wrong Idea or lose a
section) but tolerantly of wrapping (code fences, a sentence before it).

Every claim in a Briefing carries a label: [evidence: signal-1234] for something a collected
Signal shows, [researched: https://…] for a web source, [assumption] for the model's inference.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import frontmatter
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import StaleRule
from ideafinder.db.models import (
    Briefing,
    Community,
    Competitor,
    FitProfile,
    Idea,
    IdeaCompetitor,
    IdeaSignal,
    Item,
    Signal,
    SignalCompetitor,
    Source,
)
from ideafinder.pipeline.competitors import find_or_create
from ideafinder.pipeline.industry import us_businesses
from ideafinder.pipeline.score import Evidence

FORMAT_VERSION = 1
PROMPT_QUOTES = 15
HEADINGS = (
    "Problem",
    "Persona",
    "Evidence",
    "Current workarounds",
    "Competitors",
    "Willingness to pay",
    "Market math (US)",
    "Reach",
    "Build",
    "MVP scope",
    "Case against",
    "Assumptions to test",
)

# --- the reply's structured part --------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PricingHypothesis(_Strict):
    model: str
    low_usd: float = Field(ge=0)
    high_usd: float = Field(ge=0)


class MarketMath(_Strict):
    us_businesses: int | None = Field(default=None, ge=0)
    price_usd: float = Field(gt=0)
    customers_needed: int = Field(ge=0)
    share_pct: float | None = Field(default=None, ge=0)


class CompetitorEntry(_Strict):
    name: str = Field(min_length=1)
    url: str | None = None
    pricing: str | None = None
    gaps: list[str] = []


class Integration(_Strict):
    name: str
    api_access: Literal["open", "approval", "partner_only"]


class Assumption(_Strict):
    claim: str
    risk: int = Field(ge=1, le=5)
    evidence: Literal["none", "weak", "some"]
    hypothesis: str | None = None
    kill_criteria: str | None = None
    experiment: str | None = None


class Frontmatter(_Strict):
    briefing_for: str
    format_version: Literal[1]
    verdict: Literal["pursue", "maybe", "skip"]
    confidence: int = Field(ge=1, le=5)
    persona: str
    pricing_hypothesis: PricingHypothesis
    market_math: MarketMath
    competitors: list[CompetitorEntry] = []
    integrations: list[Integration] = []
    weeks_to_mvp: float = Field(gt=0)
    assumptions: list[Assumption] = Field(min_length=1)


# --- the prompt -----------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptQuote:
    signal_id: int
    kind: str
    text: str
    author: str | None
    source: str
    url: str
    when: datetime


async def strongest_quotes(session: AsyncSession, idea_id: int) -> list[PromptQuote]:
    rows = (
        await session.execute(
            select(Signal, Item.author, Item.url, Item.created_at, Source.name)
            .join(IdeaSignal, IdeaSignal.signal_id == Signal.id)
            .join(Item, Item.id == Signal.item_id)
            .join(Source, Source.id == Item.source_id)
            .where(IdeaSignal.idea_id == idea_id, Signal.quote_verified)
            .order_by(Signal.intensity.desc(), IdeaSignal.similarity.desc().nulls_last())
            .limit(PROMPT_QUOTES * 3)
        )
    ).all()
    out, seen = [], set()
    for s, author, url, when, source in rows:
        if (author or url) in seen:
            continue  # one per person, so the evidence isn't one loud voice
        seen.add(author or url)
        out.append(PromptQuote(s.id, s.kind, s.quote, author, source, url, when))
        if len(out) == PROMPT_QUOTES:
            break
    return out


async def known_competitors(session: AsyncSession, idea_id: int) -> list[tuple[str, int, int]]:
    """(name, mentions, complaints) for products named in the Idea's Signals."""
    rows = await session.execute(
        select(
            Competitor.name,
            func.count(),
            func.count().filter(Signal.kind == "complaint"),
        )
        .join(SignalCompetitor, SignalCompetitor.competitor_id == Competitor.id)
        .join(Signal, Signal.id == SignalCompetitor.signal_id)
        .join(IdeaSignal, IdeaSignal.signal_id == Signal.id)
        .where(IdeaSignal.idea_id == idea_id)
        .group_by(Competitor.id)
        .order_by(func.count().desc())
        .limit(20)
    )
    return [tuple(r) for r in rows.all()]


async def idea_communities(session: AsyncSession, idea_id: int) -> list[tuple[str, str, int]]:
    rows = await session.execute(
        select(Source.name, Community.name, func.count())
        .join(Item, Item.community_id == Community.id)
        .join(Source, Source.id == Item.source_id)
        .join(Signal, Signal.item_id == Item.id)
        .join(IdeaSignal, IdeaSignal.signal_id == Signal.id)
        .where(IdeaSignal.idea_id == idea_id)
        .group_by(Source.name, Community.name)
        .order_by(func.count().desc())
    )
    return [tuple(r) for r in rows.all()]


INSTRUCTIONS = """\
## Instructions

You are writing a Briefing for a solo founder deciding whether to build software for the problem
below. It is a problem experienced by one kind of person (the Persona); no product has been
chosen yet. Use web search to research competitors, their pricing, the APIs any product would
need, and channels to reach these customers.

Label every factual claim in the prose, right after the claim:
- [evidence: signal-1234] when one of the quotes below shows it (use its signal number)
- [researched: https://…] when a web page you found shows it
- [assumption] when it's your own inference
Unlabelled claims are not allowed. Prefer evidence and research; say plainly when something is
an assumption.

Work in two passes:
1. Write every section except "Case against": Problem, Persona, Evidence (5–10 of the strongest
   quotes below, each with its signal label and link), Current workarounds, Competitors (name,
   pricing, what users complain about), Willingness to pay (with a pricing hypothesis), Market
   math (US), Reach (concrete channels to the first 10 customers with cost and effort, built-in
   distribution such as plugin directories or app marketplaces, search terms people use, and
   the communities listed below), Build (integrations and whether their APIs are open, need
   approval or are partner-only; the hardest technical piece; weeks to an MVP for one developer
   with the founder's skills), MVP scope (3–5 core features and what to leave out), and
   Assumptions to test.
2. Then switch roles. As a skeptical investor who has seen many products like this fail, write
   "Case against" without softening it and without being swayed by pass 1: a pre-mortem (it's
   a year later and this failed; why?), why this doesn't exist yet (tiny market, hard
   distribution, bundled by incumbents, regulation, platform risk, failed predecessors), and the
   single strongest reason not to build it.

Market math is about US businesses only; say "US businesses" every time. Use the count given
below if there is one (label it [researched: https://www.census.gov/programs-surveys/cbp.html]);
otherwise estimate it and label it [assumption]. customers_needed = the monthly revenue goal ÷
your realistic monthly price, rounded up. share_pct = customers_needed ÷ US businesses × 100.

Assumptions to test: list the claims the idea depends on, ranked by risk × lack of evidence
(riskiest and least supported first). For the top three, also give a hypothesis that would show
it's true, kill criteria that would show it's false, and the cheapest experiment to find out.
"""


def output_format(idea_id: int) -> str:
    headings = "\n".join(f"## {h}" for h in HEADINGS)
    return f"""\
## Output format

Reply with only this document: YAML frontmatter between --- lines, then the twelve sections under
exactly these headings, in this order. Fill in the frontmatter values; keep its keys as they are.

---
briefing_for: idea-{idea_id}
format_version: {FORMAT_VERSION}
verdict: maybe                # pursue | maybe | skip
confidence: 3                 # 1-5, how sure you are of the verdict
persona: "..."
pricing_hypothesis: {{ model: per_seat, low_usd: 19, high_usd: 49 }}
market_math: {{ us_businesses: 98000, price_usd: 49, customers_needed: 204, share_pct: 0.2 }}
competitors:
  - {{ name: "...", url: "...", pricing: "...", gaps: ["..."] }}
integrations:
  - {{ name: "...", api_access: open }}     # open | approval | partner_only
weeks_to_mvp: 6
assumptions:
  - claim: "..."
    risk: 4                   # 1-5
    evidence: none            # none | weak | some
    hypothesis: "..."         # for the top three
    kill_criteria: "..."
    experiment: "..."
---
{headings}
"""


async def build_prompt(
    session: AsyncSession, idea: Idea, profile: FitProfile | None, evidence: Evidence | None
) -> str:
    quotes = await strongest_quotes(session, idea.id)
    competitors = await known_competitors(session, idea.id)
    communities = await idea_communities(session, idea.id)
    total, rows, year = await us_businesses(session, idea.industry_codes or [])

    lines = [f"# Briefing request: idea-{idea.id}", "", "## The idea", ""]
    lines += [
        f"Title: {idea.title}",
        f"Problem: {(idea.summary or {}).get('problem') or idea.problem_statement}",
        f"Persona: {(idea.summary or {}).get('persona') or idea.persona}",
    ]
    if idea.score is not None:
        parts = (idea.score_breakdown or {}).get("components", {})
        lines.append(
            f"Score: {idea.score:.0f}/100 ("
            + ", ".join(f"{k.replace('_', ' ')} {v:.2f}" for k, v in parts.items())
            + ")"
        )
    if evidence:
        lines.append(
            f"Evidence collected: {evidence.signals} Signals from {evidence.authors} people in "
            f"{evidence.communities} communities; {evidence.payments} about paying, "
            f"{evidence.complaints} complaints about existing products."
        )

    lines += ["", "## Strongest quotes", ""]
    for q in quotes:
        lines.append(
            f'- signal-{q.signal_id} [{q.kind}] "{q.text}" ({q.author or "someone"}, {q.source}, '
            f"{q.when:%Y-%m-%d}, {q.url})"
        )
    if not quotes:
        lines.append("- none collected yet (this Idea was added by hand)")

    lines += ["", "## Products already mentioned in the evidence", ""]
    lines += [
        f"- {name}: mentioned {n}×" + (f", {c} complaints" if c else "")
        for name, n, c in competitors
    ] or ["- none"]

    lines += ["", "## Where the evidence came from", ""]
    lines += [f"- {src}: {name} ({n} Signals)" for src, name, n in communities] or ["- nowhere yet"]

    lines += ["", "## The founder", ""]
    if profile:
        lines += [
            f"Skills: {'; '.join(profile.skills) or 'not given'}",
            f"Monthly revenue goal: ${profile.revenue_goal_usd:,}"
            if profile.revenue_goal_usd
            else "Monthly revenue goal: not given (assume $10,000)",
            f"Price range they prefer: ${profile.price_min_usd or 0}–"
            f"{profile.price_max_usd or '?'} per customer per month",
            f"Sales model: {profile.sales_model or 'not given'}",
        ]
    else:
        lines.append("No profile given. Assume one developer and a $10,000 monthly revenue goal.")

    lines += ["", "## US market data", ""]
    if total is not None:
        for r in rows:
            lines.append(
                f"- NAICS {r.naics_code} {r.label}: {r.establishments:,} US businesses "
                f"(County Business Patterns {year})"
            )
        lines.append(f"Total: {total:,} US businesses.")
    else:
        lines.append(
            "No industry count: the Persona isn't tied to one industry, or no Census data is "
            "loaded. Estimate the number of US businesses and label it [assumption]."
        )

    lines += ["", INSTRUCTIONS, output_format(idea.id)]
    return "\n".join(lines)


# --- the reply ------------------------------------------------------------------------------


@dataclass
class Parsed:
    frontmatter: Frontmatter | None = None
    body: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


FENCE = re.compile(r"^\s*```[a-z]*\s*$", re.M)
EVIDENCE_LABEL = re.compile(r"\[evidence:\s*signal-(\d+)\]")
HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)


def _extract(raw: str) -> str:
    """The document from a reply that may be wrapped in a code fence or have text around it."""
    text = raw.replace("\r\n", "\n")
    start = re.search(r"^---\s*$", text, re.M)
    if start is None:
        return text
    text = text[start.start() :]
    return FENCE.sub("", text).strip() + "\n"


def parse(raw: str, idea_id: int, known_signal_ids: set[int]) -> Parsed:
    out = Parsed()
    if not raw.strip():
        out.errors.append("The reply is empty.")
        return out
    try:
        post = frontmatter.loads(_extract(raw))
    except yaml.YAMLError as exc:
        out.errors.append(f"The frontmatter isn't valid YAML: {str(exc).splitlines()[0]}")
        return out
    if not post.metadata:
        out.errors.append(
            "No frontmatter found. The reply should start with a line of three dashes (---)."
        )
        return out
    try:
        out.frontmatter = Frontmatter.model_validate(post.metadata)
    except ValidationError as exc:
        for e in exc.errors():
            where = ".".join(str(p) for p in e["loc"])
            out.errors.append(f"Frontmatter {where}: {e['msg']}")
    if out.frontmatter and out.frontmatter.briefing_for != f"idea-{idea_id}":
        out.errors.append(
            f"This reply is for {out.frontmatter.briefing_for}, not idea-{idea_id}. "
            "Paste it on that Idea's page instead."
        )
    out.body = post.content.strip()
    found = [h for h in HEADING.findall(out.body)]
    missing = [h for h in HEADINGS if h not in found]
    if missing:
        out.errors.append("Missing sections: " + ", ".join(missing) + ".")
    unknown = sorted({int(n) for n in EVIDENCE_LABEL.findall(out.body)} - known_signal_ids)
    if unknown:
        out.warnings.append(
            "Evidence labels that aren't this Idea's Signals: "
            + ", ".join(f"signal-{n}" for n in unknown)
        )
    if out.frontmatter:
        m = out.frontmatter.market_math
        if m.us_businesses and m.customers_needed:
            expected = m.customers_needed / m.us_businesses * 100
            if m.share_pct is not None and not math.isclose(m.share_pct, expected, rel_tol=0.25):
                out.warnings.append(
                    f"Market share looks off: {m.customers_needed:,} of {m.us_businesses:,} "
                    f"US businesses is {expected:.2g}%, not {m.share_pct}%."
                )
    return out


# --- storing --------------------------------------------------------------------------------


class BriefingError(Exception):
    """A Briefing operation that doesn't apply (e.g. no prompt waiting for a reply)."""


async def latest(session: AsyncSession, idea_id: int, status: str | None = None) -> Briefing | None:
    query = select(Briefing).where(Briefing.idea_id == idea_id)
    if status:
        query = query.where(Briefing.status == status)
    return await session.scalar(query.order_by(Briefing.version.desc()).limit(1))


async def request(
    session: AsyncSession,
    idea: Idea,
    profile: FitProfile | None,
    evidence: Evidence | None,
    provider: str = "manual",
) -> Briefing:
    """Create (or refresh) the Briefing awaiting a reply, with a freshly built prompt."""
    prompt = await build_prompt(session, idea, profile, evidence)
    waiting = await latest(session, idea.id, "awaiting_response")
    if waiting is None:
        newest = await latest(session, idea.id)
        waiting = Briefing(
            idea_id=idea.id,
            version=(newest.version + 1) if newest else 1,
            status="awaiting_response",
            provider=provider,
            format_version=FORMAT_VERSION,
            prompt_text=prompt,
        )
        session.add(waiting)
    waiting.prompt_text = prompt
    waiting.signal_count_at_gen = evidence.signals if evidence else 0
    waiting.community_ids_at_gen = evidence.community_ids if evidence else []
    waiting.created_at = datetime.now(UTC)
    await session.commit()
    return waiting


async def accept_reply(
    session: AsyncSession, idea_id: int, raw: str
) -> tuple[Briefing | None, Parsed]:
    """Check a pasted reply; if it's valid, store it and its Competitors. Errors leave nothing
    changed, so you can fix the paste and try again."""
    waiting = await latest(session, idea_id, "awaiting_response")
    if waiting is None:
        raise BriefingError("There's no Briefing prompt waiting for a reply. Generate one first.")
    signal_ids = set(
        (
            await session.scalars(select(IdeaSignal.signal_id).where(IdeaSignal.idea_id == idea_id))
        ).all()
    )
    parsed = parse(raw, idea_id, signal_ids)
    if parsed.errors:
        return None, parsed
    fm = parsed.frontmatter
    for entry in fm.competitors:
        competitor_id = await find_or_create(session, entry.name, created_from="briefing")
        if competitor_id is None:
            continue
        competitor = await session.get(Competitor, competitor_id)
        competitor.url = competitor.url or entry.url
        competitor.pricing = competitor.pricing or entry.pricing
        await session.merge(IdeaCompetitor(idea_id=idea_id, competitor_id=competitor_id))
    waiting.status = "complete"
    waiting.frontmatter = fm.model_dump(mode="json")
    waiting.content_md = parsed.body
    waiting.raw_response = raw
    waiting.warnings = parsed.warnings
    waiting.completed_at = datetime.now(UTC)
    from ideafinder.validation.experiments import prefill_hypothesis

    await prefill_hypothesis(session, idea_id)  # only if you haven't written one
    await session.commit()
    return waiting, parsed


def is_stale(briefing: Briefing, e: Evidence, rule: StaleRule) -> bool:
    before = briefing.signal_count_at_gen or 0
    if e.signals > before and e.signals >= before * (1 + rule.signal_growth_pct / 100):
        return True
    return rule.on_new_community and bool(set(e.community_ids) - set(briefing.community_ids_at_gen))


async def mark_stale(
    session: AsyncSession, rule: StaleRule, evidence: dict[int, Evidence]
) -> list[int]:
    """Mark each Idea's newest complete Briefing Stale if its evidence has grown a lot since.
    Stale Briefings are never regenerated automatically."""
    newest = (
        select(Briefing.idea_id, func.max(Briefing.version).label("v"))
        .where(Briefing.status == "complete")
        .group_by(Briefing.idea_id)
        .subquery()
    )
    briefings = (
        await session.scalars(
            select(Briefing)
            .join(newest, (Briefing.idea_id == newest.c.idea_id) & (Briefing.version == newest.c.v))
            .where(Briefing.stale.is_(False))
        )
    ).all()
    marked = []
    for b in briefings:
        e = evidence.get(b.idea_id)
        if e and is_stale(b, e, rule):
            b.stale = True
            marked.append(b.idea_id)
    await session.commit()
    return marked
