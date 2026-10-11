"""Smoke test for "Continue with GitHub" (Known Issues #4 and #17):
`GET /auth/github/authorize[/link]`, `GET /auth/github/login/callback`,
`github_auth_service.sign_in_with_github`.

`TestClient(app)` against the real dev database and real Redis; GitHub itself
is monkeypatched (no network). Offline.

Gating groups:

1. `run_authorize()` — sign-in start needs no auth, link start does (401);
   the URL carries the client id, the exact redirect_uri and the state; the
   state is held in Redis; an unconfigured App is 503.
2. `run_new_user()` — a brand-new GitHub person gets an account (verified
   email, no password), the callback redirects to /login with a one-time
   exchange code and the same state, the code trades for working tokens, and
   a password login is refused. No installation anywhere -> `needs_install=1`.
3. `run_returning_user()` — the same GitHub person again is the same account.
4. `run_link_by_verified_email()` — a verified primary email matching an
   email+password account signs into it (password untouched); an unverified
   one does not link; an email the App cannot read falls back to the noreply
   address; an email owned by an account linked to a *different* GitHub person
   is refused.
5. `run_state_rules()` — state is single-use, forged / missing / expired state
   and a declined authorization all end as `github_error=1`; a decline that
   GitHub delivers to the *install* callback returns to the page that started
   the flow (/login or /onboarding).
6. `run_link_mode()` — a signed-in user links GitHub; a GitHub person already
   held by another account is refused (redirect to /onboarding).
7. `run_legacy_install_holder()` — an account that connected via the install
   flow before `github_user_id` existed is recognised by its installation's
   personal-account id.
8. `run_installations()` — an installation on the person's own account is
   connected with no install callback (#17); an organization's, or another
   App's, is not.

Run: `uv run python scripts/smoke_github_signin.py` (from `server/`).
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, or_, select  # noqa: E402

from app.api.v1 import github as github_routes  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.redis import get_redis_client  # noqa: E402
from app.core.security import create_token_pair, decrypt_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import github_service  # noqa: E402

PREFIX = "smoke-ghsignin-"
GH_BASE = 9_100_000_000  # far above any real GitHub user id
APP_ID = "424242"


def _check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail and not condition else ""))
    return condition


class FakeGitHub:
    """Stands in for the GitHub calls `sign_in_with_github` and
    `connect_installation` make. Set the fields, then run a callback."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.gh_id = GH_BASE
        self.login = "ghperson"
        self.emails: list[dict] | Exception = []
        self.installations: list[dict] = []
        self.code_ok = True

    # --- patched functions ---
    def exchange_oauth_code(self, code: str, redirect_uri: str) -> str:
        if not self.code_ok:
            raise github_service.GitHubServiceError(400, "bad_verification_code")
        return "user-token"

    def get_authenticated_user(self, token: str) -> dict:
        return {"id": self.gh_id, "login": self.login}

    def list_user_emails(self, token: str) -> list[dict]:
        if isinstance(self.emails, Exception):
            raise self.emails
        return self.emails

    def list_user_installations(self, token: str) -> list[dict]:
        return self.installations

    def get_installation_access_token(self, installation_id: str) -> dict:
        return {"token": "inst-token", "expires_at": "2099-01-01T00:00:00Z"}

    def get_installation(self, installation_id: str) -> dict:
        for installation in self.installations:
            if str(installation["id"]) == str(installation_id):
                return installation
        raise github_service.GitHubServiceError(404, "installation not found")


fake = FakeGitHub()
for name in (
    "exchange_oauth_code",
    "get_authenticated_user",
    "list_user_emails",
    "list_user_installations",
    "get_installation_access_token",
    "get_installation",
):
    setattr(github_service, name, getattr(fake, name))

settings = get_settings()
settings.GITHUB_CLIENT_ID = settings.GITHUB_CLIENT_ID or "smoke-client-id"
settings.GITHUB_CLIENT_SECRET = settings.GITHUB_CLIENT_SECRET or "smoke-client-secret"
settings.GITHUB_APP_ID = APP_ID


def _cleanup() -> None:
    db = SessionLocal()
    try:
        # Accounts made for fake GitHub people (incl. noreply-address ones) are
        # found by their GitHub ids; the rest by the email prefix.
        db.execute(
            delete(User).where(
                or_(
                    User.email.like(f"{PREFIX}%"),
                    User.github_user_id.between(str(GH_BASE), str(GH_BASE + 999)),
                    User.github_id.between(str(GH_BASE), str(GH_BASE + 999)),
                )
            )
        )
        db.commit()
    finally:
        db.close()


