"""Signal extraction: turn kept Items into Signals with the local model.

Items are sent a Thread at a time (e.g. a Jira suggestion and its comments) in chunks, and the
model lists only the Items that contain Signals. Most of a local model's time goes to writing
output, so not making it say "no Signal" for every Item cuts the work several-fold.
"""

import logging
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import Item, Signal, Source
from ideafinder.llm import LLM, JsonResult, ServerUnreachable
from ideafinder.pipeline.competitors import link_products

log = logging.getLogger(__name__)

PROMPT_VERSION = "extract-v1"
SIGNAL_KINDS = ("pain", "request", "complaint", "payment")  # Market Signals come from launches
MAX_ITEMS_PER_CHUNK = 12
MAX_CHARS_PER_CHUNK = 9000
MAX_CHARS_PER_ITEM = 2000

SOURCE_CONTEXT = {
    "hackernews": "Hacker News, a forum for software developers, founders and tech workers.",
    "jira_atlassian": (
        "Atlassian's public suggestion tracker. The first Item is a feature request for an "
        "Atlassian product (Jira, Confluence...); comments are from that product's customers. "
        "A missing feature in an Atlassian product is a complaint about that product."
    ),
    "wordpress": "WordPress.org plugin support forums and reviews, written by website owners.",
    "stackexchange": "Stack Exchange, a Q&A network; questions ask for help or recommendations.",
    "github": (
        "GitHub issues on an open-source business tool; written by its users and developers. "
        "A missing feature is a complaint about that tool."
    ),
    "shopify_apps": "Low-star reviews of a Shopify app, written by the stores that installed it.",
    "feature_boards": (
        "A software company's public feature-request board; each post asks the vendor for a "
        "feature, which is a complaint about that product."
    ),
    "zapier": "Zapier Community, where people ask how to connect apps and automate work.",
    "mastodon": "Mastodon posts under a hashtag; many are promotional rather than problems.",
    "bluesky": "Bluesky posts matching phrases like 'is there a tool'; short social posts.",
}

