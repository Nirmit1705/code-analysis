"""Smoke test for server-side logout (`POST /auth/logout`, `revoked_sessions`,
`auth_service.authenticate_token`).

`TestClient(app)` against the real dev database; offline.

Gating groups:

1. `run_session_ids()` — a pair shares one `sid`; two logins get different
   ones; `/auth/refresh` keeps the `sid`.
2. `run_logout_ends_session()` — after logout (refresh token in the body) the
   session's access token, a token minted by refresh *before* the logout, and
   the refresh token itself are all refused (`/auth/me` 401, `/auth/refresh`
   401, an authenticated route 401); another session of the same user still
   works; a second logout is still 204 with one row.
3. `run_logout_with_expired_access()` — only an *expired* access token as the
   bearer: its session ends, so that session's still-valid refresh token is
   refused.
4. `run_noop_logouts()` — no tokens, garbage, a token signed with another key,
   an action token: all 204 and no row written.
5. `run_legacy_tokens()` — a token without `sid` (minted before this change)
   is refused by `/auth/me` and `/auth/refresh`.
6. `run_websocket()` — the analysis-progress WebSocket closes with 1008 for a
   logged-out session's token.
7. `run_pruning()` — revoking a session deletes rows whose `expires_at` has
   passed, and keeps live ones.

Run: `uv run python scripts/smoke_api_auth_session.py` (from `server/`).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, func, select  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token, create_token_pair, decode_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.revoked_session import RevokedSession  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import auth_service  # noqa: E402

EMAIL = "smoke-auth-session@example.com"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


def _user() -> uuid.UUID:
    """The smoke user's id, with any revocation rows from earlier runs removed."""
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == EMAIL)).scalar_one_or_none()
        if user is None:
            user = User(email=EMAIL)
            db.add(user)
            db.commit()
            db.refresh(user)
        user_id = user.id
        db.execute(delete(RevokedSession).where(RevokedSession.user_id == user_id))
        db.commit()
        return user_id
    finally:
        db.close()


def _rows(user_id: uuid.UUID) -> int:
    db = SessionLocal()
    try:
        return db.execute(select(func.count()).select_from(RevokedSession).where(RevokedSession.user_id == user_id)).scalar_one()
    finally:
        db.close()


def _me(client: TestClient, access: str) -> int:
    return client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"}).status_code


def _refresh(client: TestClient, refresh: str):
    return client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})


def run_session_ids() -> bool:
    print("=== group 1: session ids ===")
    _user()
    client = TestClient(app)
    a1, r1 = create_token_pair(EMAIL)
    a2, r2 = create_token_pair(EMAIL)
    sid = decode_token(a1)["sid"]
    ok = _check("a pair shares one sid", sid == decode_token(r1)["sid"] and len(sid) == 32)
    ok &= _check("two logins get different sids", sid != decode_token(a2)["sid"])
    resp = _refresh(client, r1)
    ok &= _check("refresh -> 200", resp.status_code == 200, resp.text)
    if resp.status_code == 200:
        body = resp.json()
        ok &= _check("refresh keeps the sid", decode_token(body["access_token"])["sid"] == sid == decode_token(body["refresh_token"])["sid"])
    return ok


def run_logout_ends_session() -> bool:
    print("=== group 2: logout ends the whole session, and only that session ===")
    user = _user()
    client = TestClient(app)
    access, refresh = create_token_pair(EMAIL)
    other_access, other_refresh = create_token_pair(EMAIL)
    refreshed = _refresh(client, refresh).json()
    ok = _check("before logout: /me 200", _me(client, access) == 200)

    resp = client.post("/api/v1/auth/logout", json={"refresh_token": refresh}, headers={"Authorization": f"Bearer {access}"})
    ok &= _check("logout -> 204, empty body", resp.status_code == 204 and resp.content == b"", f"{resp.status_code} {resp.text}")
    ok &= _check("one revocation row", _rows(user) == 1, str(_rows(user)))
    ok &= _check("the session's access token -> 401", _me(client, access) == 401)
    ok &= _check("a token refresh minted before logout -> 401", _me(client, refreshed["access_token"]) == 401)
    ok &= _check("the refresh token -> 401", _refresh(client, refresh).status_code == 401)
    ok &= _check("the refreshed refresh token -> 401", _refresh(client, refreshed["refresh_token"]).status_code == 401)
    ok &= _check(
        "an authenticated route refuses it too",
        client.get("/api/v1/repos", headers={"Authorization": f"Bearer {access}"}).status_code == 401,
    )
    ok &= _check("another session of the same user still works", _me(client, other_access) == 200 and _refresh(client, other_refresh).status_code == 200)
    again = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    ok &= _check("logging out again -> 204, still one row", again.status_code == 204 and _rows(user) == 1)
    return ok


