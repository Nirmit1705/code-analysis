"""Request-flow layer classification for the Flow Tracer (Phase 10.5): which
*layer* of a request's path one file belongs to — `route`, `controller`,
`service`, `repository`, `model` — or `other` (utils, config, types,
middleware, UI, anything with no confident layer signal).

Pure, same discipline as the rest of `analysis/`: plain strings in, a plain
enum out, no DB/ORM/FastAPI. `analysis/trace.py` is the consumer.

Deliberately a *second* vocabulary next to `services/module_classification.py`
(the Architecture screen's grouping), not a merge of the two
(docs/PHASE_10_PLAN.md decision #6): Architecture is signed off and its
output must not move, and the tracer needs per-file layer ranks that
Architecture's component kinds (`frontend`/`gateway`/`service`/...) don't
carry. Recorded as a Known Issue.

Rules, first match wins:
1. **Owns a real `ApiEndpoint`** -> `route`. Grounded, not guessed.
2. **Directory keyword**, scanning the file's module path from the *deepest*
   segment up (the folder closest to the file is the most specific claim —
   `src/services/models/x.ts` is a model): `routes/` -> route,
   `controllers/`/`handlers/`/`resolvers/` -> controller, `services/`/
   `usecases/` -> service, `repositories/`/`dao/`/`dal/`/`persistence/` ->
   repository, `models/`/`entities/` -> model, and `middleware/`/`utils/`/
   `helpers/`/`config/`/`types/`/`constants/` -> `other` (an explicit "this is
   a pass-through folder", so e.g. `middleware/errorHandler.ts` isn't then
   mistaken for a controller by rule 3).
3. **Filename convention** — the stem's last word, split on `.`/`_`/`-` and
   camelCase, with a trailing `Impl` dropped: `authController`,
   `user.controller`, `UserController` -> controller; `*Service`,
   `user_service`, `UserServiceImpl` -> service; `*Repository`/`*Repo`/
   `*Dao` -> repository; `*Model`/`*Entity`/`models.py` -> model; a Python
   `views.py` -> controller (Django/Flask's own name for that layer).
4. Anything else -> `other`.

A curated vocabulary, not a framework detector: a codebase using names this
doesn't know simply gets `other` for those files (they become pass-through
in a trace), never a guessed layer. Two conventions deliberately *not*
matched by filename: `*Handler` (as often an error/async middleware helper as
a request handler — the `handlers/` *folder* still counts) and `*Route`/
`*Routes`/`*Router` (`ProtectedRoute.tsx`, `AppRoutes.tsx` are frontend UI;
real backend routes are caught by rule 1 or a `routes/` folder).
"""

from __future__ import annotations

import enum
import re
from pathlib import PurePosixPath


class Layer(str, enum.Enum):
    ROUTE = "route"
    CONTROLLER = "controller"
    SERVICE = "service"
    REPOSITORY = "repository"
    MODEL = "model"
    OTHER = "other"


# Position in a request's path, outermost first. A trace only follows an
# import whose target's rank is >= the importer's (route -> controller ->
# service -> repository -> model; same-layer and skip-layer imports allowed).
# `other` has no rank — it's pass-through, never a hop of its own.
LAYER_RANK: dict[Layer, int] = {
    Layer.ROUTE: 0,
    Layer.CONTROLLER: 1,
    Layer.SERVICE: 2,
    Layer.REPOSITORY: 3,
    Layer.MODEL: 4,
}

_DIR_KEYWORDS: dict[str, Layer] = {
    **dict.fromkeys(("routes", "route", "routers", "router", "endpoints"), Layer.ROUTE),
    **dict.fromkeys(("controllers", "controller", "handlers", "handler", "resolvers"), Layer.CONTROLLER),
    **dict.fromkeys(("services", "service", "usecases", "usecase", "use_cases", "use-cases"), Layer.SERVICE),
    **dict.fromkeys(
        ("repositories", "repository", "repos", "dao", "daos", "dal", "persistence"), Layer.REPOSITORY
    ),
    **dict.fromkeys(("models", "model", "entities", "entity"), Layer.MODEL),
    **dict.fromkeys(
        (
            "middleware", "middlewares", "utils", "util", "helpers", "helper",
            "config", "configs", "types", "typings", "constants",
        ),
        Layer.OTHER,
    ),
}

_FILENAME_WORDS: dict[str, Layer] = {
    **dict.fromkeys(("controller", "controllers"), Layer.CONTROLLER),
    **dict.fromkeys(("service", "services", "usecase"), Layer.SERVICE),
    **dict.fromkeys(("repository", "repositories", "repo", "dao"), Layer.REPOSITORY),
    **dict.fromkeys(("model", "models", "entity", "entities"), Layer.MODEL),
}

# camelCase / PascalCase / acronym splitter: "UserDAO" -> User, DAO;
# "speechToTextController" -> speech, To, Text, Controller.
_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def classify_layer(path: str, module: str, has_api_endpoint: bool) -> Layer:
    """`path` is the file's POSIX repo-relative path, `module` its containing
    directory (the `File.module` convention), `has_api_endpoint` whether the
    run extracted at least one `ApiEndpoint` from this file."""
    if has_api_endpoint:
        return Layer.ROUTE

    segments = [s.lower() for s in module.split("/") if s not in ("", ".")]
    for segment in reversed(segments):
        layer = _DIR_KEYWORDS.get(segment)
        if layer is not None:
            return layer

    return _layer_from_filename(PurePosixPath(path))


def _layer_from_filename(path: PurePosixPath) -> Layer:
    if path.suffix == ".py" and path.stem == "views":
        return Layer.CONTROLLER
    words = [w.lower() for part in re.split(r"[._\-]+", path.stem) for w in _WORD_RE.findall(part)]
    if len(words) > 1 and words[-1] == "impl":
        words.pop()
    if not words:
        return Layer.OTHER
    return _FILENAME_WORDS.get(words[-1], Layer.OTHER)
