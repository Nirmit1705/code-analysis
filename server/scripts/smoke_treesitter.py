"""Manual smoke test for Phase 3.2 (language detection + Tree-sitter parser
registry).

Three parts:

1. `run_language_detect_unit_checks()` — extension -> `Language` mapping,
   including the negative case (an unrecognized extension classifies as
   `None`, not an error).
2. `run_registry_fixture_checks()` — every one of the five registered
   grammars (JavaScript, TypeScript, TSX, Python, Java) parses a small
   hand-written snippet with zero Tree-sitter parse errors, plus a check
   that `get_parser` actually caches/reuses a `Parser` instead of rebuilding
   one per call.
3. `run_live_ingestion_check()` — reuses Phase 3.1's `ingest_repository`
   against real files from `Nirmit1705/SubTrack` (the same one-off
   ingestion-smoke-test repo from 3.1 — still not a commitment to a fixed
   Phase 3+ sample repo), classifies every extracted file, and parses a
   sample of real files per language actually present in that repo. Java
   isn't present in that repo, which is exactly why (2) exists — every
   grammar gets a real parse one way or another, not just whatever
   languages one sample repo happens to contain.

Run: `uv run python scripts/smoke_treesitter.py` (from `server/`).
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import ingestion, language_detect  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.treesitter import parser_registry  # noqa: E402
from app.services import github_service  # noqa: E402

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "SubTrack"

FIXTURES: dict[Language, bytes] = {
    Language.PYTHON: b"def foo(x):\n    if x:\n        return 1\n    return 2\n",
    Language.JAVASCRIPT: b"function foo(x) {\n  if (x) { return 1; }\n  return 2;\n}\n",
    Language.TYPESCRIPT: b"function foo(x: number): number {\n  return x + 1;\n}\n",
    Language.TSX: b"""
import React from "react";

export function Greeting({ name }: { name: string }) {
  if (!name) {
    return <span>Hello, world!</span>;
  }
  return <span>Hello, {name}!</span>;
}
""",
    Language.JAVA: b"""
package com.example;

public class Greeter {
    private final String name;

    public Greeter(String name) {
        this.name = name;
    }

    public String greet() {
        if (name == null) {
            return "Hello, world!";
        }
        return "Hello, " + name + "!";
    }
}
""",
}


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def run_language_detect_unit_checks() -> bool:
    print("=== language_detect: extension -> Language ===")
    cases: list[tuple[str, Language | None]] = [
        ("src/App.tsx", Language.TSX),
        ("src/index.ts", Language.TYPESCRIPT),
        ("src/legacy.jsx", Language.JAVASCRIPT),
        ("src/util.mjs", Language.JAVASCRIPT),
        ("app/main.py", Language.PYTHON),
        ("app/types.pyi", Language.PYTHON),
        ("src/Main.java", Language.JAVA),
        ("README.md", None),
        ("Dockerfile", None),
        ("noext", None),
    ]
    all_passed = True
    for path, expected in cases:
        actual = language_detect.detect_language(path)
        all_passed &= _check(f"{path} -> {expected}", actual == expected, f"got {actual}")
    return all_passed


def run_registry_fixture_checks() -> bool:
    print("\n=== Tree-sitter registry: fixture parse checks (all 5 grammars) ===")
    all_passed = True
    for language, source in FIXTURES.items():
        parser = parser_registry.get_parser(language)
        tree = parser.parse(source)
        has_error = tree.root_node.has_error
        print(f"  {language.value}: root={tree.root_node.type!r} has_error={has_error}")
        all_passed &= _check(f"{language.value} fixture parses without error", not has_error)

    parser_a = parser_registry.get_parser(Language.PYTHON)
    parser_b = parser_registry.get_parser(Language.PYTHON)
    all_passed &= _check("get_parser caches/reuses the same Parser instance", parser_a is parser_b)

    warmed = parser_registry.warm_up_all()
    all_passed &= _check("warm_up_all covers every Language member", set(warmed) == set(Language))

    return all_passed


def run_live_ingestion_check() -> bool:
    print(f"\n=== Live check: real files from {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
    try:
        installations = github_service.list_installations()
        installation = next(
            (i for i in installations if i["account"]["login"].lower() == LIVE_TEST_OWNER.lower()), None
        )
        if installation is None:
            print(f"  [SKIP] no installation found for account {LIVE_TEST_OWNER!r}")
            return True

        token = github_service.get_installation_access_token(str(installation["id"]))["token"]
        branch = github_service.get_branch(token, LIVE_TEST_OWNER, LIVE_TEST_REPO, "main")
        commit_sha = branch["commit"]["sha"]

        with ingestion.ingest_repository(
            installation_token=token, owner=LIVE_TEST_OWNER, repo=LIVE_TEST_REPO, commit_sha=commit_sha
        ) as result:
            by_language: dict[Language | None, list] = defaultdict(list)
            for f in result.files:
                by_language[language_detect.detect_language(f.path)].append(f)

            print("  file counts by detected language:")
            for language, files in sorted(
                by_language.items(), key=lambda kv: kv[0].value if kv[0] else "~unclassified"
            ):
                label = language.value if language else "(unclassified)"
                print(f"    {label}: {len(files)}")

            all_passed = True
            parsed_any = False
            for language, files in by_language.items():
                if language is None:
                    continue
                for f in files[:3]:
                    source = f.absolute_path.read_bytes()
                    parser = parser_registry.get_parser(language)
                    tree = parser.parse(source)
                    has_error = tree.root_node.has_error
                    parsed_any = True
                    print(
                        f"    parsed {f.path} ({language.value}): root={tree.root_node.type!r} "
                        f"has_error={has_error}"
                    )
                    all_passed &= _check(f"{f.path} parses without error", not has_error)

            all_passed &= _check("at least one real file was parsed", parsed_any)
            return all_passed
    except Exception as exc:  # noqa: BLE001 — smoke script, report and continue
        print(f"  [ERROR] live check failed: {exc!r}")
        return False


if __name__ == "__main__":
    r1 = run_language_detect_unit_checks()
    r2 = run_registry_fixture_checks()
    r3 = run_live_ingestion_check()
    print("\n=== Summary ===")
    print(f"language_detect unit checks: {'PASS' if r1 else 'FAIL'}")
    print(f"registry fixture checks:     {'PASS' if r2 else 'FAIL'}")
    print(f"live ingestion check:        {'PASS' if r3 else 'FAIL (see above — may be a network/credentials issue)'}")
    sys.exit(0 if (r1 and r2) else 1)
