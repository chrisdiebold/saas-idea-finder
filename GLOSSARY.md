# SaaS Idea Finder

A personal tool that finds software business ideas by collecting public complaints, requests and other evidence
of demand from the web, then helps one person decide which ideas are worth pursuing.

## Collection

**Source**:
A website or service that evidence is collected from, such as Hacker News or Stack Exchange.
_Avoid_: Site, feed, provider

**Community**:
A specific place within a Source where a group of people gathers, such as one Jira project, one Stack Exchange
site, the reviews of one app, or Hacker News as a whole.
_Avoid_: Channel, forum, sub

**Watch list**:
The Communities that are collected from regularly. It only changes with your approval; the system may suggest
additions it keeps running into.
_Avoid_: Subscriptions, targets, sources list

**Thread**:
A top-level post together with all the replies beneath it.
_Avoid_: Conversation, discussion

**Item**:
A single piece of content collected from a Source: a post, comment, review or issue, each written by one author.
_Avoid_: Post, raw item, document, record

**Author**:
A person who wrote an Item. Only Authors count toward how many people have a problem.
_Avoid_: User, poster, commenter

**Vote**:
A reaction to an Item that adds no text, such as an upvote. Votes strengthen an Idea's score but are not Authors.
_Avoid_: Like, upvote, endorsement

## Evidence

**Signal**:
One piece of evidence taken from an Item. It is about an Idea, a Competitor, or both, depending on its Signal
kind. An Item can yield zero or more Signals.
_Avoid_: Mention, data point, pain point

**Signal kind**:
What a Signal shows. One of:
- **Pain**: someone has the problem. About an Idea.
- **Request**: someone is looking for a tool that solves it. About an Idea.
- **Complaint**: someone is unhappy with an existing product. About both the Competitor and the Idea.
- **Payment**: someone would pay, or already pays, to have it solved. About an Idea.
- **Market**: a product has launched or is for sale. About a Competitor, and reaches Ideas through it.

_Avoid_: Signal type, category

**Idea**:
A recurring problem experienced by one Persona. It is not a product concept; possible products are proposed in
its Briefing.
_Avoid_: Opportunity, concept, entry, cluster, problem

**Persona**:
The kind of person who has an Idea's problem, defined by industry or role, such as "owner of a small
landscaping company".
_Avoid_: User, customer, audience, segment

**Related**:
The link between Ideas that share the same problem but have different Personas.
_Avoid_: Similar, sibling, parent/child

**Seeded Idea**:
An Idea you added yourself rather than one discovered from evidence. It goes straight to the Working List.
_Avoid_: Manual idea, custom idea, own idea

**Competitor**:
An existing product that addresses one or more Ideas. Each product is one Competitor, shared across all the
Ideas it relates to.
_Avoid_: Alternative, incumbent, rival

## Review

**Inbox**:
The ideas that are visible in the dashboard but have not yet been reviewed.
_Avoid_: Feed, new list, queue

**Review**:
The decision to move an idea out of the Inbox into either the Working List or the Archive.
_Avoid_: Triage, evaluation

**Proposed decision**:
A suggested move for an idea, with a reason, that waits for you to confirm or override: a Review outcome for an Inbox idea, or archiving a Working List idea that met its Kill criteria.
_Avoid_: Recommendation, auto-review, prediction

**Override**:
A Review where you chose differently from the Proposed decision.
_Avoid_: Correction, rejection

**Fit profile**:
Your personal criteria for ideas you would build, made of Deal-breakers, Preferences and a Revenue goal.
_Avoid_: Filters, founder profile

**Revenue goal**:
The monthly revenue an idea must be able to reach to be worth building for you.
_Avoid_: Target, MRR goal

**Deal-breaker**:
A Fit profile rule that rules an idea out entirely, such as hardware or regulated health data.
_Avoid_: Hard filter, exclusion

