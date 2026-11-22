# Idea Finder: user guide

## What it is

Idea Finder is a personal tool that finds software business ideas in what people already write
online. It reads public complaints, feature requests and questions (Hacker News, Atlassian's Jira,
WordPress.org, Stack Exchange, GitHub, the Shopify App Store, feature-request boards, Zapier
Community, Mastodon, Bluesky), pulls out the problems people describe, groups the same problem
for the same kind of person into an **Idea**, and ranks the Ideas by how strong the evidence is.
Then it helps you decide which Ideas to pursue and how to test them cheaply before you build.

Everything runs on your Mac: the database, the web app, and the AI model (in LM Studio). The only
thing that leaves your machine is a Briefing prompt, and only when you paste it into claude.ai
yourself.

## The problem it solves

Finding a SaaS idea usually starts from a product you'd like to build, and then you go looking
for people who want it. That's backwards, and it's how founders build things nobody pays for.

Idea Finder starts from the other end: problems that many different people complain about, in
more than one place, in their own words. It keeps the evidence attached (every claim links back
to the original post), argues against each Idea as well as for it, and makes you set kill
criteria before you test, so you stop early instead of drifting.

In short, it replaces "I have a hunch" with "37 people in 4 communities described this problem
in the last 3 months, 6 said they'd pay, and the existing tools get these complaints."

## Key ideas (the vocabulary)

| Term | Meaning |
|---|---|
| **Item** | One post, comment, review or issue collected from a Source. |
| **Signal** | One piece of evidence the AI found in an Item: a *pain*, a *request*, a *complaint* about a product, or *payment* (someone pays or would pay). |
| **Idea** | A recurring problem for one kind of person (the **Persona**), e.g. "landscapers re-texting crews after rain". Not a product. |
| **Related** | Ideas with the same problem but different Personas (e.g. landscapers vs plumbers). Kept separate, linked. |
| **Competitor** | An existing product mentioned in the evidence or found by a Briefing. |
| **Fit profile** | Your criteria: Deal-breakers (rule an Idea out), Preferences (skills, price range, industries) and a monthly revenue goal. |
| **Proposed decision** | The app's suggestion for an Inbox Idea (Working List, or Archive with a reason), with a confidence. Never applied without you. |
| **Briefing** | A written analysis of an Idea: competitors, market size, how to reach customers, what it takes to build, and the case against. |
| **Working List** | Ideas you've chosen to pursue (at most 3 active at once). |
| **Hypothesis / Kill criteria** | Who would pay how much, and the result that would make you drop it. Set before testing. |
| **Experiment / Outcome** | One test (interviews, landing page, pre-sale, other) ending in pass, fail or inconclusive. |
| **Lead** | Someone who described the problem and is worth contacting. You contact them yourself. |

## How an Idea moves through the app

```
 Sources ──collect──▶ Items ──extract (AI)──▶ Signals ──cluster──▶ Incubating Ideas
                                                                        │
                                       enough people, in 2+ communities │ (publish)
                                                                        ▼
            ┌────────────── Archive ◀──── you decide ◀──────────────  INBOX
            │  (resurfaces if the evidence grows,         │   (Summary + Proposed decision)
            │   for some reasons)                         ▼
            └──────────────────────────────────▶  WORKING LIST
                                                  Researching → Validating → Building
                                                  (+ Paused; Briefing, Experiments, Leads)
```

- An Idea reaches the **Inbox** when at least **5 different people** across at least
  **2 communities** described it, including at least one pain or request. Until then it's
  *Incubating* and you don't see it (except through `ideafinder ideas list`).
