# openhost-lyftr — single-container build of Lyftr (Cawlumm/lyftr) for OpenHost.
#
# Lyftr upstream ships as two images (a Go backend + an nginx-served web SPA)
# talking JWT-in-localStorage. For OpenHost we bake both into one container and
# front them with a small Python auth-proxy (auth_proxy.py) that:
#   * proxies /api/* to the Go backend on 127.0.0.1:3000,
#   * serves the built SPA assets,
#   * for the OpenHost owner, injects a bootstrap script into index.html that
#     writes a freshly-minted JWT into localStorage (Lyftr's own auth scheme),
#     so the owner is auto-logged-in; everyone else gets Lyftr's normal login.
#
# We build the backend and the web assets from a pinned upstream commit so the
# image is reproducible and the JWT Claims shape we mint stays in sync with the
# backend that validates it.
#
# Keep this pinned. It used to default to `main`, and when upstream converted
# the repo into an npm workspaces monorepo the build broke outright:
# web/package-lock.json moved to the repo root, so the old
# `COPY /src/web/package-lock.json` failed with "no such file or directory" and
# the app could not be deployed at all. Bumping this ref is a deliberate act
# that should be accompanied by a rebuild and a smoke test.
#
# Upstream only publishes pre-release tags, so until it tags a stable release we
# track a commit on its main branch that is at least a week old, fetched by sha
# so nothing upstream can change what we build. The comment names the nearest
# tag and the commit date; bump both together.
# v0.1.0-beta.8-16-g480bdb4 (main, 2026-11-05: "Light theme text meets WCAG AA contrast (#265)")
ARG LYFTR_REF=480bdb478a9dca621f5a8a3d0f0bdfc01278669f

# ---------------------------------------------------------------------------
# Stage 1: fetch upstream source at a pinned ref
# ---------------------------------------------------------------------------
FROM docker.io/alpine/git:v2.54.0@sha256:e8ceedc73bd80992ae8d58a7d24c58b8322146c354f1d2e81a97db4452068ec0 AS src
ARG LYFTR_REF
WORKDIR /src
RUN git init -q . \
 && git remote add origin https://github.com/Cawlumm/lyftr \
 && git fetch -q --depth 1 origin "${LYFTR_REF}" \
 && git checkout -q FETCH_HEAD

# ---------------------------------------------------------------------------
# Stage 2: build the Go backend (static binary)
# ---------------------------------------------------------------------------
FROM golang:1.26-alpine AS backend-builder
WORKDIR /build
COPY --from=src /src/backend/go.mod /src/backend/go.sum ./
RUN go mod download
COPY --from=src /src/backend/ ./
RUN CGO_ENABLED=0 go build -o /lyftr-api .

# ---------------------------------------------------------------------------
# Stage 3: build the web client (the Expo app's web export)
# ---------------------------------------------------------------------------
# Since #246 upstream's web client is the universal Expo app in apps/app (the
# old Vite app in web/ is gone), exported as a static single-page site. Upstream
# is an npm workspaces monorepo with one lockfile at the repo root and members
# `apps/app` and `packages/*`; `-w @lyftr/mobile` installs only the app and its
# workspace deps. The root postinstall runs patch-package over patches/, which
# the export bundles, so patches/ must be present before `npm ci`. This mirrors
# upstream's own apps/app/Dockerfile.web.
FROM node:22-alpine AS web-builder
WORKDIR /web
COPY --from=src /src/package.json /src/package-lock.json ./
COPY --from=src /src/apps/app/package.json ./apps/app/
COPY --from=src /src/packages/shared/package.json ./packages/shared/
COPY --from=src /src/patches ./patches
RUN npm ci -w @lyftr/mobile --include-workspace-root
# packages/shared ships as TypeScript source (no build step); Metro transpiles it.
COPY --from=src /src/packages/shared ./packages/shared
COPY --from=src /src/apps/app/ ./apps/app/
# Same-origin: with no stored server URL the client calls /api/v1 on the host
# the auth-proxy serves, which is what we want.
RUN EXPO_NO_TELEMETRY=1 CI=1 npm run export:web -w @lyftr/mobile

# ---------------------------------------------------------------------------
# Stage 4: runtime
# ---------------------------------------------------------------------------
FROM alpine:3.19
RUN apk add --no-cache python3 ca-certificates wget

WORKDIR /app
COPY --from=backend-builder /lyftr-api /app/lyftr-api
COPY --from=web-builder /web/apps/app/dist /app/web
COPY auth_proxy.py /app/auth_proxy.py
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 8080
ENTRYPOINT []
CMD ["/app/start.sh"]
