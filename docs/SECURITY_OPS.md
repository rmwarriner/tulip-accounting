# Security operations

**Status:** Document. Internal-beta. Last reviewed 2026-05-20.

This document is the operator's standing security reference: how
to configure Tulip securely, what to watch for, and how to
respond to incidents. It is distinct from:

- [`SECURITY.md`](../SECURITY.md) — **vulnerability reporting**
  policy for researchers (where to send a report, what the
  disclosure window is). That doc is for outside reporters; this
  one is for operators.
- The dated audit reports under [`docs/audits/`](audits/) —
  point-in-time snapshots from the Phase 8 deep security
  (2026-05-12) and privacy (2026-05-13) reviews. Those describe
  what was found and what was fixed; this doc describes how to
  run the system safely going forward.
- [`docs/THREAT_MODEL.md`](THREAT_MODEL.md) — the threat model
  Tulip's design is calibrated against. This doc operates inside
  that model; it doesn't restate it.

Read this once. Re-read the §4 incident-response section before
you need it, not when you need it.

---

## 1. Secure-by-default setup checklist

A new install should land all of these on day one.

### 1.1 Secrets

- [ ] **Master key** lives at `deploy/docker/secrets/master-key`,
      mode `0400`, owned by the host user that runs `docker
      compose`. The file is **not** committed to git (`.gitignore`
      includes `deploy/docker/secrets/`).
- [ ] **JWT secret** lives at `deploy/docker/secrets/jwt-secret`,
      same mode + ownership.
- [ ] **A copy of both files** lives in the Recovery Packet
      ([`RECOVERY.md`](RECOVERY.md) §2) — outside the host.
