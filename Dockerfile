# openhost-lyftr — single-container build of Lyftr (Cawlumm/lyftr) for OpenHost.
#
# Lyftr upstream ships as two images (a Go backend + an nginx-served Vite SPA)
# talking JWT-in-localStorage. For OpenHost we bake both into one container and
# front them with a small Python auth-proxy (auth_proxy.py) that:
#   * proxies /api/* to the Go backend on 127.0.0.1:3000,
#   * serves the built SPA assets,
#   * for the OpenHost owner, injects a bootstrap script into index.html that
#     writes a freshly-minted JWT into localStorage (Lyftr's own auth scheme),
#     so the owner is auto-logged-in; everyone else gets Lyftr's normal login.
#
# We build the backend and the web assets from a pinned upstream tag so the
# image is reproducible and the JWT Claims shape we mint stays in sync with the
# backend that validates it.
#
# Keep this pinned. It used to default to `main`, and when upstream converted
# the repo into an npm workspaces monorepo the build broke outright:
# web/package-lock.json moved to the repo root, so the old
# `COPY /src/web/package-lock.json` failed with "no such file or directory" and
# the app could not be deployed at all. Bumping this ref is a deliberate act
# that should be accompanied by a rebuild and a smoke test.
ARG LYFTR_REF=v0.1.0-beta.6

# ---------------------------------------------------------------------------
# Stage 1: fetch upstream source at a pinned ref
# ---------------------------------------------------------------------------
FROM alpine/git:latest AS src
ARG LYFTR_REF
WORKDIR /src
RUN git clone https://github.com/Cawlumm/lyftr . && git checkout "${LYFTR_REF}"

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
# Stage 3: build the Vite SPA
# ---------------------------------------------------------------------------
# Upstream is an npm workspaces monorepo: one lockfile at the repo root, with
# `web` and `packages/shared` as members. `npm ci` validates the lockfile
# against every workspace the root declares, so mobile/package.json has to be
# present even though we never install its dependencies -- `-w lyfter-web`
# keeps React Native / Expo's toolchain out of this image. This mirrors
# upstream's own web/Dockerfile.
FROM node:20-alpine AS web-builder
WORKDIR /web
COPY --from=src /src/package.json /src/package-lock.json ./
COPY --from=src /src/web/package.json ./web/
COPY --from=src /src/mobile/package.json ./mobile/
COPY --from=src /src/packages/shared/package.json ./packages/shared/
RUN npm ci -w lyfter-web --include-workspace-root
# packages/shared ships as TypeScript source (no build step); Vite transpiles it.
COPY --from=src /src/packages/shared ./packages/shared
COPY --from=src /src/web/ ./web/
# Same-origin: the SPA calls /api/v1 on the same host the auth-proxy serves, so
# VITE_API_URL is deliberately left unset.
RUN npm run build -w lyfter-web

# ---------------------------------------------------------------------------
# Stage 4: runtime
# ---------------------------------------------------------------------------
FROM alpine:3.19
RUN apk add --no-cache python3 ca-certificates wget

WORKDIR /app
COPY --from=backend-builder /lyftr-api /app/lyftr-api
COPY --from=web-builder /web/web/dist /app/web
COPY auth_proxy.py /app/auth_proxy.py
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 8080
ENTRYPOINT []
CMD ["/app/start.sh"]
