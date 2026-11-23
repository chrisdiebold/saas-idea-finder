"""Activity: what the app did lately, newest first (events from ideafinder.events)."""

from itertools import groupby

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import Event
from ideafinder.web.templating import templates

router = APIRouter()

SHOWN = 300


@router.get("/activity", response_class=HTMLResponse)
async def activity(request: Request, show: str = "all") -> HTMLResponse:
    problems = show == "problems"
    query = select(Event).order_by(Event.at.desc(), Event.id.desc()).limit(SHOWN)
    if problems:
        query = query.where(Event.level.in_(("warning", "error")))
    async with get_sessionmaker()() as session:
        found = (await session.scalars(query)).all()
    days = [
        (day, list(group)) for day, group in groupby(found, key=lambda e: e.at.astimezone().date())
    ]
    return templates.TemplateResponse(
        request, "activity.html", {"days": days, "problems": problems, "shown": SHOWN}
    )