def _user_by_email(email: str) -> User | None:
    db = SessionLocal()
    try:
        return db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    finally:
        db.close()


def _users_with_github_user_id(gh_id: int) -> list[User]:
    db = SessionLocal()
    try:
        return list(db.execute(select(User).where(User.github_user_id == str(gh_id))).scalars())
    finally:
        db.close()


def _make_user(email: str, *, password: str | None = "Pa55word!", github_id: int | None = None, github_user_id: int | None = None) -> User:
    db = SessionLocal()
    try:
        user = User(
            email=email,
            hashed_password=hash_password(password) if password else None,
            github_id=str(github_id) if github_id else None,
            github_user_id=str(github_user_id) if github_user_id else None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def _start(client: TestClient) -> str:
    resp = client.get("/api/v1/auth/github/authorize")
    assert resp.status_code == 200, resp.text
    return resp.json()["state"]


def _callback(client: TestClient, state: str | None, code: str | None = "the-code"):
    params = {}
    if state is not None:
        params["state"] = state
    if code is not None:
        params["code"] = code
    return client.get("/api/v1/auth/github/login/callback", params=params, follow_redirects=False)


def _where(resp) -> tuple[str, dict[str, str]]:
    """(path on the frontend, query params) of a callback redirect."""
    assert resp.status_code == 302, resp.status_code
    parsed = urlparse(resp.headers["location"])
    return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}


def run_authorize() -> bool:
    print("=== group 1: authorize ===")
    client = TestClient(app)
    resp = client.get("/api/v1/auth/github/authorize")
    ok = _check("sign-in start needs no auth -> 200", resp.status_code == 200, resp.text)
    body = resp.json()
    url = urlparse(body["authorize_url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    ok &= _check("goes to github.com/login/oauth/authorize", url.netloc == "github.com" and url.path == "/login/oauth/authorize")
    ok &= _check("carries the client id", q.get("client_id") == settings.GITHUB_CLIENT_ID)
    ok &= _check("redirect_uri is the API's login callback", q.get("redirect_uri", "").endswith("/api/v1/auth/github/login/callback"), q.get("redirect_uri", ""))
    ok &= _check("state in the URL equals the returned state", q.get("state") == body["state"])
    ok &= _check("state is held in Redis", get_redis_client().exists(github_routes._oauth_state_key(body["state"])) == 1)
    ok &= _check("link start without a bearer -> 401", client.get("/api/v1/auth/github/authorize/link").status_code == 401)
    user = _make_user(f"{PREFIX}link-start@example.com")
    access, _ = create_token_pair(user.email)
    resp = client.get("/api/v1/auth/github/authorize/link", headers={"Authorization": f"Bearer {access}"})
    ok &= _check("link start with a bearer -> 200", resp.status_code == 200, resp.text)
    saved = get_redis_client().get(github_routes._oauth_state_key(resp.json()["state"]))
    ok &= _check("link state remembers the user", saved is not None and user.email in saved)
    original = settings.GITHUB_CLIENT_SECRET
    settings.GITHUB_CLIENT_SECRET = ""
    try:
        ok &= _check("unconfigured App -> 503", client.get("/api/v1/auth/github/authorize").status_code == 503)
    finally:
        settings.GITHUB_CLIENT_SECRET = original
    return ok


def run_new_user() -> bool:
    print("=== group 2: brand-new GitHub person ===")
    fake.reset()
    fake.gh_id = GH_BASE + 1
    fake.login = "newperson"
    email = f"{PREFIX}new@example.com"
    fake.emails = [{"email": email, "primary": True, "verified": True}]
    client = TestClient(app)
    state = _start(client)
    path, params = _where(_callback(client, state))
    ok = _check("redirects to /login", path == "/login", path)
    ok &= _check("carries the same state back", params.get("oauth_state") == state)
    ok &= _check("needs_install=1 with no installation", params.get("needs_install") == "1", str(params))
    ok &= _check("no github_error", "github_error" not in params)
    user = _user_by_email(email)
    ok &= _check("account created with the verified email", user is not None)
    if user is None:
        return False
    ok &= _check("no password", user.hashed_password is None)
    ok &= _check("github_user_id recorded", user.github_user_id == str(fake.gh_id))
    ok &= _check("no installation connected", user.github_access_token_encrypted is None)

    resp = client.post("/api/v1/auth/github/exchange", json={"code": params["exchange_code"]})
    ok &= _check("exchange code -> tokens", resp.status_code == 200, resp.text)
    if resp.status_code == 200:
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {resp.json()['access_token']}"})
        ok &= _check("tokens work: /auth/me is that account", me.status_code == 200 and me.json()["email"] == email)
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "anything-at-all"})
    ok &= _check("password login refused for a GitHub-only account", resp.status_code == 401, str(resp.status_code))
    return ok


