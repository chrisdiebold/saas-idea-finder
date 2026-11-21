---
status: accepted
---

# Everything runs locally on the Mac, with the Raspberry Pi as the planned always-on host

Supersedes ADR 0002. With a single user, the dashboard can be a web app on localhost, so the hosted server,
Docker deployment, Tailscale link, reverse proxy and login page are no longer needed. Collection, the Postgres
database (installed with Postgres.app), the web app and the local-model work all run on the owner's Mac. A
scheduled job starts collection without the web app being open, and macOS runs missed schedules when the Mac
wakes. Nothing is lost while it sleeps: every Phase 1 Source can fetch "everything since X", so a missed day is
collected on the next run.

The owner's Raspberry Pi 4 Model B is the planned always-on host for collection, the database and the web app.
We will move to it when any of these happens: the first RSS-only Source is added (those keep only their most
recent posts), phone access is wanted, or the Mac's sleep noticeably delays collection. Until then the Pi adds a
second machine to maintain without collecting anything the Mac would miss.

## Consequences

- The move must stay a configuration change: the database address is a setting, and collection jobs run on
  their own queue, separate from local-model jobs.
- On the Pi, Postgres needs a USB 3 SSD (an SD card wears out under constant writes) and at least 4 GB of RAM.
  The local-model work stays on the Mac either way.
- The dashboard is only reachable from the Mac until the move.