**Preference**:
A Fit profile rule that makes an idea more or less attractive without ruling it out, such as price range or
favoured industries.
_Avoid_: Soft filter, weighting

**Working List**:
The ideas that have been reviewed and judged worth pursuing.
_Avoid_: Shortlist, pipeline, favorites, starred

**Active limit**:
The maximum number of Working List ideas that are not Paused.
_Avoid_: WIP limit, cap, quota

**Head-to-head**:
A choice between two ideas about which one deserves your time next. Priority on the Working List comes from
Head-to-heads.
_Avoid_: Comparison, matchup, vote

**Stage**:
How far an idea on the Working List has progressed: Researching, Validating, Building, or Paused.
Building is the final Stage.
_Avoid_: Status, phase, step

**Archive**:
The ideas that have been reviewed and set aside, each with an Archive reason.
_Avoid_: Dismissed, trash, rejected

**Archive reason**:
Why an idea was archived. "Not interested" is permanent; "too competitive", "low willingness to pay" and
"too big to build" depend on the evidence and allow the idea to resurface; "dropped after research" applies to
ideas removed from the Working List; "outside fit" applies to ideas that break the Fit profile and can resurface
if the Fit profile changes.
_Avoid_: Rejection reason, dismissal reason

**Merge**:
Combining a duplicate Idea into another Idea, which keeps its identity and takes the more advanced state of the
two. A duplicate is merged, never archived.
_Avoid_: Combine, dedupe, link

**Split**:
Separating some of an Idea's Signals into a new Idea. The original keeps its identity and state; the new Idea
is treated like any newly discovered Idea.
_Avoid_: Fork, break up

**Incubating**:
The state of an idea that is still gathering evidence and is not yet shown in the dashboard.
_Avoid_: Pending, hidden, draft

**Resurfaced**:
An archived idea returned to the Inbox because its evidence changed significantly.
_Avoid_: Reopened, revived

## Validation

**Hypothesis**:
A testable statement of who would pay how much to have an idea's problem solved, written when the idea joins the
Working List.
_Avoid_: Assumption, thesis, bet

**Kill criteria**:
Conditions, set before testing begins, under which the idea should be dropped. Meeting them leads to a Proposed
decision to archive, which you confirm.
_Avoid_: Exit criteria, stop rules

**Time limit**:
How long an idea may stay in one Stage before you must extend it (once only), Pause it, or drop it.
_Avoid_: Timebox, deadline, sprint length

**Experiment**:
One test of a Hypothesis, such as interviews, a landing page or a pre-sale offer, ending in an Outcome.
_Avoid_: Test, trial, validation step

**Outcome**:
The result of an Experiment: pass, fail or inconclusive, with the evidence behind it.
_Avoid_: Result, verdict

**Lead**:
An Author behind one of an idea's Signals who is worth contacting about the problem. A Lead moves through
to contact, contacted, replied and talked.
_Avoid_: Prospect, contact, interviewee

## Analysis

**Summary**:
A short analysis of an idea, written for every idea that reaches the Inbox so it can be reviewed quickly.
_Avoid_: Entry summary, blurb, preview

**Briefing**:
A full product analysis of an idea, covering competitors, pricing, MVP scope and how to validate it.
_Avoid_: Brief, report, analysis

**Assumption**:
A claim in a Briefing that is backed by neither a Signal nor a researched source. The riskiest Assumptions
become candidate Hypotheses.
_Avoid_: Guess, inference, unknown

**Case against**:
The part of a Briefing that argues the idea will fail, written separately from the rest to counter optimism.
_Avoid_: Pre-mortem, devil's advocate, risks section

**Validation kit**:
The material for testing an idea, created when it reaches the Validating Stage: an interview script, messages
to Leads, landing page copy and a pre-sale offer.
_Avoid_: Toolkit, playbook, outreach pack

**Stale**:
The state of a Briefing whose Idea has gained significant new evidence since the Briefing was written.
It is regenerated only when you choose.
_Avoid_: Outdated, expired
