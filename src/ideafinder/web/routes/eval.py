"""The test-set review page: confirm or correct the draft labels, one Item at a time."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import EvalItem, Item, Source
from ideafinder.pipeline.extract import SIGNAL_KINDS, thread_title

router = APIRouter(prefix="/eval")
templates = Jinja2Templates(directory=Path(__file__).parents[1] / "templates")


@router.get("", response_class=HTMLResponse)
async def review(request: Request, show: str = "unreviewed") -> HTMLResponse:
    async with get_sessionmaker()() as session:
        query = (
            select(EvalItem, Item, Source.name)
            .join(Item, Item.id == EvalItem.item_id)
            .join(Source, Source.id == Item.source_id)
            .order_by(Item.id)
        )
        if show == "unreviewed":
            query = query.where(EvalItem.reviewed_at.is_(None))
        elif show == "changed":
            query = query.where(EvalItem.labelled_by == "you")
        rows = (await session.execute(query)).all()
        cards = []
        for label, item, source_name in rows:
            title = await thread_title(session, item.source_id, item.thread_external_id, [item])
            cards.append({"label": label, "item": item, "source": source_name, "thread": title})
        total = await session.scalar(select(func.count()).select_from(EvalItem))
        reviewed = await session.scalar(
            select(func.count()).select_from(EvalItem).where(EvalItem.reviewed_at.is_not(None))
        )
        changed = await session.scalar(
            select(func.count()).select_from(EvalItem).where(EvalItem.labelled_by == "you")
        )
    return templates.TemplateResponse(
        request,
        "eval.html",
        {
            "cards": cards,
            "show": show,
            "total": total,
            "reviewed": reviewed,
            "changed": changed,
            "kinds": SIGNAL_KINDS,
        },
    )


@router.post("/{item_id}")
async def save(
    item_id: int,
    has_signal: Annotated[str, Form()],
    kinds: Annotated[list[str] | None, Form()] = None,
    note: Annotated[str, Form()] = "",
    show: Annotated[str, Form()] = "unreviewed",
) -> RedirectResponse:
    async with get_sessionmaker()() as session:
        label = await session.get(EvalItem, item_id)
        if label is not None:
            new_signal = has_signal == "yes"
            new_kinds = sorted(k for k in (kinds or []) if k in SIGNAL_KINDS) if new_signal else []
            if (new_signal, new_kinds) != (label.has_signal, sorted(label.kinds or [])):
                label.labelled_by = "you"
            label.has_signal = new_signal
            label.kinds = new_kinds
            label.note = note.strip() or None
            label.reviewed_at = datetime.now(UTC)
            await session.commit()
    return RedirectResponse(f"/eval?show={show}", status_code=303)
