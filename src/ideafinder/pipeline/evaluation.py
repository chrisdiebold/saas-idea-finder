"""The extraction test set: sample Items, label them, run models over them, compare.

The answer key is per Item: does it contain at least one Signal (pain, request, complaint or
payment), and of which kinds. Models are scored mainly on precision of "contains a Signal":
the plan's bar is ≥ 85%, because false Signals become noise in every later stage.
"""

import asyncio
import json
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import EvalItem, EvalResult, Item, Source
from ideafinder.llm import LLM
from ideafinder.pipeline.extract import Chunk, extract_chunk, thread_title

# How many test Items to draw from each (Source, kind). Weighted toward where the volume is,
# with enough of the small Sources to measure them at all. Totals 200.
STRATA: dict[tuple[str, str], int] = {
    ("jira_atlassian", "comment"): 50,
    ("jira_atlassian", "issue"): 40,
    ("hackernews", "comment"): 50,
    ("hackernews", "post"): 20,
    ("stackexchange", "question"): 20,
    ("wordpress", "review"): 10,
    ("wordpress", "post"): 10,
}


async def sample(session: AsyncSession, seed: float = 0.42) -> dict[tuple[str, str], int]:
    """Draw the test set from kept Items (replacing any unlabelled draw). Returns counts."""
    await session.execute(delete(EvalItem).where(EvalItem.has_signal.is_(None)))
    await session.execute(select(func.setseed(seed)))
    drawn = {}
    for (source_key, kind), n in STRATA.items():
        ids = (
            await session.scalars(
                select(Item.id)
                .join(Source, Source.id == Item.source_id)
                .where(
                    Source.key == source_key,
                    Item.kind == kind,
                    Item.prefilter == "keep",
                    Item.id.not_in(select(EvalItem.item_id)),
                )
                .order_by(func.random())
                .limit(n)
            )
        ).all()
        session.add_all(EvalItem(item_id=i) for i in ids)
        drawn[(source_key, kind)] = len(ids)
    await session.commit()
    return drawn


async def export_items(session: AsyncSession, path: Path) -> int:
    """Write the test Items as JSON Lines, for labelling outside the app."""
    rows = (
        await session.execute(
            select(EvalItem, Item, Source.key)
            .join(Item, Item.id == EvalItem.item_id)
            .join(Source, Source.id == Item.source_id)
            .order_by(Item.id)
        )
    ).all()
    lines = []
    for label, item, source_key in rows:
        title = await thread_title(session, item.source_id, item.thread_external_id, [item])
        record = {
            "item_id": item.id,
            "source": source_key,
            "external_id": item.external_id,
            "kind": item.kind,
            "thread": title,
            "author": item.author,
            "text": f"{item.title or ''}\n{item.body}".strip()[:3000],
            "has_signal": label.has_signal,
            "kinds": label.kinds,
            "note": label.note,
        }
        lines.append(json.dumps(record) + "\n")
    await asyncio.to_thread(path.write_text, "".join(lines))
    return len(rows)


async def import_labels(session: AsyncSession, path: Path, labelled_by: str) -> int:
    """Read labels ({"item_id", "has_signal", "kinds", "note"} per line) into the test set."""
    count = 0
    for line in (await asyncio.to_thread(path.read_text)).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        # Item ids are local to this database; (source, external_id) survives a rebuild.
        item_id = await session.scalar(
            select(Item.id)
            .join(Source, Source.id == Item.source_id)
            .where(Source.key == row["source"], Item.external_id == row["external_id"])
        )
        if item_id is None:
            continue
        label = await session.get(EvalItem, item_id)
        if label is None:
            label = EvalItem(item_id=item_id)
            session.add(label)
        if label is None:
            continue
        label.has_signal = bool(row["has_signal"])
        label.kinds = sorted(set(row.get("kinds") or [])) if label.has_signal else []
        label.note = row.get("note")
        label.labelled_by = labelled_by
        count += 1
    await session.commit()
    return count


async def run_model(session: AsyncSession, llm: LLM, model: str, run: str) -> int:
    """Run one model over every labelled test Item, one Item per call, storing its answers."""
    await session.execute(delete(EvalResult).where(EvalResult.run == run))
    rows = (
        await session.execute(
            select(Item, Source.key)
            .join(EvalItem, EvalItem.item_id == Item.id)
            .join(Source, Source.id == Item.source_id)
            .where(EvalItem.has_signal.is_not(None))
            .order_by(Item.id)
        )
    ).all()
    for item, source_key in rows:
        title = await thread_title(session, item.source_id, item.thread_external_id, [item])
        signals, result = await extract_chunk(llm, Chunk(source_key, title, [item]), model)
        session.add(
            EvalResult(
                run=run,
                model=model,
                item_id=item.id,
                predicted_signal=bool(signals),
                predicted_kinds=sorted({s.kind for s in signals}),
                output=result.data,
                seconds=result.seconds,
                output_tokens=result.output_tokens,
            )
        )
        await session.commit()
    return len(rows)


@dataclass(frozen=True)
class Score:
    run: str
    model: str
    items: int
    precision: float
    recall: float
    f1: float
    kind_agreement: float  # of true positives, share whose predicted kinds overlap the answer
    median_seconds: float
    output_tokens_per_second: float | None


def score(rows: Iterable[tuple[EvalResult, EvalItem]]) -> Score | None:
    rows = list(rows)
    if not rows:
        return None
    tp = sum(1 for r, g in rows if r.predicted_signal and g.has_signal)
    fp = sum(1 for r, g in rows if r.predicted_signal and not g.has_signal)
    fn = sum(1 for r, g in rows if not r.predicted_signal and g.has_signal)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    both = [(r, g) for r, g in rows if r.predicted_signal and g.has_signal]
    kinds = (
        sum(1 for r, g in both if set(r.predicted_kinds) & set(g.kinds)) / len(both)
        if both
        else 0.0
    )
    tokens = sum(r.output_tokens or 0 for r, _ in rows)
    seconds = sum(r.seconds for r, _ in rows)
    return Score(
        run=rows[0][0].run,
        model=rows[0][0].model,
        items=len(rows),
        precision=precision,
        recall=recall,
        f1=f1,
        kind_agreement=kinds,
        median_seconds=statistics.median(r.seconds for r, _ in rows),
        output_tokens_per_second=tokens / seconds if tokens and seconds else None,
    )


async def report(session: AsyncSession) -> list[Score]:
    runs = (await session.scalars(select(EvalResult.run).distinct().order_by(EvalResult.run))).all()
    scores = []
    for run in runs:
        rows = (
            await session.execute(
                select(EvalResult, EvalItem)
                .join(EvalItem, EvalItem.item_id == EvalResult.item_id)
                .where(EvalResult.run == run, EvalItem.has_signal.is_not(None))
            )
        ).all()
        if s := score(rows):
            scores.append(s)
    return scores


def run_name(model: str) -> str:
    return f"{model.split('/')[-1]}@{datetime.now(UTC):%Y-%m-%d}"
