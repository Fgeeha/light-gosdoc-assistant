# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Demo-grade but deployable web app showing a local LLM doing government paperwork (Russian UI and data):
checking documents against a regulations reference, drafting replies to citizen requests, and
moving data between table layouts. FastAPI backend + React SPA behind nginx; the LLM is any
OpenAI-compatible server (Ollama on the dev machine).

## Commands

All from the repo root via `make` (it uses bash and sources `.env`; `make help` lists targets).

```bash
make install                     # uv sync (backend) + npm ci (frontend)
make run                         # backend on :8000, SQLite dev.db, LLM URL rewritten host.docker.internal→localhost
make run-frontend                # Vite on :5173, proxies /api to :8000
make check                       # ruff check + ruff format --check + tsc, then pytest
make test                        # pytest only
cd backend && uv run pytest -q tests/test_app.py::test_examples_match_source_documents   # single test
make format                      # ruff fix + format
make up-local / down-local       # build images from source and run compose (site on HTTP_PORT, default 8080)
make up-prod                     # pull ghcr.io/fgeeha/light-gosdoc-assistant-{backend,frontend}:$TAG and run
make examples                    # re-parse example source docs into examples/*.json
REPEATS=3 make bench MODELS="qwen3:14b gemma3:12b"   # model comparison on the demo samples
```

Frontend build: `cd frontend && npm run build` (runs `tsc --noEmit` first). Don't pipe it through
`grep error` to judge success — check the exit code.

Tests run against SQLite with env set in `backend/tests/conftest.py`; the LLM is monkeypatched
(`llm.stream_chat` / `llm.complete_json`), so no model server is needed for `make check`.

## Architecture

**Request flow:** browser → nginx (`frontend/nginx.conf`: SPA, `/api` proxy with buffering off for
SSE, login rate limit, security headers repeated per `location` because `add_header` doesn't
inherit) → FastAPI `backend/app` → Postgres (users only) and the LLM server.

**Auth:** JWT in an httpOnly `SameSite=Strict` cookie (`security.py`). The token carries
`User.token_version`; bumping it (logout, password change) revokes all sessions. Passwords are
checked against bcrypt's 72-*byte* limit (Cyrillic = 2 bytes/char) — exceeding it must be a 422/401,
never a 500. First admin is created on startup from `ADMIN_USERNAME`/`ADMIN_PASSWORD` if the users
table is empty. Schema is `create_all` with no migrations: adding a column requires a migration
or recreating the DB volume.

**LLM client (`app/llm.py`):** hand-rolled httpx client for `/v1/chat/completions` (streaming SSE)
and `/v1/models`. Everything model-specific is config, not code: `LLM_EXTRA_BODY` (JSON, e.g.
`{"reasoning_effort":"none"}` to disable Qwen3 thinking in Ollama), `LLM_THINK_PREFILLED` for
templates that open `<think>` themselves. `strip_think` removes reasoning across chunk boundaries;
`parse_json_object` tolerates code fences, trailing prose and bare arrays (wrapped as `{"rows": …}`).

**Tool endpoints (`routers/tools.py`)** stream SSE events: `findings`/`source`/`rows`/`progress`,
`delta` (text tokens), `error`, and always a final `done`. Errors inside the stream are sent as
`error` events (status is already 200). The frontend parser is `stream()` in `frontend/src/api.ts`.

**Document check is two-stage by design** (`services/normcheck.py`): references to laws are found
deterministically by regex against `app/data/norms.json` (status, repeal date, replacement, key
facts); the model only rewrites the text using that brief, so it can't invent a law's status.
To make the checker know a new act, add an entry with `patterns` to `norms.json` — not prompt text.
A repealed act inherits `facts` from its replacement (`replaced_by` matches a `title`). Unrecognised
law-like references are reported as `unknown`. Field reference and update procedure: `docs/NORMS.md`.

**Table transform** (`services/tables.py`): parse CSV/TSV/Excel paste (rejects duplicate/empty
headers and over-wide rows instead of losing data), send rows to the model in chunks of 15 as JSON,
normalise the answer to exactly the expected row count and columns.

**Examples are generated data.** Source of truth is `backend/app/data/examples/docs/<kind>/<id>.{docx,csv,tsv}`
(plus `check/<id>.expected.docx` — the hand-written reference correction shown in the
«Эталон юриста» tab). `scripts/build_examples.py` parses them into `examples/{check,letters,tables}.json`;
a test fails if the JSON drifts. Edit the source files, then `make examples`. Non-parsed fields
(titles, `instructions`, table `expected` rows used by the bench) live only in the JSON.

**Benchmark (`backend/scripts/bench.py`)** uses the same prompt builders as the app and scores
outputs automatically (gold rules in `CHECK_GOLD`/`LETTER_GOLD`, table `expected`). Results go to
`backend/bench-results/` (gitignored); the summary header records commit and `LLM_EXTRA_BODY`.
Only compare runs from the same commit. `docs/MODELS.md` is written from `docs/bench/*` — update
both together, and don't state numbers that aren't in a saved report. `make bench` sources `.env`,
so it overrides an `LLM_EXTRA_BODY` given on the command line; call the script directly for that.

## Deployment notes

- Compose file is `docker/docker-compose.yml`; always pass `--env-file .env` (Makefile does) or
  variable substitution falls back to defaults. `LLM_EXTRA_BODY` in `.env` must be single-quoted JSON.
- Host Ollama defaults to a 4K context, too small for long documents — run it with
  `OLLAMA_CONTEXT_LENGTH=16384` (the compose `ollama` profile already sets it).
- CI (`.github/workflows/ci.yml`): backend lint/tests, frontend build, then pushes both images to
  GHCR on `main` and `v*` tags. `cancel-in-progress` cancels superseded runs.

## Conventions

- Commit messages: Russian, Conventional Commits, no mention of AI tools or assistant trailers
  (owner's global git policy).
- UI copy, prompts, comments and docs are in Russian; identifiers in English.
- Recommended model family is Qwen3 (`qwen3:14b`, `qwen3:8b` for small GPUs); see `docs/MODELS.md`.
