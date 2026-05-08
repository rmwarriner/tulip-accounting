# syntax=docker/dockerfile:1.7
#
# Tulip Accounting — runtime image
#
# Per ADR-0004 + #134 (locked design decisions in #121):
# - python:3.14-slim base (chosen over distroless for internal-beta
#   debuggability; distroless is a pre-external-beta concern).
# - Multi-stage: build stage installs the workspace; runtime stage copies
#   only the venv + source.
# - Non-root user (uid 1000).
# - Entrypoint runs `alembic upgrade head` before binding the API port
#   so a fresh volume is always migration-current.
# - HEALTHCHECK against /health (used by `docker compose up --wait` and
#   by the `tulip doctor` command in #135).

# -----------------------------------------------------------------------------
# Stage 1 — build the workspace .venv with `uv sync --no-dev`.
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/tulip/.venv \
    PYTHONDONTWRITEBYTECODE=1

# Install uv (matches the toolchain CONTRIBUTING.md mandates locally).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /opt/tulip

# Copy lockfile + pyproject first so the dependency layer caches across
# source-only edits.
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/

# Install runtime deps + the API package only. --no-dev keeps the test +
# tooling deps out of the runtime image.
RUN uv sync --frozen --no-dev --package tulip-api

# -----------------------------------------------------------------------------
# Stage 2 — runtime. Copies the venv + source; nothing else.
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PATH="/opt/tulip/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TULIP_DATABASE_URL=sqlite:////var/lib/tulip/db/tulip.db \
    TULIP_ATTACHMENT_ROOT=/var/lib/tulip/attachments

# `gosu` lets the entrypoint drop privileges from root to `tulip` after
# reading root-owned Docker secrets. We tried Docker's own uid/gid/mode
# overrides on secrets, but compose v2 ignores them outside swarm mode
# (warning emitted, no effect on CI's Linux runners). The standard Docker
# pattern for this is "entrypoint runs as root, sets up env, exec's the
# real workload as a non-root user via gosu." Image still runs as the
# `tulip` user end-to-end; root only exists for the duration of the
# entrypoint preamble.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 tulip \
    && useradd --system --uid 1000 --gid tulip --create-home --home-dir /var/lib/tulip tulip \
    && mkdir -p /var/lib/tulip/db /var/lib/tulip/attachments \
    && touch /var/lib/tulip/db/.keep /var/lib/tulip/attachments/.keep \
    && chown -R tulip:tulip /var/lib/tulip
# `.keep` files force Docker to populate the named volumes with the
# directory contents (and ownership) on first mount. Without them, the
# mount overlays an empty root-owned directory and the non-root `tulip`
# user can't write the SQLite DB file.

# Copy the venv + source. Source is needed at runtime because uv installs
# workspace packages in editable mode by default (the .venv has .pth
# entries pointing back at packages/*/src).
COPY --from=builder --chown=tulip:tulip /opt/tulip /opt/tulip

WORKDIR /opt/tulip

COPY deploy/docker/entrypoint.sh /usr/local/bin/tulip-entrypoint
RUN chmod 0755 /usr/local/bin/tulip-entrypoint

# Image starts as root so the entrypoint can read root-owned Docker
# secrets. The entrypoint drops to `tulip` via `gosu` before exec'ing
# alembic / uvicorn — see entrypoint.sh.
EXPOSE 8000

VOLUME ["/var/lib/tulip/db", "/var/lib/tulip/attachments"]

# Compose's --wait gate uses this. /health is wired in
# tulip_api.routers.health and is unauthenticated so the probe doesn't
# need credentials.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()" \
        || exit 1

ENTRYPOINT ["tulip-entrypoint"]
CMD ["uvicorn", "tulip_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
