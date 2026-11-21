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

Other commands: `ideafinder web` (web app only), `ideafinder worker [--queue collect|model]` (workers only),
`ideafinder db status`, `ideafinder db revision -m "..."`.

Settings can be overridden in `.env` (see `.env.example`).

## Development

```sh
uv run pytest             # database tests use a throwaway ideafinder_test database; skipped if Postgres is down
uv run ruff check . && uv run ruff format --check .
```
