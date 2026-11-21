# SaaS Idea Finder — Project Plan

A personal tool that collects public evidence of software problems from the web, groups it into **Ideas**,
analyzes and scores them, and helps you decide which to pursue and then **validate** them. New Ideas arrive in
the **Inbox** with a Proposed decision. You confirm or override it, moving each Idea to the **Working List**
(worth pursuing) or the **Archive** (set aside, with a reason). Ideas on the Working List are tested with
Experiments until they're built or dropped.

- **Audience:** one user (personal, non-commercial)
- **Focus:** broad collection, filtered by your Fit profile
- **Interface:** a web app on localhost. No hosting, login or notifications

**Related documents:**
- `GLOSSARY.md`: the project's vocabulary. This plan uses its terms throughout
- `docs/adr/0001-idea-is-a-problem-for-a-persona.md`: why an Idea is a problem, not a product
- `docs/adr/0003-local-first-on-the-mac.md`: why everything runs on the Mac, and when to move to the Pi
  (replaces ADR 0002)
- `docs/research/source-access.md`: how each candidate Source can be accessed (checked November 2026)

---

## 1. Sources

### Access rule
Collect only through official APIs, RSS feeds, public JSON or plain HTML that:
- needs no login,
- has no bot protection to bypass,
- is not forbidden by the site's terms of service,
- is not disallowed by its robots.txt.

Sites that charge AI crawlers or are suing scrapers are skipped. All collection is personal and non-commercial,
with polite rate limits and no redistribution.

### Phase 1
| Source | Mostly produces | Access |
|---|---|---|
| Hacker News | Pain, Request; Market (Show HN launches) | Algolia HN Search API, no auth, full history |
| Atlassian public Jira | Complaint, Request, with Votes | REST API + JQL, no auth, history back to 2004 |
| WordPress.org | Complaint (plugin reviews), Pain (support forums) | RSS per plugin: latest 30 reviews and 30 support threads, no history |
| Stack Exchange | Request, Pain | Official API with a free key (10k requests/day) |

**Known gap:** without Reddit (dropped, see below), Phase 1 leans toward developers, IT and website owners.
Non-tech small businesses stay thin until Phase 2 Sources arrive.

### Phase 2
| Source | Mostly produces | Access |
|---|---|---|
| GitHub issues | Request, with Votes (reactions) | Official API |
| Shopify App Store reviews | Complaint | Plain HTML, full history |
| Public feature-request boards (Canny, UserVoice, Featurebase; curated list including Xero) | Request, Complaint, with Votes | HTML / embedded JSON |
| Zapier Community | Request (missing integrations) | RSS (latest 100 only) |
| Product Hunt | Market | GraphQL API, free developer token |
| Mastodon | Pain, Request | Hashtag RSS / API |
| Bluesky | Pain, Request | Search API; needs a free account to page through results |

### Phase 3
| Source | Mostly produces | Access |
|---|---|---|
| Google Trends | Momentum data for scoring (not Signals) | pytrends, weekly per published Idea (ADR 0004) |

BiggerPockets was planned here but isn't collected: its terms forbid storing its content (ADR 0004).

### Reference data (not a Source of Signals)
| Dataset | Used for | Access |
|---|---|---|
| US Census Bureau County Business Patterns | Number of US businesses per industry, for market math | Census API, free key |

### Dropped
| Site | Reason |
|---|---|
| G2, Capterra, Trustpilot | Bot walls and explicit scraping bans |
| Upwork, Acquire.com, Flippa | Bot walls, login walls or scraping bans |
| Google Play, Chrome Web Store | Reviews need JavaScript and robots.txt blocks them |
| Apple App Store review feed | robots.txt blocks the feed; capped at 500 reviews per country |
| QuickBooks, Shopify, Spiceworks, Make, n8n, HubSpot community forums | Terms forbid automated access, or bot protection |
| Salesforce IdeaExchange, Microsoft Feedback | Need JavaScript; terms forbid it |
| Indie Hackers | Bot protection |
| ContractorTalk | Charges AI crawlers; owner is suing scrapers |
| Lobsters | robots.txt disallows all bots except search engines |
| YouTube | Terms limit storing data to 30 days; 100 searches a day |
| Reddit | New Data API access requests closed on October 31, 2026, and remaining public Data API access is reported to end in March 2027. developers.reddit.com is Devvit, for apps that run inside Reddit, not external data access. robots.txt and the content policy rule out unauthenticated access |
| X/Twitter, LinkedIn, Quora | API cost or anti-scraping |

