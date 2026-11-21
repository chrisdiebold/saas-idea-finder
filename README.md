# SaaS Idea Finder

A personal tool that collects public evidence of software problems, groups it into Ideas, and helps you decide
which to pursue and validate. Runs entirely on your Mac.

- **What it does and why:** `PLAN.md`
- **Vocabulary:** `GLOSSARY.md`
- **Key decisions:** `docs/adr/`

## Setup

1. Install [uv](https://docs.astral.sh/uv/) and [Postgres.app](https://postgresapp.com/) (it includes pgvector).
2. Open Postgres.app and start the server.
3. Install dependencies, then create and migrate the database:
   ```sh
   uv sync
   uv run ideafinder db upgrade
   ```
4. Check everything:
   ```sh
   uv run ideafinder doctor
   ```

## Daily use

```sh
uv run ideafinder up      # web app on http://127.0.0.1:8765 plus both workers; Ctrl+C to stop
uv run ideafinder collect # collect new Items now (the first run backfills 12 months)
```

`ideafinder collect --since-days 3` is a trial run over a short window; it doesn't move the cursors, so the
real backfill still happens later. `--source/-s KEY` limits collection to one Source. While the workers run,
collection also happens daily at 06:00.

Stack Exchange works without a key (300 requests/day) but a free key from stackapps.com raises that to 10,000:
put `IDEAFINDER_STACKEXCHANGE_KEY=...` in `.env`.

While the workers run, the pipeline also runs on its own: extraction every 10 minutes (new Signals attach to
existing Ideas), forming new Ideas weekly (Monday 05:00), and ranking daily at 07:00. To run steps by hand:

```sh
uv run ideafinder prefilter && uv run ideafinder extract --threads 50   # Items → Signals
uv run ideafinder cluster                  # Signals → Ideas (attach, then form new ones from the pool)
uv run ideafinder fit load my_fit.yaml     # your Fit profile; start from config/fit_profile.example.yaml
uv run ideafinder rank                     # score, check fit, publish to the Inbox, propose decisions
uv run ideafinder ideas list --state inbox
uv run ideafinder ideas show ID
uv run ideafinder ideas review ID working  # or: archive --reason too_competitive / restore
uv run ideafinder census load              # US business counts for market math (once a year)
uv run ideafinder brief export ID --out prompt.md   # Briefing prompt to paste into claude.ai
uv run ideafinder brief import ID reply.md          # check and store the reply
```

Briefings are written by hand for now: moving an Idea to the Working List (or **Generate
briefing** on its page) prepares a prompt; paste it into claude.ai with web search on, then paste
the reply back on the Idea's Briefing page.

Other commands: `ideafinder web` (web app only), `ideafinder worker [--queue collect|model]` (workers only),
`ideafinder db status`, `ideafinder db revision -m "..."`.

Settings can be overridden in `.env` (see `.env.example`).

## Development

```sh
uv run pytest             # database tests use a throwaway ideafinder_test database; skipped if Postgres is down
uv run ruff check . && uv run ruff format --check .
```
