"""Manual smoke test for Phase 10.5's `app.analysis.trace.trace_from`.

Hand-built graphs, each with its expected hops worked out by hand first:

1. A straight route -> controller -> service -> repository -> model chain
   (order, depths, parents, `fn` only on the start hop, `reaches_data`).
2. A branching service importing two repositories (siblings at one depth,
   ordered by path, same parent).
3. Pass-through: `service -> config/database (other) -> @prisma/client` puts
   the database on the service hop with `via`; `route -> middleware (other)
   -> service` makes the service a hop with `via`; a direct import beats a
   longer pass-through chain.
4. Backwards edges (service -> controller, model -> repository) are ignored
   but still listed in `deps`.
5. Cycles terminate: same-layer services importing each other, and a cycle
   inside the pass-through files.
6. Test files are neither hops nor pass-through (and absent from `deps`).
7. Caps: the hop cap (`truncated`, exactly `MAX_HOPS` hops), the depth cap
   (a 12-service chain stops at depth 8), and a chain that fits exactly is
   *not* truncated.
8. Start handling: `<inline>`/blank handlers give `fn=None`; an `other`
   start file reaches every layer; the lowest-index parent claims a shared
   child; `parent_index < index` for every hop of every trace above.
9. Real extractor: `scripts/fixtures/architecture_layering/` through the real
   `extract_import_edges`, `classify_layer` and `resolve_external_target`
   (the 10.6 wiring in miniature) — route -> controller -> service, the
   service reaching PostgreSQL and Redis.

Pure — no DB. Run: `uv run python scripts/smoke_trace.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.dependencies import DependencyEdgeType  # noqa: E402
from app.analysis.dependencies.imports import extract_import_edges  # noqa: E402
from app.analysis.layers import Layer, classify_layer  # noqa: E402
from app.analysis.trace import MAX_DEPTH, MAX_HOPS, ExternalRef, Trace, trace_from  # noqa: E402
from app.services.external_services import EXTERNAL_NODE_ID, resolve_external_target  # noqa: E402

PRISMA = ExternalRef("data:database", "PostgreSQL", "data", "@prisma/client")
REDIS = ExternalRef("cache:redis", "Redis", "cache", "ioredis")

_ALL_TRACES: list[tuple[str, Trace]] = []


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _trace(name: str, start: str, adjacency, layer_of, externals=None, handler=None, **caps) -> Trace:
    trace = trace_from(start, adjacency, externals or {}, layer_of, handler, **caps)
    _ALL_TRACES.append((name, trace))
    return trace


def _summary(trace: Trace) -> list[tuple[int, int | None, str, str]]:
    return [(h.depth, h.parent_index, h.layer.value, h.file_path) for h in trace.hops]


def run_straight_chain() -> bool:
    print("=== group 1: straight route -> controller -> service -> repository -> model ===")
    adjacency = {
        "routes/users.ts": ["controllers/userController.ts"],
        "controllers/userController.ts": ["services/userService.ts"],
        "services/userService.ts": ["repositories/userRepository.ts"],
        "repositories/userRepository.ts": ["models/user.ts"],
    }
    layer_of = {
        "routes/users.ts": Layer.ROUTE,
        "controllers/userController.ts": Layer.CONTROLLER,
        "services/userService.ts": Layer.SERVICE,
        "repositories/userRepository.ts": Layer.REPOSITORY,
        "models/user.ts": Layer.MODEL,
    }
    trace = _trace("straight", "routes/users.ts", adjacency, layer_of, handler="listUsers")
    expected = [
        (0, None, "route", "routes/users.ts"),
        (1, 0, "controller", "controllers/userController.ts"),
        (2, 1, "service", "services/userService.ts"),
        (3, 2, "repository", "repositories/userRepository.ts"),
        (4, 3, "model", "models/user.ts"),
    ]
    ok = _check("five hops, one per layer, each the previous one's child", _summary(trace) == expected, str(_summary(trace)))
    ok &= _check("start hop carries the handler name", trace.hops[0].fn == "listUsers", str(trace.hops[0].fn))
    ok &= _check("no other hop has a function name", all(h.fn is None for h in trace.hops[1:]))
    ok &= _check("indices are 0..4 in order", [h.index for h in trace.hops] == [0, 1, 2, 3, 4])
    ok &= _check("deps = the file's own direct imports", trace.hops[2].deps == ("repositories/userRepository.ts",))
    ok &= _check("not truncated", trace.truncated is False)
    ok &= _check("reaches_data (a model hop)", trace.reaches_data is True)
    ok &= _check(
        "layers_reached outermost first",
        trace.layers_reached == [Layer.ROUTE, Layer.CONTROLLER, Layer.SERVICE, Layer.REPOSITORY, Layer.MODEL],
        str(trace.layers_reached),
    )
    return ok


def run_branching_service() -> bool:
    print("=== group 2: a branching service (two repositories) ===")
    adjacency = {
        "routes/orders.ts": ["services/orderService.ts"],
        "services/orderService.ts": ["repositories/paymentRepo.ts", "repositories/orderRepo.ts"],
    }
    layer_of = {
        "routes/orders.ts": Layer.ROUTE,
        "services/orderService.ts": Layer.SERVICE,
        "repositories/paymentRepo.ts": Layer.REPOSITORY,
        "repositories/orderRepo.ts": Layer.REPOSITORY,
    }
    trace = _trace("branching", "routes/orders.ts", adjacency, layer_of)
    expected = [
        (0, None, "route", "routes/orders.ts"),
        (1, 0, "service", "services/orderService.ts"),  # route -> service skips the controller layer: allowed
        (2, 1, "repository", "repositories/orderRepo.ts"),
        (2, 1, "repository", "repositories/paymentRepo.ts"),
    ]
    ok = _check("both repositories are siblings under the service, path-ordered", _summary(trace) == expected, str(_summary(trace)))
    ok &= _check("handler omitted -> fn None", trace.hops[0].fn is None)
    ok &= _check("reaches_data (repository hops)", trace.reaches_data is True)
    return ok


def run_pass_through() -> bool:
    print("=== group 3: pass-through other files ===")
    adjacency = {
        "routes/auth.ts": ["middleware/auth.ts", "controllers/authController.ts"],
        "middleware/auth.ts": ["utils/jwt.ts", "config/database.ts", "services/sessionService.ts"],
        "controllers/authController.ts": ["services/authService.ts", "types/auth.ts"],
        "services/authService.ts": ["config/database.ts", "utils/jwt.ts"],
        "config/database.ts": [],
        "utils/jwt.ts": ["config/env.ts"],
    }
    layer_of = {
        "routes/auth.ts": Layer.ROUTE,
        "controllers/authController.ts": Layer.CONTROLLER,
        "services/authService.ts": Layer.SERVICE,
        "services/sessionService.ts": Layer.SERVICE,
        "middleware/auth.ts": Layer.OTHER,
        "config/database.ts": Layer.OTHER,
        # utils/jwt.ts, types/auth.ts, config/env.ts deliberately absent: missing -> other
    }
    externals = {
        "config/database.ts": [PRISMA],
        "config/env.ts": [ExternalRef(EXTERNAL_NODE_ID, "External Dependencies", "external", "dotenv")],
    }
    trace = _trace("pass-through", "routes/auth.ts", adjacency, layer_of, externals)
    by_path = {h.file_path: h for h in trace.hops}
    ok = _check(
        "no other file ever becomes a hop",
        not any(h.layer is Layer.OTHER for h in trace.hops),
        str([h.file_path for h in trace.hops]),
    )
    ok &= _check(
        "hops = route, controller, the session service reached through middleware, the auth service",
        _summary(trace)
        == [
            (0, None, "route", "routes/auth.ts"),
            (1, 0, "controller", "controllers/authController.ts"),
            (1, 0, "service", "services/sessionService.ts"),
            (2, 1, "service", "services/authService.ts"),
        ],
        str(_summary(trace)),
    )
    ok &= _check(
        "the service reached through middleware records it in via",
        by_path["services/sessionService.ts"].via == ("middleware/auth.ts",),
        str(by_path["services/sessionService.ts"].via),
    )
    ok &= _check("a directly imported hop has an empty via", by_path["controllers/authController.ts"].via == ())

    service_ext = {e.service_id: e for e in by_path["services/authService.ts"].external_services}
    ok &= _check(
        "authService -> config/database -> @prisma/client puts PostgreSQL on the service hop",
        "data:database" in service_ext and service_ext["data:database"].via == ("config/database.ts",),
        str(service_ext),
    )
    ok &= _check(
        "a two-file pass-through chain is recorded in full",
        "external" in service_ext and service_ext["external"].via == ("utils/jwt.ts", "config/env.ts"),
        str(service_ext.get("external")),
    )
    route_ext = {e.service_id: e for e in by_path["routes/auth.ts"].external_services}
    ok &= _check(
        "the route hop also reaches the database, through its middleware",
        route_ext.get("data:database") is not None
        and route_ext["data:database"].via == ("middleware/auth.ts", "config/database.ts"),
        str(route_ext),
    )
    ok &= _check(
        "the controller reaches no external service (types/auth.ts imports nothing)",
        by_path["controllers/authController.ts"].external_services == (),
    )
    ok &= _check("reaches_data via a data-kind external alone (no repository/model hop)", trace.reaches_data is True)

    # Direct import beats a longer pass-through chain, for an external and for a hop.
    adjacency2 = {
        "services/a.ts": ["config/db.ts", "utils/x.ts", "services/b.ts"],
        "utils/x.ts": ["services/b.ts", "config/db.ts"],
    }
    layer_of2 = {"services/a.ts": Layer.SERVICE, "services/b.ts": Layer.SERVICE}
    externals2 = {"services/a.ts": [PRISMA], "config/db.ts": [PRISMA]}
    trace2 = _trace("shortest-chain", "services/a.ts", adjacency2, layer_of2, externals2)
    ext = trace2.hops[0].external_services
    ok &= _check(
        "an external imported directly AND via a file keeps via=() and one entry",
        len(ext) == 1 and ext[0].via == () and ext[0].packages == ("@prisma/client",),
        str(ext),
    )
    ok &= _check(
        "a hop imported directly AND via a util keeps via=()",
        trace2.hops[1].file_path == "services/b.ts" and trace2.hops[1].via == (),
        str(_summary(trace2)),
    )
    return ok


def run_backwards_edges() -> bool:
    print("=== group 4: an edge going backwards in layer is ignored ===")
    adjacency = {
        "services/userService.ts": ["controllers/userController.ts", "repositories/userRepo.ts"],
        "controllers/userController.ts": ["services/userService.ts"],
        "repositories/userRepo.ts": ["models/user.ts", "services/userService.ts"],
        "models/user.ts": ["repositories/userRepo.ts", "routes/users.ts"],
    }
    layer_of = {
        "routes/users.ts": Layer.ROUTE,
        "controllers/userController.ts": Layer.CONTROLLER,
        "services/userService.ts": Layer.SERVICE,
        "repositories/userRepo.ts": Layer.REPOSITORY,
        "models/user.ts": Layer.MODEL,
    }
    trace = _trace("backwards", "services/userService.ts", adjacency, layer_of)
    paths = [h.file_path for h in trace.hops]
    ok = _check(
        "service start: controller (backwards) is not a hop; repository and model are",
        paths == ["services/userService.ts", "repositories/userRepo.ts", "models/user.ts"],
        str(paths),
    )
    ok &= _check("model -> route (backwards) does not appear", "routes/users.ts" not in paths)
    ok &= _check(
        "the ignored import is still listed in the start hop's deps (it is a real import)",
        "controllers/userController.ts" in trace.hops[0].deps,
        str(trace.hops[0].deps),
    )
    return ok


def run_cycles() -> bool:
    print("=== group 5: cycles terminate ===")
    adjacency = {
        "services/a.ts": ["services/b.ts"],
        "services/b.ts": ["services/c.ts"],
        "services/c.ts": ["services/a.ts", "utils/p.ts"],
        "utils/p.ts": ["utils/q.ts"],
        "utils/q.ts": ["utils/p.ts", "repositories/r.ts"],
        "repositories/r.ts": ["repositories/r.ts"],  # self-import
    }
    layer_of = {
        "services/a.ts": Layer.SERVICE,
        "services/b.ts": Layer.SERVICE,
        "services/c.ts": Layer.SERVICE,
        "repositories/r.ts": Layer.REPOSITORY,
    }
    externals = {"utils/q.ts": [REDIS]}
    trace = _trace("cycle", "services/a.ts", adjacency, layer_of, externals)
    ok = _check(
        "a -> b -> c -> (a again: skipped) and r through the p<->q utils cycle",
        _summary(trace)
        == [
            (0, None, "service", "services/a.ts"),
            (1, 0, "service", "services/b.ts"),
            (2, 1, "service", "services/c.ts"),
            (3, 2, "repository", "repositories/r.ts"),
        ],
        str(_summary(trace)),
    )
    ok &= _check("each file appears once", len({h.file_path for h in trace.hops}) == len(trace.hops))
    ok &= _check("r reached through both utils", trace.hops[3].via == ("utils/p.ts", "utils/q.ts"), str(trace.hops[3].via))
    ok &= _check(
        "Redis attached to c through the utils cycle",
        [e.service_id for e in trace.hops[2].external_services] == ["cache:redis"],
    )
    ok &= _check("a self-import is not its own dep", trace.hops[3].deps == ())
    ok &= _check("not truncated", trace.truncated is False)
    ok &= _check("Redis (cache) alone is not data; the repository hop is", trace.reaches_data is True)
    return ok


def run_test_exclusion() -> bool:
    print("=== group 6: test files are excluded ===")
    adjacency = {
        "controllers/userController.ts": [
            "services/userService.ts",
            "services/userService.test.ts",
            "__tests__/helpers/dbSetup.ts",
            "utils/testable.ts",
        ],
        "__tests__/helpers/dbSetup.ts": ["services/hiddenService.ts"],
        "services/userService.test.ts": ["services/userService.ts"],
        "utils/testable.ts": ["tests/fixtures.py"],
    }
    layer_of = {
        "controllers/userController.ts": Layer.CONTROLLER,
        "services/userService.ts": Layer.SERVICE,
        "services/userService.test.ts": Layer.SERVICE,
        "services/hiddenService.ts": Layer.SERVICE,
    }
    externals = {"__tests__/helpers/dbSetup.ts": [PRISMA], "tests/fixtures.py": [PRISMA]}
    trace = _trace("tests", "controllers/userController.ts", adjacency, layer_of, externals)
    paths = [h.file_path for h in trace.hops]
    ok = _check(
        "only the real service is a hop",
        paths == ["controllers/userController.ts", "services/userService.ts"],
        str(paths),
    )
    ok &= _check("a service reachable only through a test helper is not a hop", "services/hiddenService.ts" not in paths)
    ok &= _check(
        "no database through a test helper or test fixture",
        trace.hops[0].external_services == () and trace.reaches_data is False,
        str(trace.hops[0].external_services),
    )
    ok &= _check(
        "test files are absent from deps; the non-test util is listed",
        trace.hops[0].deps == ("services/userService.ts", "utils/testable.ts"),
        str(trace.hops[0].deps),
    )
    return ok


def run_caps() -> bool:
    print("=== group 7: hop cap and depth cap ===")
    services = [f"services/s{i:02d}.ts" for i in range(30)]
    adjacency = {"routes/wide.ts": services}
    layer_of = {"routes/wide.ts": Layer.ROUTE, **{s: Layer.SERVICE for s in services}}
    trace = _trace("wide", "routes/wide.ts", adjacency, layer_of)
    ok = _check(f"30 children -> exactly MAX_HOPS={MAX_HOPS} hops", len(trace.hops) == MAX_HOPS, str(len(trace.hops)))
    ok &= _check("truncated", trace.truncated is True)
    ok &= _check(
        "the kept children are the first by path",
        [h.file_path for h in trace.hops[1:]] == services[: MAX_HOPS - 1],
    )

    chain = [f"services/c{i:02d}.ts" for i in range(12)]
    deep_adj = {chain[i]: [chain[i + 1]] for i in range(len(chain) - 1)}
    deep_layers = {c: Layer.SERVICE for c in chain}
    deep = _trace("deep", chain[0], deep_adj, deep_layers)
    ok &= _check(
        f"12-file chain stops at depth MAX_DEPTH={MAX_DEPTH}",
        max(h.depth for h in deep.hops) == MAX_DEPTH and len(deep.hops) == MAX_DEPTH + 1,
        str([h.depth for h in deep.hops]),
    )
    ok &= _check("depth cap sets truncated", deep.truncated is True)

    exact = chain[: MAX_DEPTH + 1]
    exact_adj = {exact[i]: [exact[i + 1]] for i in range(len(exact) - 1)}
    fits = _trace("exact", exact[0], exact_adj, deep_layers)
    ok &= _check(
        "a chain that ends exactly at the depth cap is not truncated",
        len(fits.hops) == MAX_DEPTH + 1 and fits.truncated is False,
        f"{len(fits.hops)} hops, truncated={fits.truncated}",
    )

    small = _trace("small-cap", "routes/wide.ts", adjacency, layer_of, max_hops=3, max_depth=1)
    ok &= _check("caps are overridable (3 hops)", len(small.hops) == 3 and small.truncated is True)
    return ok


def run_start_handling() -> bool:
    print("=== group 8: start handling, shared children, ordering invariant ===")
    adjacency = {"routes/a.ts": ["services/s.ts"]}
    layer_of = {"routes/a.ts": Layer.ROUTE, "services/s.ts": Layer.SERVICE}
    ok = True
    for handler, expected in (("<inline>", None), ("  ", None), ("register", "register"), (" AuthController.signIn ", "AuthController.signIn")):
        t = trace_from("routes/a.ts", adjacency, {}, layer_of, handler)
        ok &= _check(f"handler {handler!r} -> fn {expected!r}", t.hops[0].fn == expected, repr(t.hops[0].fn))

    lone = _trace("lone", "routes/lonely.ts", {}, {"routes/lonely.ts": Layer.ROUTE})
    ok &= _check(
        "a start with no downstream imports is a clean single hop",
        len(lone.hops) == 1 and lone.truncated is False and lone.hops[0].deps == () and lone.reaches_data is False,
    )

    other_start = _trace(
        "other-start",
        "utils/boot.ts",
        {"utils/boot.ts": ["controllers/c.ts", "routes/r.ts"]},
        {"controllers/c.ts": Layer.CONTROLLER, "routes/r.ts": Layer.ROUTE},
    )
    ok &= _check(
        "an other start file is the outermost rank: it reaches routes and controllers",
        _summary(other_start)
        == [(0, None, "other", "utils/boot.ts"), (1, 0, "route", "routes/r.ts"), (1, 0, "controller", "controllers/c.ts")],
        str(_summary(other_start)),
    )
    ok &= _check("layers_reached puts other last", other_start.layers_reached == [Layer.ROUTE, Layer.CONTROLLER, Layer.OTHER])

    shared = _trace(
        "shared",
        "routes/r.ts",
        {
            "routes/r.ts": ["controllers/b.ts", "controllers/a.ts"],
            "controllers/a.ts": ["services/shared.ts"],
            "controllers/b.ts": ["services/shared.ts"],
        },
        {
            "routes/r.ts": Layer.ROUTE,
            "controllers/a.ts": Layer.CONTROLLER,
            "controllers/b.ts": Layer.CONTROLLER,
            "services/shared.ts": Layer.SERVICE,
        },
    )
    ok &= _check(
        "a child imported by two same-depth hops appears once, under the lower index",
        _summary(shared)[-1] == (2, 1, "service", "services/shared.ts") and len(shared.hops) == 4,
        str(_summary(shared)),
    )

    ordering_ok = True
    for name, t in _ALL_TRACES:
        for h in t.hops:
            if h.parent_index is not None and not (h.parent_index < h.index and t.hops[h.parent_index].depth == h.depth - 1):
                ordering_ok = False
                print(f"    ordering violated in {name}: {h}")
        keys = [(h.depth, -1 if h.layer is Layer.OTHER else list(Layer).index(h.layer), h.file_path) for h in t.hops]
        if keys != sorted(keys):
            ordering_ok = False
            print(f"    hops not in (depth, rank, path) order in {name}")
    ok &= _check(
        f"every hop of all {len(_ALL_TRACES)} traces: parent one level up with a smaller index; (depth, rank, path) order",
        ordering_ok,
    )
    return ok


def run_real_extractor() -> bool:
    print("=== group 9: real fixture through the real extractor + classifier + service resolver ===")
    root = Path(__file__).resolve().parent / "fixtures" / "architecture_layering"
    sources = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    edges = extract_import_edges(sources)

    adjacency: dict[str, set[str]] = {}
    externals: dict[str, list[ExternalRef]] = {}
    for e in edges:
        if e.edge_type is DependencyEdgeType.INTERNAL and e.target_file:
            adjacency.setdefault(e.source_file, set()).add(e.target_file)
        elif e.edge_type is DependencyEdgeType.EXTERNAL and e.target_package:
            resolved = resolve_external_target(e.target_package, "postgresql")
            if resolved is not None and resolved[0] != EXTERNAL_NODE_ID:
                externals.setdefault(e.source_file, []).append(ExternalRef(*resolved, e.target_package))

    route_file = "server/routes/userRoutes.js"
    layer_of = {
        path: classify_layer(path, str(PurePosixPath(path).parent), path == route_file) for path in sources
    }
    trace = _trace("real-fixture", route_file, adjacency, layer_of, externals, handler="userController.list")
    ok = _check(
        "route -> controller -> service",
        _summary(trace)
        == [
            (0, None, "route", "server/routes/userRoutes.js"),
            (1, 0, "controller", "server/controllers/userController.js"),
            (2, 1, "service", "server/services/userService.js"),
        ],
        str(_summary(trace)),
    )
    service_ext = [(e.label, e.kind, e.packages, e.via) for e in trace.hops[-1].external_services]
    ok &= _check(
        "the service hop reaches PostgreSQL (@prisma/client) and Redis (ioredis), directly",
        service_ext == [("Redis", "cache", ("ioredis",), ()), ("PostgreSQL", "data", ("@prisma/client",), ())],
        str(service_ext),
    )
    ok &= _check(
        "express (generic, filtered by the caller) is not shown as a service on the route hop",
        trace.hops[0].external_services == (),
    )
    ok &= _check("reaches_data", trace.reaches_data is True)
    return ok


def main() -> int:
    groups = [
        run_straight_chain,
        run_branching_service,
        run_pass_through,
        run_backwards_edges,
        run_cycles,
        run_test_exclusion,
        run_caps,
        run_real_extractor,
        run_start_handling,  # last: its ordering check covers every trace built above
    ]
    results = [group() for group in groups]
    print()
    print("ALL PASSED" if all(results) else "SOME CHECKS FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
