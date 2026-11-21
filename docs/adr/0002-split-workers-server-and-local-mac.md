---
status: superseded by ADR-0003
---

# Split the work between an always-on server and a local model on the owner's Mac

The web app, database, crawling, scoring and Claude-powered Briefings run on a small always-on server. All
high-volume language-model work (classifying Items into Signals, embeddings, naming Ideas, writing Summaries)
runs on the owner's Mac (M3 Pro, 36 GB) using a local model. The Mac connects to the server's database over
Tailscale and processes its own job queue whenever it's awake. We chose this to avoid paying per request for
thousands of classifications a day: the server can't run a useful local model without a GPU, and the laptop
can't be relied on to crawl around the clock.

## Considered Options

- **Everything on the Claude API from the server:** simplest, but ongoing per-request cost for work a local
  model handles well.
- **Everything on the Mac, reached remotely:** no server bill, but crawling stops whenever the laptop sleeps.
- **A GPU server:** solves both, but costs far more than the API would.
- **The Claude subscription through Claude Code headless mode:** shares limits with interactive use, and
  using a subscription to power an app is a terms-of-service gray area.

## Consequences

- New Inbox ideas are delayed while the Mac is offline; collection is not. Queued local jobs catch up when the
  Mac returns.
- The Mac worker runs natively rather than in Docker, because Docker on macOS can't use the Apple GPU.
- The database is reachable only over the private network, never from the public internet.
- Each language-model task is routed to a provider in config, so any task can move to the Claude API later
  without code changes.
