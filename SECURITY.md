# Security policy

Tulip Accounting handles personal financial data. Security is taken seriously, even though the project is solo and hobby-paced.

## Reporting a vulnerability

**Preferred:** open a private security advisory at <https://github.com/rmwarriner/tulip-accounting/security/advisories/new>. This lets us discuss the issue privately until a fix lands.

**Fallback** (if GitHub is unavailable): email <rmwarriner@icloud.com> with a description and reproduction steps.

Please do **not** open a regular issue or pull request for security-relevant findings.

## Response timeline

This is a hobby project, not a product with an SLA. Reasonable best-effort:

- Initial response within 7 days.
- A status update at most every 14 days while the issue is open.
- Critical findings (e.g. credential exposure, remote code execution, data leakage across households) get prioritized over feature work.

## Supported versions

The project is pre-1.0 (`pyproject.toml` ships `0.0.0`). All security work targets `main`. There are no supported release branches yet — this section will be revisited when 1.0 ships.

## Scope

**In scope:**

- Vulnerabilities in any of the seven `tulip-*` workspace packages.
- Vulnerabilities in default deployment configurations (FastAPI app, CLI client, encrypted backups).
- Issues in test fixtures or scaffolding that could ship with a release artifact.

**Out of scope:**

- Vulnerabilities in third-party dependencies — file those upstream. Our `dependency-audit` CI job (#76) catches known CVEs in the lockfile.
- Issues that require physical access to the user's machine or root-level OS access.
- Theoretical attacks against the documented threat model — those belong in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) discussion, not the security advisory channel.

## Recognition

If you'd like to be credited in the eventual fix's release notes, mention this in the advisory. Otherwise reports are kept anonymous by default.
