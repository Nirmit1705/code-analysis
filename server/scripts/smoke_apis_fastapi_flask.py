"""Manual smoke test for Phase 3.6 (FastAPI/Flask API extraction).

Three parts:

1. `run_fastapi_fixture()` — `scripts/fixtures/fastapi_flask/simple_app.py`
   (a plain `FastAPI()` with `@app.get` and `@app.route(methods=[...])`)
   plus `app/api/v1/{auth,items,router}.py` mirroring this repo's own real
   `server/app/api/v1/{auth,github,router}.py` layout: two `APIRouter`s
   (one with its own `prefix=`, one without) mounted into a third via
   `include_router`, one of them with an *additional* mount-time prefix —
   exercises cross-file resolution through `from pkg import submodule` +
   `submodule.router` and prefix stacking across three files.
2. `run_flask_fixture()` — `flaskapp/{users_bp,main}.py`: a `Blueprint`
   with its own `url_prefix`, registered via `register_blueprint` (no
   override prefix — see `fastapi_flask.py`'s module docstring for why
   that combination is the one this module composes exactly right),
   covering `@bp.route(methods=[...])` and the `@bp.get` shortcut.
3. `run_real_spot_check()` — runs `compose_routes` against this repo's own
   real `server/app/api/v1/{auth,github,router}.py`, confirming the
   actual composed paths match what the app actually serves (e.g.
   `POST /api/v1/auth/register`, `GET /api/v1/auth/github/install`,
   `GET /api/v1/github/repos` — the last one via `repos_router`, which has
   no `prefix=` of its own at all, only `tags=`).

   **Pre-existing gap found and fixed in passing (Phase 6.5, unrelated to
   6.5's own work)**: Phase 6.1 added `GET /repos` to `repos_router` but
   never updated this script's hardcoded `expectations` list/count, so this
   check had been silently failing (`12` real routes extracted vs. an
   expected `11`) since then — confirmed via `git stash` that it fails
   identically on `main` before any Phase 6.5 change. Fixed here by adding
   the missing expectation; not a regression from anything in Phase 6.5
   (`architecture.py` isn't even in this check's ingested file set).

Run: `uv run python scripts/smoke_apis_fastapi_flask.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.apis import ApiRoute, Framework  # noqa: E402
from app.analysis.apis.fastapi_flask import compose_routes  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "fastapi_flask"
SERVER_ROOT = Path(__file__).resolve().parent.parent  # `server/` — dict keys below are relative to here,
# matching the absolute dotted imports (`from app.api.v1 import auth`) real code in this repo uses.


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _load_files(root: Path, names: list[str]) -> dict[str, bytes]:
    return {name: (root / name).read_bytes() for name in names}


def _find(routes: list[ApiRoute], method: str, path: str) -> ApiRoute | None:
    return next((r for r in routes if r.method == method and r.path == path), None)


def run_fastapi_fixture() -> bool:
    print("=== FastAPI fixture checks ===")
    all_passed = True

    files = _load_files(
        FIXTURES_DIR, ["simple_app.py", "app/api/v1/auth.py", "app/api/v1/items.py", "app/api/v1/router.py"]
    )
    routes = compose_routes(files)
    all_passed &= _check("total routes: 8", len(routes) == 8, str([(r.method, r.path) for r in routes]))
    all_passed &= _check("every route is framework FASTAPI", all(r.framework == Framework.FASTAPI for r in routes))

    root = _find(routes, "GET", "/")
    all_passed &= _check("simple_app.py: GET / (@app.get)", root is not None and root.handler_name == "root", str(root))
    legacy_get = _find(routes, "GET", "/legacy")
    legacy_post = _find(routes, "POST", "/legacy")
    all_passed &= _check(
        "simple_app.py: @app.route(methods=[GET,POST]) -> two routes, same handler",
        legacy_get is not None
        and legacy_post is not None
        and legacy_get.handler_name == "legacy"
        and legacy_post.handler_name == "legacy",
        f"{legacy_get} / {legacy_post}",
    )

    # auth.router (own prefix "/auth") mounted with no extra prefix into
    # api_router (own prefix "/api/v1") -> "/api/v1/auth/..."
    for method, path, handler in [
        ("POST", "/api/v1/auth/register", "register"),
        ("POST", "/api/v1/auth/login", "login"),
        ("GET", "/api/v1/auth/me", "me"),
    ]:
        route = _find(routes, method, path)
        all_passed &= _check(
            f"{method} {path} (auth.router, own prefix + no include prefix) -> {handler}",
            route is not None
            and route.handler_name == handler
            and route.handler_symbol is not None
            and route.handler_symbol.name == handler,
            str(route),
        )

    # items.router (no own prefix) mounted WITH an extra "/catalog" prefix
    # into api_router -> "/api/v1/catalog/..."
    for method, path, handler in [
        ("GET", "/api/v1/catalog/items", "list_items"),
        ("GET", "/api/v1/catalog/items/{item_id}", "get_item"),
    ]:
        route = _find(routes, method, path)
        all_passed &= _check(
            f"{method} {path} (items.router, no own prefix + include prefix) -> {handler}",
            route is not None and route.handler_name == handler,
            str(route),
        )

    return all_passed


def run_flask_fixture() -> bool:
    print("\n=== Flask fixture checks ===")
    all_passed = True

    files = _load_files(FIXTURES_DIR, ["flaskapp/users_bp.py", "flaskapp/main.py"])
    routes = compose_routes(files)
    all_passed &= _check("total routes: 3", len(routes) == 3, str([(r.method, r.path) for r in routes]))
    all_passed &= _check("every route is framework FLASK", all(r.framework == Framework.FLASK for r in routes))

    cases = [
        ("GET", "/users/", "list_users"),  # @bp.route("/", methods=["GET"])
        ("GET", "/users/<int:user_id>", "get_user"),  # @bp.get shortcut
        ("POST", "/users/", "create_user"),
    ]
    for method, path, handler in cases:
        route = _find(routes, method, path)
        all_passed &= _check(
            f"{method} {path} (Blueprint own url_prefix + register_blueprint, no override) -> {handler}",
            route is not None
            and route.handler_name == handler
            and route.handler_symbol is not None
            and route.handler_symbol.name == handler,
            str(route),
        )

    return all_passed


def run_real_spot_check() -> bool:
    print("\n=== Real spot-check: this repo's own server/app/api/v1 ===")
    all_passed = True

    rel_paths = ["app/api/v1/auth.py", "app/api/v1/github.py", "app/api/v1/router.py"]
    files = {rel: (SERVER_ROOT / rel).read_bytes() for rel in rel_paths}
    routes = compose_routes(files)
    print(f"  {len(routes)} routes extracted: {sorted((r.method, r.path) for r in routes)}")

    expectations = [
        ("POST", "/api/v1/auth/register"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/refresh"),
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/auth/github/install"),
        ("GET", "/api/v1/auth/github/callback"),
        ("POST", "/api/v1/auth/github/exchange"),
        ("GET", "/api/v1/github/repos"),
        ("GET", "/api/v1/repos"),  # Phase 6.1's GET /repos (list), also on repos_router
        ("POST", "/api/v1/repos"),
        ("GET", "/api/v1/repos/{repository_id}/branches"),
        ("POST", "/api/v1/repos/{repository_id}/branches/{branch_name:path}/select"),
    ]
    for method, path in expectations:
        route = _find(routes, method, path)
        all_passed &= _check(f"real endpoint {method} {path}", route is not None, str(route))

    all_passed &= _check(f"exactly {len(expectations)} real routes extracted", len(routes) == len(expectations), str(len(routes)))

    return all_passed


def run_nested_repo_root_regression_check() -> bool:
    """Regression check for the cross-file path-resolution bug found
    during Phase 3.7's real-repo pipeline run: `run_real_spot_check`
    above ingests these same real files keyed exactly as their absolute
    imports read (`"app/api/v1/auth.py"`, no prefix), which happened to
    make `_resolve_module_prefix`'s old exact-match-only lookup work by
    coincidence. A real ingestion of this actual repo places these files
    under `server/`, which is *not* the Python package root — before the
    fix, `router.py`'s `include_router(auth.router)` /
    `include_router(github.router)` couldn't resolve `auth`/`github` back
    to their defining files, so `/api/v1` never composed and every route
    came out unprefixed (`/auth/register` instead of
    `/api/v1/auth/register`). This reproduces exactly that shape and
    checks the prefix survives.
    """
    print("\n=== Regression: real files ingested under a 'server/' prefix (nested package root) ===")
    all_passed = True

    rel_paths = ["app/api/v1/auth.py", "app/api/v1/__init__.py", "app/api/v1/github.py", "app/api/v1/router.py"]
    files = {f"server/{rel}": (SERVER_ROOT / rel).read_bytes() for rel in rel_paths}
    routes = compose_routes(files)
    print(f"  {len(routes)} routes extracted: {sorted((r.method, r.path) for r in routes)}")

    expectations = [
        ("POST", "/api/v1/auth/register"),
        ("GET", "/api/v1/auth/me"),
        ("GET", "/api/v1/auth/github/install"),
        ("GET", "/api/v1/github/repos"),
        ("POST", "/api/v1/repos/{repository_id}/branches/{branch_name:path}/select"),
    ]
    for method, path in expectations:
        route = _find(routes, method, path)
        all_passed &= _check(f"nested-root endpoint retains /api/v1 prefix: {method} {path}", route is not None, str(route))

    return all_passed


if __name__ == "__main__":
    r1 = run_fastapi_fixture()
    r2 = run_flask_fixture()
    r3 = run_real_spot_check()
    r4 = run_nested_repo_root_regression_check()
    print("\n=== Summary ===")
    print(f"FastAPI fixture:          {'PASS' if r1 else 'FAIL'}")
    print(f"Flask fixture:            {'PASS' if r2 else 'FAIL'}")
    print(f"real spot-check:          {'PASS' if r3 else 'FAIL'}")
    print(f"nested-root regression:   {'PASS' if r4 else 'FAIL'}")
    sys.exit(0 if (r1 and r2 and r3 and r4) else 1)
