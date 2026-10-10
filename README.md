# CodeLens AI

**Understand any codebase. Instantly.**

CodeLens AI is an AI-powered codebase intelligence platform. Point it at a GitHub repository and it builds a real, structural understanding of the software — architecture, dependencies, APIs, complexity, and technical debt — then layers a retrieval-grounded AI assistant on top so you can ask questions, trace features, and measure the blast radius of a change before you ship it.

![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![React](https://img.shields.io/badge/React_19-149ECA?logo=react&logoColor=white)
![Python](https://img.shields.io/badge/Python_3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL_%2B_pgvector-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis_%2B_Celery-DC382D?logo=redis&logoColor=white)

---

## The problem

A codebase past a certain size stops fitting in anyone's head:

- **Thousands of files, no single owner.** Tribal knowledge about how a system actually fits together leaves the company with whoever held it.
- **Documentation rots.** The README describes an architecture from two refactors ago; most of the real contracts were never written down at all.
- **Change impact is invisible until it isn't.** Touching one file can silently affect modules and endpoints three hops away — usually discovered in code review, sometimes in production.

CodeLens AI exists to make a codebase legible again: not by asking an LLM to summarize files from memory, but by actually parsing the code, building a dependency graph, and only then using an LLM to explain what the analysis found.

## Who it's for

| Role | What CodeLens AI gives them |
|---|---|
| **New developer** | A generated, repository-specific onboarding path instead of two weeks of shadowing |
| **Software engineer** | A grounded Q&A assistant and impact analysis instead of interrupting the module owner or guessing |
| **Senior engineer** | Evidence — coupling scores, cycles, complexity hotspots — to bring to a design review |
| **Tech lead** | A technical debt trend and a change-risk signal across analysis runs |

## Core principle

> **Algorithms determine facts → RAG retrieves the relevant context → the LLM explains and reasons over it.**

The LLM is never the source of truth for something a parser or a graph algorithm can already determine reliably. Every structural fact on screen (a file's complexity, a dependency cycle, an API route, a blast radius) comes from static analysis over the real source, persisted to Postgres. The AI layer is only invoked to retrieve and narrate that structure — every answer the assistant gives cites the real files it was derived from.

## How it works

```
 1. Connect         Authorize a GitHub App install, read-only, no code stored server-side.
 2. Analyze         The repo is ingested at an exact commit, parsed with Tree-sitter, and
                     symbols / API routes / imports / complexity are extracted per file.
 3. Build            A dependency graph is derived (cycles, coupling, orphans), technical
    intelligence      debt is scored, and the codebase is chunked, embedded and indexed.
 4. Understand       Explore the architecture graph, ask the AI assistant, trace a feature,
                     check impact before merging, and watch debt trend over time.
```

```
┌────────────────────┐        ┌──────────────────────────────────────────────┐
│   React / TanStack  │  REST  │                   FastAPI                     │
│   Start frontend     │◄──────►│  auth · github · analysis · dashboard ·       │
│  (client/)           │  WS    │  architecture · codebase · dependencies ·     │
│                      │        │  apis · debt · tracer · onboarding ·          │
│                      │        │  assistant · compare · impact · settings      │
└──────────┬───────────┘        └───────┬───────────────────┬──────────────────┘
           │                            │                   │
           │ GitHub App                 │ Celery task       │ pgvector similarity +
           │ (install / OAuth)          │ (background        │ keyword + symbol + graph
           ▼                            │  analysis run)      │ retrieval, reranked
   ┌──────────────┐                     ▼                   ▼
   │   GitHub     │            ┌────────────────┐   ┌───────────────────┐
   │ (repo, diffs)│            │ Tree-sitter     │   │ Local embedding +  │
   └──────────────┘            │ analysis        │   │ reranker models    │
                                │ pipeline        │   │ (Qwen3, on GPU)    │
                                └───────┬─────────┘   └─────────┬──────────┘
                                        │                       │
                                        ▼                       ▼
                                ┌───────────────────────────────────────┐
                                │   PostgreSQL + pgvector (facts, graph,  │
                                │   debt issues, RAG chunks) + Redis      │
                                │   (Celery broker, WS progress pub/sub)  │
                                └───────────────────┬─────────────────────┘
                                                     │
                                                     ▼
                                            ┌──────────────────┐
                                            │  Groq LLM API     │
                                            │  (Q&A, insights,  │
                                            │   change narrative)│
                                            └──────────────────┘
```

## Features

| Capability | What it does |
|---|---|
| **Architecture Explorer** | A navigable component graph (frontend / API routes / services / data stores) inferred from real imports and route definitions, not folder structure |
| **AI Codebase Assistant** | Repository-aware Q&A grounded in hybrid retrieval (vector + keyword + symbol-name + dependency-graph) over the repo's own chunks, with every answer citing real files/lines |
| **Dependency Analysis** | Circular dependencies (Tarjan SCC), module coupling, orphaned files, and outdated external packages |
| **Technical Debt** | Threshold-based rules — complexity, large files/modules, coupling, cycles, missing docs, outdated deps — rolled into one severity-weighted score, trended per analysis run, with new/resolved findings since the previous run and a grounded AI recommendation per finding |
| **API Reference** | Every route found in the source (Express, Spring Boot, FastAPI, Flask), grouped by resource, with the route declaration, middleware as written, the file's imports and what changed since the previous analysis |
| **Flow Tracer** | Pick an endpoint or any file and see the files a request can reach — route → controller → service → repository/model — computed from the real import graph, with the external services (database, cache, SDKs) it ends in and a short AI note per hop |
| **Developer Onboarding** | A repository-specific reading order, foundations first, built from the architecture components: which files to open first and why, who depends on each part, and per-user "read" progress that survives re-analysis |
| **Compare & Impact Analysis** | Diff any two completed analysis runs (added/removed/modified files, symbols, API routes, dependency edges) and compute a BFS blast-radius + deterministic risk rating for a change before you merge it |
| **Analysis History** | Every run with its trigger (push or manual), duration, file/API counts and debt score, with one-click Compare and Impact |
| **Per-file and per-component AI Insight** | On-demand, LLM-generated explanations for a file or an architecture component, grounded only in that file's or component's own analysed facts and indexed chunks |
| **Repository settings** | Choose whether a repository is re-analyzed on every push or only on request, exclude files and folders with your own ignore patterns, and start over or disconnect — each guarded by a typed confirmation |
| **Automatic re-analysis** | A signed GitHub webhook queues a new analysis whenever the tracked branch is pushed to |
| **Live Analysis Progress** | A background pipeline (`QUEUED → CLONING → PARSING → ANALYZING → BUILDING_GRAPH → INDEXING → GENERATING_INSIGHTS → COMPLETED`) streamed to the browser over WebSocket |

Nothing on a screen is a stand-in: where CodeLens AI has no data for something, the screen says so instead of showing a made-up number. The landing page's preview graph is the one place that uses sample data. See [What isn't built yet](#what-isnt-built-yet).

## Tech stack

**Frontend** (`client/`)
- React 19 + TypeScript, [TanStack Start](https://tanstack.com/start) / TanStack Router (file-based routing, SSR) + TanStack Query
- Tailwind CSS v4, Radix UI primitives, Recharts
- Vite build tooling (scaffolded by Lovable.dev)

**Backend** (`server/`)
- Python 3.12, [FastAPI](https://fastapi.tiangolo.com/), SQLAlchemy 2.0 + Alembic migrations
- PostgreSQL 17 with [pgvector](https://github.com/pgvector/pgvector) for embedding storage and HNSW similarity search
- Redis + Celery for background analysis jobs and WebSocket progress pub/sub
- [Tree-sitter](https://tree-sitter.github.io/tree-sitter/) grammars (JS/TS/TSX, Python, Java) for parsing, with framework-aware API-route extraction for Express, Spring Boot, and FastAPI/Flask
- Local embedding + reranking models (`Qwen3-Embedding-0.6B`, `Qwen3-Reranker-0.6B`) via `sentence-transformers`/`transformers`, CUDA-accelerated
- [Groq](https://groq.com/) (OpenAI-compatible, strict JSON-schema mode) for the LLM layer — the AI assistant, generated insights, and change narratives
- GitHub App integration (`httpx`) for repository access, installation tokens, and diffs

**Why this split:** deterministic facts (symbols, routes, dependency edges, complexity, debt) are computed once by the analysis pipeline and stored relationally; the RAG pipeline only retrieves relevant *context* for those facts; the LLM only ever explains or narrates — it's never asked to invent a structural claim the database could instead answer exactly.

## Repository structure

```
client/                  Frontend — React + TanStack Start (see client/CLAUDE.md for conventions)
  src/routes/             File-based routes: landing page, auth, onboarding, /app/* dashboard screens
  src/components/         UI components (architecture graph, panels, charts, code viewer, etc.)
  src/lib/                Typed API client and TanStack Query hooks per domain (sample data for the
                            landing-page preview only)

server/                  Backend — FastAPI service, independent Python project (uv-managed)
  app/api/v1/              REST endpoints (auth, github, analysis, dashboard, architecture,
                            codebase, dependencies, apis, debt, tracer, onboarding, assistant,
                            compare, impact, repository settings, webhooks)
  app/api/ws/              WebSocket analysis-progress endpoint
  app/analysis/            Pure analysis pipeline — ingestion, Tree-sitter parsing, symbol/API/
                            dependency extraction, complexity metrics, debt rules, layer
                            classification and request tracing, change diffing
  app/rag/                 Embedding, reranking, hybrid retrieval, indexing orchestration
  app/ai/                  LLM provider + prompt construction (Groq)
  app/models/, app/schemas/  SQLAlchemy models / Pydantic schemas
  app/services/, app/workers/ Persistence + read services, Celery app and background tasks
  alembic/                 Database migrations
  scripts/                 Fixture-based smoke-test scripts (no pytest suite — kept deliberately light);
                            run one with `uv run python scripts/smoke_<name>.py`
```

## Getting started

### Prerequisites

- Node.js + npm
- Python 3.12 and [`uv`](https://docs.astral.sh/uv/)
- PostgreSQL 17+ with the `pgvector` extension available
- Docker Desktop (for Redis)
- A [GitHub App](https://docs.github.com/en/apps/creating-github-apps) registration (for repository access)
- A [Groq](https://groq.com/) API key (for the AI assistant / insights)

### Frontend

```bash
cd client
npm install
npm run dev
```

### Backend

```bash
cd server
uv sync
cp .env.example .env   # fill in DATABASE_URL, GitHub App credentials, LLM_API_KEY, etc.
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

You'll also need Redis and a Celery worker running for analysis jobs:

```bash
docker compose up -d redis
uv run celery -A app.workers.celery_app worker --loglevel=info --pool=solo   # --pool=solo needed on Windows
```

Full local setup detail — environment variables, the GitHub App/webhook relay, and troubleshooting — lives in [`server/README.md`](server/README.md).

## What isn't built yet

CodeLens AI is feature-complete for the screens listed above; these are known gaps, stated plainly rather than papered over:

- **Generated documentation** — the Documentation screen is a "coming soon" placeholder with an illustration; nothing is generated yet.
- **Per-endpoint auth, request and response shapes** — the API Reference shows what the route declaration and its imports prove (path, method, middleware written on the route, handler), and says so where it can't tell whether a route is protected.
- **Frontend → backend call edges** — the architecture graph is built from imports, so a React app and an API in separate packages are not linked by an HTTP edge.
- **Function-level call graph** — the Flow Tracer works at file level: only the starting hop names a function (the route's handler).
- **One analyzed branch per repository**, and **on-push or manual** analysis only — no multi-branch tracking and no scheduled (daily) analysis.
- **Team members, notifications, API tokens and billing** — listed on the Profile screen as "not in CodeLens AI yet".
- **Proactive delivery** — for example posting the Impact result on a GitHub pull request — is planned, not built.

Backend setup, the GitHub App and webhook relay, and troubleshooting are in [`server/README.md`](server/README.md); frontend conventions are in [`client/CLAUDE.md`](client/CLAUDE.md).
