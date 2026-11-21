"""Competitors: one record per existing product, matched across spellings and domains."""

import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import Competitor, CompetitorAlias, SignalCompetitor

# Things people mention that aren't products anyone competes with.
GENERIC = {
    "spreadsheet",
    "spreadsheets",
    "email",
    "e-mail",
    "emails",
    "paper",
    "pen and paper",
    "whiteboard",
    "phone",
    "sms",
    "text message",
    "text messages",
    "calendar",
    "notes",
    "api",
    "crm",
    "erp",
    "database",
    "website",
    "app",
    "apps",
    "software",
    "tool",
    "tools",
    "script",
    "scripts",
    "ai",
    "llm",
    "chatgpt wrapper",
    "manual",
    "manually",
}
_TLD = re.compile(r"\.(com|io|app|co|net|org|ai|dev|so|hq|us|uk)$")
_PREFIXES = ("get", "try", "use")
_SUFFIXES = ("app", "hq")


def aliases_for(name: str) -> list[str]:
    """Normalised forms that identify the same product: "GetJobber", "jobber.com" -> "jobber"."""
    base = name.strip().lower()
    base = re.sub(r"^https?://", "", base)
    base = re.sub(r"^www\.", "", base).split("/")[0]
    base = _TLD.sub("", base)
    base = re.sub(r"[^a-z0-9+#]+", " ", base).strip()
    if not base or base in GENERIC or len(base) < 2:
        return []
    compact = base.replace(" ", "")
    forms = {base, compact}
    for prefix in _PREFIXES:
        if compact.startswith(prefix) and len(compact) > len(prefix) + 3:
            forms.add(compact[len(prefix) :])
    for suffix in _SUFFIXES:
        if compact.endswith(suffix) and len(compact) > len(suffix) + 3:
            forms.add(compact[: -len(suffix)])
    return sorted(forms)


async def find_or_create(
    session: AsyncSession, name: str, created_from: str = "mention"
) -> int | None:
    aliases = aliases_for(name)
    if not aliases:
        return None
    existing = await session.scalar(
        select(CompetitorAlias.competitor_id).where(CompetitorAlias.alias.in_(aliases)).limit(1)
    )
    if existing is None:
        competitor = Competitor(name=name.strip(), created_from=created_from)
        session.add(competitor)
        await session.flush()
        existing = competitor.id
    stmt = insert(CompetitorAlias).values(
        [{"alias": a, "competitor_id": existing} for a in aliases]
    )
    await session.execute(stmt.on_conflict_do_nothing(index_elements=["alias"]))
    return existing


async def link_products(session: AsyncSession, signal_id: int, products: list[str]) -> None:
    competitor_ids = set()
    for name in products:
        competitor_id = await find_or_create(session, name)
        if competitor_id is not None:
            competitor_ids.add(competitor_id)
    if competitor_ids:
        await session.execute(
            insert(SignalCompetitor)
            .values([{"signal_id": signal_id, "competitor_id": c} for c in competitor_ids])
            .on_conflict_do_nothing()
        )
