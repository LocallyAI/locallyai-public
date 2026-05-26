# Security policy

## Reporting a vulnerability

If you believe you have found a security vulnerability in LocallyAI, please
report it privately rather than opening a public GitHub issue:

- **Preferred:** email `security@locallyai.app` with the subject `SECURITY: <short description>`
- **PGP-encrypted:** import the [release-signing key](docs/release-signing-key.gpg)
  (also used to sign vendor releases) and encrypt the body
- **Fallback:** GitHub Security Advisories (private) on this repository

Please include:

1. A description of the vulnerability and the impact you assessed
2. Steps to reproduce against a clean install (`bash install.sh` with `DEPLOY_MODE=demo`)
3. The commit SHA / release tag you tested against
4. Whether you'd like public attribution after the fix lands

## What we commit to

- Acknowledge receipt within **72 hours** (often much faster, but solo-team
  caveat applies — see `SUPPORT.md`)
- Provide an initial assessment + remediation timeline within **7 days**
- Coordinate the disclosure window with you. Default: 90 days from
  acknowledgement; we'll move faster on high-severity issues
- Credit you in the release notes + `docs/security/credits.md` unless you
  ask us not to
- File a CVE for any issue affecting deployed installs that warrants one

## Scope

In-scope:

- The LocallyAI core API (`api/`, `mcp_servers/`, `watchdog/`)
- The Manager UI and Worker UI (`apps/manager-ui/`, `apps/worker-ui/`)
- The installer (`install.sh`) + ancillary scripts (`scripts/`)
- The vendor-side kill-switch and monitor Cloudflare Workers
  (`docs/kill-switch/cloudflare-worker/`, `docs/monitor/cloudflare-worker/`)
- The release-signing chain (GPG-signed annotated tags + manifest SHA-256s)
- Adapted plugins shipped from `LocallyAI/locallyai-plugins-uk-public`

Out of scope (please don't test these without authorisation):

- Anthropic upstream (`anthropics/claude-for-legal`) — report to Anthropic
- Customer deployments — only test against your own install
- Denial-of-service against the dogfood demo machine
- Social-engineering or physical-access attacks against the maintainer

## Safe harbour

We will not pursue legal action against good-faith security research
that:

- Avoids privacy violations of LocallyAI users (don't probe customer
  installs without their consent)
- Avoids destruction of data
- Reports the finding to us before disclosing it publicly

If you're unsure whether a piece of testing falls within safe harbour,
email and ask first.

## What's hardened, what's not

The repo's [`SECURITY-NOTES.md`](docs/SECURITY-NOTES.md) (planned for
Month 1) will track the threat model + the hardenings in place. Known
gaps that we publish openly:

- Secrets live in `.env` on the same disk as the audit log
  (mitigation: macOS FileVault); macOS Keychain integration is planned
  for Month 1 — see `docs/sop/disk-encryption.md`
- Per-user authentication is bearer-key today; OIDC SSO is planned for
  Month 1 — see `auth.py` for the current contract
- The vendor kill-switch is single-key-signed today; 2-of-3 GPG signing
  is planned for Month 1 — see `kill_switch.py` docstring

## Vendor-side security

Vendor-controlled infrastructure (the kill-switch Worker, the monitor
Worker, the release-signing keys) is documented in
`docs/vendor-sop/`. Sub-processors are enumerated in
`docs/vendor-sop/vendor-sub-processors.md` and on every install's
`/admin/compliance/snapshot?format=html` page.
