"""Validation screens: the Working List, Head-to-heads, and each Idea's Validation page."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import (
    Briefing,
    Experiment,
    HeadToHead,
    Idea,
    Lead,
    ProposedDecision,
    Signal,
    WorkingItem,
)
from ideafinder.validation import experiments as exp
from ideafinder.validation import head_to_head as h2h
from ideafinder.validation import leads as leads_mod
from ideafinder.validation import working
from ideafinder.validation.kits import latest_kit
from ideafinder.web.routes.ideas import _config, render
from ideafinder.web.templating import with_message

router = APIRouter()


def _go(path: str, message: str | None = None, error: str | None = None) -> RedirectResponse:
    return RedirectResponse(with_message(path, message, error), status_code=303)


async def _latest_briefing(session, idea_id: int) -> Briefing | None:
    return await session.scalar(
        select(Briefing)
        .where(Briefing.idea_id == idea_id, Briefing.status == "complete")
        .order_by(Briefing.version.desc())
        .limit(1)
    )


# --- Working List ---------------------------------------------------------------------------


@router.get("/working", response_class=HTMLResponse)
async def working_list(request: Request) -> HTMLResponse:
    now = datetime.now(UTC)
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(Idea, WorkingItem)
                .join(WorkingItem, WorkingItem.idea_id == Idea.id)
                .where(Idea.state == "working", Idea.merged_into_id.is_(None))
                .order_by(WorkingItem.priority_rank)
            )
        ).all()
        ids = [i.id for i, _ in rows]
        hypotheses = {i: await exp.current_hypothesis(session, i) for i in ids}
        last_experiment = {}
        for i in ids:
            last_experiment[i] = await session.scalar(
                select(Experiment)
                .where(Experiment.idea_id == i)
                .order_by(Experiment.started_at.desc())
                .limit(1)
            )
        lead_counts = {
            (idea_id, status): n
            for idea_id, status, n in (
                await session.execute(
                    select(Lead.idea_id, Lead.status, func.count())
                    .where(Lead.idea_id.in_(ids))
                    .group_by(Lead.idea_id, Lead.status)
                )
            ).all()
        }
        kills = (
            await session.execute(
                select(ProposedDecision, Idea)
                .join(Idea, Idea.id == ProposedDecision.idea_id)
                .where(
                    ProposedDecision.kind == "kill",
                    ProposedDecision.status == "pending",
                    Idea.state == "working",
                )
            )
        ).all()
        pending = await h2h.pending(session)
        titles = {
            i.id: i.title
            for i in (
                await session.scalars(
                    select(Idea).where(
                        Idea.id.in_([x for p in pending for x in (p.idea_a, p.idea_b)])
                    )
                )
            ).all()
        }
        active = [(i, w) for i, w in rows if w.stage in working.ACTIVE]
        return await render(
            request,
            session,
            "working.html",
            active=active,
            paused=[(i, w) for i, w in rows if w.stage == "paused"],
            expired=[(i, w) for i, w in active if working.is_expired(w, now)],
            kills=kills,
            pending=pending,
            titles=titles,
            hypotheses=hypotheses,
            last_experiment=last_experiment,
            lead_counts=lead_counts,
            now=now,
            deadline=working.deadline,
            stage_labels=working.STAGE_LABELS,
            outcomes=exp.OUTCOMES,
            types=exp.TYPES,
            active_limit=_config().scoring.validation.active_limit,
        )


# --- Head-to-heads --------------------------------------------------------------------------


@router.get("/head-to-head/{h2h_id}", response_class=HTMLResponse)
async def head_to_head_page(request: Request, h2h_id: int) -> HTMLResponse:
    async with get_sessionmaker()() as session:
        match = await session.get(HeadToHead, h2h_id)
        if match is None:
            page = await render(request, session, "missing.html", what="No such Head-to-head.")
            page.status_code = 404
            return page
        sides = []
        for idea_id in (match.idea_a, match.idea_b):
            idea = await session.get(Idea, idea_id)
            sides.append(
                {
                    "idea": idea,
                    "item": await session.get(WorkingItem, idea_id),
                    "briefing": await _latest_briefing(session, idea_id),
                    "hypothesis": await exp.current_hypothesis(session, idea_id),
                }
            )
        return await render(
            request,
            session,
            "head_to_head.html",
            match=match,
            sides=sides,
            stage_labels=working.STAGE_LABELS,
            active_limit=_config().scoring.validation.active_limit,
        )


@router.post("/head-to-head/{h2h_id}")
async def decide_head_to_head(
    h2h_id: int,
    winner: Annotated[int, Form()],
    loser_action: Annotated[str, Form()] = "kept",
    reason: Annotated[str, Form()] = "",
) -> RedirectResponse:
    async with get_sessionmaker()() as session:
        try:
            await h2h.decide(
                session, _config().scoring, h2h_id, winner, loser_action, reason or None
            )
        except working.WorkingListError as exc:
            return _go(f"/head-to-head/{h2h_id}", error=str(exc))
        title = (await session.get(Idea, winner)).title
    return _go("/working", f"“{title}” comes first.")


# --- an Idea's Validation page --------------------------------------------------------------


@router.get("/ideas/{idea_id}/validation", response_class=HTMLResponse)
async def validation_page(request: Request, idea_id: int) -> HTMLResponse:
    async with get_sessionmaker()() as session:
        idea = await session.get(Idea, idea_id)
        item = await session.get(WorkingItem, idea_id)
        if idea is None or item is None or idea.state != "working":
            return _go(f"/ideas/{idea_id}", error="This Idea isn't on the Working List.")
        experiments = (
            await session.scalars(
                select(Experiment)
                .where(Experiment.idea_id == idea_id)
                .order_by(Experiment.started_at.desc())
            )
        ).all()
        leads = (
            await session.execute(
                select(Lead, Signal.quote)
                .join(Signal, Signal.id == Lead.signal_id)
                .where(Lead.idea_id == idea_id)
                .order_by(Lead.id)
            )
        ).all()
        talked_notes = {
            e.id: [lead for lead, _ in leads if lead.experiment_id == e.id and lead.notes_md]
            for e in experiments
        }
        return await render(
            request,
            session,
            "validation.html",
            idea=idea,
            item=item,
            hypothesis=await exp.current_hypothesis(session, idea_id),
            experiments=experiments,
            talked_notes=talked_notes,
            leads=leads,
            kit=await latest_kit(session, idea_id),
            can_advance=item.stage in working.NEXT
            and await working.passed_in_stage(session, idea_id, item.stage),
            expired=working.is_expired(item),
            deadline=working.deadline(item),
            now=datetime.now(UTC),
            stage_labels=working.STAGE_LABELS,
            types=exp.TYPES,
            outcomes=exp.OUTCOMES,
            statuses=leads_mod.STATUSES,
        )


@router.post("/ideas/{idea_id}/stage")
async def change_stage(
    idea_id: int, action: Annotated[str, Form()], back: Annotated[str, Form()] = ""
) -> RedirectResponse:
    from ideafinder.pipeline.review import ReviewError, review
    from ideafinder.worker import defer

    here = back if back.startswith("/") else f"/ideas/{idea_id}/validation"
    config = _config().scoring
    async with get_sessionmaker()() as session:
        try:
            if action == "advance":
                item = await working.advance(session, idea_id)
                if item.stage == "validating":
                    if not await session.scalar(
                        select(func.count()).where(Lead.idea_id == idea_id)
                    ):
                        await leads_mod.find_leads(session, idea_id)
                        await defer("write_lead_openers", idea_id=idea_id)
                    await defer("generate_validation_kit", idea_id=idea_id)
                    return _go(
                        here,
                        "Now Validating. The Validation kit is being written in the background.",
                    )
                return _go(here, f"Now {working.STAGE_LABELS[item.stage]}.")
            if action == "pause":
                await working.pause(session, idea_id)
                return _go(here, "Paused. It no longer counts toward the Active limit.")
            if action == "resume":
                await working.resume(session, idea_id, config.validation)
                return _go(here, "Resumed with a fresh Time limit.")
            if action == "extend":
                item = await working.extend(session, idea_id)
                return _go(here, f"Extended once, to {working.deadline(item).strftime('%-d %b')}.")
            if action == "drop":
                await review(session, config, idea_id, "archive", "dropped_after_research")
                return _go("/working", "Dropped after research and archived.")
        except (working.WorkingListError, ReviewError) as exc:
            return _go(here, error=str(exc))
    return _go(here, error="Unknown action.")


@router.post("/ideas/{idea_id}/time-limit")
async def time_limit(idea_id: int, days: Annotated[int, Form()]) -> RedirectResponse:
    here = f"/ideas/{idea_id}/validation"
    async with get_sessionmaker()() as session:
        try:
            await working.set_time_limit(session, idea_id, days)
        except working.WorkingListError as exc:
            return _go(here, error=str(exc))
    return _go(here, f"Time limit set to {days} days per Stage.")


@router.post("/ideas/{idea_id}/hypothesis")
async def hypothesis(
    idea_id: int, statement: Annotated[str, Form()] = "", kill_criteria: Annotated[str, Form()] = ""
) -> RedirectResponse:
    here = f"/ideas/{idea_id}/validation"
    async with get_sessionmaker()() as session:
        try:
            await exp.set_hypothesis(session, idea_id, statement, kill_criteria)
            await session.commit()
        except working.WorkingListError as exc:
            return _go(here, error=str(exc))
    return _go(here, "Hypothesis saved.")


@router.post("/ideas/{idea_id}/experiments")
async def new_experiment(
    idea_id: int, type: Annotated[str, Form()] = "", plan: Annotated[str, Form()] = ""
) -> RedirectResponse:
    here = f"/ideas/{idea_id}/validation"
    async with get_sessionmaker()() as session:
        try:
            await exp.add_experiment(session, idea_id, type, plan)
        except working.WorkingListError as exc:
            return _go(here, error=str(exc))
    return _go(here, "Experiment started.")


@router.post("/experiments/{experiment_id}/outcome")
async def outcome(
    experiment_id: int,
    outcome: Annotated[str, Form()] = "",
    evidence: Annotated[str, Form()] = "",
    met_kill_criteria: Annotated[str, Form()] = "",
) -> RedirectResponse:
    async with get_sessionmaker()() as session:
        e = await session.get(Experiment, experiment_id)
        if e is None:
            return _go("/working", error="No such Experiment.")
        here = f"/ideas/{e.idea_id}/validation"
        try:
            await exp.record_outcome(
                session, experiment_id, outcome, evidence, met_kill_criteria == "yes"
            )
        except working.WorkingListError as exc:
            return _go(here, error=str(exc))
    if met_kill_criteria == "yes":
        return _go(
            "/working",
            "Outcome saved. It met the Kill criteria, so archiving is proposed at the top; "
            "confirm it there.",
        )
    return _go(here, "Outcome saved.")


@router.post("/ideas/{idea_id}/leads/find")
async def find(idea_id: int) -> RedirectResponse:
    from ideafinder.worker import defer

    async with get_sessionmaker()() as session:
        added = await leads_mod.find_leads(session, idea_id)
    if added:
        await defer("write_lead_openers", idea_id=idea_id)
    message = (
        f"Added {added} Leads; opening lines are being written."
        if added
        else "No new Leads: every Author with a name is already listed."
    )
    return _go(f"/ideas/{idea_id}/validation#leads", message)


@router.post("/leads/{lead_id}")
async def update_lead(
    lead_id: int, status: Annotated[str, Form()], notes: Annotated[str, Form()] = ""
) -> RedirectResponse:
    async with get_sessionmaker()() as session:
        try:
            lead, attached = await leads_mod.update_lead(session, lead_id, status, notes)
        except working.WorkingListError as exc:
            return _go("/working", error=str(exc))
        here = f"/ideas/{lead.idea_id}/validation#leads"
        if status == "talked" and attached is not None:
            return _go(
                here, "Saved. Your notes count as evidence for the open interviews Experiment."
            )
        if status == "talked" and lead.experiment_id is None:
            return _go(
                here,
                "Saved. Start an interviews Experiment to use these notes as its evidence.",
            )
    return _go(here, "Lead updated.")


@router.post("/ideas/{idea_id}/kit")
async def regenerate_kit(idea_id: int) -> RedirectResponse:
    from ideafinder.worker import defer

    await defer("generate_validation_kit", idea_id=idea_id)
    return _go(
        f"/ideas/{idea_id}/validation#kit",
        "The Validation kit is being written; refresh in a minute.",
    )