def run_returning_user() -> bool:
    print("=== group 3: returning GitHub person ===")
    fake.reset()
    fake.gh_id = GH_BASE + 1
    fake.emails = [{"email": f"{PREFIX}changed-email@example.com", "primary": True, "verified": True}]
    client = TestClient(app)
    path, params = _where(_callback(client, _start(client)))
    ok = _check("redirects to /login without error", path == "/login" and "github_error" not in params, str(params))
    users = _users_with_github_user_id(fake.gh_id)
    ok &= _check("still exactly one account for that GitHub id", len(users) == 1, str(len(users)))
    ok &= _check("a changed GitHub email does not create another account", _user_by_email(f"{PREFIX}changed-email@example.com") is None)
    return ok


def run_link_by_verified_email() -> bool:
    print("=== group 4: matching an existing email account ===")
    ok = True
    # verified primary email == existing email+password account -> sign into it
    existing = _make_user(f"{PREFIX}existing@example.com")
    fake.reset()
    fake.gh_id = GH_BASE + 10
    fake.emails = [{"email": existing.email.upper(), "primary": True, "verified": True}]
    client = TestClient(app)
    path, params = _where(_callback(client, _start(client)))
    ok &= _check("verified email match signs in", path == "/login" and "github_error" not in params, str(params))
    after = _user_by_email(existing.email)
    ok &= _check("same account got the GitHub id", after is not None and after.github_user_id == str(fake.gh_id))
    ok &= _check("its password is untouched", after is not None and after.hashed_password == existing.hashed_password)
    ok &= _check("no second account", len(_users_with_github_user_id(fake.gh_id)) == 1)

    # unverified -> must NOT link; a separate noreply account is made instead
    victim = _make_user(f"{PREFIX}victim@example.com")
    fake.reset()
    fake.gh_id = GH_BASE + 11
    fake.login = "Imposter"
    fake.emails = [{"email": victim.email, "primary": True, "verified": False}]
    path, params = _where(_callback(client, _start(client)))
    victim_after = _user_by_email(victim.email)
    ok &= _check("unverified email is never linked", victim_after is not None and victim_after.github_user_id is None)
    made = _users_with_github_user_id(fake.gh_id)
    ok &= _check("a separate account was made instead", len(made) == 1 and made[0].id != victim.id)
    ok &= _check("...on the noreply address", len(made) == 1 and made[0].email == f"{fake.gh_id}+imposter@users.noreply.github.com", made[0].email if made else "")

    # the App can't read emails (403) -> noreply fallback, sign-in still works
    fake.reset()
    fake.gh_id = GH_BASE + 12
    fake.login = "noemail"
    fake.emails = github_service.GitHubServiceError(403, "Resource not accessible")
    path, params = _where(_callback(client, _start(client)))
    made = _users_with_github_user_id(fake.gh_id)
    ok &= _check("unreadable emails -> still signs in", path == "/login" and "github_error" not in params and len(made) == 1, str(params))
    ok &= _check("...on the noreply address", len(made) == 1 and made[0].email.endswith("@users.noreply.github.com"))

    # email owned by an account linked to a different GitHub person -> refused
    taken = _make_user(f"{PREFIX}taken@example.com", github_user_id=GH_BASE + 13)
    fake.reset()
    fake.gh_id = GH_BASE + 14
    fake.emails = [{"email": taken.email, "primary": True, "verified": True}]
    path, params = _where(_callback(client, _start(client)))
    ok &= _check("email of an account linked to someone else -> github_error", params.get("github_error") == "1", str(params))
    ok &= _check("...and no account was made for the stranger", _users_with_github_user_id(fake.gh_id) == [])
    return ok