- [ ] If running with `TULIP_ENV=prod`, the boot path refuses to
      start on an ephemeral master key or JWT secret (Phase 8
      Wave-1 #223). Don't disable that check.

### 1.2 MFA

- [ ] The household's MFA policy is set:
      `tulip ai config show` (sic — actually
      `tulip households mfa-policy show`; the field lives on
      the household record). Default is `required_for_admins`.
      For a single-user installation it doesn't matter; for two
      or more users, set `required_for_all`.
- [ ] Every admin has MFA enrolled and tested:
      `tulip auth status` reports `mfa_enrolled: true`. If
      anyone is admin without MFA, fix it now.
- [ ] Recovery codes are printed and in the Recovery Packet.

### 1.3 Network

- [ ] API binds `127.0.0.1:8000` by default. Don't change to
      `0.0.0.0` without a reverse proxy in front.
- [ ] Reverse proxy terminates TLS (Caddy / Tailscale /
      Cloudflare — see [`DEPLOYMENT.md` §2](DEPLOYMENT.md)).
- [ ] Outbound 443 is open if (and only if) AI features are
      enabled. Otherwise the API needs no outbound. Audit the
      outbound traffic if you're paranoid:
      `docker compose logs api 2>&1 | grep -i 'outbound\|provider\|api.openai\|api.anthropic'`.

### 1.4 AI privacy posture

- [ ] `tulip ai config show` — default policy is "no AI",
      meaning every capability requires the household admin to
      opt in via `tulip ai config set` before any prompt leaves
      the host.
- [ ] If AI is enabled: `log_prompts` defaults to **false**.
      When on, prompts + responses are stored in `ai_invocations`
      and ride backups in the clear. The CLI warns at toggle
      time (P8 Wave-1 #245); the warning is real, not advisory.
- [ ] Provider keys are per-user when possible (`tulip ai set-key`
      defaults to user-scoped), not household-shared. Per-user
      keys can be rotated without affecting other users.

### 1.5 Backup encryption

- [ ] Backups are taken via `tulip backup` on a cron schedule
      (see [`DEPLOYMENT.md` §5](DEPLOYMENT.md)). The tarball
      carries field-encrypted columns under the master key; the
      tarball itself is **not** double-encrypted.
- [ ] Off-site replication uses the same tarball (don't re-
      encrypt; you'd just be paying for it twice).
- [ ] If `log_prompts=true`: backups carry AI prompts +
      responses in plaintext. Either turn `log_prompts` off and
      re-take, or accept that any holder of the master key sees
      every prompt ever sent.

---

## 2. Auth + session operations

### 2.1 Rate limiting

Four endpoints carry `slowapi` rate limits (P8 Wave-1 #219):

| Endpoint | Limit |
|---|---|
| `POST /v1/auth/login` | 10/minute |
| `POST /v1/auth/login/mfa` | 10/minute |
| `POST /v1/auth/login/recover` | 10/minute |
| `POST /v1/auth/refresh` | 30/minute |

Limits are per-client-IP. Excess returns `auth.rate_limited`
(429) with a `Retry-After` header. The backend is in-memory
(single-process SQLite); rates persist within a process and
reset on restart.

There is no operator surface to tune these without a code
change today. If you're hitting them legitimately
(e.g. a load test), restart the API to clear the in-memory
state.

### 2.2 Login monitoring

`audit_log` records every authenticated action. The
authentication-specific rows worth monitoring:

| `action` | What it means |
|---|---|
| `login_success` | Successful password + MFA |
| `login_failed` | Bad password (does not distinguish unknown email per #221) |
| `mfa.code_rejected` | Bad TOTP code after a successful password |
| `mfa.recovery_rejected` | Bad recovery code after a successful password |
| `mfa.recovery_login` | Successful login using a recovery code |
| `logout` | Explicit logout (refresh-token revocation) |
| `password_changed` | User changed their password |

Recommended daily check (run as cron + email yourself the
result, or wire into your monitoring stack):

```bash
docker compose -f deploy/docker/compose.yml exec -T api sqlite3 /var/lib/tulip/db/tulip.db \
"SELECT action, COUNT(*) as n FROM audit_log
 WHERE occurred_at > datetime('now','-24 hours')
   AND action LIKE 'login%' OR action LIKE 'mfa.%'
 GROUP BY action ORDER BY n DESC;"
```

A burst of `login_failed` / `mfa.code_rejected` rows from a
single IP is a credential-stuffing attempt. The rate limiter
caught it; review and consider widening the reverse proxy's
IP allow-list.

### 2.3 Session revocation

Refresh tokens are stored hashed (SHA-256) in `sessions`. Three
revocation surfaces:

```bash
# One device, the easy way:
tulip auth logout

# Every device for a user (admin):
docker compose -f deploy/docker/compose.yml exec -T api sqlite3 /var/lib/tulip/db/tulip.db \
    "DELETE FROM sessions WHERE user_id = '<user_uuid>';"

# Every session everywhere (operator-level lockout):
docker compose -f deploy/docker/compose.yml exec -T api sqlite3 /var/lib/tulip/db/tulip.db \
    "DELETE FROM sessions;"
```

Access tokens are short-lived (15 min) and not revocable
individually; killing the refresh forces re-auth within that
window.

---

## 3. PII + privacy posture

### 3.1 What's encrypted at rest

Field-level AES-256-GCM under the master key:

- TOTP secrets (`users.totp_secret_encrypted`)
- AI provider keys (per-user + per-household)
- Attachment ciphertext bytes (on disk, indexed by content hash)
- Import-batch `summary_json` (P8 #238)

See [`ARCHITECTURE.md` §7.4-7.5](ARCHITECTURE.md) for the layer
diagram and the column list.

### 3.2 What's NOT encrypted at rest

Plain SQLite columns:

- Email addresses, display names
- Transaction descriptions + amounts + references + notes
- Account names + codes
- Audit-log `before_snapshot` / `after_snapshot` (these *may*
  contain PII fragments)
- AI invocation rows: `prompt_hash` always, full `prompt_json` /
  `response_text` when `log_prompts=true`

Whole-DB SQLCipher encryption is a Phase 8 future hardening
item ([ARCHITECTURE.md §7.4](ARCHITECTURE.md)). Until it lands,
disk-level protection of the host is on the operator: full-disk
encryption on the host, controlled physical access, careful
backup-destination choice.

### 3.3 PII redaction in logs

The structlog pipeline scrubs known-sensitive fields (P8
Wave-1 #220 / #246): account numbers, passwords, TOTP secrets,
API keys, master key, **email addresses, IP addresses**, user
agents (#246). Whitelist-based, so unknown fields are emitted
as-is. Don't add new structlog event keys with raw PII without
also adding the key to the redactor's whitelist.

`audit_log.ip_address` is **not** redacted at rest — only at
log-write time. The per-host forensic depth is preserved in the
DB; logs going to a SIEM don't carry the IP. If you've
forwarded a log line through anywhere outside the host, the IP
is already gone.

### 3.4 GDPR / CCPA operator surface

Every data-subject right is wired to a CLI command. See
[`USER_RIGHTS.md`](USER_RIGHTS.md) for the full map. The
high-traffic ones:

```bash
# Art. 15 access — export everything we have on this user:
tulip admin user-export <user_id> --out ~/export.json

# Art. 16 rectification — update PII in place:
tulip users patch <user_id> --email new@example.com
tulip transactions describe-rectify <tx_id> --reason "..."

# Art. 17 erasure — admin-driven:
tulip users <user_id> --delete

# Art. 17 erasure — user-driven (household-wide):
tulip households erase-request --confirm

# Art. 21 objection — turn AI off for one user:
tulip ai admin set-user-policy <user_id> --profile local_only
```

---

## 4. Incident response

### 4.1 Suspected compromise of the host

**Do this in order. Don't skip steps; the goal is to bound the
blast radius.**

1. **Disconnect the host from the network** (pull cable / disable
   WiFi). Stops in-flight data exfiltration.

2. **Snapshot the host disk** before doing anything else if
   forensics matter (was this a real intrusion? are we writing
   a report?). `dd` to an external drive, or VM snapshot.

3. **Take an out-of-band backup** while the network is off:
   ```bash
   docker compose -f deploy/docker/compose.yml exec -T api \
       tulip backup --out - > "compromise-$(date -u +%Y-%m-%d-%H%M).tar.gz"
   ```
   This is your "last known state" backup. Don't trust it for
   restore yet — assume it carries whatever the attacker
   touched — but keep it.

4. **Rotate every secret.** All of:
   - Master key (manual procedure per
     [`QUICKSTART.md` §10](QUICKSTART.md)).
   - JWT secret (immediate; invalidates every session).
   - Every AI provider key (`tulip ai forget-key` +
     `tulip ai set-key`, or the provider's web console if the
     CLI isn't trustworthy yet).
   - Every user's password — admin-side reset on a clean host
     once §4.2 is done.

5. **Restore on a clean host** following
   [`RECOVERY.md` §4](RECOVERY.md). Start from a backup taken
   **before** the incident timestamp (whatever you have evidence
   for from the audit-log forensic review in §4.2). The
   "compromise-…" backup from step 3 stays in cold storage as
   the incident artefact.

6. **Force-reset every user's password** on the clean host. The
   user database is intact; the admin can fire password resets
   from `tulip users <id> --force-password-reset` (this issues
   a one-time reset link via the audit pathway).

### 4.2 Forensic review against the live DB

If you want to know what the attacker did before you wipe:

```bash
# Logins in the suspected window:
sqlite3 /var/lib/tulip/db/tulip.db \
"SELECT occurred_at, action, actor_user_id, ip_address
 FROM audit_log
 WHERE occurred_at BETWEEN '2026-05-19T00:00:00' AND '2026-05-20T00:00:00'
   AND (action LIKE 'login%' OR action LIKE 'mfa.%')
 ORDER BY occurred_at;"

# All actions by a suspected actor:
sqlite3 /var/lib/tulip/db/tulip.db \
"SELECT occurred_at, action, entity_type, entity_id
 FROM audit_log
 WHERE actor_user_id = '<suspected_user_id>'
 ORDER BY occurred_at DESC LIMIT 200;"

# Data exfiltration via reports / journal export:
sqlite3 /var/lib/tulip/db/tulip.db \
"SELECT occurred_at, action, actor_user_id, ip_address
 FROM audit_log
 WHERE action IN ('report.viewed', 'journal.exported', 'backup.taken')
 ORDER BY occurred_at DESC LIMIT 100;"
```

### 4.3 Lost master key

If the master key is gone and there's no copy in the Recovery
Packet — and the live host is intact — the only thing left to
do is rotate while you still can:

1. Take the API down.
2. The field-encrypted columns are now unreadable, but the live
   plaintext columns are still legible. Export the legible
   parts via `tulip admin user-export` etc. and the journal
   export.
3. Discard the live DB. The recoverable data is whatever you
   exported in step 2.
4. **Update the Recovery Packet procedure** so this doesn't
   happen again. See [`RECOVERY.md` §6](RECOVERY.md).

If the master key is gone AND the live host is also gone, the
backups are unreadable. There is no recovery procedure. This
is the design ([`THREAT_MODEL.md`](THREAT_MODEL.md) calibrates
encryption to make this exactly true for attackers; the
operator inherits the same property).

### 4.4 AI provider compromise

A cloud AI provider reports a breach or your provider key
shows up on a paste site:

1. **Immediate:** `tulip ai forget-key` for every affected
   provider, every household + user.
2. **At the provider console:** explicitly revoke the leaked
   key (don't just rotate — revoke so the attacker can't reuse
   it within an existing process's loaded creds).
3. **Audit:** every `ai.consent_changed` (P8 Wave-1 #247) and
   every `ai.invoked` row from the key's active window. The
   provider may have a copy of every prompt sent under that
   key; rotating the key doesn't reach their data.
4. **Notify** any household member whose data was in those
   prompts (GDPR Art. 33-34 if you're in scope) and decide
   whether to disable AI capabilities going forward.

---

## 5. Routine cadence

| Cadence | Task |
|---|---|
| **Daily** | `audit_log` skim (§2.2 query); backup ran (`ls -la ~/tulip-backups | tail -3`); container is up (`docker compose ps`). |
| **Weekly** | `tulip doctor`; review `docker compose logs --since 7d \| grep -iE 'error\|warning'`. |
| **Monthly** | Review user list + roles (`tulip users list`); review `tulip ai status` per user; verify off-site backup replication; review `tulip admin audit-prune --dry-run`. |
| **Quarterly** | Verify backup chain (§5.5 of [`DEPLOYMENT.md`](DEPLOYMENT.md)); review rate-limit near-misses (`SELECT COUNT(*) FROM audit_log WHERE action='auth.rate_limited' AND occurred_at > date('now','-90 day')`); review every per-user AI key (`tulip ai admin list-keys`). |
| **Annually** | Rotate JWT secret (§3.4 of [`DEPLOYMENT.md`](DEPLOYMENT.md)); rotate master key per [`QUICKSTART.md` §10](QUICKSTART.md); rotate every AI provider key; verify Recovery Packet + run the dry-run per [`RECOVERY.md` §6](RECOVERY.md); review users + roles + MFA policy. |
| **On every release** | Read `PHASE_STATUS.md` changelog for security-relevant changes; pre-upgrade backup; apply [`DEPLOYMENT.md` §6](DEPLOYMENT.md); `tulip doctor` after. |

---

## 6. Where this doc ends

- [`SECURITY.md`](../SECURITY.md) — vulnerability reporting
  policy (researcher-facing, not operator-facing).
- [`docs/THREAT_MODEL.md`](THREAT_MODEL.md) — the threat model
  Tulip's design is calibrated against.
- [`docs/audits/`](audits/) — point-in-time audit reports.
- [`docs/USER_RIGHTS.md`](USER_RIGHTS.md) — data-subject rights
  operator surface (GDPR / CCPA).
- [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) — production ops
  reference (this doc's sibling).
- [`docs/RECOVERY.md`](RECOVERY.md) — bus-factor / disaster
  recovery.
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) §7 — full
  cross-cutting concerns reference (auth, crypto, audit log).

If you're a researcher with a vulnerability to report, you want
[`SECURITY.md`](../SECURITY.md) — not this doc.
