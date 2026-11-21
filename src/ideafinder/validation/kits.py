"""Validation kits: material for testing an Idea, written when it reaches Validating.

- an interview script about past behaviour and spending, not opinions on the idea
- a tailored message for each Lead
- landing page copy built on the Persona's own words
- a pre-sale or waitlist offer
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import FitProfile, Idea, Lead, Signal, ValidationKit
from ideafinder.llm import LLM
from ideafinder.pipeline.briefing import strongest_quotes
from ideafinder.validation.experiments import current_hypothesis

SYSTEM = """\
You prepare material a solo founder uses to test whether people will pay to solve a problem.
Use the people's own words wherever you can. Be concrete and plain; no hype.

- interview_script: 8–12 questions about what the person did the last few times the problem
  happened, what it cost them in time or money, what they tried and pay for now. Never ask
  whether they'd use or buy a product, and never describe a solution.
- lead_messages: for each Lead, a short message to send as a reply to their own post: refer to
  what they wrote, say you're researching this problem (not selling), and ask for 15 minutes
  to hear how they deal with it. One per Lead, using its lead_id.
- landing_page: a headline and subheadline in the Persona's words, 3–5 bullets about outcomes,
  and a call to action that matches the offer.
- offer: a pre-sale (pay now at a discount, refundable) if people already pay for workarounds,
  otherwise a waitlist; state the price or what joining gets them."""

SCHEMA = {
    "type": "object",
    "properties": {
        "interview_script": {"type": "array", "items": {"type": "string"}},
        "lead_messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"lead_id": {"type": "integer"}, "message": {"type": "string"}},
                "required": ["lead_id", "message"],
                "additionalProperties": False,
            },
        },
        "landing_page": {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "subheadline": {"type": "string"},
                "bullets": {"type": "array", "items": {"type": "string"}},
                "call_to_action": {"type": "string"},
            },
            "required": ["headline", "subheadline", "bullets", "call_to_action"],
            "additionalProperties": False,
        },
        "offer": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["pre_sale", "waitlist"]},
                "text": {"type": "string"},
            },
            "required": ["kind", "text"],
            "additionalProperties": False,
        },
    },
    "required": ["interview_script", "lead_messages", "landing_page", "offer"],
    "additionalProperties": False,
}


async def generate_kit(session: AsyncSession, llm: LLM, idea_id: int) -> ValidationKit:
    idea = await session.get(Idea, idea_id)
    hypothesis = await current_hypothesis(session, idea_id)
    profile = await session.get(FitProfile, 1)
    quotes = await strongest_quotes(session, idea_id)
    leads = (
        await session.execute(
            select(Lead, Signal.quote)
            .join(Signal, Signal.id == Lead.signal_id)
            .where(Lead.idea_id == idea_id)
        )
    ).all()
    summary = idea.summary or {}
    lines = [
        f"Problem: {summary.get('problem') or idea.problem_statement}",
        f"Persona: {summary.get('persona') or idea.persona}",
        f"Hypothesis: {hypothesis.statement if hypothesis else 'not written yet'}",
        f"What they pay now: {summary.get('payment_note') or 'unknown'}",
    ]
    if profile and (profile.price_min_usd or profile.price_max_usd):
        lines.append(
            f"Founder's price range: ${profile.price_min_usd or 0}–"
            f"{profile.price_max_usd or '?'} per month"
        )
    lines += ["", "Their own words:"] + [f'- "{q.text[:300]}"' for q in quotes[:10]]
    lines += ["", "Leads:"] + (
        [f'- lead_id {lead.id}, {lead.author}: "{quote[:240]}"' for lead, quote in leads]
        or ["- none yet (return an empty lead_messages list)"]
    )
    result = await llm.json("validation_kit", SYSTEM, "\n".join(lines), SCHEMA, max_tokens=3000)
    known = {lead.id for lead, _ in leads}
    content = dict(result.data)
    content["lead_messages"] = {
        str(m["lead_id"]): m["message"].strip()
        for m in result.data["lead_messages"]
        if m["lead_id"] in known
    }
    kit = ValidationKit(idea_id=idea_id, content=content, model=result.model)
    session.add(kit)
    await session.commit()
    return kit


async def latest_kit(session: AsyncSession, idea_id: int) -> ValidationKit | None:
    return await session.scalar(
        select(ValidationKit)
        .where(ValidationKit.idea_id == idea_id)
        .order_by(ValidationKit.generated_at.desc(), ValidationKit.id.desc())
        .limit(1)
    )
