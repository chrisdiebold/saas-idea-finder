"""Prefilter: decide, without any model, what happens to each newly collected Item.

Outcomes (stored on items.prefilter):
- keep:   send to Signal extraction
- vote:   a text-free agreement ("+1", "upvote", "any update?"); counts as a Vote on the
          Thread's root Item rather than as an Author (GLOSSARY.md: Vote)
- market: a launch (Show HN); used for Competitors and Market evidence, not extraction
- drop:   noise, with a reason
"""

import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import Item

MIN_PROSE_CHARS = 30
MAX_COMMENTS_PER_AUTHOR_PER_THREAD = 3
BATCH = 5000

_URLS = re.compile(r"https?://\S+|\[[^\]|]*\|[^\]]*\]")
_AGREEMENT = re.compile(
    r"""^\W*(
        \+\s*\d+ | \(y\) | up-?vot(e|ed|ing) | bump(ed|ing)? | me\ too | same\ here | ditto
      | (any|is\ there\ any)\ (update|news|progress|eta)s?(\ on\ this)?
      | please\ (add|implement|fix|prioritize|prioritise)\ (this|it)(\ feature)?(\ asap)?
      | (we\ )?(need|want)\ this(\ feature)?(\ too|\ as\ well)?
      | \+1\ (for\ this|please|from\ us|here)(\ feature)?
      | please
    )\W*$""",
    re.IGNORECASE | re.VERBOSE,
)
# Launch headlines: "Files.md – open-source alternative to Obsidian". Questions stay ("Ask HN: …").
_LAUNCH_TITLE = re.compile(r"\balternatives? to\b", re.IGNORECASE)
_DISCUSSION_PREFIX = re.compile(r"^(ask|tell) hn\b", re.IGNORECASE)
_HN_JOB_THREADS = re.compile(
    r"who is hiring|who wants to be hired|freelancer\? seeking freelancer", re.IGNORECASE
)


@dataclass(frozen=True)
class Decision:
    outcome: str  # keep / drop / vote / market
    reason: str


def _prose(text: str) -> str:
    return re.sub(r"\s+", " ", _URLS.sub(" ", text)).strip()


def decide(item: Item) -> Decision:
    """The outcome for one Item, ignoring per-Thread limits (applied by `prefilter_pending`)."""
    if item.extra.get("show_hn"):
        return Decision("market", "show_hn")
    if item.extra.get("launch"):  # Product Hunt
        return Decision("market", "launch")
    if (
        item.kind == "post"
        and item.title
        and _LAUNCH_TITLE.search(item.title)
        and not _DISCUSSION_PREFIX.match(item.title)
        and "link" in item.extra  # Hacker News stories
    ):
        return Decision("market", "launch_title")
    if item.kind == "comment" and _HN_JOB_THREADS.search(item.extra.get("story_title") or ""):
        return Decision("drop", "job_post")

    prose = _prose(f"{item.title or ''} {item.body}")
    if item.kind == "comment" and len(prose) < 80 and _AGREEMENT.match(prose):
        return Decision("vote", "agreement")
    if len(prose) < MIN_PROSE_CHARS:
        return Decision("drop", "too_short")
    letters = sum(ch.isalpha() for ch in prose)
    if letters < len(prose) * 0.5:
        return Decision("drop", "not_prose")
    return Decision("keep", "kept")


async def reset_unextracted(session: AsyncSession) -> None:
    """Forget prefilter outcomes for Items not yet extracted, so changed rules apply to them."""
    await session.execute(
        update(Item)
        .where(Item.extracted_at.is_(None))
        .values(prefilter=None, prefilter_reason=None)
    )
    await session.commit()


async def prefilter_pending(session: AsyncSession) -> dict[str, int]:
    """Decide every Item without a prefilter outcome. Returns counts per outcome:reason."""
    counts: dict[str, int] = defaultdict(int)
    while True:
        items = (
            await session.scalars(
                select(Item).where(Item.prefilter.is_(None)).order_by(Item.id).limit(BATCH)
            )
        ).all()
        if not items:
            return dict(counts)

        # Comments already kept per (thread, author), so repeat posters don't dominate a Thread.
        threads = {(i.source_id, i.thread_external_id) for i in items if i.kind == "comment"}
        kept_per_author = await _kept_comment_counts(session, threads)

        ids_by_decision: dict[Decision, list[int]] = defaultdict(list)
        for item in items:
            decision = decide(item)
            if decision.outcome == "keep" and item.kind == "comment" and item.author:
                key = (item.source_id, item.thread_external_id, item.author)
                if kept_per_author[key] >= MAX_COMMENTS_PER_AUTHOR_PER_THREAD:
                    decision = Decision("drop", "repeat_author")
                else:
                    kept_per_author[key] += 1
            ids_by_decision[decision].append(item.id)

        for decision, ids in ids_by_decision.items():
            await session.execute(
                update(Item)
                .where(Item.id.in_(ids))
                .values(prefilter=decision.outcome, prefilter_reason=decision.reason)
            )
            counts[f"{decision.outcome}:{decision.reason}"] += len(ids)
        await session.commit()


async def _kept_comment_counts(session: AsyncSession, threads) -> dict[tuple, int]:
    counts: dict[tuple, int] = defaultdict(int)
    if not threads:
        return counts
    thread_ids = {t for _, t in threads}
    rows = await session.execute(
        select(Item.source_id, Item.thread_external_id, Item.author).where(
            Item.kind == "comment",
            Item.prefilter == "keep",
            Item.thread_external_id.in_(thread_ids),
        )
    )
    for source_id, thread, author in rows:
        counts[(source_id, thread, author)] += 1
    return counts
