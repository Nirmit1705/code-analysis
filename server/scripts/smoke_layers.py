"""Manual smoke test for Phase 10.5's `app.analysis.layers.classify_layer`.

Every case below is hand-labelled (the expected layer was decided by reading
the path, not by running the classifier), grouped by convention:

1. Express, directory-style (HireSight's own real layout).
2. Spring, suffix-style (`UserController.java`, `UserServiceImpl.java`, ...).
3. Python, `*_service.py` / `*_repository.py` / `models.py` / `views.py`.
4. `repositories/` / `dao/` / `usecases/` folders.
5. Precedence: a real `ApiEndpoint` beats any folder; the deepest folder
   keyword beats a shallower one; a folder keyword beats the filename.
6. Unknown -> `other` (incl. the frontend names deliberately not matched:
   `ProtectedRoute.tsx`, `AppRoutes.tsx`, root `errorHandler.ts`).

Pure — no DB. Run: `uv run python scripts/smoke_layers.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.layers import Layer, classify_layer  # noqa: E402

R, C, S, REPO, M, O = Layer.ROUTE, Layer.CONTROLLER, Layer.SERVICE, Layer.REPOSITORY, Layer.MODEL, Layer.OTHER


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _run_cases(title: str, cases: list[tuple[str, bool, Layer]]) -> bool:
    print(f"=== {title} ===")
    all_passed = True
    for path, has_api, expected in cases:
        module = str(PurePosixPath(path).parent) or "."
        got = classify_layer(path, module, has_api)
        all_passed &= _check(
            f"{path}{' (owns an endpoint)' if has_api else ''} -> {expected.value}",
            got == expected,
            f"got {got.value}",
        )
    return all_passed


def run_express_dir_style() -> bool:
    return _run_cases(
        "group 1: Express, directory-style",
        [
            ("server/src/routes/auth.ts", True, R),
            ("server/src/routes/index.ts", False, R),  # aggregator with no extracted endpoint: the folder says route
            ("server/src/controllers/authController.ts", False, C),
            ("server/src/services/authService.ts", False, S),
            ("server/src/services/llmResumeAnalyzer.ts", False, S),  # folder, not filename, decides
            ("server/src/models/User.js", False, M),
            ("server/src/middleware/authMiddleware.ts", False, O),
            ("server/src/middleware/errorHandler.ts", False, O),  # folder keeps it from reading as a handler
            ("server/src/config/database.ts", False, O),
            ("server/src/utils/authUtils.ts", False, O),
            ("server/src/types/authTypes.ts", False, O),
            ("server/src/app.ts", False, O),
            ("server/src/server.ts", True, R),  # HireSight's server.ts declares a route itself
        ],
    )


def run_spring_suffix_style() -> bool:
    base = "src/main/java/com/acme/user"
    return _run_cases(
        "group 2: Spring, suffix-style",
        [
            (f"{base}/UserController.java", True, R),  # a real @GetMapping is a route first
            (f"{base}/UserController.java", False, C),
            (f"{base}/UserService.java", False, S),
            (f"{base}/UserServiceImpl.java", False, S),  # trailing Impl dropped
            (f"{base}/UserRepository.java", False, REPO),
            (f"{base}/UserDAO.java", False, REPO),  # acronym split: User | DAO
            (f"{base}/UserEntity.java", False, M),
            (f"{base}/User.java", False, O),  # a bare class name says nothing
            (f"{base}/UserDto.java", False, O),
            ("src/main/java/com/acme/entity/User.java", False, M),
            ("src/main/java/com/acme/model/User.java", False, M),
            ("src/main/java/com/acme/Application.java", False, O),
        ],
    )


def run_python_style() -> bool:
    return _run_cases(
        "group 3: Python",
        [
            ("app/user_service.py", False, S),
            ("app/user_repository.py", False, REPO),
            ("app/user_repo.py", False, REPO),
            ("app/models.py", False, M),
            ("blog/views.py", False, C),  # Django/Flask's name for the controller layer
            ("app/api/v1/auth.py", True, R),
            ("app/core/config.py", False, O),
            ("app/main.py", False, O),
            ("service.py", False, S),
            ("scripts/views.txt", False, O),  # only a Python views.py counts
        ],
    )


def run_repository_folders() -> bool:
    return _run_cases(
        "group 4: repositories/ dao/ usecases/ folders",
        [
            ("src/repositories/order.ts", False, REPO),
            ("src/repository/order.ts", False, REPO),
            ("src/dao/order.js", False, REPO),
            ("src/persistence/orderStore.ts", False, REPO),
            ("src/usecases/createOrder.ts", False, S),
            ("src/handlers/order.go", False, C),
            ("src/resolvers/order.ts", False, C),
            ("src/entities/Order.ts", False, M),
        ],
    )


def run_precedence() -> bool:
    return _run_cases(
        "group 5: precedence",
        [
            ("src/services/billing.ts", True, R),  # endpoint beats folder
            ("src/services/models/invoice.ts", False, M),  # deepest folder wins
            ("src/models/services/invoice.ts", False, S),
            ("src/services/userRepository.ts", False, S),  # folder beats filename (plan: directory first)
            ("src/utils/userService.ts", False, O),  # an explicit pass-through folder beats filename too
            ("src/Services/Billing.ts", False, S),  # folder match is case-insensitive
        ],
    )


def run_unknown() -> bool:
    return _run_cases(
        "group 6: unknown -> other",
        [
            ("src/lib/foo.ts", False, O),
            ("client/src/components/ProtectedRoute.tsx", False, O),
            ("client/src/AppRoutes.tsx", False, O),
            ("client/src/routes.ts", False, O),
            ("client/src/pages/Dashboard.tsx", False, O),
            ("src/errorHandler.ts", False, O),  # *Handler deliberately not a filename rule
            ("README.md", False, O),
            ("src/vite-env.d.ts", False, O),
            ("Makefile", False, O),
        ],
    )


def main() -> int:
    groups = [
        run_express_dir_style,
        run_spring_suffix_style,
        run_python_style,
        run_repository_folders,
        run_precedence,
        run_unknown,
    ]
    results = [group() for group in groups]
    print()
    print("ALL PASSED" if all(results) else "SOME CHECKS FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