def run_logout_with_expired_access() -> bool:
    print("=== group 3: an expired access token still ends its session ===")
    user = _user()
    client = TestClient(app)
    access, refresh = create_token_pair(EMAIL)
    sid = decode_token(access)["sid"]
    expired = create_access_token(EMAIL, expires_delta=timedelta(minutes=-5), sid=sid)
    ok = _check("the expired token itself is refused", _me(client, expired) == 401)
    ok &= _check("its refresh token works before logout", _refresh(client, refresh).status_code == 200)
    resp = client.post("/api/v1/auth/logout", json={}, headers={"Authorization": f"Bearer {expired}"})
    ok &= _check("logout with only the expired bearer -> 204", resp.status_code == 204, resp.text)
    ok &= _check("the session's refresh token is now refused", _refresh(client, refresh).status_code == 401)
    ok &= _check("the session's live access token is refused", _me(client, access) == 401)
    ok &= _check("one row", _rows(user) == 1)
    return ok


def run_noop_logouts() -> bool:
    print("=== group 4: logouts that end nothing ===")
    user = _user()
    client = TestClient(app)
    foreign = jwt.encode(
        {"sub": EMAIL, "exp": datetime.now(timezone.utc) + timedelta(hours=1), "type": "refresh", "sid": uuid.uuid4().hex},
        "not-the-secret",
        algorithm="HS256",
    )
    from app.core.security import create_action_token  # noqa: PLC0415

    cases = {
        "no body, no bearer": dict(json={}),
        "garbage refresh token": dict(json={"refresh_token": "not.a.jwt"}),
        "token signed with another key": dict(json={"refresh_token": foreign}),
        "an action token (not access/refresh)": dict(json={"refresh_token": create_action_token(EMAIL, "github_install_state")}),
        "garbage bearer": dict(json={}, headers={"Authorization": "Bearer nonsense"}),
    }
    ok = True
    for label, kwargs in cases.items():
        resp = client.post("/api/v1/auth/logout", **kwargs)
        ok &= _check(f"{label} -> 204", resp.status_code == 204, f"{resp.status_code} {resp.text}")
    ok &= _check("no row written", _rows(user) == 0, str(_rows(user)))
    return ok


def run_legacy_tokens() -> bool:
    print("=== group 5: tokens without a session id are refused ===")
    _user()
    client = TestClient(app)
    settings = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(hours=1)
    legacy_access = jwt.encode({"sub": EMAIL, "exp": exp, "type": "access"}, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    legacy_refresh = jwt.encode({"sub": EMAIL, "exp": exp, "type": "refresh"}, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    ok = _check("legacy access -> 401", _me(client, legacy_access) == 401)
    ok &= _check("legacy refresh -> 401", _refresh(client, legacy_refresh).status_code == 401)
    ok &= _check("a current token of the same user -> 200", _me(client, create_access_token(EMAIL)) == 200)
    return ok


def run_websocket() -> bool:
    print("=== group 6: WebSocket refuses a logged-out session ===")
    _user()
    client = TestClient(app)
    access, refresh = create_token_pair(EMAIL)
    client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    try:
        with client.websocket_connect(f"/ws/analysis-runs/{uuid.uuid4()}?token={access}") as ws:
            ws.receive_json()
        return _check("closed with 1008", False, "no close")
    except WebSocketDisconnect as exc:
        return _check("closed with 1008", exc.code == 1008, str(exc.code))


def run_pruning() -> bool:
    print("=== group 7: expired revocation rows are pruned ===")
    user = _user()
    db = SessionLocal()
    try:
        old_sid, live_sid = uuid.uuid4().hex, uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        db.add_all([
            RevokedSession(sid=old_sid, user_id=user, expires_at=now - timedelta(days=1)),
            RevokedSession(sid=live_sid, user_id=user, expires_at=now + timedelta(days=3)),
        ])
        db.commit()
        auth_service.revoke_session(db, uuid.uuid4().hex, user)
        db.expire_all()
        ok = _check("expired row deleted", db.get(RevokedSession, old_sid) is None)
        ok &= _check("live row kept", db.get(RevokedSession, live_sid) is not None)
        new_rows = db.execute(select(RevokedSession).where(RevokedSession.user_id == user)).scalars().all()
        ok &= _check("new row expires ~7 days out", any(timedelta(days=6, hours=23) < r.expires_at - now <= timedelta(days=7, minutes=1) for r in new_rows if r.sid not in (old_sid, live_sid)))
        return ok
    finally:
        db.close()


def main() -> int:
    results = {
        "session ids": run_session_ids(),
        "logout ends the session": run_logout_ends_session(),
        "expired access logout": run_logout_with_expired_access(),
        "no-op logouts": run_noop_logouts(),
        "legacy tokens": run_legacy_tokens(),
        "websocket": run_websocket(),
        "pruning": run_pruning(),
    }
    _user()  # leave no rows behind
    print("\n=== summary ===")
    for name, passed in results.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
