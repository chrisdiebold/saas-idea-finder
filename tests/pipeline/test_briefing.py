"""Briefings: Census data, industry mapping, the prompt, checking replies, storing, Stale."""

import httpx2
import pytest
from sqlalchemy import select

from ideafinder.config import StaleRule, load_config
from ideafinder.db.models import Briefing, Competitor, IdeaCompetitor, MarketStat
from ideafinder.pipeline import briefing
from ideafinder.pipeline.census import parse as parse_census
from ideafinder.pipeline.industry import broadest, keywords, map_industry
from ideafinder.pipeline.review import review
from ideafinder.web.app import app
from tests.pipeline.test_rank import evidence_of

US_FILE = """"uscode","naics","lfo","est"
"98","------","-",8361342
"98","561///","-",400000
"98","561730","-",117969
"98","561730","C",5000
"""
NAMES = """"NAICS","DESCRIPTION"
"------","Total for all sectors"
"561///","Administrative and Support Services"
"561730","Landscaping Services"
"""


def reply(idea_id: int, *, drop: str | None = None, extra: str = "", share: float = 0.17) -> str:
    sections = "\n\n".join(
        f"## {h}\n\nText about {h.lower()} [assumption]." for h in briefing.HEADINGS if h != drop
    )
    return f"""Here is the briefing you asked for:

```markdown
---
briefing_for: idea-{idea_id}
format_version: 1
verdict: maybe
confidence: 3
persona: "landscapers"
pricing_hypothesis: {{ model: per_seat, low_usd: 19, high_usd: 49 }}
market_math: {{ us_businesses: 117969, price_usd: 49, customers_needed: 205, share_pct: {share} }}
competitors:
  - {{ name: "Jobber", url: "https://getjobber.com", pricing: "$69/mo", gaps: ["texting"] }}
integrations:
  - {{ name: "Twilio", api_access: open }}
weeks_to_mvp: 6
assumptions:
  - {{ claim: "Owners would pay", risk: 5, evidence: weak, hypothesis: "h", kill_criteria: "k",
      experiment: "e" }}
---
{sections}
{extra}
```
"""


# --- pure logic -----------------------------------------------------------------------------


def test_census_keeps_all_legal_forms_with_names():
    rows = parse_census(US_FILE, NAMES)
    assert [(r.naics_code, r.label, r.establishments) for r in rows] == [
        ("561", "Administrative and Support Services", 400000),
        ("561730", "Landscaping Services", 117969),
    ]


def test_industry_keywords_and_no_double_counting():
    assert "landsc" in keywords("owner of a small landscaping company")
    assert "owner" not in keywords("owner of a small landscaping company")
    assert "electronic shopping" in keywords("WooCommerce store owner")
    assert broadest(["561", "561730", "238"]) == ["561", "238"]


def test_valid_reply_passes_even_when_wrapped():
    parsed = briefing.parse(reply(7), 7, set())
    assert parsed.errors == [] and parsed.frontmatter.verdict == "maybe"
    assert parsed.body.startswith("## Problem")


def test_reply_for_another_idea_is_refused():
    parsed = briefing.parse(reply(8), 7, set())
    assert any("idea-8, not idea-7" in e for e in parsed.errors)


def test_missing_section_and_bad_fields_are_named():
    parsed = briefing.parse(reply(7, drop="Case against"), 7, set())
    assert parsed.errors == ["Missing sections: Case against."]
    bad = (
        reply(7)
        .replace("confidence: 3", "confidence: 9")
        .replace("api_access: open", "api_access: x")
    )
    errors = briefing.parse(bad, 7, set()).errors
    assert any(e.startswith("Frontmatter confidence") for e in errors)
    assert any(e.startswith("Frontmatter integrations.0.api_access") for e in errors)
    assert briefing.parse("just some text", 7, set()).errors[0].startswith("No frontmatter")


def test_unknown_evidence_and_odd_market_share_are_warnings():
    parsed = briefing.parse(
        reply(7, extra="Pain is real [evidence: signal-5] [evidence: signal-6].", share=5.0), 7, {5}
    )
    assert parsed.errors == []
    assert "signal-6" in parsed.warnings[0] and "signal-5" not in parsed.warnings[0]
    assert "Market share looks off" in parsed.warnings[1]


def test_stale_when_evidence_grows_by_half_or_reaches_a_new_community():
    rule = StaleRule(signal_growth_pct=50, on_new_community=True)
    b = Briefing(signal_count_at_gen=10, community_ids_at_gen=[1, 2, 3])
    assert not briefing.is_stale(b, evidence_of(signals=14), rule)
    assert briefing.is_stale(b, evidence_of(signals=15), rule)
    assert briefing.is_stale(b, evidence_of(signals=10, community_ids=[1, 2, 3, 4]), rule)


# --- against Postgres -----------------------------------------------------------------------


