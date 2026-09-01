"""Manual smoke test for Phase 3.1 (repository ingestion).

Two parts:

1. `run_fixture_tests()` — builds small, deliberately hostile tarballs
   in-memory (path traversal, symlink escape, decompression-bomb-style size
   lies, ignore-pattern coverage) and feeds them through
   `app.analysis.ingestion.ingest_repository`'s real extraction logic, with
   only the network download step monkeypatched out. This is the actual
   security check — deterministic, no network or credentials required.
2. `run_live_smoke_test()` — a real end-to-end run: mints a real GitHub App
   installation token and downloads+extracts a real repo
   (`Nirmit1705/SubTrack`, chosen for this one-off ingestion smoke test only
   — not a commitment that it's "the" fixed Phase 3+ sample repo), printing
   the extracted file count and a sample of ignored paths.

Run: `uv run python scripts/smoke_ingestion.py` (from `server/`).
"""

from __future__ import annotations

import io
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis import ingestion  # noqa: E402
from app.services import github_service  # noqa: E402

LIVE_TEST_OWNER = "Nirmit1705"
LIVE_TEST_REPO = "SubTrack"


def _write_entry(tar: tarfile.TarFile, name: str, data: bytes = b"x") -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _write_symlink(tar: tarfile.TarFile, name: str, target: str) -> None:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    tar.addfile(info)


