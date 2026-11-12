import subprocess
import time

from openhost_test_harness import OpenhostStack


def test_app_serves_owner(stack: OpenhostStack) -> None:
    # The app is marked running once its health check passes; "/" can take a little longer to come up.
    deadline = time.monotonic() + 60
    while True:
        response = stack.owner_session.get(f"{stack.url}/", timeout=30)
        if response.status_code < 400 or time.monotonic() > deadline:
            break
        time.sleep(2)
    assert response.status_code < 400, (
        f"GET / returned {response.status_code}: {response.text[:500]}\n\napp logs:\n{stack.app_logs()[-5000:]}"
    )


def test_owner_sso(stack: OpenhostStack) -> None:
    # SSO creates the owner's Lyftr account on first use, with an email built from the zone domain.
    response = stack.owner_session.get(f"{stack.url}/_openhost/sso", timeout=60)
    assert response.status_code == 200, (
        f"SSO returned {response.status_code}: {response.text[:500]}\n\napp logs:\n{stack.app_logs()[-5000:]}"
    )
    sso = response.json()
    # The backend accepts the token the proxy minted (same secret and claims shape) as the owner's account.
    response = stack.owner_session.get(
        f"{stack.url}/api/v1/me", headers={"Authorization": f"Bearer {sso['access_token']}"}, timeout=30
    )
    assert response.status_code == 200, f"/api/v1/me with the SSO token: {response.status_code}: {response.text[:500]}"
    me = response.json()["data"]
    assert me["id"] == sso["user"]["id"], me
    assert me["email"] == f"{stack.owner_username}@{stack.zone_domain.split(':')[0]}", me
    # The SQLite database lives in the app data dir.
    assert (stack.data_dir / "lyftr.db").is_file()


def test_owner_page_bootstraps_sso(stack: OpenhostStack) -> None:
    # The owner's page navigations get the script that fetches /_openhost/sso into localStorage; other requests don't.
    page = stack.owner_session.get(f"{stack.url}/", headers={"Accept": "text/html"}, timeout=30)
    assert page.status_code == 200 and "fetch('/_openhost/sso'" in page.text, page.text[:2000]
    asset = stack.owner_session.get(f"{stack.url}/", headers={"Accept": "*/*"}, timeout=30)
    assert asset.status_code == 200 and "/_openhost/sso" not in asset.text, asset.text[:2000]


def test_upstream_defaults_closed(stack: OpenhostStack) -> None:
    # Our env closes upstream's defaults: registration closes once the owner's account exists (REGISTRATION=first-user)
    # and there is no demo account with its published password (ENV=production).
    stack.owner_session.get(f"{stack.url}/_openhost/sso", timeout=60).raise_for_status()
    response = stack.owner_session.post(
        f"{stack.url}/api/v1/auth/register",
        json={"email": "someone-else@example.com", "password": "a-long-enough-password-123"},
        timeout=30,
    )
    assert response.status_code == 403, f"second registration: {response.status_code}: {response.text[:500]}"
    response = stack.owner_session.post(
        f"{stack.url}/api/v1/auth/login",
        json={"email": "demo@lyftr.local", "password": "password123"},
        timeout=30,
    )
    assert response.status_code in (400, 401, 403), f"demo login: {response.status_code}: {response.text[:500]}"


def test_owner_sso_survives_token_refresh(stack: OpenhostStack) -> None:
    # The client refreshes when its access token is refused; the backend checks a refresh against the account's
    # token version, so the pair the proxy minted has to pass that check, and the refreshed token has to work.
    sso = stack.owner_session.get(f"{stack.url}/_openhost/sso", timeout=60).json()
    response = stack.owner_session.post(
        f"{stack.url}/api/v1/auth/refresh", json={"refresh_token": sso["refresh_token"]}, timeout=30
    )
    assert response.status_code == 200, f"refresh with the SSO token: {response.status_code}: {response.text[:500]}"
    refreshed = response.json()["data"]
    response = stack.owner_session.get(
        f"{stack.url}/api/v1/me", headers={"Authorization": f"Bearer {refreshed['token']}"}, timeout=30
    )
    assert response.status_code == 200, f"/api/v1/me after refresh: {response.status_code}: {response.text[:500]}"
    assert response.json()["data"]["id"] == sso["user"]["id"]


def test_owner_sso_after_sessions_revoked(stack: OpenhostStack) -> None:
    # A password reset bumps the account's token version, which ends every existing session. SSO must then mint
    # tokens at the new version, or the owner would be signed straight back out. Runs last: it revokes sessions.
    old = stack.owner_session.get(f"{stack.url}/_openhost/sso", timeout=60).json()
    email = old["user"]["email"]
    subprocess.run(
        ["podman", "exec", "-i", stack.container_name, "sh", "-c",
         'DB_PATH="$OPENHOST_APP_DATA_DIR/lyftr.db" exec /app/lyftr-api reset-password "$0"', email],
        input="a-new-long-enough-password-123\n", text=True, check=True, capture_output=True, timeout=60,
    )
    response = stack.owner_session.get(
        f"{stack.url}/api/v1/me", headers={"Authorization": f"Bearer {old['access_token']}"}, timeout=30
    )
    assert response.status_code == 401, f"/api/v1/me with a revoked token: {response.status_code}: {response.text[:500]}"

    sso = stack.owner_session.get(f"{stack.url}/_openhost/sso", timeout=60).json()
    response = stack.owner_session.get(
        f"{stack.url}/api/v1/me", headers={"Authorization": f"Bearer {sso['access_token']}"}, timeout=30
    )
    assert response.status_code == 200, f"/api/v1/me with a fresh SSO token: {response.status_code}: {response.text[:500]}"
    response = stack.owner_session.post(
        f"{stack.url}/api/v1/auth/refresh", json={"refresh_token": sso["refresh_token"]}, timeout=30
    )
    assert response.status_code == 200, f"refresh with a fresh SSO token: {response.status_code}: {response.text[:500]}"
