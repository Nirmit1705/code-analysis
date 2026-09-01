"""Repository ingestion (Phase 3.1): fetches a repo at an exact commit via
GitHub's tarball API and safely extracts it to a local temp directory for
the rest of the Phase 3 pipeline to read from.

Pure with respect to the rest of the app: no DB session, no ORM models, no
Celery, no notion of a "user" or "repository" row — callers pass in raw
credentials (an installation token) and identifiers (owner/repo/commit_sha)
and get back files on disk. The caller always supplies an exact `commit_sha`;
resolving "the latest commit of the selected branch" is Phase 4/5's job,
done above this module, never here.
"""

from __future__ import annotations

import fnmatch
import logging
import shutil
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.services import github_service

logger = logging.getLogger(__name__)

# Compressed download cap — defense against a huge or runaway response,
# checked against actual streamed bytes (see `github_service.download_repository_archive`).
DEFAULT_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024  # 200 MB compressed

# Decompressed extraction cap, tracked separately from the above: gzip can
# hide a much larger payload behind a small compressed size ("decompression
# bomb"), so the compressed cap alone isn't a sufficient guard.
DEFAULT_MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024  # 1 GB uncompressed

# A repo with an enormous number of tiny files is a cheap way to exhaust
# inodes/memory even while staying under the byte caps above.
DEFAULT_MAX_FILE_COUNT = 50_000

# Matched against either the full relative path (glob) or, for a
# slash-free pattern, against any individual path segment — so
# "node_modules" ignores that directory no matter how deep it appears.
DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    ".next",
    "target",
    "out",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "*.egg-info",
)


class IngestionError(Exception):
    """Raised for any ingestion failure: an oversized archive, a malformed
    tarball, or the extraction hitting a configured safety cap."""


@dataclass(frozen=True)
class IngestedFile:
    """One file that survived ingestion, relative to the repository root
    (GitHub's generated top-level `{owner}-{repo}-{short_sha}/` directory is
    stripped off)."""

    path: str  # POSIX-style, relative, e.g. "src/app/main.py"
    absolute_path: Path
    size_bytes: int


@dataclass(frozen=True)
class IngestionResult:
    root_dir: Path
    files: list[IngestedFile]
    ignored_paths: list[str] = field(default_factory=list)


def _matches_ignore_patterns(relative_posix_path: str, ignore_patterns: tuple[str, ...]) -> bool:
    parts = PurePosixPath(relative_posix_path).parts
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(relative_posix_path, pattern):
            return True
        if "/" not in pattern and any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def _safe_relative_path(member_name: str) -> str | None:
    """Validates and strips the leading path component (GitHub's generated
    top-level directory) from a tar member name, returning a safe,
    repo-relative POSIX path — or `None` if the member should be rejected
    outright: an absolute path, a Windows drive-letter path, a `..`
    traversal segment anywhere in the path, or an empty/top-level-only
    entry.
    """
    # Tar entries are POSIX paths, but a maliciously crafted archive could
    # still smuggle backslashes intended to be reinterpreted as separators
    # on a Windows host — normalize defensively before parsing.
    normalized = member_name.replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        return None

    posix = PurePosixPath(normalized)
    if posix.is_absolute() or posix.drive:
        return None
    if any(part in ("..", "") for part in posix.parts):
        return None
    if len(posix.parts) < 2:
        return None  # the shared top-level directory entry itself

    return "/".join(posix.parts[1:])


@contextmanager
def ingest_repository(
    *,
    installation_token: str,
    owner: str,
    repo: str,
    commit_sha: str,
    ignore_patterns: tuple[str, ...] = DEFAULT_IGNORE_PATTERNS,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
    max_file_count: int = DEFAULT_MAX_FILE_COUNT,
) -> Iterator[IngestionResult]:
    """Fetches `repo` at `commit_sha` via GitHub's tarball API and extracts
    it into a fresh temp directory, enforcing (in order): a compressed
    download-size cap, a decompressed extraction-size cap, a file-count cap,
    and per-entry path-traversal / symlink / device-file rejection.
    `ignore_patterns` skips whole directories/files before they're ever
    written to disk (defaults cover `.git/`, `node_modules/`, `vendor/`, and
    other common build/dependency directories).

    A context manager so the temp directory is always cleaned up, success or
    failure:

        with ingest_repository(installation_token=..., owner=..., repo=...,
                                commit_sha=...) as result:
            for f in result.files:
                ...
    """
    with tempfile.TemporaryDirectory(prefix="analysis-ingest-") as tmp_str:
        tmp_dir = Path(tmp_str)
        archive_path = tmp_dir / "archive.tar.gz"
        extract_dir = tmp_dir / "extracted"
        extract_dir.mkdir()
        extract_dir_resolved = extract_dir.resolve()

        try:
            github_service.download_repository_archive(
                installation_token=installation_token,
                owner=owner,
                repo=repo,
                ref=commit_sha,
                dest_path=archive_path,
                max_bytes=max_archive_bytes,
            )
        except github_service.GitHubArchiveTooLargeError as exc:
            raise IngestionError(
                f"repository archive exceeded the {exc.limit_bytes}-byte download cap"
            ) from exc

        files: list[IngestedFile] = []
        ignored: list[str] = []
        extracted_bytes = 0

        try:
            with tarfile.open(archive_path, mode="r:gz") as tar:
                for member in tar:
                    relative_path = _safe_relative_path(member.name)
                    if relative_path is None:
                        ignored.append(member.name)
                        continue

                    if member.isdir():
                        continue
                    if not member.isfile():
                        # Symlinks, hardlinks, devices, fifos — not a
                        # legitimate part of a GitHub-generated source
                        # tarball, and a classic extraction-escape vector.
                        ignored.append(relative_path)
                        continue

                    if _matches_ignore_patterns(relative_path, ignore_patterns):
                        ignored.append(relative_path)
                        continue

                    if len(files) >= max_file_count:
                        raise IngestionError(
                            f"repository exceeded the {max_file_count}-file cap during extraction"
                        )

                    extracted_bytes += member.size
                    if extracted_bytes > max_extracted_bytes:
                        raise IngestionError(
                            f"repository exceeded the {max_extracted_bytes}-byte "
                            "extracted-size cap (possible decompression bomb)"
                        )

                    dest_path = extract_dir / relative_path
                    try:
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        resolved = dest_path.resolve()
                        if not resolved.is_relative_to(extract_dir_resolved):
                            # Belt-and-suspenders on top of `_safe_relative_path`'s
                            # traversal checks — never write outside extract_dir.
                            ignored.append(relative_path)
                            continue
                        source = tar.extractfile(member)
                        if source is None:
                            continue
                        with open(dest_path, "wb") as out:
                            shutil.copyfileobj(source, out, length=1024 * 1024)
                    except OSError as exc:
                        # e.g. Windows MAX_PATH on a deeply nested path — skip
                        # this one file rather than fail the whole ingestion.
                        logger.warning("skipping %r: %s", relative_path, exc)
                        ignored.append(relative_path)
                        continue

                    files.append(
                        IngestedFile(path=relative_path, absolute_path=dest_path, size_bytes=member.size)
                    )
        except tarfile.TarError as exc:
            raise IngestionError(f"malformed repository archive: {exc}") from exc

        yield IngestionResult(root_dir=extract_dir, files=files, ignored_paths=ignored)
