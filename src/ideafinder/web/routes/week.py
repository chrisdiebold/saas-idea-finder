"""This Week and Health."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, func, select, text

from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import Briefing, Idea, IdeaReview, Lead, ProposedDecision, WorkingItem
from ideafinder.health import get_health
from ideafinder.settings import get_settings
from ideafinder.status import get_status
from ideafinder.validation.working import ACTIVE, STAGE_LABELS, deadline
from ideafinder.web.routes.ideas import render
from ideafinder.web.templating import templates

router = APIRouter()
WEEK = timedelta(days=7)

MOVERS_SQL = text("""
SELECT d.id, d.title, d.state, d.score, s.score AS before, s.week
FROM ideas d
JOIN LATERAL (
    SELECT score, week FROM idea_snapshots
    WHERE idea_id = d.id AND week <= :before AND score IS NOT NULL
    ORDER BY week DESC LIMIT 1
) s ON true
WHERE d.merged_into_id IS NULL AND d.score IS NOT NULL AND d.state <> 'archived'
  AND abs(d.score - s.score) >= 1
ORDER BY abs(d.score - s.score) DESC
LIMIT 6
""")


@router.get("/week", response_class=HTMLResponse)
async def this_week(request: Request) -> HTMLResponse:
    now = datetime.now(UTC)
    since = now - WEEK
    async with get_sessionmaker()() as session:
        live = and_(Idea.merged_into_id.is_(None))
        new_inbox = (
            await session.scalars(
                select(Idea)
                .where(live, Idea.state == "inbox", Idea.published_at >= since)
                .where(Idea.resurfaced_at.is_(None) | (Idea.resurfaced_at < since))
                .order_by(Idea.score.desc().nulls_last())
            )
        ).all()
        resurfaced = (
            await session.scalars(
                select(Idea).where(live, Idea.state == "inbox", Idea.resurfaced_at >= since)
            )
        ).all()
        outside_fit = (
            await session.execute(
                select(Idea, IdeaReview.note)
                .join(IdeaReview, IdeaReview.idea_id == Idea.id)
                .where(IdeaReview.action == "auto_outside_fit", IdeaReview.created_at >= since)
                .order_by(IdeaReview.created_at.desc())
            )
        ).all()
        working_rows = (
            await session.execute(
                select(Idea, WorkingItem)
                .join(WorkingItem, WorkingItem.idea_id == Idea.id)
                .where(Idea.state == "working", WorkingItem.stage.in_(ACTIVE))
            )
        ).all()
        expiring = sorted(
            [(i, w, deadline(w)) for i, w in working_rows if deadline(w) <= now + WEEK],
            key=lambda t: t[2],
        )
        kills = (
            await session.execute(
                select(ProposedDecision, Idea)
                .join(Idea, Idea.id == ProposedDecision.idea_id)
                .where(ProposedDecision.kind == "kill", ProposedDecision.status == "pending")
            )
        ).all()
        awaiting_replies = (
            await session.execute(
                select(Lead, Idea.title)
                .join(Idea, Idea.id == Lead.idea_id)
                .where(Lead.status == "contacted", Idea.state == "working")
                .order_by(Lead.idea_id)
            )
        ).all()
        stale = (
            await session.execute(
                select(Briefing, Idea)
                .join(Idea, Idea.id == Briefing.idea_id)
                .where(Briefing.stale, Briefing.stale_at >= since)
            )
        ).all()
        last_monday = (now - timedelta(days=now.weekday() + 7)).date()
        movers = (await session.execute(MOVERS_SQL, {"before": last_monday})).mappings().all()
        inbox_waiting = await session.scalar(
            select(func.count()).where(Idea.state == "inbox", live)
        )
        return await render(
            request,
            session,
            "week.html",
            since=since,
            now=now,
            new_inbox=new_inbox,
            resurfaced=resurfaced,
            outside_fit=outside_fit,
            expiring=expiring,
            kills=kills,
            awaiting_replies=awaiting_replies,
            stale=stale,
            movers=movers,
            inbox_waiting=inbox_waiting,
            stage_labels=STAGE_LABELS,
        )


@router.get("/health", response_class=HTMLResponse)
@router.get("/status", response_class=HTMLResponse, include_in_schema=False)
def health_page(request: Request) -> HTMLResponse:
    """Plain `def`: these checks make blocking calls, so FastAPI runs them in a thread."""
    settings = get_settings()
    status = get_status(settings)
    health = get_health(settings, status.config) if status.database.up_to_date else None
    return templates.TemplateResponse(
        request, "status.html", {"status": status, "health": health, "settings": settings}
    )
