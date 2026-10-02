"""Manual smoke test for Phase 10.8's `app.services.documentation_service`
(generated Overview / Architecture / Modules / API documents).

One hand-seeded repository (same "insert directly, don't run the real pipeline"
precedent as `smoke_api_tracer.py`) whose expected numbers are summed straight
from the seed dicts below, so the assertions are independent of the service's
own code. The model is always a stub — nothing touches the real Groq API.

Seed (all Express/TypeScript unless noted): a root README and a *deeper*
`sub/README.md` (the shallowest must win), an entry file nothing imports
(`server.ts`), `client/src/main.tsx`, `app.ts` imported only by a test (still an
entry point), a barrel `index.ts` that `server.ts` imports (not one), a two-file
cycle (`utils/a.ts` <-> `utils/b.ts`), a server `Services` and a client `Services`
(same label, so both must be disambiguated), nine endpoints in two route files, a
database import (-> PostgreSQL) and generic packages (hidden).

Gating groups (offline, real DB):
1. `run_blocks()` — each type's blocks contain the seeded facts and only them:
   exact numbers, lists and lines; no model call for Architecture/API; prompt
   grounding for Overview/Modules (shallowest README only, capped endpoints).
2. `run_persistence()` — one row per (repository, type); regenerate replaces
   (no duplicate, newer `generated_at`, new content, `analysis_run_id` follows a
   newer run); the enum is stored by member name; `list_documents` order; the
   documents go with their run.
3. `run_model_failures()` — every model failure mode still stores the document
   with `ai_written=False` and every deterministic fact; a partial Modules
   answer keeps the valid entries; the 429 wait-and-retry (retries once when the
   advised wait is short, never when it is long or the error is something else).
4. `run_no_readme_and_not_analyzed()` — no README -> no AI paragraph and no
   model call; no completed run (or only a QUEUED one) -> `NotAnalyzedError`
   and nothing stored.
5. `run_isolation()` — another repository's data and an older run's data never
   appear.

Group 6 (non-gating) generates all four for HireSight with the real model and
checks the stats against raw SQL.

Run: `uv run python scripts/smoke_documentation_service.py` (from `server/`).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.ai.provider import LLMProviderError  # noqa: E402
from app.analysis.apis import Framework  # noqa: E402
from app.analysis.chunking import RagChunkType  # noqa: E402
from app.analysis.debt import DebtIssueType, DebtSeverity  # noqa: E402
from app.analysis.dependencies import DependencyEdgeType  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.api_endpoint import ApiEndpoint  # noqa: E402
from app.models.debt_issue import DebtIssue  # noqa: E402
from app.models.dependency import DependencyEdge as DependencyEdgeRow  # noqa: E402
from app.models.file import File  # noqa: E402
from app.models.generated_document import DocType, GeneratedDocument  # noqa: E402
from app.models.rag_chunk import RagChunk  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import analysis_read_service, documentation_service as ds  # noqa: E402
from smoke_storage import _clear_previous_runs, _get_or_create_repository, _get_or_create_user  # noqa: E402

# The failure groups provoke the service's own warning/exception logs on purpose.
logging.getLogger("app.services.documentation_service").setLevel(logging.CRITICAL)

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "HireSight"

TS, TSX = Language.TYPESCRIPT, Language.TSX

FILES: dict[str, tuple[Language | None, int]] = {
    "README.md": (None, 10),
    "sub/README.md": (None, 6),
    "server/src/server.ts": (TS, 30),
    "server/src/app.ts": (TS, 15),
    "server/src/index.ts": (TS, 5),
    "server/src/utils/a.ts": (TS, 12),
    "server/src/utils/b.ts": (TS, 8),
    "server/src/routes/users.ts": (TS, 40),
    "server/src/routes/health.ts": (TS, 8),
    "server/src/controllers/userController.ts": (TS, 70),
    "server/src/services/userService.ts": (TS, 120),
    "server/__tests__/user.test.ts": (TS, 25),
    "client/src/main.tsx": (TSX, 20),
    "client/src/App.tsx": (TSX, 50),
    "client/src/services/api.ts": (TS, 30),
}
EDGES = [
    ("server/src/server.ts", "server/src/routes/users.ts"),
    ("server/src/server.ts", "server/src/index.ts"),
    ("server/src/index.ts", "server/src/utils/a.ts"),
    ("server/src/utils/a.ts", "server/src/utils/b.ts"),
    ("server/src/utils/b.ts", "server/src/utils/a.ts"),
    ("server/src/routes/users.ts", "server/src/controllers/userController.ts"),
    ("server/src/controllers/userController.ts", "server/src/services/userService.ts"),
    ("server/__tests__/user.test.ts", "server/src/services/userService.ts"),
    ("server/__tests__/user.test.ts", "server/src/app.ts"),
    ("client/src/main.tsx", "client/src/App.tsx"),
    ("client/src/App.tsx", "client/src/services/api.ts"),
]
EXTERNAL = [
    ("server/src/services/userService.ts", "@prisma/client"),
    ("server/src/controllers/userController.ts", "express"),
    ("client/src/services/api.ts", "axios"),
]
# (method, path, file, handler_name, middleware, line)
ENDPOINTS = [
    ("GET", "/api/users", "server/src/routes/users.ts", "listUsers", ["auth"], 10),
    ("POST", "/api/users", "server/src/routes/users.ts", "<inline>", ["auth", "validate"], 20),
    ("GET", "/api/users/:id", "server/src/routes/users.ts", "getUser", [], 30),
    ("PUT", "/api/users/:id", "server/src/routes/users.ts", "updateUser", ["auth"], 40),
    ("DELETE", "/api/users/:id", "server/src/routes/users.ts", "deleteUser", ["auth"], 50),
    ("GET", "/api/users/:id/orders", "server/src/routes/users.ts", "listOrders", [], 60),
    ("POST", "/api/orders", "server/src/routes/users.ts", "<inline>", [], 70),
    ("GET", "/api/orders", "server/src/routes/users.ts", "listAllOrders", [], 80),
    ("GET", "/health", "server/src/routes/health.ts", "health", None, 5),
]
README_CHUNKS = {
    "README.md": ["# Acme Orders\n\nAcme Orders is a small demo order service.", "## Setup\n\nRun npm install."],
    "sub/README.md": ["SUB_README_ONLY_TEXT must never be shown."],
}
# (type, severity, file or None, description)
DEBT = [
    (DebtIssueType.HIGH_COMPLEXITY, DebtSeverity.HIGH, "server/src/services/userService.ts", "complexity 31"),
    (DebtIssueType.LARGE_FILE, DebtSeverity.MEDIUM, "server/src/controllers/userController.ts", "70 lines"),
    (DebtIssueType.MISSING_DOCUMENTATION, DebtSeverity.LOW, None, "documentation coverage 10%"),
]
DEBT_SCORE = 3.0 + 2.0 + 1.0


class _Stub:
    """A fake `LLMProvider` that answers by the schema it is given: README ->
    `{summary}`, Modules -> `{modules:[{id, summary}]}` for every component id
    found in the prompt (or only `only_ids`). Records every user message."""

    def __init__(self, *, summary="Acme Orders is a demo order service.", only_ids=None, error=None, raw=None, fail_times=0):
        self.summary, self.only_ids, self.error, self.raw = summary, only_ids, error, raw
        self.fail_times = fail_times
        self.calls: list[str] = []
        self.attempts = 0

    def complete(self, *, system: str, messages: list[dict], response_schema: dict | None = None) -> str:
        self.attempts += 1
        self.calls.append(messages[0]["content"])
        if self.attempts <= self.fail_times:
            raise self.error
        if self.error is not None and self.fail_times == 0:
            raise self.error
        if self.raw is not None:
            return self.raw
        props = (response_schema or {}).get("properties", {})
        if "summary" in props:
            return json.dumps({"summary": self.summary})
        ids = re.findall(r"^\[(.+?)\] ", messages[0]["content"], flags=re.M)
        if self.only_ids is not None:
            ids = [i for i in ids if i in self.only_ids]
        return json.dumps({"modules": [{"id": i, "summary": f"{i} appears to be a part of the system."} for i in ids]})


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def n(i: int) -> str:
    return f"{i:,}"


# --- seeding ---------------------------------------------------------------------------


def _seed(
    db: Session,
    repository: Repository,
    *,
    commit_sha="d" * 40,
    status=AnalysisRunStatus.COMPLETED,
    completed_ago_days=0,
    files=FILES,
    edges=EDGES,
    external=EXTERNAL,
    endpoints=ENDPOINTS,
    readme_chunks=README_CHUNKS,
    debt=DEBT,
    debt_score=DEBT_SCORE,
) -> AnalysisRun:
    run = AnalysisRun(
        repository_id=repository.id,
        branch="main",
        commit_sha=commit_sha,
        status=status,
        file_count=len(files),
        module_count=len({str(PurePosixPath(p).parent) for p in files}),
        api_count=len(endpoints),
        dependency_count=len(edges) + len(external),
        debt_score=debt_score,
        database_provider="postgresql",
        completed_at=datetime.now(timezone.utc) - timedelta(days=completed_ago_days)
        if status is AnalysisRunStatus.COMPLETED
        else None,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    file_by_path: dict[str, File] = {}
    for path, (language, loc) in files.items():
        row = File(
            repository_id=repository.id, analysis_run_id=run.id, path=path, language=language, size_bytes=100,
            module=str(PurePosixPath(path).parent), loc=loc, last_analyzed_commit_sha=commit_sha,
        )
        db.add(row)
        db.flush()
        file_by_path[path] = row
    for source, target in edges:
        db.add(DependencyEdgeRow(
            repository_id=repository.id, analysis_run_id=run.id, source_file_id=file_by_path[source].id,
            target_file_id=file_by_path[target].id, edge_type=DependencyEdgeType.INTERNAL,
        ))
    for source, package in external:
        db.add(DependencyEdgeRow(
            repository_id=repository.id, analysis_run_id=run.id, source_file_id=file_by_path[source].id,
            target_package_name=package, edge_type=DependencyEdgeType.EXTERNAL,
        ))
    for method, path, file_path, handler, middleware, line in endpoints:
        db.add(ApiEndpoint(
            repository_id=repository.id, analysis_run_id=run.id, method=method, path=path,
            file_id=file_by_path[file_path].id, module=file_by_path[file_path].module, framework=Framework.EXPRESS,
            handler_name=handler, handler_line_start=line, middleware=middleware,
        ))
    for file_path, chunks in readme_chunks.items():
        if file_path not in file_by_path:
            continue
        for i, chunk_text in enumerate(chunks):
            db.add(RagChunk(
                repository_id=repository.id, analysis_run_id=run.id, file_id=file_by_path[file_path].id,
                chunk_type=RagChunkType.DOCUMENTATION, chunk_index=i, chunk_count=len(chunks),
                # The chunker prefixes every chunk with this header line; the service must strip it.
                chunk_text=f"[DOCUMENTATION] {file_path}\n\n{chunk_text}",
                line_start=1, line_end=2, module=file_by_path[file_path].module, chunk_metadata={},
                embedding=[0.0] * 1024,
            ))
    for issue_type, severity, file_path, description in debt:
        db.add(DebtIssue(
            analysis_run_id=run.id, issue_type=issue_type, severity=severity,
            file_id=file_by_path[file_path].id if file_path else None,
            module=str(PurePosixPath(file_path).parent) if file_path else None, description=description, metric_value=1.0,
        ))
    db.commit()
    db.refresh(run)
    return run


def _repo(db: Session, name: str):
    user = _get_or_create_user(db, f"smoke-docs-{name}@local.test")
    repository = _get_or_create_repository(db, user, github_repo_id=f"smoke-docs-{name}", owner="smoke-docs", name=name)
    _clear_previous_runs(db, repository)
    db.execute(text("delete from generated_documents where repository_id = :r"), {"r": repository.id})
    db.commit()
    return user, repository


def _lines(blocks, heading):
    block = next((b for b in blocks if b.get("h") == heading), None)
    return block.get("list", []) if block else None


def _all_text(blocks) -> str:
    parts = []
    for b in blocks:
        parts += [b.get("h", ""), b.get("p", ""), *b.get("list", []), b.get("code", "")]
    return "\n".join(parts)


# --- group 1 ------------------------------------------------------------------------------


def run_blocks() -> bool:
    print("=== group 1 (gating): each type's blocks hold the seeded facts, and only them ===")
    db = SessionLocal()
    try:
        _user, repo = _repo(db, "blocks")
        _seed(db, repo)
        stub = _Stub()
        ok = True

        # --- overview
        doc = ds.generate_document(db, repo, DocType.OVERVIEW, provider=stub)
        blocks = doc.content["blocks"]
        total_loc = sum(loc for _lang, loc in FILES.values())
        supported = sum(1 for lang, _ in FILES.values() if lang is not None)
        ok &= _check("overview: one model call, grounded in the SHALLOWEST README only",
                     len(stub.calls) == 1 and "Acme Orders is a small demo order service." in stub.calls[0]
                     and "Run npm install." in stub.calls[0] and "SUB_README_ONLY_TEXT" not in stub.calls[0]
                     and "README file: README.md" in stub.calls[0], stub.calls[0][:200])
        ok &= _check("overview: the chunker's '[DOCUMENTATION] <path>' header lines are stripped from what the model sees",
                     "[DOCUMENTATION]" not in stub.calls[0], stub.calls[0][:200])
        ok &= _check("overview: first block is the AI paragraph, flagged, headed with the README path",
                     blocks[0] == {"h": "What README.md says", "p": "Acme Orders is a demo order service.", "ai": True}, str(blocks[0]))
        glance = _lines(blocks, "At a glance")
        modules = len({str(PurePosixPath(p).parent) for p in FILES})
        components = 9  # (root), sub, server/src, routes, controllers, services, __tests__, client/src, client/services
        expected_glance = [
            f"{len(FILES)} files analyzed, {n(total_loc)} lines of code",
            f"{supported} files in a supported language (JavaScript, TypeScript, Python or Java)",
            f"{modules} modules and {components} architecture components",
            f"{len(ENDPOINTS)} API endpoints (Express)",
            "1 circular dependency cycle",
            f"{len(DEBT)} technical debt findings (score {DEBT_SCORE:g})",
            "Database: PostgreSQL, read from the project's schema file",
        ]
        ok &= _check("overview: 'At a glance' is exactly the seeded counts", glance == expected_glance, f"{glance} vs {expected_glance}")
        ts_files = [loc for lang, loc in FILES.values() if lang is TS]
        tsx_files = [loc for lang, loc in FILES.values() if lang is TSX]
        parsed = sum(ts_files) + sum(tsx_files)
        expected_langs = [
            f"TypeScript: {len(ts_files)} files, {sum(ts_files)} lines ({sum(ts_files) * 100 / parsed:.1f}% of source lines)",
            f"TSX: {len(tsx_files)} files, {sum(tsx_files)} lines ({sum(tsx_files) * 100 / parsed:.1f}% of source lines)",
        ]
        ok &= _check("overview: languages by lines, README files excluded", _lines(blocks, "Languages") == expected_langs, str(_lines(blocks, "Languages")))
        ok &= _check("overview: entry points = named like one AND not imported by a non-test file",
                     _lines(blocks, "Likely entry points") == ["client/src/main.tsx", "server/src/app.ts", "server/src/server.ts"],
                     str(_lines(blocks, "Likely entry points")))
        ok &= _check("overview: ai_written, and a source line naming the README, the short sha and the branch",
                     doc.ai_written is True and doc.content["source"]
                     == "Analysis of 15 files at ddddddd on main · README: README.md summarised by AI", doc.content["source"])

        # --- architecture
        before = len(stub.calls)
        doc = ds.generate_document(db, repo, DocType.ARCHITECTURE, provider=stub)
        blocks = doc.content["blocks"]
        ok &= _check("architecture: makes no model call and is not AI-written", len(stub.calls) == before and doc.ai_written is False
                     and not any(b.get("ai") for b in blocks))
        comp = _lines(blocks, "Components")
        ok &= _check(f"architecture: {components} components listed; the two 'Services' are told apart by id",
                     len(comp) == components and any(c.startswith("Services (server/services):") for c in comp)
                     and any(c.startswith("Services (client/services):") for c in comp), str(comp))
        ok &= _check("architecture: API Routes carries its 9 endpoints", any(c.startswith("API Routes:") and c.endswith("9 endpoints") for c in comp))
        svc = _lines(blocks, "External services")
        ok &= _check("architecture: PostgreSQL named and attributed to the server Services only",
                     svc == ["PostgreSQL (data), used by Services (server/services)"], str(svc))
        text_all = _all_text(blocks)
        ok &= _check("architecture: generic packages (express, axios) are not listed as services",
                     "express" not in text_all.lower() and "axios" not in text_all.lower()
                     and "Other third-party packages that are not individually recognised are imported by" in text_all)
        cyc = _lines(blocks, "Circular dependencies")
        ok &= _check("architecture: the a<->b cycle is called out, with its component",
                     cyc == ["2 files import each other in a loop: a.ts, b.ts (in server/src)"], str(cyc))
        links = _lines(blocks, "How components depend on each other")
        ok &= _check("architecture: component links use the shared-import counts and the disambiguated labels",
                     "Controllers depends on Services (server/services) (1 import)" in links
                     and "API Routes depends on Controllers (1 import)" in links, str(links))

        # --- modules
        stub = _Stub()
        doc = ds.generate_document(db, repo, DocType.MODULES, provider=stub)
        blocks = doc.content["blocks"]
        ok &= _check("modules: one batched call for all components", len(stub.calls) == 1)
        ok &= _check(f"modules: one block per internal component ({components}), no block for the external service",
                     len(blocks) == components and not any(b["h"] == "PostgreSQL" for b in blocks), str([b["h"] for b in blocks]))
        ok &= _check("modules: every block carries a flagged AI one-liner for its own id",
                     all(b.get("ai") is True and b["p"].endswith("appears to be a part of the system.") for b in blocks)
                     and doc.ai_written is True)
        api_block = next(b for b in blocks if b["h"] == "API Routes")
        ok &= _check("modules: API Routes facts: kind, files, lines, endpoint count with 3 examples",
                     api_block["list"][0] == "API / gateway: 2 files, 48 lines"
                     and api_block["list"][2] == "9 endpoints: DELETE /api/users/:id, GET /api/orders, GET /api/users…",
                     str(api_block["list"]))
        ok &= _check("modules: the prompt caps a component's endpoints at 6 with '(+3 more)'", "(+3 more)" in stub.calls[0])
        services = next(b for b in blocks if b["h"] == "Services (server/services)")
        ok &= _check("modules: Services lists its dependency, importers, and its debt findings",
                     any(line == "Depends on: PostgreSQL (1 import)" for line in services["list"])
                     and any(line.startswith("Used by: Controllers (1 import)") for line in services["list"])
                     and any(line == "1 technical debt finding (worst: high)" for line in services["list"]), str(services["list"]))
        src = next(b for b in blocks if b["h"] == "server/src")
        ok &= _check("modules: the cycle files are counted on their component", "2 files of this component are part of a circular dependency" in src["list"], str(src["list"]))

        # --- api
        before = len(stub.calls)
        doc = ds.generate_document(db, repo, DocType.API, provider=stub)
        blocks = doc.content["blocks"]
        ok &= _check("api: makes no model call and is not AI-written", len(stub.calls) == before and doc.ai_written is False)
        ok &= _check("api: grouped by resource, groups in order",
                     [b.get("h") for b in blocks[1:]] == ["/api/orders", "/api/users", "/health"], str([b.get("h") for b in blocks]))
        ok &= _check("api: /api/users lines exact (handler, 'inline callback', middleware names, file:line, sort order)",
                     _lines(blocks, "/api/users") == [
                         "GET /api/users → listUsers (middleware: auth) · server/src/routes/users.ts:10",
                         "POST /api/users → an inline callback (middleware: auth, validate) · server/src/routes/users.ts:20",
                         "DELETE /api/users/:id → deleteUser (middleware: auth) · server/src/routes/users.ts:50",
                         "GET /api/users/:id → getUser · server/src/routes/users.ts:30",
                         "PUT /api/users/:id → updateUser (middleware: auth) · server/src/routes/users.ts:40",
                         "GET /api/users/:id/orders → listOrders · server/src/routes/users.ts:60",
                     ], str(_lines(blocks, "/api/users")))
        ok &= _check("api: a route with no recorded middleware shows none, and the intro states what is not extracted",
                     _lines(blocks, "/health") == ["GET /health → health · server/src/routes/health.ts:5"]
                     and "9 endpoints in 3 resource groups" in blocks[0]["p"] and "Request and response shapes" in blocks[0]["p"])

        original = ds.MAX_API_ENDPOINTS
        ds.MAX_API_ENDPOINTS = 4
        try:
            capped = ds.build_api_blocks(ds.load_facts(db, repo))
        finally:
            ds.MAX_API_ENDPOINTS = original
        listed = sum(len([line for line in b.get("list", []) if not line.startswith("…")]) for b in capped)
        ok &= _check("api: the endpoint cap is enforced and the omission is stated", listed == 4 and "5 more endpoints are not listed here" in _all_text(capped), str(capped[-1]))
        return ok
    finally:
        db.close()


# --- group 2 ------------------------------------------------------------------------------


def run_persistence() -> bool:
    print("=== group 2 (gating): one row per section, upsert, stored by member name ===")
    db = SessionLocal()
    try:
        _user, repo = _repo(db, "persist")
        run1 = _seed(db, repo, commit_sha="1" * 40, completed_ago_days=1)
        ok = True
        for t in DocType:
            ds.generate_document(db, repo, t, provider=_Stub(summary="First version."))
        docs = ds.list_documents(db, repo.id)
        ok &= _check("four rows, one per type, in DocType order",
                     [d.doc_type for d in docs] == [DocType.OVERVIEW, DocType.ARCHITECTURE, DocType.MODULES, DocType.API])
        ok &= _check("each row records the run it was built from", all(d.analysis_run_id == run1.id for d in docs))
        names = [r[0] for r in db.execute(text("select doc_type from generated_documents where repository_id=:r order by doc_type"), {"r": repo.id}).all()]
        ok &= _check("the enum is stored by member NAME (Known Issue #15)", names == ["API", "ARCHITECTURE", "MODULES", "OVERVIEW"], str(names))
        ok &= _check("content round-trips as {blocks, source}", set(docs[0].content) == {"blocks", "source"} and isinstance(docs[0].content["blocks"], list))

        first = ds.get_document(db, repo.id, DocType.OVERVIEW)
        first_at, first_id = first.generated_at, first.id
        ds.generate_document(db, repo, DocType.OVERVIEW, provider=_Stub(summary="Second version."))
        again = ds.get_document(db, repo.id, DocType.OVERVIEW)
        count = db.execute(text("select count(*) from generated_documents where repository_id=:r and doc_type='OVERVIEW'"), {"r": repo.id}).scalar()
        ok &= _check("regenerating replaces: still exactly one OVERVIEW row, same id", count == 1 and again.id == first_id)
        ok &= _check("the new content replaced the old and generated_at moved forward",
                     again.content["blocks"][0]["p"] == "Second version." and again.generated_at > first_at)

        run2 = _seed(db, repo, commit_sha="2" * 40)
        ok &= _check("sanity: the new run is the latest completed one", analysis_read_service.get_latest_completed_run(db, repo.id).id == run2.id)
        ds.generate_document(db, repo, DocType.OVERVIEW, provider=_Stub())
        ok &= _check("regenerating after a re-analysis moves analysis_run_id to the newer run (that difference is what makes a doc stale)",
                     ds.get_document(db, repo.id, DocType.OVERVIEW).analysis_run_id == run2.id
                     and ds.get_document(db, repo.id, DocType.API).analysis_run_id == run1.id)

        db.execute(text("delete from analysis_runs where id = :r"), {"r": run1.id})
        db.commit()
        left = {d.doc_type for d in ds.list_documents(db, repo.id)}
        ok &= _check("deleting a run deletes the documents built from it (ON DELETE CASCADE)",
                     left == {DocType.OVERVIEW}, str(left))
        return ok
    finally:
        db.close()


# --- group 3 ------------------------------------------------------------------------------


def run_model_failures() -> bool:
    print("=== group 3 (gating): generation never fails because the model did ===")
    db = SessionLocal()
    try:
        _user, repo = _repo(db, "failures")
        _seed(db, repo)
        facts = ds.load_facts(db, repo)
        sleeps: list[float] = []
        original_sleep = ds._sleep
        ds._sleep = lambda s: sleeps.append(s)
        ok = True
        try:
            failures = {
                "LLMProviderError": _Stub(error=LLMProviderError("boom")),
                "RuntimeError (misconfigured provider)": _Stub(error=RuntimeError("LLM_PROVIDER not supported")),
                "unexpected exception": _Stub(error=ValueError("weird")),
                "not JSON": _Stub(raw="Here is the summary"),
                "JSON but a list": _Stub(raw="[1]"),
                "wrong keys": _Stub(raw=json.dumps({"other": 1})),
                "empty text": _Stub(raw=json.dumps({"summary": "   ", "modules": []})),
                "non-string text": _Stub(raw=json.dumps({"summary": 5, "modules": "x"})),
            }
            for label, stub in failures.items():
                ov = ds.generate_document(db, repo, DocType.OVERVIEW, provider=stub)
                md = ds.generate_document(db, repo, DocType.MODULES, provider=stub)
                det = _lines(ov.content["blocks"], "At a glance") == _lines(ds.build_overview_blocks(facts, None), "At a glance")
                ok &= _check(
                    f"{label}: both documents stored, ai_written=False, no ai block, every fact present",
                    ov.ai_written is False and md.ai_written is False and not any(b.get("ai") for b in ov.content["blocks"] + md.content["blocks"])
                    and det and len(md.content["blocks"]) == 9 and all("list" in b for b in md.content["blocks"]),
                )
                ok &= _check(f"{label}: the source line says the AI text is unavailable",
                             "AI summary unavailable" in ov.content["source"] and "AI role lines unavailable" in md.content["source"],
                             f"{ov.content['source']} | {md.content['source']}")

            # partial Modules answer: keep valid entries, drop the rest
            partial = _Stub(only_ids={"server/services", "client/src"})
            md = ds.generate_document(db, repo, DocType.MODULES, provider=partial)
            with_ai = sorted(b["h"] for b in md.content["blocks"] if b.get("ai"))
            ok &= _check("partial answer: only the answered components get an AI line; the rest are still documented",
                         with_ai == ["Services (server/services)", "client/src"] and len(md.content["blocks"]) == 9 and md.ai_written is True, str(with_ai))
            messy = _Stub(raw=json.dumps({"modules": [
                {"id": "server/services", "summary": "Real‑one here."}, {"id": "server/services", "summary": "duplicate"},
                {"id": "nope", "summary": "unknown id"}, {"id": 4, "summary": "bad id"}, {"id": "client/src", "summary": " "},
                {"id": "server/src", "summary": "x" * 400}, "junk",
            ]}))
            md = ds.generate_document(db, repo, DocType.MODULES, provider=messy)
            got = {b["h"]: b["p"] for b in md.content["blocks"] if b.get("ai")}
            ok &= _check("garbled entries dropped; odd spaces and no-break hyphens normalised; long text cut to 300",
                         set(got) == {"Services (server/services)", "server/src"} and got["Services (server/services)"] == "Real-one here."
                         and len(got["server/src"]) == 300, str(got))

            # 429 handling
            rate = _Stub(error=LLMProviderError('Groq API error (429): {"message":"... Please try again in 2.5s. Need more tokens?"}'), fail_times=1)
            ov = ds.generate_document(db, repo, DocType.OVERVIEW, provider=rate)
            ok &= _check("a short 429 is waited out (advised + 0.5s) and retried ONCE, then the AI paragraph is present",
                         sleeps == [3.0] and rate.attempts == 2 and ov.ai_written is True, f"sleeps={sleeps} attempts={rate.attempts}")
            sleeps.clear()
            long_wait = _Stub(error=LLMProviderError("Groq API error (429): Please try again in 45.0s."))
            ov = ds.generate_document(db, repo, DocType.OVERVIEW, provider=long_wait)
            ok &= _check("a long 429 is not waited for: one attempt, no sleep, document still stored facts-only",
                         sleeps == [] and long_wait.attempts == 1 and ov.ai_written is False)
            other = _Stub(error=LLMProviderError("Groq API error (500): server error try again in 2s"))
            ds.generate_document(db, repo, DocType.OVERVIEW, provider=other)
            ok &= _check("a non-429 error is never retried", sleeps == [] and other.attempts == 1)
            twice = _Stub(error=LLMProviderError("Groq API error (429): Please try again in 1.0s."), fail_times=5)
            ds.generate_document(db, repo, DocType.OVERVIEW, provider=twice)
            ok &= _check("a second 429 is not retried again (bounded at one retry)", twice.attempts == 2, str(twice.attempts))
        finally:
            ds._sleep = original_sleep
        return ok
    finally:
        db.close()


# --- group 4 ------------------------------------------------------------------------------


def run_no_readme_and_not_analyzed() -> bool:
    print("=== group 4 (gating): no README; nothing analyzed ===")
    db = SessionLocal()
    try:
        _user, repo = _repo(db, "noreadme")
        _seed(db, repo, readme_chunks={})
        stub = _Stub()
        doc = ds.generate_document(db, repo, DocType.OVERVIEW, provider=stub)
        blocks = doc.content["blocks"]
        ok = _check("no README: the model is not called", stub.calls == [])
        ok &= _check("no README: no AI paragraph, ai_written False, and the source says so",
                     not any(b.get("ai") for b in blocks) and blocks[0]["h"] == "At a glance" and doc.ai_written is False
                     and "no README found" in doc.content["source"], doc.content["source"])

        _user2, repo2 = _repo(db, "emptyreadme")
        _seed(db, repo2, readme_chunks={"README.md": ["   "]})
        stub2 = _Stub()
        doc2 = ds.generate_document(db, repo2, DocType.OVERVIEW, provider=stub2)
        ok &= _check("a README whose stored chunks are blank counts as no README", stub2.calls == [] and "no README found" in doc2.content["source"])

        _user3, repo3 = _repo(db, "none")
        stored = lambda: db.execute(text("select count(*) from generated_documents where repository_id=:r"), {"r": repo3.id}).scalar()  # noqa: E731
        for label in ("no run at all", "only a QUEUED run"):
            if label != "no run at all":
                _seed(db, repo3, status=AnalysisRunStatus.QUEUED)
            for t in DocType:
                try:
                    ds.generate_document(db, repo3, t, provider=_Stub())
                    raised = False
                except ds.NotAnalyzedError:
                    raised = True
                ok &= _check(f"{label}: {t.value} raises NotAnalyzedError", raised)
            ok &= _check(f"{label}: nothing was stored", stored() == 0)
        return ok
    finally:
        db.close()


# --- group 5 ------------------------------------------------------------------------------


def run_isolation() -> bool:
    print("=== group 5 (gating): another repository's and an older run's data never appear ===")
    db = SessionLocal()
    try:
        _user, repo = _repo(db, "isolation")
        old_files = {**FILES, "legacy/old_only.ts": (TS, 99)}
        _seed(db, repo, commit_sha="a" * 40, completed_ago_days=3, files=old_files,
              endpoints=[*ENDPOINTS, ("GET", "/legacy-only", "server/src/routes/health.ts", "old", [], 1)],
              readme_chunks={"README.md": ["OLD_RUN_README_TEXT"]})
        _seed(db, repo)
        _user2, other = _repo(db, "isolation-other")
        _seed(db, other, endpoints=[("GET", "/other-repo-only", "server/src/routes/health.ts", "x", [], 1)],
              readme_chunks={"README.md": ["OTHER_REPO_README_TEXT"]})
        ok = True
        stub = _Stub()
        for t in DocType:
            doc = ds.generate_document(db, repo, t, provider=stub)
            text_all = _all_text(doc.content["blocks"]) + doc.content["source"]
            ok &= _check(f"{t.value}: no 'legacy', no other repository's endpoint", "legacy" not in text_all and "other-repo-only" not in text_all)
        ok &= _check("the model was never shown the older run's or the other repository's README",
                     not any("OLD_RUN_README_TEXT" in c or "OTHER_REPO_README_TEXT" in c for c in stub.calls))
        ok &= _check("the other repository has no stored document from this repository's generation", ds.list_documents(db, other.id) == [])
        return ok
    finally:
        db.close()


# --- group 6 (non-gating) ---------------------------------------------------------------------


def run_live_bonus() -> bool:
    print("\n=== group 6 (non-gating): HireSight, real model — stats vs raw SQL ===")
    db = SessionLocal()
    try:
        best = None
        for r in db.execute(select(Repository).where(Repository.owner == LIVE_TEST_OWNER, Repository.name == LIVE_TEST_REPO)).scalars().all():
            run = analysis_read_service.get_latest_completed_run(db, r.id)
            if run is not None and (best is None or (run.completed_at or run.created_at) > (best[1].completed_at or best[1].created_at)):
                best = (r, run)
        if best is None:
            print("  [SKIP] no HireSight repository with a completed run in this database")
            return True
        repo, run = best
        docs = {t: ds.generate_document(db, repo, t) for t in DocType}
        sql = lambda q: db.execute(text(q), {"r": run.id}).scalar()  # noqa: E731
        files, loc = sql("select count(*) from files where analysis_run_id=:r"), sql("select coalesce(sum(loc),0) from files where analysis_run_id=:r")
        eps, debt = sql("select count(*) from api_endpoints where analysis_run_id=:r"), sql("select count(*) from debt_issues where analysis_run_id=:r")
        glance = _lines(docs[DocType.OVERVIEW].content["blocks"], "At a glance")
        print(f"  info: ai_written={ {t.value: d.ai_written for t, d in docs.items()} }")
        ok = _check("overview: file count, line count, endpoints and debt findings equal raw SQL",
                    glance[0] == f"{files} files analyzed, {int(loc):,} lines of code" and f"{eps} API endpoints" in glance[3]
                    and glance[5].startswith(f"{debt} technical debt findings"), str(glance))
        api_lines = sum(len(b.get("list", [])) for b in docs[DocType.API].content["blocks"][1:])
        ok &= _check("api: the listed endpoint lines equal COUNT(*) from api_endpoints", api_lines == eps, f"{api_lines} vs {eps}")
        modules = [b for b in docs[DocType.MODULES].content["blocks"]]
        ok &= _check("modules: every block has facts; AI lines (if any) are flagged", all("list" in b for b in modules))
        return ok
    finally:
        db.close()


def main() -> int:
    gating = [run_blocks(), run_persistence(), run_model_failures(), run_no_readme_and_not_analyzed(), run_isolation()]
    bonus = run_live_bonus()
    print("\n" + ("ALL GATING GROUPS PASS" if all(gating) else "SOME GATING GROUPS FAILED") + ("" if bonus else " (live bonus failed)"))
    return 0 if all(gating) else 1


if __name__ == "__main__":
    sys.exit(main())
