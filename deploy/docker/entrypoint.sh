#!/usr/bin/env sh
# Tulip Accounting — container entrypoint
#
# Starts as root so it can read root-owned Docker secrets at
# /run/secrets/..., then drops to the non-root `tulip` user via `gosu`
# for alembic + uvicorn. Compose v2 outside swarm mode ignores secret
# uid/gid/mode overrides, so root-then-drop is the portable pattern.
#
# Three jobs, in order:
#
#   1. Promote TULIP_KEY_FILE / TULIP_JWT_SECRET_FILE (if set) into
#      TULIP_MASTER_KEY / TULIP_JWT_SECRET env vars by reading the
#      file as root, then unset the *_FILE vars so the API's
#      file-store permission gate (#132) doesn't fire — the gate
#      protects against on-disk key files an attacker might read
#      from the host filesystem; Docker secrets live in container-
#      private tmpfs and have a different threat model.
#
#   2. Run `alembic upgrade head` against TULIP_DATABASE_URL as the
#      `tulip` user (the volume is tulip-owned per the Dockerfile).
#
#   3. exec the CMD (uvicorn) as `tulip`.
#
# All three steps are idempotent.

set -eu

# ---- 1. Secret promotion (root-only; the *_FILE vars need root read) ------
if [ -n "${TULIP_KEY_FILE:-}" ] && [ -f "${TULIP_KEY_FILE}" ]; then
    TULIP_MASTER_KEY="$(cat "${TULIP_KEY_FILE}")"
    export TULIP_MASTER_KEY
    unset TULIP_KEY_FILE
fi

if [ -n "${TULIP_JWT_SECRET_FILE:-}" ] && [ -f "${TULIP_JWT_SECRET_FILE}" ]; then
    TULIP_JWT_SECRET="$(cat "${TULIP_JWT_SECRET_FILE}")"
    export TULIP_JWT_SECRET
    unset TULIP_JWT_SECRET_FILE
fi

# ---- 1.5. Reclaim ownership of bind-mounted data dirs --------------------
# The Dockerfile chowns /var/lib/tulip to uid 1000 at image-build time, but
# a bind mount overlays that with the host directory's ownership (typically
# the host user's uid on Linux, often 501 on macOS Docker Desktop). Without
# this, `gosu tulip alembic upgrade head` below fails with EACCES on the
# very first boot against an empty bind mount. Idempotent — a no-op once
# the dirs are already tulip-owned.
chown -R tulip:tulip /var/lib/tulip/db /var/lib/tulip/attachments

# ---- 2. Migrate as the tulip user (writes to the tulip-owned volume) -----
# Skip migration when TULIP_SKIP_MIGRATION=1 — used by tests or by
# operators running `docker compose run` for one-off commands.
if [ "${TULIP_SKIP_MIGRATION:-0}" != "1" ]; then
    gosu tulip alembic -c packages/tulip-storage/alembic.ini upgrade head
fi

# ---- 3. Hand off to the CMD as the tulip user ----------------------------
exec gosu tulip "$@"
