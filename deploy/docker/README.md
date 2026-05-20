# Tulip Accounting — Docker compose deploy (`#134`)

Internal-beta self-host. Boots the API + persistent SQLite + persistent attachment
store + Docker-secret-injected master key & JWT secret.

## First-run setup

```bash
# 1. Generate the secrets (32-byte master key, 48-byte JWT secret).
mkdir -p deploy/docker/secrets
python -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())' \
  > deploy/docker/secrets/master-key
python -c 'import secrets; print(secrets.token_urlsafe(48))' \
  > deploy/docker/secrets/jwt-secret
chmod 0400 deploy/docker/secrets/*

# 2. Build + boot.
docker compose -f deploy/docker/compose.yml up --build --wait

# 3. Register a household + log in (CLI runs on the host).
TULIP_API_URL=http://127.0.0.1:8000 uv run tulip register \
  --email me@example.com --household Mine --display-name Me --password-stdin
```

## Operations

| Goal | Command |
|---|---|
| Stop, keep data | `docker compose -f deploy/docker/compose.yml down` |
| Stop, **wipe** data | `docker compose -f deploy/docker/compose.yml down -v` |
| Tail logs | `docker compose -f deploy/docker/compose.yml logs -f api` |
| Run a one-off command | `docker compose -f deploy/docker/compose.yml run --rm api <cmd>` |
| Healthcheck (manual) | `curl http://127.0.0.1:8000/health` |

## Backup + restore

The `tulip backup` / `tulip restore` commands (#133) operate on the SQLite file
+ attachment tree directly — they don't go through the API. To run them against
this compose stack from the host:

```bash
# Read the SQLite path + attachment root from the compose volume mounts.
DB_VOL=$(docker volume inspect tulip-docker_tulip-db --format '{{.Mountpoint}}')
ATT_VOL=$(docker volume inspect tulip-docker_tulip-attachments --format '{{.Mountpoint}}')

TULIP_KEY_FILE=deploy/docker/secrets/master-key \
TULIP_DATABASE_URL="sqlite:///$DB_VOL/tulip.db" \
TULIP_ATTACHMENT_ROOT="$ATT_VOL" \
  uv run tulip backup --out ~/tulip-$(date +%F).tar.gz
```

(`tulip-docker_` is compose's per-project volume prefix; replace if you renamed
the project.)

## Troubleshooting

### Container starts unhealthy with `sqlite3.OperationalError: unable to open database file`

The DB path is a host bind mount (`./data/db`) so the project-scoped
sqlite MCP server can read live state. Two failure modes:

1. **First boot on macOS** — Docker Desktop's virtio-fs silently no-ops
   `chown` on bind mounts, so the entrypoint's `chown -R tulip` doesn't
   actually take effect. The entrypoint compensates with a follow-up
   `chmod 0777` on the bind-mount dir; if you see this error anyway,
   verify the host-side `data/db` is writable (`ls -la deploy/docker/data/db`).

2. **Phantom file from a pre-#397 named-volume run** — if you originally
   ran an older revision that used a named `tulip-db` volume, Docker
   Desktop's bind-mount cache can carry a stale ghost entry for
   `tulip.db` even after the volume is removed. Symptom: from inside the
   container, `os.path.exists("/var/lib/tulip/db/tulip.db")` returns
   True but `open()` fails with `FileNotFoundError`. Recover by
   recreating the host directory:

   ```bash
   docker compose -f deploy/docker/compose.yml down
   rm -rf deploy/docker/data/db
   mkdir -p deploy/docker/data/db
   docker compose -f deploy/docker/compose.yml up --build --wait
   ```

## What's deliberately not here

- TLS termination — internal-beta is localhost-bound. Put it behind Caddy /
  Tailscale Funnel / your favourite reverse proxy if exposing it.
- Postgres backend — Phase 9.
- Image publishing to GHCR — internal beta builds locally; publishing is a
  post-beta concern.
- Multi-host orchestration (k8s, Nomad) — out of scope for v1.

See [#134](https://github.com/rmwarriner/tulip-accounting/issues/134) for the
full design discussion and [#121](https://github.com/rmwarriner/tulip-accounting/issues/121)
for the umbrella tracking pre-internal-beta hardening.
