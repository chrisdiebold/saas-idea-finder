"""Settings (the Fit profile) and Communities (the Watch list with its evidence)."""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import text

from ideafinder.db.engine import get_sessionmaker
from ideafinder.pipeline.fit import FitProfileFile, get_profile, save_profile
from ideafinder.web.routes.ideas import render
from ideafinder.web.templating import with_message

router = APIRouter()


def _lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _int(value: str) -> int | None:
    value = value.strip().replace(",", "").lstrip("$")
    return int(value) if value else None


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    async with get_sessionmaker()() as session:
        profile = await get_profile(session)
        form = (
            {
                "skills": "\n".join(profile.skills),
                "price_min_usd": profile.price_min_usd or "",
                "price_max_usd": profile.price_max_usd or "",
                "sales_model": profile.sales_model or "",
                "preferred_industries": "\n".join(profile.preferred_industries),
                "avoided_industries": "\n".join(profile.avoided_industries),
                "deal_breakers": "\n".join(profile.deal_breakers),
                "revenue_goal_usd": profile.revenue_goal_usd or "",
            }
            if profile
            else {}
        )
        return await render(request, session, "settings.html", profile=profile, form=form)


@router.post("/settings", response_class=HTMLResponse, response_model=None)
async def save_settings(
    request: Request,
    skills: Annotated[str, Form()] = "",
    price_min_usd: Annotated[str, Form()] = "",
    price_max_usd: Annotated[str, Form()] = "",
    sales_model: Annotated[str, Form()] = "",
    preferred_industries: Annotated[str, Form()] = "",
    avoided_industries: Annotated[str, Form()] = "",
    deal_breakers: Annotated[str, Form()] = "",
    revenue_goal_usd: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    form = {
        "skills": skills,
        "price_min_usd": price_min_usd,
        "price_max_usd": price_max_usd,
        "sales_model": sales_model,
        "preferred_industries": preferred_industries,
        "avoided_industries": avoided_industries,
        "deal_breakers": deal_breakers,
        "revenue_goal_usd": revenue_goal_usd,
    }
    problem = None
    try:
        data = FitProfileFile(
            skills=_lines(skills),
            price_min_usd=_int(price_min_usd),
            price_max_usd=_int(price_max_usd),
            sales_model=sales_model or None,
            preferred_industries=_lines(preferred_industries),
            avoided_industries=_lines(avoided_industries),
            deal_breakers=_lines(deal_breakers),
            revenue_goal_usd=_int(revenue_goal_usd),
        )
    except ValidationError as exc:  # before ValueError: it's a subclass
        problem = "; ".join(e["msg"] for e in exc.errors())
    except ValueError:
        problem = "Amounts must be whole numbers of dollars, like 500."
    if problem:
        async with get_sessionmaker()() as session:
            profile = await get_profile(session)
            return await render(
                request, session, "settings.html", profile=profile, form=form, error=problem
            )
    async with get_sessionmaker()() as session:
        before = await get_profile(session)
        old_version = before.version if before else 0
        profile = await save_profile(session, data)
    if profile.version == old_version:
        message = "Nothing changed."
    else:
        message = (
            f"Saved as version {profile.version}. Ideas are checked against it on the next "
            "ranking run."
        )
    return RedirectResponse(with_message("/settings", message), status_code=303)


COMMUNITIES_SQL = """
SELECT c.id, c.name, c.external_ref, c.watch_status, s.name AS source,
       coalesce(i.items, 0) AS items, coalesce(i.kept, 0) AS kept,
       coalesce(g.signals, 0) AS signals, coalesce(g.ideas, 0) AS ideas
FROM communities c
JOIN sources s ON s.id = c.source_id
LEFT JOIN (
    SELECT community_id, count(*) AS items, count(*) FILTER (WHERE prefilter = 'keep') AS kept
    FROM items GROUP BY 1
) i ON i.community_id = c.id
LEFT JOIN (
    SELECT it.community_id, count(*) AS signals, count(DISTINCT x.idea_id) AS ideas
    FROM signals sg
    JOIN items it ON it.id = sg.item_id
    LEFT JOIN idea_signals x ON x.signal_id = sg.id
    GROUP BY 1
) g ON g.community_id = c.id
ORDER BY c.watch_status, s.name, c.name
"""


@router.get("/communities", response_class=HTMLResponse)
async def communities(request: Request) -> HTMLResponse:
    async with get_sessionmaker()() as session:
        rows = (await session.execute(text(COMMUNITIES_SQL))).mappings().all()
        watched = [r for r in rows if r["watch_status"] == "watched"]
        suggested = [r for r in rows if r["watch_status"] == "suggested"]
        return await render(
            request, session, "communities.html", watched=watched, suggested=suggested
        )