- Before publishing, it's checked against your Fit profile. Breaking a Deal-breaker archives it
  automatically as *outside fit* (you'll see these on This Week).
- Archived Ideas keep collecting evidence. Ones archived as *too competitive*, *low willingness
  to pay* or *too big to build* come back as **Resurfaced** if their evidence triples or spreads
  to a new community. *Not interested* is permanent.

## Getting started (one time)

You need [uv](https://docs.astral.sh/uv/), [Postgres.app](https://postgresapp.com/) and
[LM Studio](https://lmstudio.ai/).

1. **Postgres:** open Postgres.app and start the server.
2. **LM Studio:** download the two models and start the server:
   ```sh
   lms get qwen/qwen3.6-35b-a3b          # MLX 4-bit, about 20 GB
   lms get text-embedding-nomic-embed-text-v1.5
   lms server start
   ```
3. **The app:**
   ```sh
   uv sync
   uv run ideafinder db upgrade
   uv run ideafinder doctor               # says what's missing and how to fix it
   uv run ideafinder census load          # US business counts for market sizing (once a year)
   ```
4. **Your Fit profile:** start the app (below), open **Settings**, and fill in your Deal-breakers,
   skills, price range, sales model, industries and monthly revenue goal. Keep Deal-breakers to
   things you'd never do; everything else is a Preference.
5. **The daily schedule** (so collection runs without the app open):
   ```sh
   uv run ideafinder schedule show        # see what will be installed
   uv run ideafinder schedule install     # daily run at 06:00, backup at 02:30
   ```
   If the Mac is asleep at 06:00, the run happens when it wakes. Steps that need the AI are
   skipped if LM Studio isn't running and catch up next time, so keep LM Studio's server on.

Optional credentials go in a `.env` file (see `.env.example`): a GitHub token for more GitHub
history, a Product Hunt token to turn on Product Hunt, a Bluesky app password for deeper search.

## Opening the app

```sh
uv run ideafinder up
```

Then open **http://127.0.0.1:8765**. This starts the web app plus the background workers (which
write Summaries, Lead openers and Validation kits). Press Ctrl+C to stop. The app is only
reachable from your Mac.

The top bar has: **This Week**, **Inbox** (with a count), **Working List** (with a count),
**Archive**, **Add idea**, and on the right **Communities**, **Settings** and **Health**.

## The weekly workflow

A good rhythm is one review session a week (more if the Inbox fills up), plus a few minutes on
Working List Ideas as your experiments move.

### 1. Start with This Week

**This Week** answers "what changed and what needs me?":

- **Needs you:** Time limits ending or passed, Kill criteria that were met, and Leads you
  contacted who may have replied.
- **New in the Inbox** and **Resurfaced** Ideas.
- **Archived automatically: outside fit**, with which Deal-breaker each one broke. If one looks
  wrong, your Deal-breaker is probably too broad.
- **Biggest movers:** scores that rose or fell most since last week.
- **Briefings gone Stale:** Ideas whose evidence grew a lot since their Briefing was written.

### 2. Triage the Inbox

Each Inbox Idea comes with a **Summary** (the problem, who has it, the strongest quote, what
people pay now, a one-line verdict), a **score** out of 100, and a **Proposed decision**.

- **Clear calls** (the model is at least 80% sure) are grouped at the top as a checklist. Untick
  any you want to judge yourself, then **Confirm ticked proposals**.
- **Needs your judgement** lists the rest in full. For each, either confirm the proposal, move it
  to the Working List, archive it with a reason, or merge it into another Idea if it's a duplicate.

Keyboard: `j`/`k` move between Ideas, `y` confirm the proposal, `w` Working List, `a` archive
(then pick a reason), `m` merge (then type the other Idea's number or title), `o` open.

Archive reasons matter: *too competitive*, *low willingness to pay* and *too big to build* let
an Idea come back if the evidence changes; *not interested* doesn't. Choosing differently from
the proposal is recorded as an override, which will later tune the scoring.

### 3. Look closer at an Idea

Open an Idea (`o`, or click its title). You'll see:

- The **Summary and verdict**, then the decision buttons.
- **Evidence:** every Signal with the person's own words, highlighted, linked to the original post.
- On the right: the **score breakdown** (volume, spread, intensity, payment, momentum,
  competitive gap, fit, solo-buildable), the weekly **trend**, **Google searches** for the
  problem, the **Fit** check, **Related** Ideas, **products mentioned**, your **notes** and the
  **history**.

If an Idea mixes two different problems, tick the Signals that belong elsewhere and **Split
ticked Signals into a new Idea**. If two Ideas are the same, **Merge** one into the other.

### 4. Get a Briefing

Moving an Idea to the Working List prepares a Briefing prompt (you can also click **Generate
briefing** on any Idea). The Briefing is written by Claude on claude.ai, by hand:

1. On the Idea's **Briefing** page, click **Copy prompt**.
2. Paste it into claude.ai with **web search turned on**, and copy the whole reply.
3. Paste the reply back on the Briefing page and click **Check and save reply**. If something's
   missing or malformed it tells you what, and keeps your paste so you can fix it.

The Briefing covers the problem, evidence, current workarounds, competitors and their pricing,
willingness to pay, **US market math** (how many US businesses, how many customers you'd need for
your revenue goal), how to reach the first 10 customers, what it takes to build, an MVP scope, a
separate **Case against**, and the riskiest assumptions to test. Every claim is labelled:
*evidence* (links to a Signal), *source* (a web page) or *assumption*.

Briefings are never rewritten automatically. When an Idea's evidence grows a lot, its Briefing is
marked **Stale**; generate a new version when you want one.

Prefer the terminal? `ideafinder brief export ID --out prompt.md` and
`ideafinder brief import ID reply.md` do the same.

### 5. Validate on the Working List

The **Working List** shows your active Ideas in priority order, each with its Stage, days left
in its Time limit, current Hypothesis, latest Experiment and Lead counts. Paused Ideas are at
the bottom. Anything that needs a decision is pinned at the top under **Decide now**.

Open an Idea's **Validation** page to work on it:

1. **Hypothesis and Kill criteria.** Write who would pay how much to have the problem solved,
   and what result would make you drop it. If the Briefing is in, this is pre-filled from its
   riskiest assumption; edit it into your own words. Set the Kill criteria *before* testing.
2. **Experiments.** Start one (interviews, landing page, pre-sale offer, other) with a plan that
   says what counts as a pass. When it's done, record the Outcome and the evidence. If the result
   meets your Kill criteria, tick **This meets the Kill criteria**: the app proposes archiving
   the Idea as *dropped after research*, and you confirm it on the Working List.
3. **Moving on.** Researching → Validating → Building. Each step needs a **passed Experiment**
   in the current Stage. Building is the last Stage.
4. **Leads.** Click **Find Leads** to list up to 10 people behind the strongest Signals, each with
   a link to their post and a suggested opening line that asks about their experience (not a
   pitch). **Contact them yourself**, by replying on their own post. Track each one: to contact,
   contacted, replied, talked. When you mark someone *talked*, your notes become evidence for the
   open interviews Experiment.
5. **Validation kit.** When an Idea reaches Validating, the app writes an interview script
   (questions about what people did and paid, never "would you buy this?"), a message for each
   Lead, landing-page copy in the Persona's own words, and a pre-sale or waitlist offer.

**Time limits.** Each Stage has a Time limit (14 days by default; change it per Idea). When it
runs out, the Idea is pinned at the top: **Extend once**, **Pause**, or **Drop**. There's no
second extension.

**The Active limit and Head-to-heads.** At most 3 Ideas can be active (Paused ones don't count).
Moving a 4th in, from the Inbox, from Paused, or by adding your own, starts a **Head-to-head**:
the two Ideas side by side (Summary, score, Hypothesis, Briefing verdict and market math, and its
Case against), and you pick which one stays. The other is Paused or archived. About once a week
the app also asks you to compare two neighbouring active Ideas; that only changes the order.
Priority on the Working List comes only from these choices.

### 6. Add your own Ideas

**Add idea** lets you enter a problem you already know about (a short name, the problem, and who
has it). It goes straight to the Working List, picks up matching evidence already collected, and
keeps collecting from then on.

## Settings, Communities and Health

- **Settings:** your Fit profile. Saving a change re-checks Ideas on the next daily run (or click
  **Re-check Ideas now**). Ideas archived as *outside fit* come back if they no longer break a
  Deal-breaker.
- **Communities:** each place evidence comes from, with how many Items, Signals and Ideas it has
  produced. The Watch list itself is edited in `config/sources.yaml`.
- **Health:** whether the daily routine is actually happening: the last daily run and what it did,
  whether the schedule is installed, the newest backup, whether LM Studio is reachable and has the
  right models, the extraction backlog, and failed jobs. Every warning comes with the command
  that fixes it. Check here first if the Inbox seems quiet.

## Behind the scenes: the daily run

Every morning at 06:00 (or when the Mac wakes), `ideafinder daily`:

1. **Collects** new posts from every Source.
2. **Prefilters** them (drops short, non-English or off-topic posts; no AI).
3. **Extracts** Signals with the local model, for up to 90 minutes; the rest of the backlog waits
   for the next day.
4. **Attaches** new Signals to existing Ideas, and once a week **forms** new Ideas from the
   Signals that don't fit any (and names them).
5. **Ranks:** scores every Idea, checks fit, publishes to the Inbox, resurfaces, writes Summaries,
   maps industries for market math, proposes decisions, and marks Briefings Stale.
6. Looks up **Google search interest** for Inbox and Working List Ideas (weekly per Idea).

At 02:30 a **backup** of the database is written to `backups/` (14 days are kept). To restore one:

```sh
pg_restore --clean --if-exists --dbname ideafinder backups/ideafinder-YYYYMMDD-HHMMSS.dump
```

## Command reference

| Command | What it does |
|---|---|
| `ideafinder up` | Web app plus background workers (daily use) |
| `ideafinder doctor` | Checks setup and says how to fix problems |
| `ideafinder daily [--force]` | Runs the whole daily chain now |
| `ideafinder collect [-s SOURCE] [--since-days N]` | Collect now; `--since-days` is a trial that doesn't move the cursors |
| `ideafinder prefilter` / `extract --threads N [--source KEY]` / `cluster` / `rank` / `trends` | Run one step of the chain |
| `ideafinder ideas list [--state inbox]` / `show ID` | Ideas from the terminal |
| `ideafinder ideas merge A B` / `split ID SIGNAL...` / `review ID working\|archive\|restore` | Decisions from the terminal |
| `ideafinder fit show` / `fit load FILE` | Your Fit profile (the Settings page does the same) |
| `ideafinder brief export ID` / `brief import ID FILE` | Briefings by hand |
| `ideafinder census load` | US business counts (once a year) |
| `ideafinder backup [--list]` | Back up now, or list backups |
| `ideafinder schedule show\|install\|uninstall` | The launchd schedule |
| `ideafinder eval ...` | The extraction test set (for choosing or checking the AI model) |

## Known limits (as of this version)

- **Early on, the Inbox may stay empty.** Ideas need evidence from at least 5 people in at least
  2 communities, so the Inbox fills only as several Sources get extracted. Extraction takes turns
  between Sources so no single backlog holds up the rest; to work through particular Sources
  first, use `ideafinder extract --source KEY` or `--skip-source KEY`. The Inbox's **Run ranking
  now** button only queues a run: if the worker is busy extracting, it starts after that.
- **The AI makes mistakes.** About 85% of the posts it flags as evidence really are (measured on
  a 200-post test set whose labels you haven't reviewed yet). Quotes are checked against the
  original text, and every Signal links to its post, so check the evidence before acting.
- **Clustering thresholds were tuned on Jira text** and may need adjusting as other Sources'
  Signals arrive.
- **Some Sources have no history** (Zapier keeps its latest 100 topics; WordPress.org its latest
  30 reviews), so they only keep up if the daily run happens regularly.
- **Canny and UserVoice boards show no dates**, so those ideas are dated when first collected.
- **Product Hunt is off** until you add a token; launches aren't turned into Competitors yet.
- **Google Trends** comes from an unofficial library and may stop working when Google changes
  its website; Momentum then falls back to Signals alone.
- **Briefings are manual** (paste into claude.ai) until a Claude API key is set up.
