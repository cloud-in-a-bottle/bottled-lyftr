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
    assert response.json()["access_token"]