def _write_dir(tar: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.DIRTYPE
    tar.addfile(info)


def _build_archive(path: Path, entries: list[tuple]) -> None:
    """`entries` is a list of ("file", name, data) / ("symlink", name, target)
    / ("dir", name) tuples."""
    with tarfile.open(path, "w:gz") as tar:
        for entry in entries:
            kind = entry[0]
            if kind == "file":
                _write_entry(tar, entry[1], entry[2])
            elif kind == "symlink":
                _write_symlink(tar, entry[1], entry[2])
            elif kind == "dir":
                _write_dir(tar, entry[1])
            else:
                raise ValueError(f"unknown entry kind: {kind}")


def _ingest_fixture(archive_entries: list[tuple], **kwargs):
    """Builds a fixture archive and runs it through `ingest_repository`,
    with the network download step replaced by a copy of the fixture file to
    `dest_path` — everything downstream (extraction, safety checks) is the
    real code under test."""
    with tempfile.TemporaryDirectory(prefix="smoke-fixture-") as tmp:
        archive_path = Path(tmp) / "fixture.tar.gz"
        _build_archive(archive_path, archive_entries)

        def fake_download(*, dest_path: Path, **_kwargs) -> int:
            dest_path.write_bytes(archive_path.read_bytes())
            return archive_path.stat().st_size

        with patch.object(github_service, "download_repository_archive", side_effect=fake_download):
            with ingestion.ingest_repository(
                installation_token="fake-token", owner="owner", repo="repo", commit_sha="deadbeef", **kwargs
            ) as result:
                # Copy out what we need before the temp dir is cleaned up.
                return (
                    sorted(f.path for f in result.files),
                    sorted(result.ignored_paths),
                    result.root_dir,
                )


def _check(label: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def run_fixture_tests() -> bool:
    print("=== Fixture-based security tests (no network) ===")
    all_passed = True
    top = "owner-repo-abc123"

    # 1. Baseline: legitimate files kept, common junk directories ignored.
    files, ignored, _root = _ingest_fixture(
        [
            ("dir", f"{top}/"),
            ("file", f"{top}/src/main.py", b"print('hi')\n"),
            ("file", f"{top}/README.md", b"# hi\n"),
            ("dir", f"{top}/node_modules/"),
            ("file", f"{top}/node_modules/pkg/index.js", b"module.exports = {}\n"),
            ("dir", f"{top}/.git/"),
            ("file", f"{top}/.git/HEAD", b"ref: refs/heads/main\n"),
            ("file", f"{top}/backend/__pycache__/mod.pyc", b"\x00\x01"),
        ]
    )
    all_passed &= _check("kept src/main.py and README.md", files == ["README.md", "src/main.py"], str(files))
    all_passed &= _check(
        "ignored node_modules/, .git/, __pycache__/ contents",
        "node_modules/pkg/index.js" in ignored and ".git/HEAD" in ignored and "backend/__pycache__/mod.pyc" in ignored,
        str(ignored),
    )

    # 2. Path traversal via "../" segments must never escape extract_dir.
    files, ignored, root = _ingest_fixture(
        [
            ("dir", f"{top}/"),
            ("file", f"{top}/../../evil.txt", b"pwned"),
            ("file", f"{top}/safe.txt", b"ok"),
        ]
    )
    escaped = (root.parent.parent / "evil.txt").exists() or (root.parent / "evil.txt").exists()
    all_passed &= _check("traversal entry rejected, not written to disk", not escaped)
    all_passed &= _check("traversal entry recorded as ignored", any("evil.txt" in p for p in ignored), str(ignored))
    all_passed &= _check("sibling legitimate file still extracted", files == ["safe.txt"], str(files))

    # 3. Backslash-encoded traversal (defends a Windows host against a
    #    maliciously crafted archive using the "wrong" separator).
    files, ignored, root = _ingest_fixture(
        [("dir", f"{top}/"), ("file", top + "\\..\\..\\evil2.txt", b"pwned")]
    )
    escaped = (root.parent.parent / "evil2.txt").exists()
    all_passed &= _check("backslash traversal entry rejected", not escaped and files == [])

    # 4. Absolute path entry.
    files, ignored, root = _ingest_fixture([("dir", f"{top}/"), ("file", "/etc/passwd", b"pwned")])
    all_passed &= _check("absolute-path entry rejected", files == [] and any("passwd" in p for p in ignored))

    # 5. Symlink escape — must be refused outright, never followed/extracted.
    files, ignored, root = _ingest_fixture(
        [
            ("dir", f"{top}/"),
            ("symlink", f"{top}/link_to_etc", "/etc/passwd"),
            ("file", f"{top}/real.txt", b"ok"),
        ]
    )
    all_passed &= _check(
        "symlink entry rejected, plain file still extracted",
        files == ["real.txt"] and any("link_to_etc" in p for p in ignored),
        f"files={files} ignored={ignored}",
    )
    all_passed &= _check("no symlink materialized on disk", not any(p.is_symlink() for p in root.rglob("*")))

    # 6. Decompressed-size cap (guards against a decompression bomb: a
    #    small compressed archive whose header claims a huge payload).
    try:
        _ingest_fixture(
            [("dir", f"{top}/"), ("file", f"{top}/big.bin", b"x" * 1000)],
            max_extracted_bytes=100,
        )
        all_passed &= _check("oversized extraction raises IngestionError", False)
    except ingestion.IngestionError:
        all_passed &= _check("oversized extraction raises IngestionError", True)

    # 7. File-count cap.
    try:
        _ingest_fixture(
            [("dir", f"{top}/")] + [("file", f"{top}/f{i}.txt", b"x") for i in range(10)],
            max_file_count=3,
        )
        all_passed &= _check("file-count cap raises IngestionError", False)
    except ingestion.IngestionError:
        all_passed &= _check("file-count cap raises IngestionError", True)

    return all_passed


def run_live_smoke_test() -> bool:
    print(f"\n=== Live smoke test: {LIVE_TEST_OWNER}/{LIVE_TEST_REPO} ===")
    try:
        installations = github_service.list_installations()
        installation = next(
            (i for i in installations if i["account"]["login"].lower() == LIVE_TEST_OWNER.lower()), None
        )
        if installation is None:
            print(f"  [SKIP] no installation found for account {LIVE_TEST_OWNER!r}")
            return True

        token_data = github_service.get_installation_access_token(str(installation["id"]))
        token = token_data["token"]

        branch = github_service.get_branch(token, LIVE_TEST_OWNER, LIVE_TEST_REPO, "main")
        commit_sha = branch["commit"]["sha"]
        print(f"  resolved {LIVE_TEST_OWNER}/{LIVE_TEST_REPO}@main -> {commit_sha}")

        with ingestion.ingest_repository(
            installation_token=token,
            owner=LIVE_TEST_OWNER,
            repo=LIVE_TEST_REPO,
            commit_sha=commit_sha,
        ) as result:
            print(f"  extracted {len(result.files)} files into {result.root_dir}")
            for f in result.files[:10]:
                print(f"    - {f.path} ({f.size_bytes} bytes)")
            if len(result.files) > 10:
                print(f"    ... and {len(result.files) - 10} more")
            print(f"  ignored {len(result.ignored_paths)} paths (sample):")
            for p in result.ignored_paths[:10]:
                print(f"    - {p}")
        return len(result.files) > 0
    except Exception as exc:  # noqa: BLE001 — smoke script, report and continue
        print(f"  [ERROR] live smoke test failed: {exc!r}")
        return False


if __name__ == "__main__":
    fixtures_ok = run_fixture_tests()
    live_ok = run_live_smoke_test()
    print("\n=== Summary ===")
    print(f"fixture tests: {'PASS' if fixtures_ok else 'FAIL'}")
    print(f"live smoke test: {'PASS' if live_ok else 'FAIL (see above — may be a network/credentials issue)'}")
    sys.exit(0 if fixtures_ok else 1)
