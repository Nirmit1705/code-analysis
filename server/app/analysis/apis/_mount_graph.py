"""Generic "mount graph" prefix resolution shared by `express.py` (sub-router
mounting via `app.use(prefix, router)`) and `fastapi_flask.py` (FastAPI
`include_router(prefix=...)`, Flask `register_blueprint(url_prefix=...)`).

The shared shape: a set of router/app-like objects, each identified by an
opaque hashable key (in practice `(file_path, variable_name)`), some of
which are mounted into others at a given path segment, and some of which
additionally carry their own fixed prefix set at construction time
(`APIRouter(prefix=...)` / `Blueprint(url_prefix=...)` — Express has no
such concept, so `express.py` simply never populates `own_prefix`).

For every key, `resolve_prefixes` returns every full path prefix at which
that key's own routes are reachable — a list because a router can be
mounted at more than one point (or not mounted at all, in which case it's
treated as reachable at its own `own_prefix` alone, root-relative).

Pure: no DB, no FastAPI, no Celery, no knowledge of Express/FastAPI/Flask
syntax at all — that parsing lives in the per-framework modules.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Mapping

from app.analysis.apis._paths import join_url_path


def resolve_prefixes(
    edges: Iterable[tuple[Hashable, str, Hashable]],
    *,
    own_prefix: Mapping[Hashable, str] = {},
    join: Callable[[str, str], str] = join_url_path,
) -> dict[Hashable, list[str]]:
    """`edges` is `(parent_key, mount_segment, child_key)` — "`child_key`
    is mounted into `parent_key` at `mount_segment`" (e.g. the string
    argument to `app.use(...)`/`include_router(..., prefix=...)`;
    `""` if no prefix was given at the mount site).

    A key with no incoming edge (never mounted anywhere) is a root,
    reachable at its own `own_prefix` (or `""`/`"/"` if it has none —
    Express's plain case). A key *with* incoming edges is reachable at
    `join(join(<reachable prefix of each parent>, mount_segment),
    own_prefix[key])` for every incoming edge — possibly more than one
    composed prefix if it's mounted more than once.

    A mount cycle (A mounts B mounts A) is not valid real-world Express/
    FastAPI/Flask usage; detected via a recursion-stack check and broken
    by treating the back-edge as if it didn't exist (that key falls back
    to its own root behavior for that path) rather than recursing forever.
    """
    incoming: dict[Hashable, list[tuple[Hashable, str]]] = defaultdict(list)
    keys: set[Hashable] = set(own_prefix)
    for parent, segment, child in edges:
        incoming[child].append((parent, segment))
        keys.add(parent)
        keys.add(child)

    memo: dict[Hashable, list[str]] = {}

    def resolve(key: Hashable, stack: frozenset[Hashable]) -> list[str]:
        if key in memo:
            return memo[key]
        parents = incoming.get(key, [])
        own = own_prefix.get(key, "")
        if not parents or key in stack:
            result = [own]
        else:
            next_stack = stack | {key}
            seen: set[str] = set()
            result = []
            for parent, segment in parents:
                for base in resolve(parent, next_stack):
                    composed = join(join(base, segment), own)
                    if composed not in seen:
                        seen.add(composed)
                        result.append(composed)
            if not result:
                result = [own]
        memo[key] = result
        return result

    return {key: resolve(key, frozenset()) for key in keys}
