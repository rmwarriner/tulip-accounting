# Tulip Accounting — QUICKSTART

This guide takes you from an empty machine to a household with a real
imported, reconciled, and backed-up statement in about 20 minutes. It is
the **entry point** for internal-beta users: every command is
copy-paste-runnable; every step has a verifiable success signal.

> **Internal beta scope.** Tulip is single-machine, single-tenant
> SQLite right now. You're hosting a personal-accounting stack on your
> own laptop, home server, or Tailscale-connected VPS — not a service
> for other people. Reverse-proxy / multi-machine deployments are
> deliberately out of scope until external beta (see `docs/ARCHITECTURE.md`
> §10).

---

## 1. Prerequisites

- **Docker** with the Compose v2 plugin. Verify with `docker compose version`.
- **[uv](https://docs.astral.sh/uv/)** for running the CLI from the
  cloned repo. One-line install:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- Git, to clone the repo.

That's it. No Python toolchain on the host (`uv` manages it); no
Postgres, no Redis. The whole stack is the API container plus a
SQLite volume.

---

## 2. Install

Clone the repo and generate the secret material the API needs at boot:

```bash
git clone https://github.com/rmwarriner/tulip-accounting.git
cd tulip-accounting

mkdir -p deploy/docker/secrets
python3 -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())' \
  > deploy/docker/secrets/master-key
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' \
  > deploy/docker/secrets/jwt-secret
chmod 0400 deploy/docker/secrets/*
```

The master key is what encrypts your TOTP secrets and attachment
contents. **Back it up immediately** — losing it is unrecoverable
(per `docs/THREAT_MODEL.md` §5.2). A plain copy on a separate medium
is fine for internal beta; a password manager entry works too.

Bring the stack up:

```bash
docker compose -f deploy/docker/compose.yml up --build --wait
```

`--wait` blocks until the API's `/health` endpoint returns 200, so when
the command returns you have a running stack on `http://127.0.0.1:8000`.

Verify with the doctor:

```bash
export TULIP_API_URL=http://127.0.0.1:8000
uv run tulip doctor
```

Expected: `tulip doctor: all checks passed (6/6)` — except the
"Token store" check, which is a warning ("run `tulip auth login` ...")
because you haven't registered yet. That's fine.

> **If `tulip doctor` reports any hard failure** (exit 2), read the
> per-check stderr lines: each names the env var or path you need to
> fix. The most common one on fresh installs is a master-key file with
> wrong permissions; the message tells you exactly what `chmod` to run.

---

## 3. First household

Register the household's first user (this user is automatically the
admin):

```bash
uv run tulip register \
  --email me@example.com \
  --display-name "Your Name" \
  --household "My Household" \
  --password-stdin <<< 'pick-a-strong-passphrase'
```

> The `--password-stdin` form is for scripts; in an interactive shell
> just omit the heredoc and the CLI will prompt you (hidden input).

