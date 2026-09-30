"""Smoke test for Phase 9.7's `change_narrative_service` + `build_change_
explanation_prompt` (the AI narrative on Compare and Impact).

Five gating groups, all offline (a stub `LLMProvider`, no DB, no network):

1. The prompt: built only from the pre-digested facts, every section present
   or "none", long lists capped with a "(+N more)" entry, the impact section
   present only when impact data is, and a strict single-field JSON schema.
2. The happy path: a well-formed response comes back as plain text, with the
   odd narrow/no-break spaces the model emits normalized away and an absurdly
   long answer truncated.
3. Every way it can go wrong degrades to `None` and **never raises**: bad
   JSON, a JSON array, a missing key, a non-string, an empty string,
   `LLMProviderError`, a misconfigured provider (`RuntimeError`), and an
   arbitrary exception — the plan's own literal contract.
4. An empty diff makes no LLM call at all (nothing to explain, nothing to
   invent), even when the debt score alone moved.
5. The cache: the same facts twice make one call, different facts make two,
   and a failure is never cached (the next call retries).

Group 6 (non-gating live bonus) runs the real Groq provider over the two most
recent real, *different-commit* completed runs of the `Nirmit1705/HireSight`
repository in this database, for both Compare and Impact facts, and prints the
narratives for a human to eyeball against what is actually true of the diff.
It skips cleanly if that repository has no suitable pair of runs.

Run: `uv run python scripts/smoke_change_narrative.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import func, select  # noqa: E402

from app.ai.prompts import build_change_explanation_prompt  # noqa: E402
from app.ai.provider import LLMProviderError  # noqa: E402
from app.analysis.change import ApiFact, ChangeSet, FileFact, SymbolFact, classify_change, diff_analysis_runs  # noqa: E402
from app.analysis.dependencies import DependencyEdge, DependencyEdgeType  # noqa: E402
from app.analysis.language_detect import Language  # noqa: E402
from app.analysis.symbols import SymbolKind  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_run import AnalysisRun, AnalysisRunStatus  # noqa: E402
from app.models.repository import Repository  # noqa: E402
from app.services import analysis_read_service, change_narrative_service, impact_service  # noqa: E402
from app.services.change_narrative_service import build_change_facts, generate_change_explanation  # noqa: E402
from app.services.impact_service import ImpactedFile, ImpactResult, ScoreBreakdown  # noqa: E402

LIVE_TEST_FULL_NAME = "Nirmit1705/HireSight"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    result = "PASS" if condition else "FAIL"
    print(f"  [{result}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


class _Stub:
    """A fake `LLMProvider`: returns a fixed string, or raises a fixed
    exception, and counts its calls."""

    def __init__(self, response: str | None = None, *, raises: Exception | None = None):
        self._response = response
        self._raises = raises
        self.calls = 0

    def complete(self, *, system: str, messages: list[dict], response_schema: dict | None = None) -> str:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        assert self._response is not None
        return self._response


def _edge(source: str, target: str, *, external: bool = False) -> DependencyEdge:
    return DependencyEdge(
        source_file=source,
        module=str(Path(source).parent).replace("\\", "/"),
        language=Language.TYPESCRIPT,
        edge_type=DependencyEdgeType.EXTERNAL if external else DependencyEdgeType.INTERNAL,
        target_file=None if external else target,
        target_package=target if external else None,
        raw_specifier=target,
        line=1,
    )


def _change_set(*, added_files: int = 1, modified: int = 1) -> ChangeSet:
    return ChangeSet(
        added_files=[FileFact(path=f"src/new_{i}.ts", language=Language.TYPESCRIPT, loc=10) for i in range(added_files)],
        removed_files=[],
        modified_files=[f"src/mod_{i}.ts" for i in range(modified)],
        added_symbols=[SymbolFact(file_path="src/new_0.ts", name="makeThing", kind=SymbolKind.FUNCTION)]
        if added_files
        else [],
        removed_symbols=[],
        added_apis=[ApiFact(method="POST", path="/api/things", file_path="src/new_0.ts")] if added_files else [],
        removed_apis=[ApiFact(method="GET", path="/api/old", file_path="src/old.ts")],
        added_edges=[_edge("src/mod_0.ts", "@prisma/client", external=True)] if modified else [],
        removed_edges=[],
    )


def _impact() -> ImpactResult:
    return ImpactResult(
        risk="MEDIUM",
        score=31,
        direct=[ImpactedFile(path="src/ctrl.ts", hops=1, why="imports `src/mod_0.ts`")],
        indirect=[ImpactedFile(path="src/routes.ts", hops=2, why="imports `src/ctrl.ts`")],
        affected_apis=["POST /api/things"],
        breakdown=ScoreBreakdown(
            file_points=9.0, api_points=5.0, cycle_bonus=0.0, change_kind="non-breaking", multiplier=0.75,
            excluded_test_files=2,
        ),
    )


_GOOD = '{"explanation": "One file changed and two others depend on it."}'


# --- group 1: the prompt -------------------------------------------------------------


def run_prompt() -> bool:
    print("=== group 1: build_change_explanation_prompt — built only from the given facts ===")
    all_passed = True

    facts = build_change_facts(
        base_ref="main@aaaaaaa", head_ref="main@bbbbbbb", change_set=_change_set(), debt_score_delta=-3.5, impact=_impact()
    )
    system, messages, schema = build_change_explanation_prompt(facts)
    user = messages[0]["content"]

    all_passed &= _check("one user message, no extra turns", len(messages) == 1 and messages[0]["role"] == "user")
    all_passed &= _check(
        "the comparison, every added/removed/modified fact and the debt delta are in the prompt",
        all(
            s in user
            for s in (
                "main@aaaaaaa -> main@bbbbbbb",
                "src/new_0.ts",
                "src/mod_0.ts",
                "makeThing (function) in src/new_0.ts",
                "POST /api/things",
                "GET /api/old",
                "added: src/mod_0.ts imports @prisma/client",
                "-3.5",
            )
        ),
        user,
    )
    all_passed &= _check(
        "empty sections say 'none' instead of disappearing (Files removed, Symbols removed)",
        "Files removed: none" in user and "Symbols removed: none" in user,
        user,
    )
    all_passed &= _check(
        "the impact section carries risk, score, change type, both dependent lists, the APIs and the excluded tests",
        all(
            s in user
            for s in (
                "risk MEDIUM",
                "score 31/100",
                "change type non-breaking",
                "src/ctrl.ts",
                "src/routes.ts",
                "POST /api/things",
                "2 test file(s)",
            )
        ),
        user,
    )
    all_passed &= _check(
        "the system prompt forbids guessing intent, markdown, and claiming anything is unaffected/safe without facts",
        "Do not guess why" in system and "no markdown" in system and "absence of information is not evidence" in system,
    )
    all_passed &= _check("the shared grounding preamble is included", "Answer only using the provided context" in system)
    all_passed &= _check(
        "strict single-field schema: {explanation: string}, required, no additional properties",
        schema["required"] == ["explanation"]
        and schema["additionalProperties"] is False
        and schema["properties"] == {"explanation": {"type": "string"}},
        str(schema),
    )

    no_impact = build_change_facts(
        base_ref="a", head_ref="b", change_set=_change_set(), debt_score_delta=0.0, impact=None
    )
    all_passed &= _check(
        "no impact section at all when called from Compare",
        "Downstream impact" not in build_change_explanation_prompt(no_impact)[1][0]["content"],
    )

    big = build_change_facts(
        base_ref="a", head_ref="b", change_set=_change_set(added_files=20, modified=0), debt_score_delta=0.0
    )
    all_passed &= _check(
        "20 added files are capped at 12, plus a '(+8 more)' entry",
        len(big.added_files) == 13 and big.added_files[-1] == "(+8 more)",
        str(big.added_files),
    )
    return all_passed


# --- group 2: happy path -------------------------------------------------------------


def run_happy_path() -> bool:
    print("\n=== group 2: a well-formed response comes back as clean plain text ===")
    all_passed = True
    facts = build_change_facts(base_ref="a", head_ref="b", change_set=_change_set(), debt_score_delta=0.0)

    change_narrative_service.clear_cache()
    out = generate_change_explanation(facts, _Stub(_GOOD))
    all_passed &= _check("returns the explanation string", out == "One file changed and two others depend on it.", repr(out))

    change_narrative_service.clear_cache()
    out = generate_change_explanation(
        facts, _Stub('{"explanation": "  It changed\\u202fin\\u00a0server\\u2009code.  "}')
    )
    all_passed &= _check(
        "narrow / no-break / thin spaces become normal spaces and the ends are trimmed",
        out == "It changed in server code.",
        repr(out),
    )

    change_narrative_service.clear_cache()
    out = generate_change_explanation(facts, _Stub('{"explanation": "' + "x" * 5000 + '"}'))
    all_passed &= _check("an absurdly long answer is truncated", out is not None and len(out) == 1500, str(len(out or "")))
    return all_passed


# --- group 3: every failure degrades to None ----------------------------------------


def run_degrade() -> bool:
    print("\n=== group 3: every failure mode -> None, and it never raises ===")
    all_passed = True
    facts = build_change_facts(base_ref="a", head_ref="b", change_set=_change_set(), debt_score_delta=0.0)

    cases: list[tuple[str, _Stub]] = [
        ("malformed JSON", _Stub("not json at all")),
        ("a JSON array instead of an object", _Stub('["explanation"]')),
        ("an object missing the `explanation` key", _Stub('{"summary": "x"}')),
        ("a non-string `explanation`", _Stub('{"explanation": 42}')),
        ("an empty explanation", _Stub('{"explanation": ""}')),
        ("a whitespace-only explanation", _Stub('{"explanation": "   \\n "}')),
        ("LLMProviderError (real provider failure)", _Stub(raises=LLMProviderError("simulated Groq outage"))),
        ("RuntimeError (misconfigured provider)", _Stub(raises=RuntimeError("LLM_PROVIDER not supported"))),
        ("an arbitrary unexpected exception", _Stub(raises=ValueError("boom"))),
    ]
    for label, stub in cases:
        change_narrative_service.clear_cache()
        try:
            out = generate_change_explanation(facts, stub)
        except Exception as exc:  # noqa: BLE001
            all_passed &= _check(f"{label} -> None", False, f"RAISED {exc!r}")
            continue
        all_passed &= _check(f"{label} -> None, no exception", out is None and stub.calls == 1, repr(out))
    return all_passed


# --- group 4: empty diff --------------------------------------------------------------


def run_empty_diff() -> bool:
    print("\n=== group 4: an empty diff makes no LLM call ===")
    empty = ChangeSet(
        added_files=[], removed_files=[], modified_files=[], added_symbols=[], removed_symbols=[],
        added_apis=[], removed_apis=[], added_edges=[], removed_edges=[],
    )
    facts = build_change_facts(base_ref="a", head_ref="b", change_set=empty, debt_score_delta=12.0)
    stub = _Stub(_GOOD)
    change_narrative_service.clear_cache()
    out = generate_change_explanation(facts, stub)
    return _check(
        "nothing differs (even though the debt score moved) -> None with zero provider calls",
        out is None and stub.calls == 0,
        f"out={out!r}, calls={stub.calls}",
    )


# --- group 5: cache -------------------------------------------------------------------


def run_cache() -> bool:
    print("\n=== group 5: the in-process cache ===")
    all_passed = True
    facts_a = build_change_facts(base_ref="a", head_ref="b", change_set=_change_set(), debt_score_delta=0.0)
    facts_b = build_change_facts(base_ref="a", head_ref="c", change_set=_change_set(), debt_score_delta=0.0)

    change_narrative_service.clear_cache()
    stub = _Stub(_GOOD)
    first = generate_change_explanation(facts_a, stub)
    second = generate_change_explanation(facts_a, stub)
    all_passed &= _check("the same facts twice -> one provider call, same text", first == second and stub.calls == 1, f"calls={stub.calls}")
    generate_change_explanation(facts_b, stub)
    all_passed &= _check("different facts -> a second call", stub.calls == 2, f"calls={stub.calls}")

    change_narrative_service.clear_cache()
    failing = _Stub(raises=LLMProviderError("down"))
    generate_change_explanation(facts_a, failing)
    recovered = _Stub(_GOOD)
    out = generate_change_explanation(facts_a, recovered)
    all_passed &= _check(
        "a failure is never cached: the next call retries and succeeds",
        out is not None and recovered.calls == 1,
        f"out={out!r}, calls={recovered.calls}",
    )
    return all_passed


# --- group 6: live bonus --------------------------------------------------------------


def run_live_bonus() -> bool:
    print(f"\n=== group 6 (non-gating): real Groq narratives over {LIVE_TEST_FULL_NAME}'s real runs ===")
    db = SessionLocal()
    try:
        # HireSight exists as several Repository rows (one per test user) —
        # take the one with the most completed runs.
        row = db.execute(
            select(AnalysisRun.repository_id, func.count().label("n"))
            .join(Repository, Repository.id == AnalysisRun.repository_id)
            .where(Repository.full_name == LIVE_TEST_FULL_NAME, AnalysisRun.status == AnalysisRunStatus.COMPLETED)
            .group_by(AnalysisRun.repository_id)
            .order_by(func.count().desc())
        ).first()
        if row is None:
            print(f"  [SKIP] no completed runs for {LIVE_TEST_FULL_NAME} in this database")
            return True

        runs = list(
            db.execute(
                select(AnalysisRun)
                .where(AnalysisRun.repository_id == row.repository_id, AnalysisRun.status == AnalysisRunStatus.COMPLETED)
                .order_by(AnalysisRun.completed_at.desc())
            ).scalars()
        )
        head = runs[0]
        base = next((r for r in runs[1:] if r.commit_sha != head.commit_sha and r.branch == head.branch), None)
        if base is None:
            print("  [SKIP] no earlier completed run with a different commit on the same branch")
            return True

        print(f"  head {head.commit_sha[:7]}  vs  base {base.commit_sha[:7]}  (branch {head.branch})")
        change_set = diff_analysis_runs(
            analysis_read_service.load_run_facts(db, base.id), analysis_read_service.load_run_facts(db, head.id)
        )
        changed = (
            {f.path for f in change_set.added_files}
            | {f.path for f in change_set.removed_files}
            | set(change_set.modified_files)
        )
        impact = impact_service.compute_impact(
            db, head_analysis_run_id=head.id, changed_file_paths=changed, change_kind=classify_change(change_set)
        )

        for label, with_impact in (("COMPARE narrative", None), ("IMPACT narrative", impact)):
            facts = build_change_facts(
                base_ref=f"{base.branch}@{base.commit_sha[:7]}",
                head_ref=f"{head.branch}@{head.commit_sha[:7]}",
                change_set=change_set,
                debt_score_delta=head.debt_score - base.debt_score,
                impact=with_impact,
            )
            change_narrative_service.clear_cache()
            text = generate_change_explanation(facts)
            print(f"\n  {label}:")
            print(f"    facts: {len(facts.modified_files)} modified, {len(facts.added_files)} added, "
                  f"{len(facts.dependency_changes)} dependency change(s)"
                  + (f", impact {impact.risk}/{impact.score}" if with_impact else ""))
            print(f"    -> {text!r}")
            if text is None:
                print("  [WARN] live call returned None (provider down or no key?)")
                return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [ERROR] live bonus failed: {exc!r}")
        return False
    finally:
        db.close()


def main() -> int:
    r1 = run_prompt()
    r2 = run_happy_path()
    r3 = run_degrade()
    r4 = run_empty_diff()
    r5 = run_cache()
    r6 = run_live_bonus()

    print("\n=== Summary ===")
    print(f"1. Prompt:                       {'PASS' if r1 else 'FAIL'}")
    print(f"2. Happy path:                   {'PASS' if r2 else 'FAIL'}")
    print(f"3. Every failure -> None:        {'PASS' if r3 else 'FAIL'}")
    print(f"4. Empty diff, no call:          {'PASS' if r4 else 'FAIL'}")
    print(f"5. Cache:                        {'PASS' if r5 else 'FAIL'}")
    print(f"6. Live bonus (non-gating):      {'PASS' if r6 else 'FAIL (see above)'}")
    return 0 if (r1 and r2 and r3 and r4 and r5) else 1


if __name__ == "__main__":
    sys.exit(main())
