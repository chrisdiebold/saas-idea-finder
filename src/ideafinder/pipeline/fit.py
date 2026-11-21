"""The Fit profile and the Fit check (PLAN.md §3 step 6).

The Fit profile lives in the database (edited in Settings from M5). Until then it's loaded from a
YAML file with `ideafinder fit load`. Each change raises its version, so Ideas are checked again.

One local-model call per Idea answers everything that needs judgement about it:
- which Deal-breakers it breaks (any → archived as "outside fit", no Proposed decision)
- how well it matches the Preferences (the Fit part of the score)
- whether the Persona is a business (feeds the Payment part of the score)
- whether one developer with your skills could build an MVP in 4–6 weeks (Solo-buildable)
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import FitProfile, Idea, IdeaSignal, Signal
from ideafinder.llm import LLM

PROFILE_ID = 1
QUOTES_FOR_CHECK = 5


class FitProfileFile(BaseModel):
    """What `ideafinder fit load` accepts. See config/fit_profile.example.yaml."""

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = []
    price_min_usd: int | None = Field(default=None, ge=0)
    price_max_usd: int | None = Field(default=None, ge=0)
    sales_model: Literal["self_serve", "sales_led", "either"] | None = None
    preferred_industries: list[str] = []
    avoided_industries: list[str] = []
    deal_breakers: list[str] = []
    revenue_goal_usd: int | None = Field(default=None, ge=0)


def read_profile_file(path: Path) -> FitProfileFile:
    return FitProfileFile.model_validate(yaml.safe_load(path.read_text()) or {})


async def get_profile(session: AsyncSession) -> FitProfile | None:
    return await session.get(FitProfile, PROFILE_ID)


async def save_profile(session: AsyncSession, data: FitProfileFile) -> FitProfile:
    """Store the profile. The version goes up only if something actually changed."""
    profile = await get_profile(session)
    values = data.model_dump()
    if profile is None:
        profile = FitProfile(id=PROFILE_ID, version=1, **values)
        session.add(profile)
    elif any(getattr(profile, k) != v for k, v in values.items()):
        for k, v in values.items():
            setattr(profile, k, v)
        profile.version += 1
    await session.commit()
    return profile


def profile_as_text(profile: FitProfile) -> str:
    def items(values: list[Any]) -> str:
        return "; ".join(values) if values else "none given"

    price = (
        f"${profile.price_min_usd or 0}–{profile.price_max_usd or '?'} per customer per month"
        if profile.price_min_usd or profile.price_max_usd
        else "not given"
    )
    return "\n".join(
        [
            f"Skills: {items(profile.skills)}",
            f"Price range: {price}",
            f"Sales model: {profile.sales_model or 'not given'}",
            f"Preferred industries: {items(profile.preferred_industries)}",
            f"Avoided industries: {items(profile.avoided_industries)}",
            f"Monthly revenue goal: ${profile.revenue_goal_usd or '?'}",
        ]
    )


SYSTEM = """\
You check a software business idea against a solo founder's Fit profile. The idea is a problem
experienced by a kind of person (the Persona); no product has been chosen yet.

Answer:
- deal_breakers: the founder's Deal-breakers that ANY reasonable software product for this problem
  would break, copied word for word from the list. Only list one when it clearly applies; if a
  product could avoid it, it doesn't apply. Usually this is empty.
- fit: 0.0–1.0, how well the idea matches the founder's Preferences (skills, price range, sales
  model, industries, whether the revenue goal is reachable). 0.5 when you can't tell.
- b2b: true if the people who have the problem would pay as a business (including sole traders).
- solo_buildable: 0.0–1.0, how likely one developer with these skills could build a useful first
  version in 4–6 weeks.
- notes: one sentence on the main reason for the fit score."""

SCHEMA = {
    "type": "object",
    "properties": {
        "deal_breakers": {"type": "array", "items": {"type": "string"}},
        "fit": {"type": "number"},
        "b2b": {"type": "boolean"},
        "solo_buildable": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["deal_breakers", "fit", "b2b", "solo_buildable", "notes"],
    "additionalProperties": False,
}


async def _quotes(session: AsyncSession, idea_id: int, limit: int) -> list[str]:
    rows = await session.scalars(
        select(Signal.quote)
        .join(IdeaSignal, IdeaSignal.signal_id == Signal.id)
        .where(IdeaSignal.idea_id == idea_id, Signal.quote_verified)
        .order_by(Signal.intensity.desc(), IdeaSignal.similarity.desc().nulls_last())
        .limit(limit)
    )
    return [q for q in rows.all() if q]


def render(idea: Idea, profile: FitProfile, quotes: list[str]) -> str:
    deal_breakers = "\n".join(f"- {d}" for d in profile.deal_breakers) or "- none"
    evidence = "\n".join(f'- "{q[:300]}"' for q in quotes) or "- none"
    return (
        f"IDEA: {idea.title}\nProblem: {idea.problem_statement}\nPersona: {idea.persona}\n"
        f"Evidence quotes:\n{evidence}\n\nFIT PROFILE\n{profile_as_text(profile)}\n"
        f"Deal-breakers:\n{deal_breakers}"
    )


def clean(data: dict[str, Any], profile: FitProfile) -> dict[str, Any]:
    """Keep only Deal-breakers that are really in the profile, and clamp the numbers."""
    known = {d.strip().lower(): d for d in profile.deal_breakers}
    broken = [known[d.strip().lower()] for d in data["deal_breakers"] if d.strip().lower() in known]
    return {
        "deal_breakers": sorted(set(broken)),
        "fit": min(1.0, max(0.0, float(data["fit"]))),
        "b2b": bool(data["b2b"]),
        "solo_buildable": min(1.0, max(0.0, float(data["solo_buildable"]))),
        "notes": data["notes"].strip(),
    }


async def check(session: AsyncSession, llm: LLM, idea: Idea, profile: FitProfile) -> dict:
    """Run the Fit check and store the result on the Idea (not committed)."""
    quotes = await _quotes(session, idea.id, QUOTES_FOR_CHECK)
    result = await llm.json(
        "fit_check", SYSTEM, render(idea, profile, quotes), SCHEMA, max_tokens=600
    )
    idea.fit = clean(result.data, profile)
    idea.fit_version = profile.version
    return idea.fit


def needs_check(idea: Idea, profile: FitProfile | None) -> bool:
    return profile is not None and not idea.seeded and idea.fit_version != profile.version