Market Signals come only from Product Hunt and Show HN; there is no revenue data from businesses for sale.

### Collection rules
- **English only.** Items in other languages are skipped before any model sees them.
- **Backfill:** the first run collects 12 months of history, or as much as each Source allows. Collection is daily
  after that, and every collector resumes from "everything since the last run", so nothing is lost while the Mac
  sleeps. Exceptions:
  - **WordPress.org** feeds hold only their latest 30 entries, so there is no history. Daily collection keeps up
    even with the busiest plugins.
  - **Jira** follows each suggestion's last-updated time, so an old suggestion that gets a new comment or vote
    is collected again with all its comments.
- **Watch list:** each Source has Communities to collect from (Stack Exchange sites, Jira projects, WordPress
  plugins). The system **suggests** Communities it keeps running into, with evidence;
  you approve or ignore each one.
- **Queries:** where a Source supports search, collectors also run pain-phrase queries:
  - Requests: "is there a tool", "is there an app", "I wish there was", "looking for software"
  - Payment: "I'd pay for", "happy to pay", "take my money"
  - Workarounds: "we use a spreadsheet", "manually every", "copy and paste between"
  - Complaints: "alternative to", "switching from", "so frustrated with", "overpriced"
  - Questions: "how do you guys handle", "what do you use for"
- **Source-specific strategies:**
  - **Jira:** unresolved suggestions sorted by Votes
  - **WordPress.org:** 1–3★ reviews and support threads of popular plugins
  - **Stack Exchange:** all questions on Software Recommendations (every one is a request); pain-phrase
    searches on the other Watch-listed sites
  - **Hacker News:** pain-phrase searches over stories and comments, plus every Show HN launch

---

## 2. Architecture

```
 collectors ──▶ Items ──▶ prefilter ──▶ extract (local model) ──▶ Signals ──▶ Competitors
                                                                    │
                                              embed + cluster ◀─────┘
                                                     │
                                Ideas ──▶ Fit check ──▶ score ──▶ publish ──▶ Proposed decision
                                                                                  │
                       Inbox ──(you confirm/override)──▶ Working List ──▶ Archive
                                                              │
                         Briefing · Hypothesis · Experiments · Leads · Validation kit
```

### Runtime (see ADR 0003)
Everything runs on the Mac (M3 Pro, 36 GB):
- **Postgres 18 + pgvector 0.8**, installed with Postgres.app.
- **`ideafinder up`** starts the web app (localhost) and both workers in one command for daily use.
- **Scheduled jobs** (launchd) start the daily collect → extract → cluster → score → publish chain without the
  web app being open. macOS runs a missed schedule when the Mac wakes.
- **Two job queues:**
  - `collect`: collection, prefilter, clustering, scoring, publishing, resurfacing
  - `model`: everything that uses the local model or embeddings

  They are kept separate so that moving collection to the Pi later only changes config.
- **Backups:** nightly `pg_dump`, compressed, to a folder covered by Time Machine or iCloud Drive; keep 14 days.

**Moving to the Raspberry Pi 4 Model B** (when the first RSS-only Source is added, phone access is wanted, or
sleep delays collection): Postgres, the `collect` queue and the web app move to the Pi with a USB 3 SSD (needs
≥ 4 GB RAM). The `model` queue stays on the Mac and connects to the Pi's database. Only config changes.

### Stack
- **Python 3.13 + asyncio**, managed with `uv`; `httpx.AsyncClient` + `tenacity` for HTTP and retries; `typer` for the CLI
- **Concurrency:** all collectors run at the same time under `asyncio.TaskGroup`, each with its own rate limiter
  (`aiolimiter`) sized to the Source's quota, plus a global cap on open connections
