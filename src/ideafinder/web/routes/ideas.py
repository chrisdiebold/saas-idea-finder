"""The review screens: Inbox, Working List, Archive, Idea detail, and Add idea."""

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import load_config
from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import (
    Competitor,
    Idea,
    IdeaReview,
    IdeaSignal,
    IdeaSnapshot,
    ProposedDecision,
    SignalCompetitor,
)
from ideafinder.pipeline.briefing import latest
from ideafinder.settings import get_settings
from ideafinder.web.templating import REASON_LABELS, templates, with_message

router = APIRouter()

NEW_FOR_DAYS = 7
MERGE_TARGETS = 300
ARCHIVE_PERIODS = {"30": 30, "90": 90, "365": 365, "all": None}


def _config():
    return load_config(get_settings().config_dir)


async def nav_counts(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(Idea.state, func.count())
        .where(Idea.merged_into_id.is_(None), Idea.state.in_(("inbox", "working")))
        .group_by(Idea.state)
    )
    return dict(rows.all())


async def render(request: Request, session: AsyncSession, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        name,
        {
            "nav": await nav_counts(session),
            "msg": request.query_params.get("msg"),
            "error": request.query_params.get("error"),
            **context,
        },
    )


def _pending():
    return and_(ProposedDecision.idea_id == Idea.id, ProposedDecision.status == "pending")


async def merge_targets(session: AsyncSession, exclude: int | None = None) -> list[Idea]:
    query = (
        select(Idea)
        .where(Idea.merged_into_id.is_(None))
        .order_by(Idea.score.desc().nulls_last())
        .limit(MERGE_TARGETS)
    )
    if exclude is not None:
        query = query.where(Idea.id != exclude)
    return list((await session.scalars(query)).all())


# --- Inbox ----------------------------------------------------------------------------------


@router.get("/", include_in_schema=False)
async def home() -> RedirectResponse:
    return RedirectResponse("/inbox", status_code=307)


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request) -> HTMLResponse:
    confident_at = _config().scoring.inbox.confident_at
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(Idea, ProposedDecision)
                .outerjoin(ProposedDecision, _pending())
                .where(Idea.state == "inbox", Idea.merged_into_id.is_(None))
                .order_by(Idea.score.desc().nulls_last(), Idea.id)
            )
        ).all()
        confident = [(i, p) for i, p in rows if p and p.confidence >= confident_at]
        uncertain = [(i, p) for i, p in rows if not (p and p.confidence >= confident_at)]
        new_since = datetime.now(UTC) - timedelta(days=NEW_FOR_DAYS)
        return await render(
            request,
            session,
            "inbox.html",
            confident=confident,
            uncertain=uncertain,
            new_since=new_since,
            confident_at=confident_at,
            targets=await merge_targets(session),
        )


@router.post("/inbox/confirm")
async def confirm_all(ids: Annotated[list[int] | None, Form()] = None) -> RedirectResponse:
    from ideafinder.pipeline.review import ReviewError, review

    config = _config().scoring
    done, problems = 0, []
    async with get_sessionmaker()() as session:
        for idea_id in ids or []:
            proposal = await session.scalar(
                select(ProposedDecision).where(
                    ProposedDecision.idea_id == idea_id, ProposedDecision.status == "pending"
                )
            )
            if proposal is None:
                continue
            try:
                await review(session, config, idea_id, proposal.proposal, proposal.archive_reason)
                done += 1
            except ReviewError as exc:
                await session.rollback()
                problems.append(f"#{idea_id}: {exc}")
    message = f"Confirmed {done} proposal{'s' if done != 1 else ''}." if done else None
    return RedirectResponse(
        with_message("/inbox", message, "; ".join(problems) or None), status_code=303
    )


# --- Reviews, Merge, Split, notes -----------------------------------------------------------


def _back(back: str, fallback: str) -> str:
    return back if back.startswith("/") and not back.startswith("//") else fallback


def _done_message(idea: Idea) -> str:
    if idea.state == "working":
        return f"Moved “{idea.title}” to the Working List."
    if idea.state == "archived":
        return f"Archived “{idea.title}”: {REASON_LABELS.get(idea.archive_reason, '')}."
    return f"Restored “{idea.title}” to the Inbox."


@router.post("/ideas/{idea_id}/review")
async def review_idea(
    idea_id: int,
    to: Annotated[str, Form()],
    reason: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    back: Annotated[str, Form()] = "",
) -> RedirectResponse:
    from ideafinder.pipeline.review import ReviewError, review

    target = _back(back, f"/ideas/{idea_id}")
    async with get_sessionmaker()() as session:
        try:
            idea = await review(
                session, _config().scoring, idea_id, to, reason or None, note.strip() or None
            )
        except ReviewError as exc:
            return RedirectResponse(with_message(target, error=str(exc)), status_code=303)
    return RedirectResponse(with_message(target, _done_message(idea)), status_code=303)


