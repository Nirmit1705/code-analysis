"""URL path composition helper shared by all three Phase 3.6 framework
analyzers — the only piece of route-path arithmetic they all need
(Express prefix + route path, Spring's class `@RequestMapping` prefix +
method annotation path, FastAPI/Flask router-prefix + include/register
prefix + route decorator path).

Leading-underscore module name: internal support code for the `apis`
package, not a public extraction entry point (matches the plain-function
style of the rest of `analysis/`, just scoped private to this package).
"""

from __future__ import annotations


def join_url_path(prefix: str, path: str) -> str:
    """Composes a path prefix and a route's own path into one full URL
    path, normalizing the slash between them.

    `path=""` (nothing to add — e.g. a `prefix=`/`url_prefix=` kwarg that
    was never given) returns `prefix` unchanged rather than appending a
    spurious trailing slash. Otherwise a missing leading slash on either
    side is tolerated defensively (both frameworks' own conventions
    always include one, but a fixture or real source might not).

        join_url_path("/api", "/users") == "/api/users"
        join_url_path("", "/users") == "/users"
        join_url_path("/api/v1", "") == "/api/v1"
        join_url_path("", "") == "/"
    """
    prefix = prefix.rstrip("/") if prefix else ""
    if not path:
        return prefix or "/"
    if not path.startswith("/"):
        path = "/" + path
    return (prefix + path) or "/"
