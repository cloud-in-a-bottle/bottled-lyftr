# bottled-lyftr

[Lyftr](https://github.com/Cawlumm/lyftr) (self-hosted workout, weightlifting,
bodyweight and nutrition tracker) packaged for Cloud in a Bottle with one-click owner SSO.

Log workouts and sets, build programs, run guided gym sessions, and track your
bodyweight and PRs over time — all backed by a single SQLite file on your zone.

## Architecture

Upstream Lyftr ships as two images (a Go/Gin backend + an nginx-served Vite
SPA) that talk via JWT-in-`localStorage`. This package bakes both into one
container and fronts them with a small Python auth-proxy (`auth_proxy.py`):

- proxies `/api/*` to the Go backend (`127.0.0.1:3000`, SQLite),
- serves the built SPA assets,
- auto-logs-in the Cloud in a Bottle owner.

## SSO model

Lyftr has no session cookies and no header/REMOTE_USER auth — it is a
localStorage-JWT SPA. So we bridge Cloud in a Bottle's `X-OpenHost-Is-Owner` signal into
Lyftr's own JWT scheme:

1. On the owner's first HTML navigation, the auth-proxy injects a tiny
   bootstrap script into `index.html`.
2. That script (only if no token is present) calls `/_openhost/sso`.
3. `/_openhost/sso` verifies the owner header, ensures the owner's Lyftr
   account exists (created once via the backend register endpoint with a
   **throwaway random password that is never stored**), then mints access +
   refresh JWTs signed with the same `JWT_SECRET` the backend validates.
4. The script writes those tokens into `localStorage` and reloads — the owner
   lands already logged-in.

Anonymous visitors get Lyftr's normal login form (no auto-login).

### Credential handling

- **No user password is ever written to disk.** The auto-created account's
  password is random, used once, and discarded; owner auth is JWT-only.
- `$OPENHOST_APP_DATA_DIR/.jwt_secret` is the app's own JWT **signing key**
  (not a user credential). It is persisted so sessions survive restarts; its
  threat model is identical to the SQLite DB sitting beside it.
- `$OPENHOST_APP_DATA_DIR/.owner_uid` stores only the owner's numeric user id
  (not a secret).

## Persistence

Everything lives under `$OPENHOST_APP_DATA_DIR`:
- `lyftr.db` — the SQLite database (all workouts, weight logs, programs).
- `.jwt_secret`, `.owner_uid` — see above.

## Upstream

Lyftr is MIT-licensed: https://github.com/Cawlumm/lyftr
