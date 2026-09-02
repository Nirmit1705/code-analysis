"""Manual smoke test for Phase 3.6 (Spring Boot API extraction).

Fixtures under `scripts/fixtures/spring_boot/`:

- `UserController.java`: `@RestController` + class-level `@RequestMapping
  ("/api/users")`, covering `@GetMapping`/`@PostMapping`/`@PutMapping`/
  `@DeleteMapping`, a bare `@GetMapping` (no path — inherits the class
  prefix alone), an explicit `@RequestMapping(value=..., method=
  RequestMethod.PATCH)`, and a plain unannotated method that must produce
  no route.
- `NegativeCases.java`: a `@GetMapping`-annotated method on a class that
  is *not* `@RestController`/`@Controller` (must produce nothing), and a
  `@Controller` (not `@RestController`) class (must still be recognized).

No real spot-check against this repo's own source: there's no Java code
in this repo (matches `java.py`'s own fixture-only precedent, Phase 3.5).

Run: `uv run python scripts/smoke_apis_spring_boot.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.apis import ApiRoute, Framework  # noqa: E402
from app.analysis.apis.spring_boot import extract_routes  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "spring_boot"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _find(routes: list[ApiRoute], method: str, path: str) -> ApiRoute | None:
    return next((r for r in routes if r.method == method and r.path == path), None)


def run() -> bool:
    print("=== Spring Boot route extraction checks ===")
    all_passed = True

    source = (FIXTURES_DIR / "UserController.java").read_bytes()
    routes = extract_routes(source, file_path="UserController.java")
    all_passed &= _check("UserController.java: 6 routes", len(routes) == 6, str(routes))
    all_passed &= _check("every route is framework SPRING_BOOT", all(r.framework == Framework.SPRING_BOOT for r in routes))

    cases = [
        ("GET", "/api/users/{id}", "getUser"),
        ("GET", "/api/users", "listUsers"),  # bare @GetMapping, no path -> class prefix alone
        ("POST", "/api/users/", "createUser"),
        ("PUT", "/api/users/{id}", "updateUser"),
        ("DELETE", "/api/users/{id}", "deleteUser"),
        ("PATCH", "/api/users/{id}/archive", "archiveUser"),  # @RequestMapping(value=..., method=...)
    ]
    for method, path, handler in cases:
        route = _find(routes, method, path)
        all_passed &= _check(
            f"{method} {path} -> {handler}",
            route is not None
            and route.handler_name == handler
            and route.handler_symbol is not None
            and route.handler_symbol.name == handler
            and route.handler_symbol.kind.value == "method",
            str(route),
        )

    all_passed &= _check(
        "notARoute (no annotation) produces no route", all(r.handler_name != "notARoute" for r in routes)
    )

    # --- negative cases: class-level annotation requirement ---
    source = (FIXTURES_DIR / "NegativeCases.java").read_bytes()
    routes = extract_routes(source, file_path="NegativeCases.java")
    all_passed &= _check(
        "PlainService (no @RestController/@Controller): 0 routes despite @GetMapping on its method",
        all(r.handler_name != "notAController" for r in routes),
        str(routes),
    )
    home = _find(routes, "GET", "/home")
    all_passed &= _check(
        "PageController (@Controller, not @RestController): still recognized",
        home is not None and home.handler_name == "home",
        str(home),
    )
    all_passed &= _check("NegativeCases.java: exactly 1 route total", len(routes) == 1, str(routes))

    return all_passed


if __name__ == "__main__":
    ok = run()
    print("\n=== Summary ===")
    print(f"fixture tests: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