- **JSON validation:** `pydantic` models for each Source's responses (only the fields we use)
- **Database:** Postgres + pgvector (nearest-Idea lookups, full-text search); SQLAlchemy + Alembic
- **Job queue and scheduling:** `procrastinate` (Postgres-backed, async, named queues, periodic tasks) + launchd
- **LLM routing:** one `llm.complete(task, schema, ...)` interface; `config/models.yaml` assigns each task to a
  provider and model:
  | Task | Default |
  |---|---|
  | extract Signals from an Item | local model |
  | name an Idea / write its problem statement | local model |
  | Fit check against Deal-breakers and Preferences | local model |
  | Proposed decision (with reason and confidence) | local model |
  | Summary | local model |
  | Persona → industry code mapping for market math | local model |
  | Lead opening line | local model |
  | Validation kit | local model (can be switched to `manual` for higher-quality copy) |
  | Briefing (needs web search + strong reasoning) | `manual` provider (copy-paste into claude.ai); later Claude Opus 5.5 + web search |
- **Local model:** Ollama or LM Studio (MLX backend). A 20–30B mixture-of-experts model is the sweet spot on
  36 GB; a dense ~14B model is the fallback. The M2 eval set decides.
- **Embeddings:** `sentence-transformers` (`bge-small-en`, 384-dim) on the Apple GPU
- **Web app:** FastAPI + Jinja + HTMX + Tailwind, Alpine.js for keyboard shortcuts. No JS build step. Bound to
  `127.0.0.1` only

---

## 3. Pipeline

1. **Collect.** Each collector implements `fetch(cursor) -> AsyncIterator[Item]`. Upserts are idempotent on
   `(source, external_id)`. Each Item records Source, Community, Thread, Author and Votes. Comments are their own Items.
2. **Prefilter** (no model):
   - Drop non-English Items, Items that are too short, deleted, duplicates or from bots.
   - Keep Items that match a pain phrase, come from a Watch-listed Community, or have enough Votes.
3. **Extract** (local model). Each Item yields **zero or more Signals**, each describing one problem:
   - `kind`: pain | request | complaint | payment | market
   - problem statement, Persona, intensity 1–5, quote, workaround
   - products mentioned, matched to **Competitors** by normalized name and URL (unknown products create new Competitors)
   - `model` and `prompt_version`, so Items can be re-extracted later
4. **Attach:**
   - Pain, Request and Payment Signals attach to an Idea.
   - Complaint Signals attach to the Competitor and an Idea.
   - Market Signals attach only to a Competitor and reach Ideas through it.
5. **Cluster** (incremental, so Idea IDs stay stable; ADR 0001):
   - Embed the problem statement together with its Persona.
   - Attach to the nearest Idea above a threshold, including archived Ideas; otherwise put it in a holding pool.
   - Run HDBSCAN on the pool weekly to form new Incubating Ideas, which the local model names.
   - Link Ideas with the same problem but different Personas as **Related**.
6. **Fit check** (local model, against your Fit profile):
   - Breaking a **Deal-breaker** archives the Idea automatically as **"outside fit"**, without a Proposed
     decision. It's counted in This Week and can resurface if the Fit profile changes.
   - **Preferences** produce a fit score used in scoring.
   - Seeded Ideas are exempt.
7. **Score** (recomputed daily, weekly snapshots). Weights live in `config/scoring.yaml`, and the breakdown is shown:
   - **Volume:** distinct Authors and recent Signals. Each Thread's contribution is capped.
   - **Spread:** distinct Communities (weighted heavily)
   - **Intensity:** Signal intensity plus Votes, normalized per Community. Votes never count as Authors.
   - **Payment:** Payment Signals, B2B Persona, Competitors that charge
   - **Momentum:** last 4 weeks vs. the 4 before, blended half-and-half with Google Trends where available
   - **Competitive gap:** Complaint Signals relative to the number of Competitors
   - **Fit:** from Preferences
   - **Solo-buildable:** the local model's estimate of an MVP in 4–6 weeks for one developer with your skills
8. **Publish.** An Incubating Idea moves to the Inbox when it has ≥ 5 distinct Authors, across ≥ 2 Communities,
   with at least one Pain or Request Signal (all configurable).
9. **Proposed decision** (local model). Each published Idea gets a suggested Review outcome (Working List, or
   Archive with a reason), a one-line rationale, and a confidence. **It is never applied without you.**
10. **Resurface.** An archived Idea returns to the Inbox as Resurfaced when its evidence changes significantly
    (e.g. 3× momentum, or a new Community). This applies only for the reasons "too competitive", "low willingness
    to pay" and "too big to build", and for "outside fit" after a Fit profile change.
