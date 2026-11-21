"""The Briefing page: the prompt to copy, the place to paste the reply, and the Briefing itself."""

import html
import re
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markdown_it import MarkdownIt
from markupsafe import Markup

from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import Idea
from ideafinder.pipeline import briefing
from ideafinder.web.routes.ideas import _config, render
from ideafinder.web.templating import with_message

router = APIRouter()
_md = MarkdownIt("commonmark", {"html": False}).enable("table")

EVIDENCE = re.compile(r"\[evidence:\s*signal-(\d+)\]")
RESEARCHED = re.compile(r"\[researched:\s*(https?://[^\]\s]+)\s*\]")
ASSUMPTION = re.compile(r"\[assumption\]", re.I)
H2 = re.compile(r"(?=<h2>)")


def render_briefing(markdown: str, idea_id: int) -> Markup:
    """Markdown → HTML (raw HTML in the reply is escaped), with claim labels as small marks and
    the Case against set apart."""
    out = _md.render(markdown)
    out = EVIDENCE.sub(
        lambda m: (
            f'<a class="claim evidence" href="/ideas/{idea_id}#signal-{m.group(1)}" '
            f'title="Signal {m.group(1)}">evidence</a>'
        ),
        out,
    )
    out = RESEARCHED.sub(
        lambda m: (
            f'<a class="claim researched" href="{html.escape(m.group(1), quote=True)}" '
            'target="_blank" rel="noopener noreferrer">source</a>'
        ),
        out,
    )
    out = ASSUMPTION.sub('<span class="claim assumption">assumption</span>', out)
    sections = H2.split(out)
    out = "".join(
        f'<section class="against">{s}</section>' if s.startswith("<h2>Case against") else s
        for s in sections
    )
    return Markup(out)


@router.get("/ideas/{idea_id}/briefing", response_class=HTMLResponse)
async def briefing_page(request: Request, idea_id: int) -> HTMLResponse:
    async with get_sessionmaker()() as session:
        return await _page(request, session, idea_id)


async def _page(request, session, idea_id, *, paste: str = "", parsed=None, status: int = 200):
    idea = await session.get(Idea, idea_id)
    if idea is None:
        page = await render(request, session, "missing.html", what=f"There's no Idea #{idea_id}.")
        page.status_code = 404
        return page
    done = await briefing.latest(session, idea_id, "complete")
    waiting = await briefing.latest(session, idea_id, "awaiting_response")
    page = await render(
        request,
        session,
        "briefing.html",
        idea=idea,
        done=done,
        body=render_briefing(done.content_md, idea_id) if done else None,
        waiting=waiting,
        paste=paste,
        parsed=parsed,
    )
    page.status_code = status
    return page


@router.post("/ideas/{idea_id}/briefing/generate")
async def generate(idea_id: int) -> RedirectResponse:
    from ideafinder.llm import LLM, ModelUnavailable
    from ideafinder.pipeline.fit import get_profile
    from ideafinder.pipeline.industry import latest_year, map_industry
    from ideafinder.pipeline.score import gather_evidence

    config = _config()
    note = None
    async with get_sessionmaker()() as session:
        idea = await session.get(Idea, idea_id)
        if idea is None:
            return RedirectResponse(f"/ideas/{idea_id}", status_code=303)
        if idea.industry_codes is None and await latest_year(session):
            try:
                async with LLM(config.models) as llm:
                    await map_industry(session, llm, idea)
                await session.commit()
            except ModelUnavailable:
                await session.rollback()
                note = "LM Studio isn't reachable, so the prompt has no US business count."
        e = (await gather_evidence(session, config.scoring, [idea_id])).get(idea_id)
        await briefing.request(session, idea, await get_profile(session), e)
    return RedirectResponse(
        with_message(f"/ideas/{idea_id}/briefing", "Prompt ready to copy.", note),
        status_code=303,
    )


@router.post("/ideas/{idea_id}/briefing/reply", response_class=HTMLResponse, response_model=None)
async def reply(
    request: Request, idea_id: int, response: Annotated[str, Form()] = ""
) -> HTMLResponse | RedirectResponse:
    async with get_sessionmaker()() as session:
        try:
            stored, parsed = await briefing.accept_reply(session, idea_id, response)
        except briefing.BriefingError as exc:
            return RedirectResponse(
                with_message(f"/ideas/{idea_id}/briefing", error=str(exc)), status_code=303
            )
        if stored is None:
            # Keep the paste so it can be fixed rather than copied again.
            return await _page(request, session, idea_id, paste=response, parsed=parsed, status=422)
    fm = parsed.frontmatter
    message = f"Briefing saved: {fm.verdict}, confidence {fm.confidence} of 5."
    if parsed.warnings:
        message += " Check the notes at the top."
    return RedirectResponse(with_message(f"/ideas/{idea_id}/briefing", message), status_code=303)
