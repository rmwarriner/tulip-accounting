# Deployment — production ops

**Status:** Document. Internal-beta. Last reviewed 2026-05-20.

This document is the operator's manual for running Tulip in
production on your own hardware: steady-state operation, reverse
proxy patterns, backups + restore, upgrades, key rotation.

It is **not** the first-time install guide ([QUICKSTART.md](QUICKSTART.md)
covers that), the disaster-recovery guide ([RECOVERY.md](RECOVERY.md)
covers rebuilding from zero), or the security-ops guide
([SECURITY_OPS.md](SECURITY_OPS.md) covers ongoing security
posture). Read this for steady-state production ops; read those
three for first-install, disaster recovery, and security
posture respectively.

---

## 1. Host requirements

Tulip is designed for a single-machine deployment. A NAS,
home-lab Linux VM, or a small VPS is the target.

| Resource | Minimum | Comfortable |
|---|---|---|
| **CPU** | 1 vCPU | 2 vCPU |
| **RAM** | 512 MB | 2 GB (1 GB+ if running cloud AI alongside) |
| **Disk** | 2 GB for the image + 1 GB headroom per year of data | 10+ GB SSD |
| **Network** | Outbound 443 for AI providers (optional) | Same |
| **OS** | Docker + Compose v2 | Same — image is debian-slim |

**Outbound network policy:** the API itself doesn't need
outbound except for the AI provider call when opt-in AI is
configured. If you've never run `tulip ai set-key`, the
container makes zero outbound connections.

**Port policy:** the image binds `127.0.0.1:8000` by default
([`compose.yml`](../deploy/docker/compose.yml)). Don't expose
8000 directly to the public internet — put it behind a reverse
proxy (§2).

---

## 2. Reverse-proxy patterns

Tulip doesn't ship its own TLS. Two recommended patterns:

### 2.1 Caddy (simple, free TLS via Let's Encrypt)

```caddyfile
tulip.example.com {
    reverse_proxy 127.0.0.1:8000
    encode gzip
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
}
```

Add `tulip.example.com` to DNS pointing at the host's public IP,
restart Caddy, and the TLS handshake is automatic on first hit.

To restrict to known clients only (recommended for an
internal-beta posture):

```caddyfile
tulip.example.com {
    @allowed_ips {
        remote_ip 203.0.113.42 198.51.100.0/24
    }
    handle @allowed_ips {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        respond 403
    }
}
```

### 2.2 Tailscale Funnel (zero DNS, mesh-VPN-only)

If Tulip is for personal use and you have Tailscale, expose it
only to your tailnet — no public DNS, no public TLS cert.

```bash
sudo tailscale serve --tls-terminated-tcp=8000 \
    --bg http://127.0.0.1:8000
```

The hostname is `https://<machine-name>.<your-tailnet>.ts.net`.
Devices on the tailnet can reach it; nothing else can.

### 2.3 Why not bare HTTP

Don't. The CLI's bearer-token auth (P3.2) protects the API, but
plaintext bearer tokens over WiFi are a credential-stealing
vector. Even on a LAN, terminate TLS at the reverse proxy.

---

## 3. Persistent volumes — what lives where

```
deploy/docker/
├── compose.yml
├── secrets/
│   ├── master-key         # 32-byte base64, mode 0400
│   └── jwt-secret         # 48-byte url-safe, mode 0400
└── data/
    └── db/
        └── tulip.db       # SQLite DB (bind mount per #397)

(named volume) tulip-attachments → /var/lib/tulip/attachments
                                   inside the container
```

### What goes in backups

Both the SQLite DB and the attachment store. The `tulip backup`
CLI bundles them into a single tarball with a manifest +
master-key envelope. See §5.

### What does NOT go in backups

`deploy/docker/secrets/`. The master key is a different artifact
from the data it encrypts; back them up separately, store them
separately. See [`RECOVERY.md`](RECOVERY.md) §2.

### Volume sizing