@router.post("/ideas/{idea_id}/confirm")
async def confirm_one(idea_id: int, back: Annotated[str, Form()] = "") -> RedirectResponse:
    from ideafinder.pipeline.review import ReviewError, review

    target = _back(back, f"/ideas/{idea_id}")
    async with get_sessionmaker()() as session:
        proposal = await session.scalar(
            select(ProposedDecision).where(
                ProposedDecision.idea_id == idea_id, ProposedDecision.status == "pending"
            )
        )
        if proposal is None:
            return RedirectResponse(
                with_message(target, error="This Idea has no proposal waiting."), status_code=303
            )
        try:
            idea = await review(
                session, _config().scoring, idea_id, proposal.proposal, proposal.archive_reason
            )
        except ReviewError as exc:
            return RedirectResponse(with_message(target, error=str(exc)), status_code=303)
    return RedirectResponse(with_message(target, _done_message(idea)), status_code=303)


def _idea_id(text: str) -> int | None:
    """Accept "123", "#123" or "123 Title" (what the merge box suggests)."""
    match = re.match(r"\s*#?(\d+)", text)
    return int(match.group(1)) if match else None


@router.post("/ideas/{idea_id}/merge")
async def merge_idea(
    idea_id: int, into: Annotated[str, Form()], back: Annotated[str, Form()] = ""
) -> RedirectResponse:
    from ideafinder.pipeline.cluster import ClusterError, merge

    target_id = _idea_id(into)
    fallback = _back(back, f"/ideas/{idea_id}")
    if target_id is None:
        return RedirectResponse(
            with_message(fallback, error="Pick the Idea to merge into."), status_code=303
        )
    async with get_sessionmaker()() as session:
        try:
            target = await merge(session, idea_id, target_id, _config().scoring.clustering)
        except ClusterError as exc:
            return RedirectResponse(with_message(fallback, error=str(exc)), status_code=303)
    return RedirectResponse(
        with_message(f"/ideas/{target.id}", f"Merged #{idea_id} into this Idea."),
        status_code=303,
    )


@router.post("/ideas/{idea_id}/split")
async def split_idea(
    idea_id: int, signal_ids: Annotated[list[int] | None, Form()] = None
) -> RedirectResponse:
    from ideafinder.llm import LLM, ModelUnavailable
    from ideafinder.pipeline.cluster import ClusterError, split

    config = _config()
    here = f"/ideas/{idea_id}"
    try:
        async with LLM(config.models) as llm, get_sessionmaker()() as session:
            new = await split(session, llm, idea_id, signal_ids or [], config.scoring.clustering)
    except (ClusterError, ModelUnavailable) as exc:
        return RedirectResponse(with_message(here, error=str(exc)), status_code=303)
    return RedirectResponse(
        with_message(f"/ideas/{new.id}", f"Split {len(signal_ids)} Signals off #{idea_id}."),
        status_code=303,
    )


@router.post("/ideas/{idea_id}/notes")
async def save_notes(idea_id: int, notes: Annotated[str, Form()] = "") -> RedirectResponse:
    async with get_sessionmaker()() as session:
        idea = await session.get(Idea, idea_id)
        if idea is not None:
            idea.notes_md = notes.strip() or None
            await session.commit()
    return RedirectResponse(with_message(f"/ideas/{idea_id}", "Notes saved."), status_code=303)


# --- Add idea -------------------------------------------------------------------------------


@router.get("/ideas/new", response_class=HTMLResponse)
async def new_idea_form(request: Request) -> HTMLResponse:
    async with get_sessionmaker()() as session:
        return await render(request, session, "new_idea.html", form={})


