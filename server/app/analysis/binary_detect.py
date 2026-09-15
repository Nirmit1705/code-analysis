"""Binary-content detection — a small, targeted addition alongside Phase
3.7's metrics (`metrics.py::compute_file_metrics`'s own docstring
previously noted "Phase 3 has no separate 'is this file text' gate"; this
module is that gate).

Reuses the exact method already proven correct and already user-verified
at `app/api/v1/codebase.py::get_file_content` (Phase 6.7) — a strict
UTF-8 decode, `True` on `UnicodeDecodeError`, `False` on success — rather
than a different heuristic (e.g. a NUL-byte sniff over a fixed prefix
window, the classic git approach) that could disagree with that endpoint
on some edge-case file. This is deliberately "is this decodable as the
encoding the rest of this pipeline already assumes" (see
`compute_file_metrics`'s own `errors="replace"` UTF-8 decode and every
`symbols/*.py` Tree-sitter parser — UTF-8 throughout this codebase), not
a general-purpose "is this file binary" oracle for every possible text
encoding.

Known limitation, not attempted to be solved here: a genuine text file
encoded in something other than UTF-8 (e.g. Windows-1252/Latin-1, or
UTF-16) will fail this check and be misclassified as binary, losing its
real line count — the same limitation `get_file_content` already has
today, just not yet exercised by metrics. Judged acceptable for this
platform's initial scope (JS/TS/TSX/Python/Java source and common
config/doc formats are overwhelmingly UTF-8 in practice); revisit with a
real encoding-detection library only if real-repo dogfooding shows this
misclassifying genuine source files.

Pure with respect to the rest of the app, matching every other module
under `analysis/`: no DB session, no ORM models, no Celery task decorator.
"""

from __future__ import annotations


def is_binary(source: bytes) -> bool:
    """`True` if `source` cannot be decoded as UTF-8 text (a strict
    decode — no `errors=` argument — so any invalid byte sequence
    anywhere in the file trips it, not a fixed prefix window); `False`
    for genuine (or empty) UTF-8-decodable bytes. Never raises."""
    try:
        source.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False