| Item | Growth rate (rough) |
|---|---|
| SQLite DB | ~5 KB per transaction, ~100 KB per import batch (after compaction) |
| Attachments | Size of every imported statement file (you control the rate) |
| Audit log | ~500 B per audit row; pruned per [`USER_RIGHTS.md`](USER_RIGHTS.md) retention policy |

Plan for several years of household-scale data to fit in well
under a gigabyte. The image itself dominates the install size.

---

## 4. Logging

### 4.1 What's emitted

The API uses `structlog` JSON output to stdout. Docker captures
it via the default `json-file` log driver.

```bash
# Tail live:
docker compose -f deploy/docker/compose.yml logs -f api

# Last N lines:
docker compose -f deploy/docker/compose.yml logs --tail 200 api

# Since timestamp:
docker compose -f deploy/docker/compose.yml logs --since 1h api
```

### 4.2 What's redacted

A structlog processor scrubs known-sensitive fields before
serialisation: account numbers, password fields, TOTP secrets,
API keys, the master key, plus email + IP addresses (Phase 8
Wave-1 #220 / #246). Unknown fields are emitted as-is — be
careful adding new structlog event fields in any custom code.

### 4.3 Log rotation

Docker's `json-file` driver rotates by default at 10 MB × 1
file. For a home-lab where you want history, override in
`compose.override.yml`:

```yaml
services:
  api:
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"
```

For a real log pipeline (Loki, Vector, fluentd), point a
`docker logs --follow api` sidecar at your collector, or switch
to the `journald` log driver and consume from systemd.

### 4.4 Healthchecks

The image declares a Docker `HEALTHCHECK` that polls `/health`
every 30 s. `docker compose ps` surfaces healthy/unhealthy
state. Operators with a real monitoring stack should also point
external probes (Healthchecks.io / Uptime-Kuma / cron + curl)
at the reverse-proxy public URL — Docker's healthcheck doesn't
cover the proxy or DNS path.

`tulip doctor` (#135) is the standing smoke command — five
checks (API reachable, master key loaded from file, migration
head matches, attachment root writable, token store reachable).
Run it after every upgrade and on a quarterly cadence as part
of §6.

A Prometheus metrics endpoint is filed as [#86](https://github.com/rmwarriner/tulip-accounting/issues/86)
and not yet shipped. Until then, the healthcheck endpoint +
`tulip doctor` are the available signals.

---

## 5. Backup + restore

### 5.1 Take a backup

The runtime image bundles `tulip-cli` so `tulip backup` can run
inside the container against the in-volume SQLite file:

```bash
docker compose -f deploy/docker/compose.yml exec -T api \
    tulip backup --out - > "tulip-$(date -u +%Y-%m-%d).tar.gz"
```

The tarball contains: the SQLite DB (via `.backup()` for
concurrent-safe snapshotting), the attachment store, and a
manifest with format version + Tulip version + alembic head +
HMAC-SHA256 master-key envelope ([`ARCHITECTURE.md` §7.5](ARCHITECTURE.md)).

### 5.2 Automated backup with cron

```cron
# Daily at 03:00 local, retain 30 days
0 3 * * * docker compose -f /home/tulip/tulip-accounting/deploy/docker/compose.yml exec -T api \
    tulip backup --out - > /home/tulip/backups/tulip-$(date -u +\%Y-\%m-\%d).tar.gz 2>/dev/null \
    && find /home/tulip/backups -name 'tulip-*.tar.gz' -mtime +30 -delete
```

Adjust the retention window to your RPO. The QUICKSTART
([§9](QUICKSTART.md)) walks through one specific
grandfather-father-son schedule.

### 5.3 Off-site replication

The backup is already encrypted at the column level + carries
the master-key envelope. Synthesise off-site replication with
your tool of choice; the tarball is just a file:

- **rclone** to B2 / Cloudflare R2 / iDrive e2:
  ```bash
  rclone sync /home/tulip/backups remote:tulip-backups --max-age 35d
  ```
- **restic** to a self-hosted SFTP or another NAS:
  ```bash
  restic -r sftp:offsite:/tulip backup /home/tulip/backups
  ```

The remote storage doesn't need to be encrypted at rest — the
tarball already is. Don't pay double for it.

### 5.4 Restore

[`RECOVERY.md`](RECOVERY.md) §4 covers the full disaster-recovery
restore. The shorthand for "I'm restoring on the same host" is:

```bash
docker compose -f deploy/docker/compose.yml down
# Clear the live DB:
rm deploy/docker/data/db/tulip.db
docker compose -f deploy/docker/compose.yml up --wait
docker compose -f deploy/docker/compose.yml exec -T api \
    tulip restore --in - < /path/to/backup.tar.gz
tulip doctor
```

The restore CLI refuses on master-key mismatch (good — it's
detecting that you're trying to restore against the wrong host),
on schema-head mismatch (good — it's detecting a version skew
that needs explicit `--force`), or on a non-empty target
(good — refuses to clobber an existing DB without the operator
explicitly wiping it first).

### 5.5 Verify the backup chain

Quarterly:

```bash
# Newest backup parses cleanly:
docker compose -f deploy/docker/compose.yml exec -T api \
    tulip backup-inspect /backups/tulip-$(date +%Y-%m-%d).tar.gz
```

Annually:

```bash
# Full restore to a scratch host. See RECOVERY.md §6.
```

---

## 6. Upgrade path

### 6.1 Read the changelog first

```bash
git fetch origin
git log --oneline main..origin/main
```

Look for changes under `packages/tulip-storage/src/tulip_storage/migrations/`
(schema migrations require attention) or in `pyproject.toml`
files (dependency bumps are usually fine but read the diff).

### 6.2 Standard upgrade (safe path)

```bash
cd ~/tulip-accounting
git pull --ff-only

# Belt: take a backup *first*.
docker compose -f deploy/docker/compose.yml exec -T api \
    tulip backup --out - > "/backups/pre-upgrade-$(date -u +%Y-%m-%d).tar.gz"

# Bring the new image up. The entrypoint runs alembic upgrade
# head before exec'ing uvicorn, so migrations land
# automatically.
docker compose -f deploy/docker/compose.yml up --build --wait

# Verify:
docker compose -f deploy/docker/compose.yml exec -T api tulip doctor
```

The `--wait` flag blocks until the healthcheck passes. If it
times out, the upgrade has failed; see §6.4.

### 6.3 Pre-upgrade backup is non-negotiable

`alembic upgrade head` is forward-only in this codebase. If a
migration corrupts data (and migrations are tested, but real
upgrades sometimes hit cases CI didn't cover), the only path
back is restoring the pre-upgrade backup. **Tag the backup with
the version you're upgrading from**:

```bash
git rev-parse --short HEAD  # capture this in the filename
```

So `pre-upgrade-2026-05-20-from-a1b2c3d.tar.gz` tells you
later which Tulip version produced this file.

### 6.4 Recovering from a failed upgrade

Symptoms: `docker compose up --wait` times out / `tulip doctor`
fails / API logs show a Python traceback at boot.

```bash
docker compose -f deploy/docker/compose.yml down
git checkout <previous-commit>
# Restore the pre-upgrade backup:
rm deploy/docker/data/db/tulip.db
docker compose -f deploy/docker/compose.yml up --build --wait
docker compose -f deploy/docker/compose.yml exec -T api \
    tulip restore --in - < /backups/pre-upgrade-*.tar.gz
```

Then file an issue on the repo with the traceback so the bug
gets fixed before you (or someone else) hits it again.

### 6.5 Major version upgrades

Tulip is pre-1.0 (internal beta); there's no major-version
contract yet. Treat every release as if it could carry a
breaking migration and follow §6.2 + §6.3 every time.

---

## 7. Key + secret rotation

### 7.1 Master key rotation

Documented in [`QUICKSTART.md` §10](QUICKSTART.md). The short
version: take the API down, decrypt-then-re-encrypt every
field-encrypted column with the new key (manual script — not a
CLI command), swap the file, bring the API up. The manual step
is intentional; getting it wrong corrupts every TOTP secret +
attachment + AI key.

When to rotate:

- **Annually** as part of routine ops.
- **Immediately** if the key file ever leaves the operator's
  control (laptop stolen, backup destination compromised, paper
  copy lost). See [`SECURITY_OPS.md`](SECURITY_OPS.md) #428 for
  the incident-response procedure.

After rotation: take a fresh backup with the new key. Old
backups remain decryptable only with the old key, so either
keep the old key archived (and segregated) or accept that
older backups are now unreadable.

### 7.2 JWT secret rotation

Faster — no data is encrypted under the JWT secret; rotating
just invalidates every outstanding session. Every user has to
log in again:

```bash
docker compose -f deploy/docker/compose.yml down
chmod 0600 deploy/docker/secrets/jwt-secret
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' \
    > deploy/docker/secrets/jwt-secret
chmod 0400 deploy/docker/secrets/jwt-secret
docker compose -f deploy/docker/compose.yml up --wait
```

When to rotate:

- **Annually.**
- **Immediately** if the secret ever leaves the operator's
  control. Same rationale as the master key.
- **After a security incident** where session tokens could
  have been captured.

### 7.3 AI provider key rotation

Per-household / per-user provider keys are field-encrypted at
rest under the master key. Rotate from the CLI:

```bash
# Per-user (the default):
tulip ai forget-key --provider anthropic
tulip ai set-key --provider anthropic --key sk-ant-...

# Per-household (admin only):
tulip ai admin forget-key --provider anthropic --household me
tulip ai admin set-key --provider anthropic --household me --key sk-ant-...
```

Audit trail:
[`ai.consent_changed`](USER_RIGHTS.md) rows in `audit_log` per
P8 Wave-1 #247.

When to rotate:

- **After a host compromise** — the master key (and therefore
  the encrypted provider keys at rest) may have leaked. Even
  if the master key is rotated, the provider key on the cloud
  side is the second exposure.
- **At provider request** (e.g. their security incident).

---

## 8. Routine ops cadence

A standing schedule for a single-operator deployment.

| Cadence | Task | Where |
|---|---|---|
| **Daily** | Cron-driven backup runs | §5.2 |
| **Weekly** | `tulip doctor` and skim `docker compose logs --since 7d \| grep -i error` | §4.4 |
| **Monthly** | Review `audit_log` highlights — `tulip admin audit-prune --dry-run` + a quick `SELECT action, COUNT(*) FROM audit_log WHERE occurred_at > date('now','-30 day') GROUP BY action` | [`USER_RIGHTS.md`](USER_RIGHTS.md) |
| **Quarterly** | `tulip backup-inspect` against the newest backup; verify off-site replication; review user list + roles | §5.5 |
| **Annually** | Full restore dry-run on scratch host; rotate JWT secret; verify Recovery Packet contents; review MFA recovery code consumption | [`RECOVERY.md`](RECOVERY.md) §6 |
| **On every release** | Read `PHASE_STATUS.md` changelog; pre-upgrade backup; apply §6.2; `tulip doctor` after | §6 |

---

## 9. Where this doc ends

This doc handles **production operations** — running the
system over time. The companion docs cover adjacent concerns:

- [`docs/QUICKSTART.md`](QUICKSTART.md) — first-time setup
  walkthrough + the manual key-rotation cookbook.
- [`docs/RECOVERY.md`](RECOVERY.md) — bus-factor / successor
  recovery: rebuild from zero if the host is destroyed.
- [`docs/SECURITY_OPS.md`](SECURITY_OPS.md) (#428) — operator
  security ops + incident response.
- [`docs/USER_RIGHTS.md`](USER_RIGHTS.md) — GDPR / CCPA
  data-subject-rights operator surface.
- [`docs/THREAT_MODEL.md`](THREAT_MODEL.md) — the threat model
  Tulip's design is calibrated against.
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — full system design
  (what backups contain, how field encryption works, etc.).
- [`SECURITY.md`](../SECURITY.md) — vulnerability reporting
  policy (distinct from operator-facing security ops).

If you're reading this looking for first-install instructions,
you're in the wrong doc — go to [`QUICKSTART.md`](QUICKSTART.md).
