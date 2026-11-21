"""Persona → US industry codes (NAICS), for market math in Briefings.

The local model only chooses among codes whose names share a word with the Persona, so it can't
invent codes. A Persona that's a role found in every industry ("Jira administrator") maps to no
code, and market math says so instead of guessing.
"""

import re

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import Idea, MarketStat
from ideafinder.llm import LLM

MAX_CANDIDATES = 40
STOPWORDS = {
    "owner", "owners", "small", "large", "medium", "sized", "company", "companies", "business",
    "businesses", "manager", "managers", "team", "teams", "people", "user", "users", "person",
    "who", "with", "that", "their", "they", "them", "from", "work", "working", "workers",
    "independent", "local", "service", "services", "firm", "firms", "agency", "agencies",
    "operator", "operators", "professional", "professionals", "staff", "employee", "employees",
}  # fmt: skip

SYSTEM = """\
You map a Persona to US industry codes (NAICS) so we can count how many US businesses could buy
a product for them. Choose the codes for the businesses that employ or are run by this Persona.
- Pick at most 3 codes, from the candidates only. Prefer the most specific code that covers
  the Persona; pick a broader one when the Persona spans several specific industries.
- If the Persona is a role found in most industries (e.g. "accountant at any company",
  "software developer"), return no codes: an industry count would be misleading."""


# Words people use that Census industry names don't ("WooCommerce store" → online retail).
SYNONYMS = {
    "ecommerce": ["electronic shopping"],
    "commerce": ["electronic shopping"],
    "woocommerce": ["electronic shopping"],
    "shopify": ["electronic shopping"],
    "online": ["electronic shopping"],
    "saas": ["software publishers", "computer systems design"],
    "startup": ["software publishers", "computer systems design"],
    "agency": ["advertising agencies", "computer systems design"],
    "clinic": ["offices of physicians", "offices of dentists"],
    "restaurant": ["restaurants"],
    "landlord": ["lessors of residential"],
    "realtor": ["real estate agents"],
}


def keywords(persona: str) -> list[str]:
    """Search terms for the Persona: rough stems of its meaningful words ("landscaping" →
    "landsc"), plus industry names for common words Census doesn't use."""
    words = re.findall(r"[a-z]+", persona.lower().replace("e-commerce", "ecommerce"))
    terms = {w[:6] for w in words if len(w) >= 4 and w not in STOPWORDS}
    for w in words:
        terms.update(SYNONYMS.get(w, []))
    return sorted(terms)


async def latest_year(session: AsyncSession) -> int | None:
    return await session.scalar(select(func.max(MarketStat.year)))


async def candidates(session: AsyncSession, persona: str, year: int) -> list[MarketStat]:
    stems = keywords(persona)
    if not stems:
        return []
    rows = await session.scalars(
        select(MarketStat)
        .where(MarketStat.year == year, or_(*[MarketStat.label.ilike(f"%{s}%") for s in stems]))
        .order_by(MarketStat.establishments.desc())
        .limit(MAX_CANDIDATES)
    )
    return list(rows.all())


async def map_industry(session: AsyncSession, llm: LLM, idea: Idea) -> list[str]:
    """Choose and store the Idea's industry codes (not committed). [] if none fit."""
    year = await latest_year(session)
    persona = (idea.summary or {}).get("persona") or idea.persona
    options = await candidates(session, persona, year) if year else []
    if not options:
        idea.industry_codes = []
        return []
    listing = "\n".join(
        f"- {o.naics_code}: {o.label} ({o.establishments:,} US businesses)" for o in options
    )
    schema = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "items": {"type": "string", "enum": [o.naics_code for o in options]},
            }
        },
        "required": ["codes"],
        "additionalProperties": False,
    }
    prompt = f"Persona: {persona}\nProblem: {idea.problem_statement}\n\nCandidates:\n{listing}"
    result = await llm.json("industry_codes", SYSTEM, prompt, schema, max_tokens=200)
    valid = {o.naics_code for o in options}
    chosen = [c for c in dict.fromkeys(result.data.get("codes", [])) if c in valid][:3]
    idea.industry_codes = chosen
    return chosen


def broadest(codes: list[str]) -> list[str]:
    """Drop codes inside another chosen code, so no business is counted twice."""
    return [c for c in codes if not any(c != o and c.startswith(o) for o in codes)]


async def us_businesses(
    session: AsyncSession, codes: list[str]
) -> tuple[int | None, list[MarketStat], int | None]:
    """Total US establishments for the codes, the rows behind it, and the data year."""
    year = await latest_year(session)
    if not codes or year is None:
        return None, [], year
    rows = list(
        (
            await session.scalars(
                select(MarketStat)
                .where(MarketStat.year == year, MarketStat.naics_code.in_(broadest(codes)))
                .order_by(MarketStat.naics_code)
            )
        ).all()
    )
    return (sum(r.establishments for r in rows) if rows else None), rows, year


async def map_industries(session: AsyncSession, llm: LLM) -> int:
    """Map Inbox and Working List Ideas that haven't been mapped yet (their Briefings need it)."""
    from ideafinder.llm import ModelUnavailable, ServerUnreachable

    if await latest_year(session) is None:
        return 0  # no Census data loaded: nothing to map against
    ideas = (
        await session.scalars(
            select(Idea).where(
                Idea.state.in_(("inbox", "working")),
                Idea.merged_into_id.is_(None),
                Idea.industry_codes.is_(None),
            )
        )
    ).all()
    mapped = 0
    for idea in ideas:
        try:
            await map_industry(session, llm, idea)
        except ServerUnreachable:
            raise
        except ModelUnavailable:
            await session.rollback()
            continue
        await session.commit()
        mapped += 1
    return mapped
