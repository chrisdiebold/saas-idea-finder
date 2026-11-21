---
status: accepted
---

# Google Trends is read through pytrends; BiggerPockets is not collected

Phase 3 planned two additions. Both ran into the access rule (PLAN.md §1: official APIs, RSS,
public JSON or plain HTML that the site's terms and robots.txt allow).

**Google Trends** has no generally available API: the official Trends API is an
application-only alpha (announced July 2025, still alpha in late 2026, no self-serve signup).
pytrends reads the same JSON the Trends website uses, so it isn't an official interface. We use
it anyway, as a deliberate exception, because Trends is reference data for one score component
(Momentum), not a Source of anyone's posts, and the use is personal and tiny: one request per
Inbox or Working List Idea per week, spaced out, stopping for the day on the first "slow down".
No proxies, rotating identities or other ways around Google's limits are used.

**BiggerPockets** publishes an RSS feed (listed in its robots.txt), but its terms say "You will
not incorporate and/or store the Content within your internal systems". Storing posts is what a
Source does, so it isn't collected. Real-estate investors' problems can still arrive through
other Sources (e.g. Mastodon #realestate).

## Consequences

- If Google's Trends API becomes generally available, switch `pipeline/trends.py` to it; the
  rest (search terms from Summaries, the ratio, the Momentum blend) stays the same.
- pytrends is unmaintained and may break when Google changes the website. When it does, Momentum
  falls back to Signals alone for Ideas without fresh Trends data; nothing else depends on it.
- Revisit BiggerPockets only if its terms change.