Now log in. This stores tokens locally (OS keyring by default; set
`TULIP_TOKEN_STORE` to a file path if you're on a headless box):

```bash
uv run tulip auth login --email me@example.com \
  --password-stdin <<< 'pick-a-strong-passphrase'
```

Confirm:

```bash
uv run tulip auth status
```

Should print `Logged in at http://127.0.0.1:8000` with your email and
an access-token TTL.

---

## 4. Seed accounts

Tulip is double-entry — every transaction has at least two postings
that sum to zero. You need at least one asset account (your bank) and
one or more expense accounts to post against.

```bash
uv run tulip accounts add --code 1010 --name "Checking" --type asset --currency USD
uv run tulip accounts add --code 5100 --name "Groceries" --type expense --currency USD
uv run tulip accounts add --code 5200 --name "Rent"      --type expense --currency USD
uv run tulip accounts add --code 5300 --name "Fuel"      --type expense --currency USD
uv run tulip accounts add --code 5400 --name "Dining"    --type expense --currency USD
uv run tulip accounts add --code 4000 --name "Salary"    --type income  --currency USD
```

List to confirm:

```bash
uv run tulip accounts list
```

You should see six rows; the tree view collapses to flat at this size.

---

## 5. Import a statement

The repo ships a sample OFX file under `docs/quickstart-fixtures/`
that mirrors a realistic month: salary deposits, a few common spend
categories, rent. Import it:

```bash
BATCH_ID=$(uv run tulip --json imports ofx docs/quickstart-fixtures/sample-statement.ofx \
  --account 1010 | jq -r .id)
echo "Imported as batch $BATCH_ID"
```

The batch starts in PENDING state — nothing's in your ledger yet.
Inspect what's queued:

```bash
uv run tulip imports show "$BATCH_ID"
```

You should see six lines: two payroll credits, four debits.

Now promote them to PENDING transactions in the ledger:

```bash
uv run tulip imports apply "$BATCH_ID"
```

Verify the balance:

```bash
uv run tulip balance --account 1010
```

Should show `3611.88 USD` (matches the OFX `LEDGERBAL`).

---

## 6. Reconcile

A reconciliation pairs each imported statement line with a ledger
transaction so the statement and the ledger agree. For our sample,
the auto-matcher will pair every line on its own.

Open a reconciliation envelope:

```bash
RECON_ID=$(uv run tulip --json reconcile create \
  --account 1010 \
  --batch "$BATCH_ID" \
  --period 2026-05-01..2026-05-31 \
  --starting 0.00 \
  --ending 3611.88 | jq -r .id)
echo "Opened reconciliation $RECON_ID"
```

Run the auto-matcher:

```bash
uv run tulip reconcile auto-match "$RECON_ID"
```

Inspect the four-section review pane — envelope summary, matches,
unmatched statement lines, unmatched ledger transactions:

```bash
uv run tulip reconcile show "$RECON_ID"
```

For this fixture every line should be high-confidence matched; both
unmatched sections render `(none)`.

Finalise:

```bash
uv run tulip reconcile complete "$RECON_ID"
```

`complete` runs a strict balance check (`statement_ending_balance =
sum(matched ledger postings)`) and stamps each matched transaction
with `reconciled_at`. If you ever see a `reconciliation.unbalanced`
error here, look at `tulip reconcile show` first — there's a 4-section
diff that tells you which lines aren't accounted for.

---

## 7. Close the month

```bash
PERIOD_ID=$(uv run tulip --json periods list | jq -r '.[0].id')
uv run tulip periods close "$PERIOD_ID"
```

Subsequent writes inside that date range now return `period.closed`
(400). To re-open if you find a missing transaction:

```bash
uv run tulip periods reopen "$PERIOD_ID"
```

`tulip periods list` shows the current status of every period — colored
red when soft-closed.

---

## 8. Backup

The stack's SQLite DB and attachments live in Docker volumes inside
the container. `tulip backup` is bundled into the runtime image so it
can read the volume directly. Stream a backup to a host-side `.tar.gz`:

```bash
docker compose -f deploy/docker/compose.yml exec -T api \
  tulip backup --out - > "tulip-backup-$(date -u +%Y%m%d-%H%M).tar.gz"
```

A few notes:

- The `-T` is important: it disables TTY allocation so the tar bytes
  reach your shell uncorrupted.
- The tarball is **not** re-encrypted. The field-encrypted columns
  (TOTP secrets, attachments) stay encrypted inside; the rest is
  plain SQLite. Keep backups on storage you trust.
- The backup includes a manifest with the master-key envelope — restore
  will refuse to load if your current key doesn't match.

Inspect a backup without restoring:

```bash
uv run tulip backup-inspect tulip-backup-*.tar.gz
```

Restore (against a fresh stack — see `tulip restore --help`):

```bash
docker compose -f deploy/docker/compose.yml down -v
# Restart, then:
docker compose -f deploy/docker/compose.yml exec -T api \
  tulip restore --in - < tulip-backup-20260601-1200.tar.gz
```

---

## 9. Cookbook: rotating the master key

Tulip doesn't ship a `tulip key-rotate` command for internal beta —
the cost of getting it wrong (un-decryptable TOTP secrets across an
unknown subset of users) outweighs the convenience. The manual
procedure is short and explicit. Run it during a maintenance window.

**Step 1**. Take the API down. Field encryption is not online-rotatable;
in-flight reads against an old key while the new key is being written
would deadlock or corrupt.

```bash
docker compose -f deploy/docker/compose.yml down
```

**Step 2**. Take a backup of the current install in case anything goes
wrong:

```bash
docker compose -f deploy/docker/compose.yml up --wait
docker compose -f deploy/docker/compose.yml exec -T api \
  tulip backup --out - > "pre-rotation-$(date -u +%Y%m%d-%H%M).tar.gz"
docker compose -f deploy/docker/compose.yml down
```

**Step 3**. Mint a new key and decrypt-then-re-encrypt every field-encrypted
column. This step requires a small Python script that loads each row,
decrypts with the old key, re-encrypts with the new key, writes back —
it isn't a one-liner and intentionally isn't a CLI command. Track it
under `ops/` in your own deployment; a worked example lives in
[`docs/THREAT_MODEL.md` §5.2](THREAT_MODEL.md).

**Step 4**. Replace the key file and bring the API back up:

```bash
chmod 0600 deploy/docker/secrets/master-key  # writable for the swap
python3 -c 'import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())' \
  > deploy/docker/secrets/master-key
chmod 0400 deploy/docker/secrets/master-key
docker compose -f deploy/docker/compose.yml up --wait
uv run tulip doctor
```

Doctor should report `master key loaded from file`.

---

## What's next

- Run `tulip --help` to discover commands not covered here: `envelopes`,
  `sinking-funds`, `refills`, `transfer`, `budget-inflow`.
- File issues at https://github.com/rmwarriner/tulip-accounting/issues —
  internal beta is the point at which feedback is most actionable.
- Read [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) if you want to
  understand *why* the shape of the code looks the way it does;
  [`docs/THREAT_MODEL.md`](THREAT_MODEL.md) for the security model.

---

## Tear-down

Stop the stack but **preserve** volumes (so `up` brings your data back):

```bash
docker compose -f deploy/docker/compose.yml down
```

Stop the stack and **wipe** all data (irreversible):

```bash
docker compose -f deploy/docker/compose.yml down -v
rm -rf deploy/docker/secrets
```