async def census(session):
    session.add_all(
        [
            MarketStat(naics_code="561730", year=2023, label="Landscaping Services",
                       establishments=117969),
            MarketStat(naics_code="54132", year=2023, label="Landscape Architectural Services",
                       establishments=20000),
        ]
    )  # fmt: skip
    await session.commit()


@pytest.mark.db
async def test_industry_mapping_only_accepts_offered_codes(session, llm, fake, evidence):
    await census(session)
    idea = await evidence.idea([], persona="owner of a landscaping company")
    fake.answer("industry_codes", codes=["561730", "999999"])
    assert await map_industry(session, llm, idea) == ["561730"]
    assert (
        "561730: Landscaping Services (117,969 US businesses)" in fake.prompts["industry_codes"][0]
    )
    other = await evidence.idea([], persona="Jira administrator")
    assert await map_industry(session, llm, other) == []  # no candidates: no model call


@pytest.mark.db
async def test_prompt_then_reply_round_trip(session, evidence, repo_config_dir):
    await census(session)
    ids = [await evidence.signal(None, f"p{i}", community=f"c{i % 2}") for i in range(3)]
    idea = await evidence.idea(ids, industry_codes=["561730"], title="Rain delays")
    b = await briefing.request(session, idea, None, evidence_of(signals=3, community_ids=[1, 2]))
    assert (b.version, b.status, b.signal_count_at_gen) == (1, "awaiting_response", 3)
    for sid in ids:
        assert f"signal-{sid} [pain]" in b.prompt_text
    assert "117,969 US businesses (County Business Patterns 2023)" in b.prompt_text
    assert f"briefing_for: idea-{idea.id}" in b.prompt_text
    assert "Case against" in b.prompt_text and "skeptical investor" in b.prompt_text

    # Asking again refreshes the same waiting prompt rather than piling up versions.
    again = await briefing.request(session, idea, None, None)
    assert again.id == b.id

    stored, parsed = await briefing.accept_reply(session, idea.id, reply(999))
    assert stored is None and parsed.errors  # wrong idea: nothing saved
    stored, parsed = await briefing.accept_reply(
        session, idea.id, reply(idea.id, extra=f"Real [evidence: signal-{ids[0]}].")
    )
    assert stored.status == "complete" and parsed.warnings == []
    jobber = await session.scalar(select(Competitor).where(Competitor.name == "Jobber"))
    assert jobber.pricing == "$69/mo" and jobber.created_from == "briefing"
    assert await session.scalar(select(IdeaCompetitor.competitor_id)) == jobber.id

    with pytest.raises(briefing.BriefingError):
        await briefing.accept_reply(session, idea.id, reply(idea.id))  # nothing waiting now
    nxt = await briefing.request(session, idea, None, None)
    assert nxt.version == 2

    rule = load_config(repo_config_dir).scoring.stale
    assert await briefing.mark_stale(session, rule, {idea.id: evidence_of(signals=5)}) == [idea.id]


@pytest.mark.db
async def test_moving_to_working_list_prepares_a_briefing(session, evidence, repo_config_dir):
    config = load_config(repo_config_dir).scoring
    idea = await evidence.idea([await evidence.signal(None, "a")], state="inbox")
    await review(session, config, idea.id, "working")
    b = await briefing.latest(session, idea.id)
    assert b.status == "awaiting_response" and b.provider == "manual"


@pytest.mark.db
async def test_briefing_page_keeps_a_bad_paste_and_renders_a_good_one(session, evidence):
    ids = [await evidence.signal(None, "a")]
    idea = await evidence.idea(ids)
    await briefing.request(session, idea, None, None)
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://t"
    ) as client:
        page = await client.get(f"/ideas/{idea.id}/briefing")
        assert "Copy prompt" in page.text and f"briefing_for: idea-{idea.id}" in page.text

        bad = reply(idea.id, drop="Reach")
        r = await client.post(f"/ideas/{idea.id}/briefing/reply", data={"response": bad})
        assert r.status_code == 422 and "Missing sections: Reach." in r.text
        assert "Text about build" in r.text  # the paste is still there

        good = reply(
            idea.id, extra=f"Pain [evidence: signal-{ids[0]}] [researched: https://x.io/a]."
        )
        r = await client.post(f"/ideas/{idea.id}/briefing/reply", data={"response": good})
        assert r.status_code == 303
        page = (await client.get(f"/ideas/{idea.id}/briefing")).text
        assert f'href="/ideas/{idea.id}#signal-{ids[0]}"' in page
        assert 'class="claim researched" href="https://x.io/a"' in page
        assert '<section class="against"><h2>Case against</h2>' in page
        assert "117,969 US businesses" in page
        idea_page = (await client.get(f"/ideas/{idea.id}")).text
        assert "Read the Briefing" in idea_page and f'id="signal-{ids[0]}"' in idea_page