@router.post("/ideas/new", response_class=HTMLResponse, response_model=None)
async def new_idea(
    request: Request,
    title: Annotated[str, Form()] = "",
    problem_statement: Annotated[str, Form()] = "",
    persona: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    from ideafinder.llm import LLM, ModelUnavailable
    from ideafinder.pipeline.seed import create_seeded
    from ideafinder.worker import defer

    config = _config()
    form = {"title": title, "problem_statement": problem_statement, "persona": persona}
    try:
        async with LLM(config.models) as llm, get_sessionmaker()() as session:
            seeded = await create_seeded(
                session, llm, config.scoring.clustering, title, problem_statement, persona
            )
    except (ValueError, ModelUnavailable) as exc:
        async with get_sessionmaker()() as session:
            return await render(request, session, "new_idea.html", form=form, error=str(exc))
    if seeded.attached:
        await defer("summarize_idea", idea_id=seeded.idea.id)
    found = (
        f"Found {seeded.attached} matching Signals; the Summary is being written."
        if seeded.attached
        else "No matching Signals yet; new ones will attach as they're collected."
    )
    return RedirectResponse(
        with_message(f"/ideas/{seeded.idea.id}", f"Added to the Working List. {found}"),
        status_code=303,
    )


# --- Idea detail ----------------------------------------------------------------------------


def sparkline(values: list[float], width: int = 240, height: int = 48) -> str | None:
    """SVG polyline points for a score trend (0-100), or None with fewer than two points."""
    if len(values) < 2:
        return None
    step = width / (len(values) - 1)
    return " ".join(
        f"{i * step:.1f},{height - (v / 100) * height:.1f}" for i, v in enumerate(values)
    )


@router.get("/ideas/{idea_id}", response_class=HTMLResponse)
async def idea_page(request: Request, idea_id: int) -> HTMLResponse:
    from ideafinder.pipeline.cluster import idea_detail

    async with get_sessionmaker()() as session:
        detail = await idea_detail(session, idea_id)
        if detail is None:
            page = await render(
                request, session, "missing.html", what=f"There's no Idea #{idea_id}."
            )
            page.status_code = 404
            return page
        competitors = (
            await session.execute(
                select(Competitor, func.count())
                .join(SignalCompetitor, SignalCompetitor.competitor_id == Competitor.id)
                .join(IdeaSignal, IdeaSignal.signal_id == SignalCompetitor.signal_id)
                .where(IdeaSignal.idea_id == idea_id)
                .group_by(Competitor.id)
                .order_by(func.count().desc())
            )
        ).all()
        snapshots = (
            await session.scalars(
                select(IdeaSnapshot)
                .where(IdeaSnapshot.idea_id == idea_id)
                .order_by(IdeaSnapshot.week)
            )
        ).all()
        trend = [s.score for s in snapshots if s.score is not None]
        briefing_done = await latest(session, idea_id, "complete")
        briefing_waiting = await latest(session, idea_id, "awaiting_response")
        return await render(
            request,
            session,
            "idea.html",
            d=detail,
            idea=detail.idea,
            competitors=competitors,
            snapshots=snapshots,
            trend=sparkline(trend),
            targets=await merge_targets(session, exclude=idea_id),
            briefing_done=briefing_done,
            briefing_waiting=briefing_waiting,
        )


# --- Working List and Archive ---------------------------------------------------------------


@router.get("/working", response_class=HTMLResponse)
async def working(request: Request) -> HTMLResponse:
    async with get_sessionmaker()() as session:
        ideas = (
            await session.scalars(
                select(Idea)
                .where(Idea.state == "working", Idea.merged_into_id.is_(None))
                .order_by(Idea.score.desc().nulls_last())
            )
        ).all()
        return await render(request, session, "working.html", ideas=ideas)


@router.get("/archive", response_class=HTMLResponse)
async def archive(request: Request, reason: str = "", period: str = "all") -> HTMLResponse:
    async with get_sessionmaker()() as session:
        query = (
            select(Idea)
            .where(Idea.state == "archived", Idea.merged_into_id.is_(None))
            .order_by(Idea.score.desc().nulls_last())
        )
        if reason in REASON_LABELS:
            query = query.where(Idea.archive_reason == reason)
        days = ARCHIVE_PERIODS.get(period)
        if days:
            recent = (
                select(IdeaReview.idea_id)
                .where(
                    IdeaReview.action.in_(("to_archive", "auto_outside_fit")),
                    IdeaReview.created_at >= datetime.now(UTC) - timedelta(days=days),
                )
                .scalar_subquery()
            )
            query = query.where(Idea.id.in_(recent))
        ideas = (await session.scalars(query)).all()
        counts = dict(
            (
                await session.execute(
                    select(Idea.archive_reason, func.count())
                    .where(Idea.state == "archived", Idea.merged_into_id.is_(None))
                    .group_by(Idea.archive_reason)
                )
            ).all()
        )
        return await render(
            request,
            session,
            "archive.html",
            ideas=ideas,
            reason=reason,
            period=period,
            counts=counts,
        )


@router.post("/rank-now")
async def rank_now(back: Annotated[str, Form()] = "/inbox") -> RedirectResponse:
    """Queue a ranking run (score, fit, publish, summaries, proposals) for the worker."""
    from ideafinder.worker import defer

    await defer("rank_ideas", timestamp=int(time.time()))
    return RedirectResponse(
        with_message(
            _back(back, "/inbox"),
            "Ranking queued. The worker runs it next; refresh in a minute or two.",
        ),
        status_code=303,
    )