11. **Summaries** (local model). Written on publish and rewritten automatically when evidence changes significantly.
    A Summary covers the problem, the Persona, the 3 strongest quotes, a payment note and a one-line verdict.
12. **Staleness.** A Briefing is marked **Stale** when its Idea gains significant new evidence (e.g. 50% more
    Signals, or a new Community). It is never regenerated automatically.

---

## 4. Briefings

Generated when you move an Idea to the Working List or click **Generate briefing**. Every claim is labelled
**evidence** (cites a Signal/Item), **researched** (cites a web source) or **Assumption** (the model's own
inference).

### Briefing template
- **Problem** and **Persona**
- **Evidence:** 5–10 strongest quotes, linked to the original Items
- **Current workarounds**
- **Competitors:** name, pricing, complaints (including Complaint and Market Signals already collected)
- **Willingness to pay** and a pricing hypothesis
- **Market math (US):**
  - number of US businesses matching the Persona (County Business Patterns, by industry code)
  - a realistic price
  - customers needed to reach your **Revenue goal**
  - the share of the market that represents

  Always labelled "US businesses".
- **Reach:**
  - concrete channels to the first 10 customers, with cost and effort notes
  - built-in distribution (plugin directories, app marketplaces)
  - search terms people use for the problem
  - the Communities the Signals came from
- **Build:**
  - required integrations and whether their APIs are open, need approval, or are partner-only
  - the hardest technical piece
  - weeks to MVP for one developer with your skills
- **MVP scope:** 3–5 core features, what to leave out
- **Case against** (written separately from the rest, to counter optimism):
  - pre-mortem
  - why this doesn't exist yet (tiny market, hard distribution, bundled by incumbents, regulation, platform
    risk, failed predecessors)
  - the single strongest reason not to build it
- **Assumptions to test:** ranked by risk × lack of evidence. For the top ones: a suggested **Hypothesis**,
  **Kill criteria** and the cheapest **Experiment**. These pre-fill the Idea's validation setup.
- **Score breakdown**

### Manual Briefing flow (until a Claude API key is set up)
1. **Generate briefing** creates a Briefing with status `awaiting_response` and shows a self-contained prompt with
   **Copy prompt**. The prompt contains:
   - the Idea's problem statement, Persona and scores
   - the strongest quotes with links
   - known Competitors
   - your Fit profile's skills and Revenue goal
   - the US business count for the mapped industry codes
   - the instructions, including a separate "Case against" pass
   - the exact output format

   It's always **one prompt and one paste**, even though the API version would make several calls.
2. Paste it into claude.ai (web search on) and copy the reply.
3. Paste the reply into **Paste response**. The app validates it and stores it; if validation fails, it shows
   what's wrong and keeps your paste.

**Output format:** Markdown with YAML frontmatter. Structured fields go in the frontmatter; the prose goes under
fixed headings.
```markdown
---
briefing_for: idea-142          # must match, so a paste can't land on the wrong Idea
format_version: 1
verdict: pursue | maybe | skip
confidence: 1-5
persona: "..."
pricing_hypothesis: { model: per_seat, low_usd: 19, high_usd: 49 }
market_math: { us_businesses: 98000, price_usd: 49, customers_needed: 204, share_pct: 0.2 }
competitors:
  - { name: "...", url: "...", pricing: "...", gaps: ["..."] }
integrations:
  - { name: "QuickBooks", api_access: open | approval | partner_only }
weeks_to_mvp: 6
assumptions:
  - { claim: "...", risk: 1-5, evidence: none | weak | some,
      hypothesis: "...", kill_criteria: "...", experiment: "..." }
---
## Problem
## Persona
## Evidence
## Current workarounds
## Competitors
## Willingness to pay
## Market math (US)
## Reach
## Build
## MVP scope
## Case against
## Assumptions to test
```
Inline labels in the prose: `[evidence: signal-1234]`, `[researched: https://…]`, `[assumption]`.

Parsing: `python-frontmatter` + pydantic, plus a check that all required headings are present. It tolerates a
reply wrapped in a code fence or with extra text before it. Frontmatter Competitors are matched to existing
Competitors or create new ones.

**Shortcuts:**
- Put the fixed instructions in a claude.ai Project, so each copied prompt is only the Idea's evidence.
- `ideafinder brief export/import <id>` lets a Claude Code session do the whole loop.

