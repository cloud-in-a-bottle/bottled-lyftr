"""OpenHost auth-proxy sidecar for Lyftr.

Lyftr is a localStorage-JWT single-page app: the Go backend issues HS256 JWTs
(access + refresh) which the SPA stores in ``localStorage`` and sends as
``Authorization: Bearer``. There are no session cookies and no REMOTE_USER /
header auth to hook, so cookie/header injection patterns don't apply.

Instead this proxy bridges OpenHost SSO into Lyftr's own auth scheme:

  * It fronts everything on the OpenHost-routed port and:
      - proxies ``/api/*`` to the Go backend (127.0.0.1:3000),
      - serves the built SPA assets from WEB_ROOT,
      - serves ``index.html`` for SPA routes.
  * On an owner HTML navigation (router-stamped ``X-OpenHost-Is-Owner: true``),
    it injects a tiny bootstrap ``<script>`` into ``index.html``. That script,
    only when no ``access_token`` is already present, fetches ``/_openhost/sso``,
    writes the returned ``access_token`` / ``refresh_token`` / ``user`` into
    ``localStorage`` and reloads — so the owner lands already logged-in.
  * ``/_openhost/sso`` (served here, never proxied) verifies the owner header,
    ensures the owner's Lyftr account exists (creating it via the backend's
    register endpoint with a throwaway random password that is never stored),
    then mints access+refresh JWTs with the SAME ``JWT_SECRET`` the backend
    validates against, and the SAME Claims shape (user_id/email/type/exp/iat).

Security model:
  * ``X-OpenHost-Is-Owner`` is trusted because the OpenHost router strips any
    client-supplied ``X-OpenHost-*`` and only re-adds it after verifying the
    zone_auth session. We only ever mint a token on the owner branch.
  * No user password is ever written to disk. The auto-created account's
    password is random, used once against the backend, and discarded. Owner
    login is JWT-only thereafter.
  * ``/_openhost/sso`` and ``/_healthz`` are handled locally and never proxied.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import logging
import mimetypes
import os
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = int(os.environ.get("AUTH_PROXY_LISTEN_PORT", "8080"))
BACKEND_HOST = os.environ.get("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "3000"))
WEB_ROOT = os.environ.get("WEB_ROOT", "/app/web")

JWT_SECRET = os.environ.get("JWT_SECRET", "")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "owner@lyftr.local")
# Access-token lifetime for minted owner tokens; matches the backend's
# JWT_EXPIRY so the SPA's saved session stays valid for the same window.
ACCESS_TTL = int(os.environ.get("JWT_EXPIRY", "86400"))
REFRESH_TTL = 30 * 24 * 3600

OWNER_HEADER = "X-OpenHost-Is-Owner"

HOP_BY_HOP = frozenset(
    h.lower()
    for h in (
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailer", "transfer-encoding", "upgrade", "host",
    )
)

logging.basicConfig(
    level=os.environ.get("AUTH_PROXY_LOG_LEVEL", "INFO"),
    format="[auth-proxy] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("auth_proxy")

# The bootstrap script injected into index.html for owner navigations. It is a
# no-op if a token already exists, so it only runs on the first owner visit
# (or after logout). It never loops: the SSO fetch either succeeds (and we set
# a token, so the reloaded page skips this) or fails (and we do nothing).
_BOOTSTRAP = """
<script>
(function () {
  try {
    if (localStorage.getItem('access_token')) return;
    if (sessionStorage.getItem('__oh_sso_tried')) return;
    sessionStorage.setItem('__oh_sso_tried', '1');
    fetch('/_openhost/sso', { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.access_token) return;
        localStorage.setItem('access_token', d.access_token);
        localStorage.setItem('refresh_token', d.refresh_token);
        localStorage.setItem('user', JSON.stringify(d.user));
        localStorage.removeItem('server_url');
        window.location.replace('/');
      })
      .catch(function () {});
  } catch (e) {}
})();
</script>
"""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def mint_jwt(user_id: int, email: str, token_type: str, ttl: int) -> str:
    """Mint an HS256 JWT matching Lyftr's backend Claims shape."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "user_id": user_id,
        "email": email,
        "type": token_type,
        "exp": now + ttl,
        "iat": now,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    ).encode("ascii")
    sig = hmac.new(JWT_SECRET.encode(), signing_input, hashlib.sha256).digest()
    return signing_input.decode("ascii") + "." + _b64url(sig)


def _backend_request(method: str, path: str, body: bytes | None = None,
                     headers: dict | None = None):
    conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=30)
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    conn.request(method, path, body=body, headers=h)
    resp = conn.getresponse()
    data = resp.read()
    status = resp.status
    conn.close()
    return status, data


