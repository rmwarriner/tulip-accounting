#!/usr/bin/env sh
# Tulip Accounting — container entrypoint
#
# Runs as user `tulip` in the runtime image. Three jobs, in order:
#
#   1. If TULIP_JWT_SECRET_FILE is set (compose path; the secret comes
#      from /run/secrets/...), promote its contents into TULIP_JWT_SECRET
#      so the API config loader (which reads the env var directly) sees
#      it. Same trick TULIP_KEY_FILE uses for the master key, except the
#      JWT secret config doesn't yet have a file-store path; we do the
#      env-promotion in the container instead.
#
#   2. Run `alembic upgrade head` against TULIP_DATABASE_URL so a fresh
#      volume is always migration-current. Crucially, this happens before
#      the API binds to its port — the docker-compose --wait gate then
#      waits for /health, by which point the schema is guaranteed up.
#
#   3. exec the original CMD (uvicorn).
#
# All three steps are idempotent.

set -eu

# ---- 1. JWT secret from file (if configured) -------------------------------
if [ -n "${TULIP_JWT_SECRET_FILE:-}" ] && [ -f "${TULIP_JWT_SECRET_FILE}" ]; then
    TULIP_JWT_SECRET="$(cat "${TULIP_JWT_SECRET_FILE}")"
    export TULIP_JWT_SECRET
fi

# ---- 2. Migrate to head ----------------------------------------------------
# Skip migration when TULIP_SKIP_MIGRATION=1 — used by tests or by
# operators running `docker compose run` for one-off commands that
# don't need (or shouldn't trigger) schema work.
if [ "${TULIP_SKIP_MIGRATION:-0}" != "1" ]; then
    alembic -c packages/tulip-storage/alembic.ini upgrade head
fi

# ---- 3. Hand off to the original CMD --------------------------------------
exec "$@"