When an API key is ready, set `briefing.provider: anthropic`.

---

## 5. Validation

When an Idea joins the Working List:
- **Hypothesis:** who would pay how much to have the problem solved. Pre-filled from the Briefing's top
  Assumption, if there is one; you edit it.
- **Kill criteria:** set before testing starts.
- **Time limit:** default 2 weeks per Stage, editable per Idea.

### Stages and rules
- **Stages:** Researching → Validating → Building, plus Paused.
- **Moving forward:** advancing a Stage requires a **passed Experiment** in the current one.
- **Kill criteria met:** when an Experiment's Outcome meets the Kill criteria, the system creates a **Proposed
  decision** to archive as "dropped after research", with the Experiment as evidence. You confirm it.
- **Time limit expired:** the Idea appears at the top of the Working List. You choose **extend once**, **Pause**
  or **drop**; a second extension isn't offered.
- **Active limit:** 3 Ideas in Researching, Validating or Building; Paused doesn't count. When the list is full,
  moving an Idea in (from the Inbox, from Paused, or as a Seeded Idea) starts a **Head-to-head** against the
  lowest-priority active Idea. The loser is Paused or archived, your choice.
- **Priority:** comes from Head-to-heads, which the app also asks for occasionally between active Ideas. There is
  no drag-to-reorder.

### Experiments
- **Types:** interviews, landing page, pre-sale offer, other.
- **Each Experiment has:** a plan, a start date, an **Outcome** (pass / fail / inconclusive) and evidence notes.

### Leads
- **Who:** created from the Authors of an Idea's highest-intensity Signals.
- **Each Lead has:** a link to their post, a suggested opening line based on what they wrote, a status
  (to contact → contacted → replied → talked) and notes.
- **Contact is always manual,** through the Source's normal reply feature.
- **Privacy:** nothing is stored beyond what they posted publicly.
- **Evidence:** notes from "talked" Leads attach to an interview Experiment as its evidence.

### Validation kit
Generated when an Idea reaches Validating:
- an interview script that asks about past behaviour and spending, not opinions on the idea
- tailored messages for each Lead
- landing page copy built on the Persona's own words
- a pre-sale or waitlist offer

---

## 6. Data model (Postgres)

| Table | Key columns |
|---|---|
| `sources` | id, name, phase, enabled |
| `communities` | id, source_id, external_ref, name, watch_status (watched/suggested/ignored), suggestion_evidence (jsonb) |
| `items` | id, source_id, community_id, external_id (unique with source), thread_external_id, parent_external_id, url, title, body, author, language, votes, created_at, fetched_at, raw_json |
| `signals` | id, item_id, kind, problem_statement, persona, intensity, quote, workaround, embedding `vector(384)`, model, prompt_version |
| `ideas` | id, title, problem_statement, persona, industry_codes, state (incubating/inbox/working/archived), seeded, merged_into_id, centroid `vector(384)`, first_seen, last_seen, published_at, score, score_breakdown (jsonb), fit (jsonb), summary_md, summary_updated_at |
| `idea_signals` | idea_id, signal_id, similarity |
| `idea_relations` | idea_id, related_idea_id |
| `competitors` / `competitor_aliases` | id, name, url, pricing, created_from / competitor_id, alias |
| `idea_competitors`, `signal_competitors` | link tables |
| `proposed_decisions` | id, idea_id, kind (review/kill), proposal, archive_reason, rationale, confidence, status (pending/confirmed/overridden), created_at, resolved_at |
| `idea_reviews` | id, idea_id, action (to_working/to_archive/restored/resurfaced/merged/split/auto_outside_fit), archive_reason, overrode_proposal, note, score_at_action, signal_count_at_action, created_at |
| `working_items` | idea_id, stage, stage_started_at, time_limit_days, extended_in_stage, priority_rank, notes_md |
| `hypotheses` | id, idea_id, statement, kill_criteria, current, created_at |
| `experiments` | id, idea_id, hypothesis_id, stage, type, plan_md, outcome (pass/fail/inconclusive/null), evidence_md, started_at, ended_at |
| `leads` | id, idea_id, signal_id, author, source_id, url, opener, status (to_contact/contacted/replied/talked), notes_md, experiment_id |
| `head_to_heads` | id, idea_a, idea_b, winner, trigger (full_list/periodic), loser_action, created_at |
| `validation_kits` | id, idea_id, content_md, generated_at |
| `briefings` | id, idea_id, version, status, stale, provider, format_version, prompt_text, frontmatter (jsonb), content_md, raw_response, signal_count_at_gen, community_count_at_gen, created_at, completed_at |
| `fit_profile` | single row: skills, price_min/max, sales_model, preferred_industries, avoided_industries, deal_breakers (jsonb), revenue_goal_usd, version |
| `market_stats` | naics_code, geography (US), establishments, year |
| `idea_snapshots` | idea_id, week, signal_count, author_count, community_count, score |
| `scoring_suggestions` | id, proposed_weights, explanation, evidence, status |
| `runs` | id, stage, source_id, started_at, finished_at, items, errors, cursor |
| `llm_usage` | id, task, provider, model, input_tokens, output_tokens, cost_usd, created_at |

