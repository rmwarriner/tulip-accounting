# Recovery and continuity

**Status:** Document. Internal-beta. Last reviewed 2026-05-20.

This document covers the **bus-factor** problem: if the
maintainer (you) is unavailable for an extended period — hit by
a bus, hospitalised, locked out of your own machine — what does
the household need in order to recover five years of accounting
data?

Confidentiality and integrity are covered elsewhere
([`THREAT_MODEL.md`](THREAT_MODEL.md)). This document covers
**availability** in the limit case where the one person who
knows how the system works isn't around to explain it.

Read this once. Have a second person (spouse, sibling, trusted
friend) read it too. Run the dry-run procedure in §6 at least
once. Re-read it annually.

---

## 1. The two failure modes

Two distinct disasters; the recovery procedures diverge after
step 1.

| Failure | What's lost | What you need |
|---|---|---|
| **The maintainer is unavailable** (e.g. hospitalised) | Nothing in the system; only the operator. | The Recovery Packet (§2), a second person, this doc. |
| **The host is destroyed** (drive failure, lost device, ransomware) | The live DB + attachment store. | The most recent backup, the master key, this doc. |

Both recoveries depend on the same artifacts. Storing them
correctly is the single most important thing in this document.

---

## 2. The Recovery Packet

Everything below needs to exist **outside** the host running
Tulip. The host might be the thing that's destroyed; secrets
that only live there don't help anyone.

### 2.1 Required items

1. **Master encryption key** (32 bytes, base64-encoded — the
   contents of `deploy/docker/secrets/master-key`). Without
   this, the backups are unrecoverable. Field-encrypted columns
   (TOTP secrets, attachment bytes, AI provider keys) decrypt
   only against this exact key.

2. **JWT secret** (the contents of
   `deploy/docker/secrets/jwt-secret`). Less critical than the
   master key — a successor can mint a new one — but having it
   preserves outstanding sessions across the recovery boot.

3. **Maintainer's MFA recovery codes** (the 16-char base32
   strings printed once by `tulip auth mfa verify`, stored as
   argon2id hashes in the DB). Used to log in as the
   maintainer when their TOTP authenticator is gone. Each code
   is single-use; only the ones not yet redeemed against the
   live DB still work after a restore.

4. **The most recent encrypted backup tarball** (whatever
   `tulip backup --out` produced most recently). The backup
   carries the SQLite DB + attachment store + an HMAC-SHA256
   master-key envelope. See [`ARCHITECTURE.md` §7.5](ARCHITECTURE.md)
   for the tarball format.

5. **A copy of the Tulip source tree**, pinned to the version
   the backup was taken against. If GitHub disappears, you
   can't restore against the unrelated future version that
   master will be by then. Mirror to a second git remote (Codeberg,
   self-hosted Gitea, sourcehut) or keep a periodic
   `git bundle` next to the backups.

6. **This document**. Recovery needs the procedure printed
   somewhere your successor can read without already having
   the running system.

### 2.2 Suggested storage

Pick at least two of these:

- **Bank safety deposit box.** Print everything on paper.
  Master key + JWT secret + MFA recovery codes are all short
  base64/base32 strings — a single sheet fits all of them.
- **Hardware password manager** (1Password Families,
  Bitwarden organisation) with the successor as an emergency
  contact / vault sharer. The vault should hold the secrets
  + a pointer to the backup location + a copy of this doc.
- **Encrypted USB drive** in a fireproof safe at home. Restic
  / VeraCrypt / age-encrypted tarball. The decryption key for
  the drive lives with the bank box or password manager — not
  in your head.
