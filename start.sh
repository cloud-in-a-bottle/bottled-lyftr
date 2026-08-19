#!/bin/sh
# OpenHost entrypoint for Lyftr.
#
# Boots the Go backend (127.0.0.1:3000, SQLite) and the auth-proxy sidecar
# (0.0.0.0:8080, the OpenHost-routed port). The auth-proxy serves the SPA,
# proxies /api/* to the backend, and auto-logs-in the OpenHost owner by
# minting a Lyftr JWT signed with the same JWT_SECRET the backend validates.
set -e

PERSIST="${OPENHOST_APP_DATA_DIR:-/app/data}"
mkdir -p "$PERSIST"

DB_PATH="$PERSIST/lyftr.db"

# ---------------------------------------------------------------------------
# JWT signing secret.
#
# The backend signs JWTs with JWT_SECRET; the auth-proxy mints owner tokens
# with the SAME secret so the backend accepts them. It must be stable across
# restarts (otherwise every restart invalidates the owner's saved session),
# so we persist it. This is the app's own signing key — the same threat model
# as the SQLite DB sitting beside it (anyone who can read this file can read
# the DB). It is NOT a user password: no user credential is ever written to
# disk. The owner's on-disk password does not exist; owner auth is JWT-only.
# ---------------------------------------------------------------------------
SECRET_FILE="$PERSIST/.jwt_secret"
if [ ! -f "$SECRET_FILE" ]; then
    head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$SECRET_FILE"
    chmod 600 "$SECRET_FILE"
fi
JWT_SECRET="$(cat "$SECRET_FILE")"
export JWT_SECRET

# Owner identity used for the auto-created Lyftr account (email-shaped).
RAW_OWNER="${OPENHOST_OWNER_USERNAME:-owner}"
SAFE_OWNER="$(printf '%s' "$RAW_OWNER" | tr -cd 'A-Za-z0-9._+-')"
[ -z "$SAFE_OWNER" ] && SAFE_OWNER="owner"
OWNER_EMAIL="${SAFE_OWNER}@${OPENHOST_ZONE_DOMAIN:-lyftr.local}"
export OWNER_EMAIL

echo "[start] db=$DB_PATH owner=$OWNER_EMAIL"

# --- backend ---------------------------------------------------------------
# ENV=production is load-bearing, not cosmetic. Upstream's config falls back to
# a hardcoded, publicly known JWT secret ("change-me-in-production-min-32-chars!!")
# whenever JWT_SECRET is unset, and only refuses to boot on that default when
# ENV is exactly "production". A non-production ENV additionally turns on
# allow-all CORS, gin debug mode, and seeds a demo account with a published
# password. We set both ENV and JWT_SECRET; neither alone is sufficient.
export ENV=production
export PORT=3000
export DB_TYPE=sqlite
export DB_PATH="$DB_PATH"
# Registration is open by default upstream, which would let anyone who can
# reach the app create an account. The app is owner-only (openhost.toml sets
# public_paths = []), so the only registration that ever needs to succeed is
# the auth-proxy creating the owner's account on first boot. "first-user"
# allows exactly that and then closes: it is open only while the users table
# is empty, re-checked inside the insert transaction.
export REGISTRATION=first-user
# Never seed upstream's demo@lyftr.local / password123 account. This is already
# the default when ENV=production, but it is cheap to be explicit about a
# published credential.
export DEMO_MODE=false
# Same-origin requests via the auth-proxy: allow all origins (Bearer auth, no
# cookies, so this is not a credential-exposure risk).
export CORS_ORIGIN="*"
# Long-lived access tokens so the owner's minted session doesn't expire mid-use.
export JWT_EXPIRY="86400"

/app/lyftr-api &
BACKEND_PID=$!

# --- auth-proxy (foreground-ish) -------------------------------------------
export AUTH_PROXY_LISTEN_PORT="${PORT_OPENHOST:-8080}"
export BACKEND_HOST=127.0.0.1
export BACKEND_PORT=3000
export WEB_ROOT=/app/web

python3 /app/auth_proxy.py &
PROXY_PID=$!

# If either process dies, take the whole container down so OpenHost restarts it.
wait -n "$BACKEND_PID" "$PROXY_PID"
echo "[start] a child exited; shutting down"
kill "$BACKEND_PID" "$PROXY_PID" 2>/dev/null || true
wait || true
exit 1