**Archive reasons:**
- `not_interested`: permanent
- `too_competitive`, `low_willingness_to_pay`, `too_big_to_build`: resurface on evidence
- `outside_fit`: resurfaces on a Fit profile change
- `dropped_after_research`: permanent

---

## 7. Dashboard (localhost)

### Idea lifecycle
```
Incubating ──(publish)──▶ INBOX ──(confirm/override)──▶ WORKING LIST ──(kill criteria / dropped)──▶ ARCHIVE
     │                      │                              Researching → Validating → Building (+ Paused)
     │                      └──────────────────────────────▶ ARCHIVE ──(resurface)──▶ INBOX
     └──(Deal-breaker)──▶ ARCHIVE "outside fit"
Seeded Idea ─────────────────────────────────────────────▶ WORKING LIST (Head-to-head if full)
```
- **Archived Ideas keep collecting Signals.**
- **Merge:** the target Idea keeps its identity and takes the more advanced state of the two. Its Briefing
  becomes Stale.
- **Split:** the original Idea keeps its identity and state. The split-off part follows the normal rules.

### Views
- **Inbox:**
  - **Confident** Proposed decisions are grouped under **Confirm all**; the **uncertain** ones are listed
    individually.
  - Each card shows the Summary, proposal + rationale, score, # Communities and badges (New / Resurfaced).
  - Keyboard shortcuts: `y` confirm, `w` Working List, `a` Archive, `m` Merge into…, `o` open.
- **Working List:**
  - Active Ideas (≤ 3) in priority order, then Paused Ideas.
  - Each shows its Stage, Time limit countdown, current Hypothesis, latest Experiment Outcome and Lead counts.
  - Expired Time limits and kill-criteria proposals are pinned to the top.
- **Head-to-head:** two Ideas side by side (Summary, score, plus Case against and Market math where a Briefing
  exists), pick one.
- **Archive:** filter by reason and date; restore.
- **Idea detail:** Briefing (with Stale badge and claim labels), Summary, score breakdown, trend chart, evidence
  feed, Competitors, Related Ideas, and a **Validation** tab: Hypothesis, Kill criteria, Experiments, Leads,
  Validation kit. Also Review history, notes, and Split/Merge.
- **Add idea:** create a Seeded Idea; it searches existing Signals and gets a Summary.
- **This Week:**
  - new Inbox Ideas and Resurfaced Ideas
  - "outside fit" auto-archives (count + list)
  - expiring Time limits, pending kill-criteria proposals and Lead replies to log
  - newly Stale Briefings and the biggest movers
- **Settings:** Fit profile (Deal-breakers, Preferences, skills, Revenue goal); changing it re-checks fit.
- **Communities:** the Watch list, plus suggestions with evidence.
- **Scoring:** weights and open weight suggestions (accept/reject).
- **Health:** last run per Source, errors, `model` queue backlog, last backup.

---

## 8. Project layout

