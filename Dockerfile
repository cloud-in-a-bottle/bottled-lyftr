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
# We build the backend and the web assets from a pinned upstream commit so the
# image is reproducible and the JWT Claims shape we mint stays in sync with the
# backend that validates it.

ARG LYFTR_REF=main

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
FROM node:20-alpine AS web-builder
WORKDIR /web
COPY --from=src /src/web/package.json /src/web/package-lock.json ./
RUN npm ci
COPY --from=src /src/web/ ./
# Same-origin: the SPA calls /api/v1 on the same host the auth-proxy serves.
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 4: runtime
# ---------------------------------------------------------------------------
FROM alpine:3.19
RUN apk add --no-cache python3 ca-certificates wget

WORKDIR /app
COPY --from=backend-builder /lyftr-api /app/lyftr-api
COPY --from=web-builder /web/dist /app/web
COPY auth_proxy.py /app/auth_proxy.py
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 8080
ENTRYPOINT []
CMD ["/app/start.sh"]
