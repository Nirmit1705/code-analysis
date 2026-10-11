# AI Codebase Intelligence Platform (Backend)

FastAPI + SQLAlchemy/Alembic + PostgreSQL/pgvector + Redis/Celery. This README covers **local development setup only** — getting a clean checkout of `server/` into a fully working local environment. For what's actually built and what's next, see `CLAUDE.md` and `../docs/BACKEND_IMPLEMENTATION_PLAN.md`.

PostgreSQL, Redis, the FastAPI server, and a Celery worker are the normal local backend development requirements — all four need to be running together. Smee is optional: a relay client needed only when you're working on GitHub webhook delivery, not for normal API/analysis development. Each is documented below with its exact command.

## Prerequisites

- **Python 3.12** (pinned in `.python-version`; `pyproject.toml` accepts 3.11+) and [`uv`](https://docs.astral.sh/uv/) as the package manager. This project is not installed as an importable package (`[tool.uv] package = false` in `pyproject.toml`), so every command below is run through `uv run <cmd>`, not a bare command against a manually activated venv.
- **PostgreSQL 17.x with the `pgvector` extension available**, installed natively (a native Windows install is what this project's own dev environment actually uses — see "Database" below for why the Postgres service in `docker-compose.yml` is *not* this).
- **Docker Desktop**, for running Redis locally.
- **Node.js (`npx`)**, only if you're working on GitHub webhook delivery — needed for the Smee relay client.

## One-time environment setup

1. Copy `.env.example` to `.env` and fill it in:
   - `DATABASE_URL` — point at your native Postgres install, e.g. `postgresql://postgres:password@localhost:5432/codebase_intelligence` (percent-encode special characters in the password, e.g. `@` → `%40`).
   - `REDIS_URL` — `redis://localhost:6379/0` (matches the Redis container below; no change needed).
   - `SECRET_KEY` — any random string (used to sign JWTs).
   - `TOKEN_ENCRYPTION_KEY` — a Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
   - `GITHUB_APP_ID`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_PRIVATE_KEY_PATH`, `GITHUB_WEBHOOK_SECRET` — from your GitHub App's settings page (App ID/Client ID/Client Secret/webhook secret shown there directly; `GITHUB_PRIVATE_KEY_PATH` points at the `.pem` file you download from the same page — keep it outside version control, e.g. under a gitignored `secrets/` directory).
   - `SMEE_WEBHOOK_URL` — only needed for webhook work; a channel URL from [smee.io](https://smee.io) (click "Start a new channel").
   - `LLM_API_KEY` — not required yet; Phase 8's job.
   - Embedding/reranker settings (`EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`/`EMBEDDING_DIM`/`RERANKER_PROVIDER`/`RERANKER_MODEL`) — required as of Phase 7.4/7.5; `.env.example` already carries the correct local-model values, no manual edits needed. The Celery worker calls the embedding model for real on every completed analysis run now (`app/rag/indexing.py`), not just from an opt-in smoke script — see the note under "Celery worker" below.
   - `BACKEND_URL` — this API's public base URL (default `http://localhost:8000`); "Continue with GitHub" builds its OAuth `redirect_uri` from it.
   - **Note:** `SMEE_WEBHOOK_URL` is documented in `.env.example` but `app/core/config.py::Settings` doesn't declare it (silently ignored — `extra="ignore"`); it's for the Smee command below, not read by app code.
   - **GitHub App settings for "Continue with GitHub"** (on the App's settings page; nothing to set in `.env`): add `{BACKEND_URL}/api/v1/auth/github/login/callback` (locally `http://localhost:8000/api/v1/auth/github/login/callback`) under **Callback URL**, and set **Permissions & events → Account permissions → Email addresses** to **Read-only**. Leave "Request user authorization (OAuth) during installation" off. Without the email permission sign-in still works but a person gets a `…@users.noreply.github.com` account instead of matching their existing email account.
2. Install dependencies: `uv sync` (from `server/`).

## Database

Real local development uses a **native Postgres install**, not a container. `docker-compose.yml`'s `postgres` service (mapped to host port `5433`, not `5432`) exists purely for reproducibility/documentation — nothing in this app points at it by default. Don't run it expecting it to be your dev database; use your native install (port `5432`, matching `DATABASE_URL` above).

Once `DATABASE_URL` points at a real, reachable Postgres instance, run migrations:

```
uv run alembic upgrade head
```

This also enables the `vector` extension (pgvector) automatically — it's done by the first migration, not a manual step.

There's no seed/bootstrap script. Once migrations are applied, create your first user through the running API (`POST /api/v1/auth/register`) — see "Quick sanity check" below.

## The four local processes

### 1. FastAPI server

```
uv run uvicorn app.main:app --reload
```

Serves on `http://localhost:8000` by default.

### 2. Redis

```
docker compose up -d redis
```

(Run from `server/`.) Starts the `ai-codebase-redis` container on `localhost:6379` — the Celery broker/result backend, and the backing transport for analysis-run WebSocket progress events. This is the only service from `docker-compose.yml` that local dev actually depends on; the file's `postgres` service is documentation-only (see "Database" above).

### 3. Celery worker

```
uv run celery -A app.workers.celery_app worker --loglevel=info --pool=solo
```

**`--pool=solo` is required on native Windows** — Celery's default prefork pool doesn't work there. If you're on Linux/macOS, or running the worker inside Docker/WSL, the default pool works and `--pool=solo` isn't needed.

**As of Phase 7.5**, the worker's `INDEXING` stage loads the local embedding model (`Qwen/Qwen3-Embedding-0.6B`) for real on every analysis run — the first run after a fresh checkout downloads it (~1GB) from the Hugging Face Hub (cached afterward under `%USERPROFILE%\.cache\huggingface`), so expect that first run's `INDEXING` stage to take noticeably longer than the rest of the pipeline.

### 4. Smee relay (webhook work only)

Not needed for normal API/analysis development — only when testing GitHub webhook delivery (a signed `push` to a repository's selected branch queues a re-analysis; see below).

```
npx smee-client@latest --url <your SMEE_WEBHOOK_URL from .env> --path /api/v1/webhooks/github --port 8000
```

This forwards webhook deliveries from your Smee channel to the local FastAPI server. The target route (`/api/v1/webhooks/github`, Phase 9.5) verifies the `X-Hub-Signature-256` HMAC against `GITHUB_WEBHOOK_SECRET` (which must match the secret configured on the GitHub App) and answers `401` for a bad/missing signature. A valid `push` to the repository's selected branch returns `202` and queues a `webhook`-triggered analysis run (the Celery worker must be running to process it); every other valid delivery (e.g. GitHub's `ping`, other branches/events) returns `200 {"status":"ignored",...}`.

## Quick sanity check

- API: `GET http://localhost:8000/api/v1/health` should return `200` (it also round-trips a DB query, so this confirms Postgres connectivity too).
- Celery worker: check its own startup log — it should show both `run_repository_analysis` and `ping` registered, and connect to Redis without error. No need to actually invoke a task to consider the process "up."
- To exercise the full stack, register a user (`POST /api/v1/auth/register`), then follow the GitHub App connect flow from the frontend (or `server/scripts/smoke_*.py` for backend-only checks — see `CLAUDE.md` for what each smoke script covers).

## Troubleshooting

- **Wrong Postgres port** — native dev Postgres is `5432`; the `docker-compose.yml` Postgres service (doc-only, not used by default) is `5433`. Don't mix them up in `DATABASE_URL`.
- **Redis connection refused** — make sure Docker Desktop is running before `docker compose up -d redis`.
- **Celery worker won't start / hangs on Windows** — you're likely missing `--pool=solo`; the default prefork pool isn't supported on native Windows.
- **Smee deliveries return 401** — the signature check failed: `GITHUB_WEBHOOK_SECRET` in `server/.env` doesn't match the webhook secret on the GitHub App (or is empty — an empty secret rejects everything by design).
- **Smee client fails with `Error: The provided URL is invalid.`** — if you're running the command from **Git Bash** (MSYS), its automatic Unix-path conversion silently rewrites a leading-`/` argument like `--path /api/v1/webhooks/github` into something like `C:/Program Files/Git/api/v1/webhooks/github`, which then fails URL validation. Run the Smee command from PowerShell or `cmd.exe` instead (verified working there), or, if you must use Git Bash, disable the conversion for that one call: `MSYS_NO_PATHCONV=1 npx smee-client@latest --url ... --path /api/v1/webhooks/github --port 8000`.
- **An analysis run completes but has no RAG chunks** — RAG indexing (Phase 7.5) is deliberately non-fatal: a failed embedding call (no network for the first-run model download, out of memory, etc.) is logged server-side but never fails the run — `AnalysisRun.status` still reaches `COMPLETED`. Check the worker's log for `RAG indexing failed` around that run's timestamp; the run's `rag_chunks` stay empty (or partial) until a subsequent run succeeds, and Phase 7.6+ retrieval has nothing to find in the meantime.