SYSTEM_PROMPT = """\
You find evidence of software business opportunities in what people write online.

For each numbered Item, decide whether its author describes a problem they (or their organisation)
actually have. If so, record one Signal per distinct problem. Most Items contain no Signal;
leave those out entirely.

Signal kinds:
- pain: the author has a problem or does tedious work they'd like to avoid.
- request: the author is looking for a tool, feature or product that would solve a problem.
- complaint: the author is unhappy with an existing product (name it in "products").
- payment: the author would pay, or already pays, to have the problem solved.

Not Signals: "+1"-style agreement with nothing added; announcements of products the author
built; answers or advice to someone else; jokes; general opinions about the industry; job posts.

For each Signal:
- item: the Item's number.
- problem_statement: one neutral sentence describing the problem, not a solution.
  Good: "Small landscaping companies spend hours turning site photos into written quotes."
  Bad: "Needs an AI quoting app."
- persona: who has the problem, by role and industry, e.g. "office manager at a dental practice"
  or "Jira administrator at a mid-size software company". Use what the Item says; if it says
  nothing, infer the most likely role from context.
- intensity: 1 (mild annoyance) to 5 (costly, urgent or blocking).
- quote: an exact excerpt from the Item, at most 200 characters, that shows the problem.
- workaround: what they do today instead, if they say; otherwise null.
- products: names of existing products mentioned in connection with the problem (may be empty).
"""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "integer"},
                    "kind": {"type": "string", "enum": list(SIGNAL_KINDS)},
                    "problem_statement": {"type": "string"},
                    "persona": {"type": "string"},
                    "intensity": {"type": "integer", "minimum": 1, "maximum": 5},
                    "quote": {"type": "string"},
                    "workaround": {"type": ["string", "null"]},
                    "products": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "item",
                    "kind",
                    "problem_statement",
                    "persona",
                    "intensity",
                    "quote",
                    "workaround",
                    "products",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["signals"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Chunk:
    """Items from one Thread, sent to the model together."""

    source_key: str
    thread_title: str | None
    items: list[Item]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + " […]"


def render_chunk(chunk: Chunk) -> str:
    lines = [f"Source: {SOURCE_CONTEXT.get(chunk.source_key, chunk.source_key)}"]
    if chunk.thread_title:
        lines.append(f"Thread: {chunk.thread_title}")
    for number, item in enumerate(chunk.items, start=1):
        header = f"\n[Item {number}] ({item.kind}"
        if item.author:
            header += f", by {item.author}"
        header += ")"
        lines.append(header)
        if item.title and item.kind != "comment":
            lines.append(f"Title: {item.title}")
        lines.append(_clip(item.body, MAX_CHARS_PER_ITEM))
    return "\n".join(lines)


def chunk_thread(source_key: str, thread_title: str | None, items: Sequence[Item]) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[Item] = []
    size = 0
    for item in items:
        length = min(len(item.body), MAX_CHARS_PER_ITEM) + len(item.title or "")
        if current and (len(current) >= MAX_ITEMS_PER_CHUNK or size + length > MAX_CHARS_PER_CHUNK):
            chunks.append(Chunk(source_key, thread_title, current))
            current, size = [], 0
        current.append(item)
        size += length
    if current:
        chunks.append(Chunk(source_key, thread_title, current))
    return chunks


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_appears(quote: str, item: Item) -> bool:
    """Whether the model's quote really comes from the Item (ignoring case and whitespace)."""
    needle = _normalise(quote.strip(" \"'“”…."))
    return bool(needle) and needle in _normalise(f"{item.title or ''} {item.body}")


@dataclass(frozen=True)
class ParsedSignal:
    item: Item
    kind: str
    problem_statement: str
    persona: str
    intensity: int
    quote: str
    quote_verified: bool
    workaround: str | None
    products: list[str]


def parse_output(chunk: Chunk, data: dict[str, Any]) -> list[ParsedSignal]:
    """Validate the model's answer against the chunk; drop Signals that don't hold up."""
    parsed = []
    for raw in data.get("signals", []):
        number = raw.get("item")
        if not isinstance(number, int) or not 1 <= number <= len(chunk.items):
            log.warning("model referred to Item %r, which isn't in the chunk", number)
            continue
        if raw.get("kind") not in SIGNAL_KINDS or not raw.get("problem_statement", "").strip():
            continue
        item = chunk.items[number - 1]
        quote = (raw.get("quote") or "").strip()
        parsed.append(
            ParsedSignal(
                item=item,
                kind=raw["kind"],
                problem_statement=raw["problem_statement"].strip(),
                persona=(raw.get("persona") or "").strip() or "unknown",
                intensity=min(5, max(1, int(raw.get("intensity") or 1))),
                quote=quote,
                quote_verified=quote_appears(quote, item),
                workaround=(raw.get("workaround") or None),
                products=[p.strip() for p in raw.get("products") or [] if p and p.strip()],
            )
        )
    return parsed


async def extract_chunk(
    llm: LLM, chunk: Chunk, model: str | None = None
) -> tuple[list[ParsedSignal], JsonResult]:
    result = await llm.json(
        "extract", SYSTEM_PROMPT, render_chunk(chunk), OUTPUT_SCHEMA, model=model
    )
    return parse_output(chunk, result.data), result


async def pending_chunks(session: AsyncSession, max_threads: int) -> list[Chunk]:
    """Chunks for the oldest Threads that still have kept, unextracted Items."""
    threads = (
        await session.execute(
            select(Item.source_id, Item.thread_external_id, func.min(Item.id).label("first"))
            .where(Item.prefilter == "keep", Item.extracted_at.is_(None))
            .group_by(Item.source_id, Item.thread_external_id)
            .order_by("first")
            .limit(max_threads)
        )
    ).all()
    if not threads:
        return []
    source_keys = dict((await session.execute(select(Source.id, Source.key))).all())

    chunks: list[Chunk] = []
    for source_id, thread, _ in threads:
        items = (
            await session.scalars(
                select(Item)
                .where(
                    Item.source_id == source_id,
                    Item.prefilter == "keep",
                    Item.extracted_at.is_(None),
                    Item.thread_external_id.is_(None)
                    if thread is None
                    else Item.thread_external_id == thread,
                )
                .order_by(Item.created_at, Item.id)
            )
        ).all()
        title = await thread_title(session, source_id, thread, items)
        chunks.extend(chunk_thread(source_keys[source_id], title, items))
    return chunks


async def thread_title(
    session: AsyncSession, source_id: int, thread: str | None, items
) -> str | None:
    for item in items:
        if item.title and item.kind != "comment":
            return item.title
    if thread is None:
        return None
    root_title = await session.scalar(
        select(Item.title).where(Item.source_id == source_id, Item.external_id == thread)
    )
    if root_title:
        return root_title
    # Hacker News comments carry their story's title.
    return next((i.extra.get("story_title") for i in items if i.extra.get("story_title")), None)


async def store_signals(
    session: AsyncSession, chunk: Chunk, signals: list[ParsedSignal], model: str
) -> int:
    for s in signals:
        signal = Signal(
            item_id=s.item.id,
            kind=s.kind,
            problem_statement=s.problem_statement,
            persona=s.persona,
            intensity=s.intensity,
            quote=s.quote,
            quote_verified=s.quote_verified,
            workaround=s.workaround,
            model=model,
            prompt_version=PROMPT_VERSION,
        )
        session.add(signal)
        await session.flush()
        await link_products(session, signal.id, s.products)
    await session.execute(
        update(Item)
        .where(Item.id.in_([i.id for i in chunk.items]))
        .values(extracted_at=datetime.now(UTC))
    )
    return len(signals)


@dataclass
class ExtractReport:
    chunks: int = 0
    items: int = 0
    signals: int = 0
    failed_chunks: int = 0
    seconds: float = 0.0
    output_tokens: int = 0
    by_kind: dict[str, int] | None = None


async def extract_pending(
    session: AsyncSession, llm: LLM, max_threads: int, model: str | None = None
) -> ExtractReport:
    report = ExtractReport(by_kind=defaultdict(int))
    for chunk in await pending_chunks(session, max_threads):
        try:
            signals, result = await extract_chunk(llm, chunk, model)
        except ServerUnreachable:
            raise  # nothing else will work either; the job is retried later
        except Exception as exc:
            # Leave the Items unextracted so they're retried; a bad chunk shouldn't stop the batch.
            report.failed_chunks += 1
            log.warning("extraction failed for a %s chunk: %s", chunk.source_key, exc)
            await session.rollback()
            continue
        report.signals += await store_signals(session, chunk, signals, result.model)
        await session.commit()
        report.chunks += 1
        report.items += len(chunk.items)
        report.seconds += result.seconds
        report.output_tokens += result.output_tokens or 0
        for s in signals:
            report.by_kind[s.kind] += 1
    report.by_kind = dict(report.by_kind)
    return report