def run_state_rules() -> bool:
    print("=== group 5: state ===")
    fake.reset()
    fake.gh_id = GH_BASE + 20
    fake.emails = [{"email": f"{PREFIX}state@example.com", "primary": True, "verified": True}]
    client = TestClient(app)
    state = _start(client)
    _, first = _where(_callback(client, state))
    _, replay = _where(_callback(client, state))
    ok = _check("first use works", "github_error" not in first, str(first))
    ok &= _check("replaying the same state -> github_error", replay.get("github_error") == "1", str(replay))
    _, forged = _where(_callback(client, "not-a-real-state"))
    ok &= _check("forged state -> github_error", forged.get("github_error") == "1")
    _, missing = _where(_callback(client, None))
    ok &= _check("missing state -> github_error", missing.get("github_error") == "1")
    state = _start(client)
    get_redis_client().delete(github_routes._oauth_state_key(state))
    _, expired = _where(_callback(client, state))
    ok &= _check("expired state -> github_error", expired.get("github_error") == "1")
    ttl = get_redis_client().ttl(github_routes._oauth_state_key(_start(client)))
    ok &= _check("state has a TTL of at most 10 minutes", 0 < ttl <= 600, str(ttl))
    state = _start(client)
    path, declined = _where(_callback(client, state, code=None))
    ok &= _check("declined on github.com -> github_error", path == "/login" and declined.get("github_error") == "1", str(declined))
    ok &= _check("...and that state is spent", get_redis_client().exists(github_routes._oauth_state_key(state)) == 0)
    # github.com sends a *decline* to the App's first Callback URL (the install
    # callback), not to /login/callback — it must still return to the page that
    # started the flow, not drop the person on onboarding.
    def _declined_on_install_callback(state: str):
        return client.get(
            "/api/v1/auth/github/callback",
            params={"error": "access_denied", "state": state},
            follow_redirects=False,
        )

    state = _start(client)
    path, declined = _where(_declined_on_install_callback(state))
    ok &= _check("sign-in declined, delivered to the install callback -> /login?github_error", path == "/login" and declined.get("github_error") == "1", f"{path} {declined}")
    ok &= _check("...and that state is spent", get_redis_client().exists(github_routes._oauth_state_key(state)) == 0)
    link_user = _make_user(f"{PREFIX}decliner@example.com")
    access, _ = create_token_pair(link_user.email)
    state = client.get("/api/v1/auth/github/authorize/link", headers={"Authorization": f"Bearer {access}"}).json()["state"]
    path, declined = _where(_declined_on_install_callback(state))
    ok &= _check("link declined, delivered to the install callback -> /onboarding?github_error", path == "/onboarding" and declined.get("github_error") == "1", f"{path} {declined}")
    path, unknown = _where(_declined_on_install_callback("not-a-real-state"))
    ok &= _check("an unknown state on the install callback still -> /onboarding?github_error (old behaviour)", path == "/onboarding" and unknown.get("github_error") == "1", f"{path} {unknown}")
    fake.code_ok = False
    _, bad_code = _where(_callback(client, _start(client)))
    ok &= _check("GitHub rejects the code -> github_error", bad_code.get("github_error") == "1")
    fake.code_ok = True
    return ok


def run_link_mode() -> bool:
    print("=== group 6: link mode ===")
    client = TestClient(app)
    user = _make_user(f"{PREFIX}linker@example.com")
    access, _ = create_token_pair(user.email)
    headers = {"Authorization": f"Bearer {access}"}

    fake.reset()
    fake.gh_id = GH_BASE + 30
    fake.emails = [{"email": f"{PREFIX}someone-else@example.com", "primary": True, "verified": True}]
    state = client.get("/api/v1/auth/github/authorize/link", headers=headers).json()["state"]
    path, params = _where(_callback(client, state))
    ok = _check("redirects to /onboarding", path == "/onboarding", path)
    after = _user_by_email(user.email)
    ok &= _check("GitHub attached to the signed-in account (not to the GitHub email's)", after is not None and after.github_user_id == str(fake.gh_id))
    ok &= _check("the exchange code is for that account", client.post("/api/v1/auth/github/exchange", json={"code": params["exchange_code"]}).status_code == 200)
    ok &= _check("no extra account was created", len(_users_with_github_user_id(fake.gh_id)) == 1)

    other = _make_user(f"{PREFIX}other@example.com")
    access, _ = create_token_pair(other.email)
    state = client.get("/api/v1/auth/github/authorize/link", headers={"Authorization": f"Bearer {access}"}).json()["state"]
    path, params = _where(_callback(client, state))
    ok &= _check("a GitHub person held by another account -> /onboarding?github_error", path == "/onboarding" and params.get("github_error") == "1", f"{path} {params}")
    ok &= _check("...and stays with its owner", len(_users_with_github_user_id(fake.gh_id)) == 1 and _users_with_github_user_id(fake.gh_id)[0].email == user.email)
    return ok


