"""Leads: Authors of an Idea's strongest Signals, worth contacting about the problem.

Contact is always manual, through the Source's own reply feature. Nothing is stored beyond what
the person posted publicly (their name on the Source and a link to the post). Notes from Leads
you've talked to become evidence for an interviews Experiment.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import Experiment, Idea, IdeaSignal, Item, Lead, Signal
from ideafinder.llm import LLM, ModelUnavailable, ServerUnreachable
from ideafinder.validation.working import WorkingListError

MAX_LEADS = 10
STATUSES = {
    "to_contact": "To contact",
    "contacted": "Contacted",
    "replied": "Replied",
    "talked": "Talked",
}


async def find_leads(session: AsyncSession, idea_id: int, limit: int = MAX_LEADS) -> int:
    """Add Leads from the highest-intensity Signals, one per Author. Returns how many are new."""
    existing = set(
        (await session.scalars(select(Lead.author).where(Lead.idea_id == idea_id))).all()
    )
    rows = (
        await session.execute(
            select(Signal.id, Item.author, Item.source_id, Item.url)
            .join(IdeaSignal, IdeaSignal.signal_id == Signal.id)
            .join(Item, Item.id == Signal.item_id)
            .where(IdeaSignal.idea_id == idea_id, Item.author.is_not(None))
            .order_by(Signal.intensity.desc(), IdeaSignal.similarity.desc().nulls_last())
        )
    ).all()
    added = 0
    for signal_id, author, source_id, url in rows:
        if len(existing) >= limit:
            break
        if author in existing:
            continue
        existing.add(author)
        session.add(
            Lead(idea_id=idea_id, signal_id=signal_id, author=author, source_id=source_id, url=url)
        )
        added += 1
    await session.commit()
    return added


OPENER_SYSTEM = """\
You suggest the first line of a reply to someone who described a problem online. The founder
will post it manually, replying to that person's own post. Write one or two sentences that:
- refer to the specific thing they described, in their terms
- ask about their past experience (what they did last time, what it cost them), not about an
  idea or product
- are friendly and plain, with no pitch, no links, no flattery and no request for a call yet."""

OPENER_SCHEMA = {
    "type": "object",
    "properties": {"opener": {"type": "string"}},
    "required": ["opener"],
    "additionalProperties": False,
}


async def write_openers(session: AsyncSession, llm: LLM, idea_id: int) -> int:
    idea = await session.get(Idea, idea_id)
    rows = (
        await session.execute(
            select(Lead, Signal.quote, Signal.problem_statement)
            .join(Signal, Signal.id == Lead.signal_id)
            .where(Lead.idea_id == idea_id, Lead.opener.is_(None))
        )
    ).all()
    written = 0
    for lead, quote, problem in rows:
        prompt = (
            f"The problem (for context only): {idea.problem_statement}\n"
            f'What {lead.author} wrote: "{quote}"\nTheir point: {problem}'
        )
        try:
            result = await llm.json(
                "lead_opener", OPENER_SYSTEM, prompt, OPENER_SCHEMA, max_tokens=200
            )
        except ServerUnreachable:
            raise
        except ModelUnavailable:
            continue
        lead.opener = result.data["opener"].strip()
        await session.commit()
        written += 1
    return written


async def update_lead(
    session: AsyncSession, lead_id: int, status: str, notes: str | None = None
) -> tuple[Lead, Experiment | None]:
    """Change a Lead's status and notes. When you've talked, the Lead is attached to the Idea's
    open interviews Experiment (if there is one) so its notes count as evidence."""
    lead = await session.get(Lead, lead_id)
    if lead is None:
        raise WorkingListError(f"There's no Lead {lead_id}")
    if status not in STATUSES:
        raise WorkingListError(f"A Lead's status is one of: {', '.join(STATUSES.values())}")
    lead.status = status
    if notes is not None:
        lead.notes_md = notes.strip() or None
    experiment = None
    if status == "talked" and lead.experiment_id is None:
        experiment = await session.scalar(
            select(Experiment)
            .where(
                Experiment.idea_id == lead.idea_id,
                Experiment.type == "interviews",
                Experiment.outcome.is_(None),
            )
            .order_by(Experiment.started_at.desc())
            .limit(1)
        )
        if experiment is not None:
            lead.experiment_id = experiment.id
    await session.commit()
    return lead, experiment