def ensure_owner_user() -> tuple[int, str]:
    """Return (user_id, email) for the owner, creating the account if needed.

    Creation goes through the backend's public register endpoint with a
    throwaway random password (never persisted). If the account already exists
    we log in once with... we don't know the password — so instead we read the
    user id back from the register conflict path is not possible. We therefore
    key off a deterministic marker file storing ONLY the numeric user id (not a
    secret) so subsequent boots skip creation and reuse the id.
    """
    marker = os.path.join(os.environ.get("OPENHOST_APP_DATA_DIR", "/app/data"),
                          ".owner_uid")
    if os.path.exists(marker):
        try:
            with open(marker) as f:
                uid = int(f.read().strip())
            return uid, OWNER_EMAIL
        except (ValueError, OSError):
            pass

    # First boot: register the owner account.
    pw = secrets.token_urlsafe(32)
    payload = json.dumps({"email": OWNER_EMAIL, "password": pw}).encode()
    status, data = _backend_request("POST", "/api/v1/auth/register", payload)
    if status in (200, 201):
        obj = json.loads(data)
        uid = int(obj["data"]["user"]["id"])
    elif status == 409:
        # Account already exists but we have no marker (e.g. legacy DB). We
        # cannot recover the id via the API without the password, so we fall
        # back to id 1 (the first-registered user, which the owner is on a
        # fresh single-user deploy). Persist it so this path isn't hit again.
        log.warning("owner account exists without marker; assuming user_id=1")
        uid = 1
    else:
        raise RuntimeError(f"register failed: {status} {data!r}")

    try:
        with open(marker, "w") as f:
            f.write(str(uid))
        os.chmod(marker, 0o600)
    except OSError as e:
        log.warning("could not persist owner uid marker: %s", e)
    return uid, OWNER_EMAIL


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "openhost-lyftr-authproxy"

    def log_message(self, fmt: str, *args: object) -> None:
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- helpers ------------------------------------------------------------
    def _send_bytes(self, status: int, body: bytes, ctype: str,
                    extra: dict | None = None) -> None:
        self.close_connection = True
        self.send_response_only(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _is_owner(self) -> bool:
        return self.headers.get(OWNER_HEADER, "").strip().lower() == "true"

    # -- SSO endpoint -------------------------------------------------------
    def _handle_sso(self) -> None:
        if not self._is_owner():
            self._send_bytes(403, b'{"error":"not owner"}', "application/json")
            return
        if not JWT_SECRET:
            self._send_bytes(500, b'{"error":"no secret"}', "application/json")
            return
        try:
            uid, email = ensure_owner_user()
        except Exception as e:  # noqa: BLE001
            log.error("ensure_owner_user failed: %s", e)
            self._send_bytes(500, b'{"error":"sso failed"}', "application/json")
            return
        access = mint_jwt(uid, email, "access", ACCESS_TTL)
        refresh = mint_jwt(uid, email, "refresh", REFRESH_TTL)
        body = json.dumps({
            "access_token": access,
            "refresh_token": refresh,
            "user": {"id": uid, "email": email},
        }).encode()
        self._send_bytes(200, body, "application/json",
                         {"Cache-Control": "no-store"})

    # -- static SPA ---------------------------------------------------------
    def _serve_static(self) -> None:
        # Normalise the path, block traversal.
        path = self.path.split("?", 1)[0]
        rel = path.lstrip("/")
        candidate = os.path.normpath(os.path.join(WEB_ROOT, rel))
        if not candidate.startswith(os.path.realpath(WEB_ROOT)):
            candidate = os.path.join(WEB_ROOT, "index.html")

        serve_index = False
        if os.path.isdir(candidate) or not os.path.isfile(candidate):
            serve_index = True

        if serve_index:
            index_path = os.path.join(WEB_ROOT, "index.html")
            try:
                with open(index_path, "rb") as f:
                    html = f.read()
            except OSError:
                self._send_bytes(404, b"not found", "text/plain")
                return
            # Inject the SSO bootstrap for owner HTML navigations only.
            accept = self.headers.get("Accept", "")
            if self._is_owner() and "text/html" in accept:
                html = html.replace(b"</head>", _BOOTSTRAP.encode() + b"</head>", 1)
            self._send_bytes(200, html, "text/html; charset=utf-8",
                             {"Cache-Control": "no-store"})
            return

        ctype, _ = mimetypes.guess_type(candidate)
        try:
            with open(candidate, "rb") as f:
                data = f.read()
        except OSError:
            self._send_bytes(404, b"not found", "text/plain")
            return
        self._send_bytes(200, data, ctype or "application/octet-stream")

    # -- backend proxy ------------------------------------------------------
    def _proxy_backend(self) -> None:
        out_headers: list[tuple[str, str]] = []
        for key, value in self.headers.items():
            kl = key.lower()
            if kl in HOP_BY_HOP or kl == "content-length":
                continue
            out_headers.append((key, value))
        out_headers.append(("Host", f"{BACKEND_HOST}:{BACKEND_PORT}"))

        body = None
        length = self.headers.get("Content-Length")
        if length:
            try:
                body = self.rfile.read(int(length))
            except (ValueError, OSError):
                self._send_bytes(400, b"bad body", "text/plain")
                return
        try:
            conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=120)
            conn.putrequest(self.command, self.path, skip_host=True,
                            skip_accept_encoding=True)
            for k, v in out_headers:
                conn.putheader(k, v)
            if body is not None:
                conn.putheader("Content-Length", str(len(body)))
            conn.endheaders()
            if body:
                conn.send(body)
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            log.warning("backend error: %s", exc)
            self._send_bytes(502, b"upstream error", "text/plain")
            return
        self.close_connection = True
        self.send_response_only(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() in HOP_BY_HOP or k.lower() == "content-length":
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        conn.close()

    # -- dispatch -----------------------------------------------------------
    def _handle(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/_healthz":
            self._send_bytes(200, b"ok", "text/plain")
            return
        if path == "/_openhost/sso":
            self._handle_sso()
            return
        if path == "/api/" or path.startswith("/api/") or path == "/health":
            self._proxy_backend()
            return
        self._serve_static()

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_PATCH = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle


def main() -> None:
    httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    httpd.daemon_threads = True
    log.info("listening on %s:%d -> backend %s:%d, web=%s",
             LISTEN_HOST, LISTEN_PORT, BACKEND_HOST, BACKEND_PORT, WEB_ROOT)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