- **Off-site backup destination** (rclone to a B2 / Cloudflare
  R2 bucket, restic to a friend's NAS). This is for the
  encrypted backup tarball only — the secrets stay elsewhere.

**Don't** put the master key in the same place as the encrypted
backups. The whole point of encryption-at-rest is that an
attacker who finds the backups still needs the key. Keeping
them together defeats the model.

### 2.3 The named successor

One person needs to know:

- Where the Recovery Packet is.
- That they're the named successor.
- That this document exists and how to find it.

They don't need to know how Tulip works internally. They need
to know that, in an emergency, the path is:

> Open the Recovery Packet. Read `docs/RECOVERY.md`. Follow §3
> or §4. Ask someone technical for help if stuck; the procedure
> is documented so they don't need to invent anything.

Tell them now. Write down their name and contact below as a
forcing function:

```
Successor name:    ___________________________________
Successor email:   ___________________________________
Recovery packet:   ___________________________________
Last verified:     ___________________________________
```

---

## 3. Recovery — the maintainer is unavailable, host is intact

The host is running. The DB is on disk. The maintainer just
can't log in to drive things.

### Step 1. Get console / SSH access to the host

The successor needs shell access to the machine running Tulip.
If this requires the maintainer's password manager, that's
covered by the household's broader successor plan — outside
this document.

### Step 2. Log in as the maintainer using a recovery code

```bash
# From any machine with the tulip CLI:
TULIP_API_URL=http://<host>:8000 tulip auth login \
  --email <maintainer-email> \
  --password-stdin <<< '<maintainer-password>' \
  --recovery <<< '<one-of-the-recovery-codes>'
```

The password is needed too — recovery codes are the *MFA*
second factor, not a password reset. If the password is also
unknown, an admin user has to reset it; see §3.4.

### Step 3. Add the successor as an admin

```bash
tulip register \
  --household '<current-household-id>' \
  --email <successor-email> \
  --display-name '<Successor Name>' \
  --password-stdin
```

Then promote them:

```bash
# As the maintainer (logged in via recovery code):
tulip users grant <successor-user-id> --role admin
```

The successor can now log in as themselves and operate the
system. The maintainer's account stays in the DB; their data
isn't lost.

### Step 4. Enrol MFA for the successor

`tulip auth mfa enroll` then `verify`. **Print the new recovery
codes immediately** and put them in the Recovery Packet.

### Step 5 (optional). Erase the maintainer's account

GDPR Art. 17 right-to-erasure path, if the maintainer's
unavailability is permanent and the household wants their data
gone:

```bash
tulip users <maintainer-user-id> --delete
```

See [`USER_RIGHTS.md`](USER_RIGHTS.md) for what this does and
doesn't reach (the audit-log retention policy preserves a
redacted footprint for compliance; the actor's PII is wiped
per #235).

---

## 4. Recovery — the host is destroyed

The drive is gone. Drive failure, lost laptop, ransomware,
whatever. You have the Recovery Packet and need to restore on
new hardware.

### Step 1. Stand up a fresh host

Any machine that can run Docker + Compose v2 + `git`. The
[QUICKSTART](QUICKSTART.md) §1-2 path is a 60-second install.

### Step 2. Restore the master key + JWT secret

```bash
# After cloning the source tree:
mkdir -p deploy/docker/secrets
echo '<master-key from Recovery Packet>' > deploy/docker/secrets/master-key
echo '<jwt-secret from Recovery Packet>' > deploy/docker/secrets/jwt-secret
chmod 0400 deploy/docker/secrets/*
```

The trailing newline matters less than getting the base64
content exactly right — `cat deploy/docker/secrets/master-key`
should match what was in the packet character-for-character.

### Step 3. Check out the same Tulip version the backup was
taken against

```bash
cd tulip-accounting
git checkout <commit-or-tag-from-Recovery-Packet>
```

If you don't know the exact version, try `main` first; the
restore CLI refuses on schema mismatch so you'll get a clear
error if the version is wrong, not silent corruption.

### Step 4. Boot empty

```bash
docker compose -f deploy/docker/compose.yml up --build --wait
```

This creates an empty DB. The restore in the next step replaces
it.

### Step 5. Restore from the backup tarball

```bash
docker compose -f deploy/docker/compose.yml exec -T api \
  tulip restore --from - < /path/to/backup.tar.gz
```

The restore CLI validates the master-key envelope before
overwriting; if the key doesn't match the one the backup was
taken under, restore refuses without touching the live DB.

### Step 6. Verify

```bash
docker compose -f deploy/docker/compose.yml exec -T api \
  tulip doctor
```

Expect: API reachable, master key loaded from file, migration
head matches, attachment root writable, token store reachable.

Run one read query as a smoke test:

```bash
TULIP_API_URL=http://127.0.0.1:8000 tulip auth login \
  --email <your-email> --password-stdin <<<'<your-password>'
# (will prompt for MFA; use a recovery code if the new device
# doesn't have your TOTP secret yet)
tulip balance
```

The trial balance should match what you remember from before
the disaster, give or take whatever transactions weren't
committed yet at backup time.

### Step 7. Re-enrol MFA on the new device

Recovery codes get you in once; re-enrol immediately so future
logins don't keep consuming codes:

```bash
tulip auth mfa enroll
tulip auth mfa verify --code <from-authenticator-app>
# Print the new recovery codes and update the Recovery Packet.
```

---

## 5. What can't be recovered

Be honest with yourself about the limits.

- **The master key is the single point of failure.** If you
  lose the key file and every paper copy is gone too, the
  backups are cryptographic noise. There is no recovery
  procedure that doesn't start with the master key. This is
  intentional — losing it is supposed to mean the data is
  unrecoverable to an attacker too.

- **Backups can't restore data that wasn't in them.** If the
  most recent backup is from last Sunday and the disaster is
  Friday, you lose five days. The §6 cadence section addresses
  this.

- **Old PII residue in old backups.** GDPR right-to-erasure
  removes data from the live DB, but every backup taken before
  the erasure still contains the data ([`USER_RIGHTS.md` §Erasure](USER_RIGHTS.md)
  documents the residue lifecycle). After a restore, re-run any
  outstanding erasure requests against the restored DB.

- **External AI provider invocations.** If you enabled cloud AI
  (Anthropic / OpenAI) and your provider keys were active, the
  external provider has a copy of every prompt + response you
  ever sent. Restoring from a backup doesn't reach into their
  systems. Rotate your provider keys after any host
  compromise; see [`SECURITY_OPS.md`](SECURITY_OPS.md) (#428)
  when it lands.

---

## 6. The dry run — do this annually

A documented procedure that's never been tested is a wish list.
At least once a year, run the §4 restore against a scratch
directory on a different machine.

### Suggested cadence

- **Monthly:** verify a backup landed (size > 0, recent
  timestamp). One-liner: `ls -la ~/tulip-backups | tail -3`.
- **Quarterly:** spot-check that the most recent backup
  manifests cleanly: `tulip backup-inspect ~/tulip-backups/<latest>.tar.gz`.
- **Annually:** full restore dry-run on a scratch host. The
  whole §4 procedure end-to-end, against a temp directory, with
  the actual Recovery Packet you'd hand the successor. If any
  step doesn't work, fix it now while you can.

### Why annually

- New software releases change the backup format. Verify the
  restore still works against the current `main`.
- Recovery code consumption: every login-via-recovery uses one.
  Track how many remain; regenerate via
  `tulip auth mfa recovery-codes regenerate` and update the
  Recovery Packet.
- Successor changes. Marriage, divorce, falling out with the
  named person — re-confirm the successor is still the right
  one.
- Storage media degradation. The USB drive in the safe might
  have unreadable sectors three years from now. Verify the
  packet is still readable.

### Validate the success criterion

After a dry-run, the successor (not you) should be able to:

1. Open the Recovery Packet.
2. Stand up a fresh host using §4.
3. See the trial balance match what was in the live system
   when the backup was taken.

Without help from you. If they can't, the gap is the documentation
— update this doc now.

---

## 7. Cross-references

- [`docs/QUICKSTART.md`](QUICKSTART.md) §10 — manual key
  rotation procedure (rotates without an unavailability event).
- [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) §7.4-7.5 — how
  encryption-at-rest + backups actually work under the hood.
- [`docs/THREAT_MODEL.md`](THREAT_MODEL.md) — the threat model
  Tulip's encryption is calibrated against.
- [`docs/USER_RIGHTS.md`](USER_RIGHTS.md) — operator surface for
  GDPR / CCPA data-subject rights.
- [`docs/SECURITY_OPS.md`](SECURITY_OPS.md) (#428) — operator
  security ops + incident response. RECOVERY focuses on
  successor-driven continuity; SECURITY_OPS focuses on
  steady-state security posture.
- [`docs/DEPLOYMENT.md`](DEPLOYMENT.md) (#427) — production
  deployment + ongoing ops. RECOVERY focuses on rebuilding from
  zero; DEPLOYMENT focuses on running the live system.
- [`SECURITY.md`](../SECURITY.md) — vulnerability reporting
  policy (distinct from operator-facing security ops).

---

## 8. Checklist

A printable cut of this doc for the Recovery Packet front
page:

```
RECOVERY CHECKLIST                                  Print and store
                                                    with the packet
[ ] Master key file contents
[ ] JWT secret file contents
[ ] MFA recovery codes (16 chars each, XXXX-XXXX-XXXX-XXXX)
[ ] Latest encrypted backup tarball (or a known location for it)
[ ] Tulip source tree git commit/tag
[ ] This document

Successor:        _______________________________________________
Successor email:  _______________________________________________

Recovery packet
storage location: _______________________________________________

Verified working: _____ / _____ / _____   by _______________

If the maintainer is unavailable AND the host is running:
  → Read docs/RECOVERY.md §3.

If the host is destroyed:
  → Read docs/RECOVERY.md §4.

Both procedures take less than an hour.
```