def run_legacy_install_holder() -> bool:
    print("=== group 7: account connected via the install flow before github_user_id ===")
    legacy = _make_user(f"{PREFIX}legacy@example.com", github_id=GH_BASE + 40)
    fake.reset()
    fake.gh_id = GH_BASE + 40
    fake.emails = [{"email": f"{PREFIX}not-the-same@example.com", "primary": True, "verified": True}]
    client = TestClient(app)
    path, params = _where(_callback(client, _start(client)))
    ok = _check("signs into the account that installed on this person's account", path == "/login" and "github_error" not in params, str(params))
    after = _user_by_email(legacy.email)
    ok &= _check("that account now also has github_user_id", after is not None and after.github_user_id == str(fake.gh_id))
    ok &= _check("no second account", _user_by_email(f"{PREFIX}not-the-same@example.com") is None)
    return ok


def _installation(installation_id: int, account_id: int, *, account_type: str = "User", app_id: str = APP_ID) -> dict:
    return {"id": installation_id, "app_id": int(app_id), "account": {"id": account_id, "type": account_type, "login": "x"}}


def run_installations() -> bool:
    print("=== group 8: installations (Known Issue #17) ===")
    client = TestClient(app)
    ok = True

    fake.reset()
    fake.gh_id = GH_BASE + 50
    fake.emails = [{"email": f"{PREFIX}inst-own@example.com", "primary": True, "verified": True}]
    fake.installations = [_installation(777001, fake.gh_id)]
    path, params = _where(_callback(client, _start(client)))
    user = _user_by_email(f"{PREFIX}inst-own@example.com")
    ok &= _check("own-account installation: no needs_install", path == "/login" and "needs_install" not in params, str(params))
    ok &= _check("...installation connected, no install callback involved", user is not None and user.github_access_token_encrypted is not None and decrypt_token(user.github_access_token_encrypted) == "777001")
    ok &= _check("...github_id is the installation account's", user is not None and user.github_id == str(fake.gh_id))

    fake.reset()
    fake.gh_id = GH_BASE + 51
    fake.emails = [{"email": f"{PREFIX}inst-org@example.com", "primary": True, "verified": True}]
    fake.installations = [_installation(777002, GH_BASE + 900, account_type="Organization")]
    path, params = _where(_callback(client, _start(client)))
    user = _user_by_email(f"{PREFIX}inst-org@example.com")
    ok &= _check("organization installation is NOT auto-connected", user is not None and user.github_access_token_encrypted is None)
    ok &= _check("...so needs_install=1", params.get("needs_install") == "1", str(params))

    fake.reset()
    fake.gh_id = GH_BASE + 52
    fake.emails = [{"email": f"{PREFIX}inst-otherapp@example.com", "primary": True, "verified": True}]
    fake.installations = [_installation(777003, fake.gh_id, app_id="999")]
    path, params = _where(_callback(client, _start(client)))
    user = _user_by_email(f"{PREFIX}inst-otherapp@example.com")
    ok &= _check("another App's installation is ignored", user is not None and user.github_access_token_encrypted is None and params.get("needs_install") == "1")

    # an account that already has an installation is not overwritten
    fake.reset()
    fake.gh_id = GH_BASE + 50
    fake.emails = [{"email": f"{PREFIX}inst-own@example.com", "primary": True, "verified": True}]
    fake.installations = [_installation(777099, fake.gh_id)]
    _where(_callback(client, _start(client)))
    user = _user_by_email(f"{PREFIX}inst-own@example.com")
    ok &= _check("an already-connected installation is left alone", user is not None and decrypt_token(user.github_access_token_encrypted) == "777001")
    return ok


def main() -> int:
    _cleanup()
    try:
        results = [
            run_authorize(),
            run_new_user(),
            run_returning_user(),
            run_link_by_verified_email(),
            run_state_rules(),
            run_link_mode(),
            run_legacy_install_holder(),
            run_installations(),
        ]
    finally:
        _cleanup()
    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