```
saas-idea-finder/
  pyproject.toml
  GLOSSARY.md
  docs/adr/  docs/research/
  deploy/launchd/          # plists for scheduled collection and backups
  config/
    sources.yaml           # Sources, Watch lists, queries
    scoring.yaml           # weights, Thread cap, publish/resurface/stale thresholds
    models.yaml            # task → provider/model routing
  src/ideafinder/
    cli.py                 # up | collect | extract | cluster | score | publish | brief export/import | backup
    worker.py              # procrastinate app: tasks, schedule, "collect" and "model" queues
    db/                    # SQLAlchemy models, Alembic migrations
    collectors/            # base.py, hn.py, jira.py, wordpress.py, stackexchange.py, ...
    reference/             # census_cbp.py (US business counts)
    pipeline/              # prefilter, extract, competitors, embed, cluster, fit, score, publish, propose, resurface, summary
    briefing/              # prompt builder, parser, staleness
    validation/            # hypotheses, experiments, leads, kits, head_to_head, time limits
    llm/                   # routing interface; providers: ollama, anthropic, manual
    prompts/               # versioned prompt templates
    web/                   # app.py, routes/, templates/, static/
  tests/
    fixtures/              # recorded API responses per Source
    eval/                  # hand-labelled Items for extraction accuracy
```

---

## 9. Milestones

| # | Milestone | Done when |
|---|---|---|
| M0 | Skeleton | `uv` project, Postgres.app + pgvector, Alembic migrations, config loading, CLI and worker stubs, `ideafinder up` serving an empty localhost page |
| M1 | Phase 1 collectors | Hacker News, Jira, WordPress.org, Stack Exchange; cursors, rate limits, English filter, 12-month backfill |
| M2 | Signals | Prefilter, LLM routing, Ollama, Signal extraction, Competitor matching, embeddings; ~200-Item eval set to pick the local model (≥ 85% precision on whether an Item contains a Signal) |
| M3 | Ideas | Incremental clustering by problem + Persona, Related links, naming; Merge/Split in the CLI |
| M4 | Rank and propose | Fit profile + Fit check, scoring, snapshots, publish threshold, Proposed decisions, resurfacing |
| M5 | Review dashboard | Inbox (Confirm all), Archive, Idea detail, Settings, Communities; Merge/Split, Seeded Ideas, auto-refreshing Summaries |
| M6 | Briefings | Census reference data, prompt builder with claim labels, Case against, market math, Reach/Build, Assumptions; parser; manual flow; `brief export/import`; Stale marking |
| M7 | Validation | Working List with Hypotheses, Kill criteria, Time limits, Experiments, Leads, Validation kits, Active limit, Head-to-heads |
| M8 | Run it daily | launchd schedules, nightly backups, This Week, Health page |
| M9 | Phase 2 Sources | GitHub, Shopify App Store, feature-request boards, Zapier, Product Hunt, Mastodon, Bluesky. **Adding Zapier triggers the Pi move.** |
| M10 | Phase 3 Sources | Google Trends (BiggerPockets dropped, ADR 0004) |
| M11 | Tuning | Scoring suggestions from Overrides and Review history; tighten prompts against the eval set |
| (when triggered) | Move to the Pi | Postgres, `collect` queue and web app on the Pi 4 with a USB 3 SSD; Mac keeps the `model` queue |

M0–M8 gives the full workflow (discover → review → validate) with four Sources.

---

## 10. Cost
- **$0 running cost.** Everything runs locally; Briefings use your Claude subscription through the manual flow.
- **Optional later:** a Claude API key for automatic Briefings (a few dollars a month, with a $15/month cap).
- **Optional when triggered:** a USB 3 SSD for the Pi.

## 11. Risks
- **No Reddit:** non-tech Personas stay thin until Phase 2.
- **Source access changes:** re-check a Source before building its collector; the Health page flags failures.
- **Noise and duplicate Ideas:** clustering threshold tuning, Merge/Split, and the eval set.
- **One viral Thread inflating an Idea:** the Thread cap, distinct Authors and Community spread.
- **Local model quality** for Proposed decisions and Validation kits: measured by your Override rate; any task can
  be switched to another provider in config.
- **Rubber-stamping Proposed decisions:** only confident ones are grouped, and the Override rate is shown on the
  Scoring page.
- **Single machine:** nightly backups covered by Time Machine or iCloud Drive.
- **Lead etiquette:** manual contact only, through each platform's normal features, and only about the problem
  the person described.

## 12. Defaults (change if needed)
- Time limit 2 weeks per Stage; Active limit 3; publish threshold 5 Authors / 2 Communities / 1 Pain or Request
- Briefings only when you move an Idea to the Working List or click the button
- Market math covers US businesses only
