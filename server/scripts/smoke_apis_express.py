"""Manual smoke test for Phase 3.6 (Express API extraction).

Two fixture scenarios under `scripts/fixtures/express/`, both run through
`compose_routes` together in one pass (distinct file paths, so no key
collisions):

1. CommonJS: `app.js` (`app.get`/`app.post`, one named handler + one
   inline handler) mounting `routes/users.js` (`Router()` via destructured
   `require`, all five verbs) at `/api/users` via `app.use('/api/users',
   usersRouter)` — exercises cross-file mount resolution through
   `require`/`module.exports`.
2. ESM: `esm_app.mjs` mounting `esm_router.mjs` at `/api/items` via
   `import`/`export default` instead of CommonJS.
3. `frontend_http_client.js` — regression fixture for the false positive
   found during Phase 6.5's real dogfooding against `Nirmit1705/HireSight`:
   `axios.get/post(...)` calls (plus a variable literally named `router`
   bound to something unrelated) share the exact same `.get/.post(path,
   ...)` call shape as a real Express route, but none of these identifiers
   are ever bound via `express()`/`express.Router()`. Asserts this fixture
   contributes zero routes and that the real-world false-positive paths
   (`/token`, `/aptitude/start`, `/aptitude/history`) never appear.

No real spot-check against this repo's own source: the frontend is
React/TanStack, not Express — there's no real Express code in this repo to
check against (matches `java.py`'s fixture-only precedent, for the same
reason: no real Java in this repo either).

Run: `uv run python scripts/smoke_apis_express.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.apis import ApiRoute, Framework  # noqa: E402
from app.analysis.apis.express import compose_routes  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "express"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _load_files() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in FIXTURES_DIR.rglob("*"):
        if path.is_file():
            rel = path.relative_to(FIXTURES_DIR).as_posix()
            files[rel] = path.read_bytes()
    return files


def _find(routes: list[ApiRoute], method: str, path: str, file_path: str | None = None) -> ApiRoute | None:
    return next(
        (r for r in routes if r.method == method and r.path == path and (file_path is None or r.file_path == file_path)),
        None,
    )


def run() -> bool:
    print("=== Express route extraction checks ===")
    all_passed = True

    files = _load_files()
    all_passed &= _check(
        "fixture files loaded",
        set(files)
        == {"app.js", "routes/users.js", "esm_app.mjs", "esm_router.mjs", "frontend_http_client.js"},
        str(set(files)),
    )

    routes = compose_routes(files)
    all_passed &= _check(
        "total routes: 11 (8 CommonJS + 3 ESM; frontend_http_client.js contributes 0)",
        len(routes) == 11, str(len(routes)),
    )
    all_passed &= _check(
        "every route is framework EXPRESS", all(r.framework == Framework.EXPRESS for r in routes)
    )

    # --- CommonJS: app.js's own direct routes ---
    health = _find(routes, "GET", "/health", "app.js")
    all_passed &= _check(
        "GET /health -> handler linked to healthCheck (FUNCTION)",
        health is not None
        and health.handler_name == "healthCheck"
        and health.handler_symbol is not None
        and health.handler_symbol.name == "healthCheck",
        str(health),
    )
    echo = _find(routes, "POST", "/echo", "app.js")
    all_passed &= _check(
        "POST /echo -> inline handler, no linked symbol",
        echo is not None and echo.handler_symbol is None and echo.handler_name == "<inline>",
        str(echo),
    )

    # --- CommonJS: routes/users.js mounted at /api/users ---
    mounted_cases = [
        ("GET", "/api/users/", "listUsers"),
        ("GET", "/api/users/:id", "getUser"),
        ("POST", "/api/users/", "createUser"),
        ("PUT", "/api/users/:id", "updateUser"),
        ("DELETE", "/api/users/:id", "deleteUser"),
        ("PATCH", "/api/users/:id", "patchUser"),
    ]
    for method, path, handler in mounted_cases:
        route = _find(routes, method, path, "routes/users.js")
        all_passed &= _check(
            f"{method} {path} (mounted sub-router) -> {handler}",
            route is not None
            and route.handler_name == handler
            and route.handler_symbol is not None
            and route.handler_symbol.name == handler,
            str(route),
        )

    # --- ESM: esm_app.mjs + esm_router.mjs mounted at /api/items ---
    status = _find(routes, "GET", "/status", "esm_app.mjs")
    all_passed &= _check("GET /status (ESM app, inline handler)", status is not None and status.handler_symbol is None)
    for method, path, handler in [("GET", "/api/items/", "listItems"), ("POST", "/api/items/", "createItem")]:
        route = _find(routes, method, path, "esm_router.mjs")
        all_passed &= _check(
            f"{method} {path} (ESM mounted sub-router) -> {handler}",
            route is not None and route.handler_symbol is not None and route.handler_symbol.name == handler,
            str(route),
        )

    # --- regression: non-Express .get/.post calls must NOT be recorded as routes ---
    frontend_routes = [r for r in routes if r.file_path == "frontend_http_client.js"]
    all_passed &= _check(
        "no routes originate from frontend_http_client.js (axios calls, non-Express 'router' variable)",
        frontend_routes == [], str(frontend_routes),
    )
    false_positive_paths = [r for r in routes if r.path in ("/token", "/aptitude/start", "/aptitude/history")]
    all_passed &= _check(
        "the real false-positive paths (/token, /aptitude/start, /aptitude/history) are not present anywhere",
        false_positive_paths == [], str(false_positive_paths),
    )

    return all_passed


if __name__ == "__main__":
    ok = run()
    print("\n=== Summary ===")
    print(f"fixture tests: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
