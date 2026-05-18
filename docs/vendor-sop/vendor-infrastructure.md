# Vendor infrastructure inventory

> Every account, every secret, every key — where it lives, who owns it,
> where it's backed up, when it rotates. **Without this inventory,
> succession is impossible.** Update the same day you change anything.

This chapter is the vendor's own asset register. The corresponding
register on the firm side is the per-firm `firm-profile.md` in
vendor-records — that one tells you what hardware the firm runs;
this one tells you what *we* run.

---

## Account inventory

### GitHub

| Account | Purpose | Owner | 2FA |
|---|---|---|---|
| `LocallyAI` (org) | Code repo, releases, deploy keys to firms | Emanuel | Required org-wide; TOTP via authenticator |
| `LocallyAI/locallyai` | Main code repo, **private** | Emanuel | inherits org 2FA |
| `LocallyAI/vendor-records` | Firm profiles + DPAs + issued-token log, **private** | Emanuel | inherits org 2FA |
| `TheApolloTheory` | Legacy account (pre-rebrand) | Emanuel | Active 2FA |

**Backup**: GitHub holds the canonical copy. Local clones on Emanuel's
Mac act as the secondary. **Add a third location** (off-site clone on a
separate machine/disk) once team > 1.

**Recovery**: GitHub recovery uses the account email + 2FA. The recovery
codes for the LocallyAI org owner account are in 1Password under
"GitHub LocallyAI — recovery codes". A printed copy is in the sealed
envelope (see [vendor-team.md §key-custody](vendor-team.md#key-custody-matrix)).

### Cloudflare

| Resource | Account | Purpose | Plan |
|---|---|---|---|
| CF account | `your-cf-account.workers.dev` (or your account name) | Worker hosting | Free tier |
| Worker `locallyai-killswitch` | `your-cf-account.workers.dev` | TOTP-gated kill switch | Free (~50 firms before paid plan needed) |
| Worker `locallyai-monitor` | `your-cf-account.workers.dev` | Vendor health dashboard | Free (Workers + KV + Assets all on free tier) |
| KV namespace `KILLSWITCH` | (kill-switch worker) | Status JSON | Free tier limits OK |
| KV namespace `FIRM_STATE` | (monitor worker) | 5-min heartbeats per firm | Free tier OK to ~50 firms |
| KV namespace `ALERTS` | (monitor worker) | Open + acked alerts | Free tier OK |
| DNS for `locallyai.app` | (registrar) → CF nameservers | Domain serving | Free tier |

**Recovery**: CF recovery uses account email + 2FA. Backup codes in
1Password under "Cloudflare — backup codes" + sealed envelope.

> **Account separation note**: the kill switch and the monitor live on
> the **same** Cloudflare account today (single-person team, free-tier
> headroom doesn't justify two accounts). When team > 1 or firms > 30,
> split: monitor stays on the operations CF account, kill switch moves
> to a CF account whose credentials are held by the founder + sealed
> envelope only. Reason: kill switch should be defensible against an
> on-call engineer going rogue.

### Resend (email)

- **Domain**: `notifications.locallyai.app` (or wherever the SPF + DKIM are set up)
- **API key**: in CF Worker secret (`RESEND_API_KEY` on the monitor Worker)
- **API key copy**: 1Password under "Resend — API key"
- **Free tier**: 3,000 emails/month — enough for ~10× the largest realistic
  vendor alert volume

### Domain registrar

- **Domain**: `locallyai.app`
- **Registrar**: (e.g., Namecheap / Porkbun / Cloudflare Registrar) — **document the actual one in 1Password**
- **2FA**: enabled
- **Auto-renew**: enabled (else: calendar reminder 60 days before expiry)
- **WHOIS privacy**: enabled

**Recovery**: backup codes in 1Password + sealed envelope.

### 1Password

- **Vault structure**:
  - `Founder vault` — everything sensitive (sealed envelope replicas,
    keys, GPG passphrase). Founder only.
  - `Vendor team vault` — credentials shared with all vendor team members
    (CF API tokens for routine deploys, Resend key, etc.).
  - `Per-firm vault` — secure-share only; never persisted (used to share
    telemetry tokens with firm IT).
- **2FA**: required
- **Emergency Kit**: printed + in sealed envelope (1Password's recovery
  flow specifically requires this)

### Apple Developer (future — not yet enrolled)

- **Status**: not yet enrolled. Required when we want to code-sign the
  Tauri client apps so end users don't need to right-click → Open.
- **Cost**: $99/year individual, $299/year organisation.
- **Enrolment**: founder name, requires D-U-N-S number for org.

### Hugging Face (sub-processor only)

We don't have a paid HF account or any private models hosted there. We
download public models. No HF account is required for normal operation;
the firm's office Mac downloads anonymously. Still listed in
[vendor-sub-processors.md](vendor-sub-processors.md) for DPA passthrough.

---

## Secret inventory

Sorted by sensitivity tier. Tier A = catastrophic if leaked. Tier B =
serious. Tier C = recoverable.

### Tier A — catastrophic if leaked

| Secret | Where stored | Backup | Rotation |
|---|---|---|---|
| GPG release-signing private key | macOS Keychain (pinentry-mac) | Encrypted USB in fireproof safe + sealed envelope (paper copy of revocation certificate) | **Never rotate**. Only revoke + reissue if leaked. Revocation procedure: [V5 §gpg-leak](vendor-incidents-own-infra.md#gpg-release-signing-key-leak) |
| GPG passphrase | Memorised + 1Password Founder vault | Sealed envelope | Per-incident; otherwise stable |
| Kill-switch TOTP secret (base32) | Phone authenticator + 1Password Founder vault | Sealed envelope | Per-incident; otherwise stable |
| Kill-switch recovery codes (10 codes) | 1Password Founder vault | Sealed envelope | When all 10 used, regenerate the pool |
| Domain registrar account password + 2FA | 1Password Founder vault | Sealed envelope | Annual |

### Tier B — serious

| Secret | Where stored | Backup | Rotation |
|---|---|---|---|
| Monitor TOTP secret (base32) | Phone authenticator + 1Password | Sealed envelope | Per-incident |
| Monitor recovery codes (10 codes) | 1Password | Sealed envelope | When pool used up |
| Cloudflare account password + 2FA | 1Password Founder vault | Sealed envelope | Annual |
| Cloudflare API tokens (per-Worker `wrangler` deploy tokens) | 1Password Vendor team vault | n/a (rotatable) | Annual; on engineer offboarding |
| GitHub LocallyAI org owner password + 2FA | 1Password Founder vault | Sealed envelope | Annual |
| Per-firm telemetry tokens | `~/.locallyai/vendor/firms-registry.json` (mode 0600) | Time Machine to encrypted disk + quarterly off-site backup | Per firm on suspected compromise; optionally annually |
| Vendor's own audit HMAC key (if we ever run our own audited service) | n/a today | n/a | n/a |

### Tier C — recoverable

| Secret | Where stored | Backup | Rotation |
|---|---|---|---|
| Resend API key | CF Worker secret + 1Password | n/a | Annual |
| 1Password account password | Memorised | Recovery via Emergency Kit | Per-incident |
| GitHub Personal Access Tokens (gh CLI keyring) | macOS Keychain | n/a | Per-machine; auto-created on `gh auth login` |
| `gh` CLI keyring (general) | macOS Keychain | n/a | n/a |

---

## Backup locations

| Location | Contents | Access | Audit cadence |
|---|---|---|---|
| Local Time Machine (encrypted external SSD) | Whole laptop incl. `~/.locallyai/`, GPG keychain, 1Password local cache | Founder | Weekly automated; integrity check monthly |
| Off-site Time Machine (rotated quarterly) | Same as above | Founder | Quarterly rotation |
| Sealed envelope #1 (off-site fireproof safe) | GPG passphrase, TOTP secrets, recovery codes for all accounts, domain registrar 2FA, 1P Emergency Kit | Founder + (when team > 1) succession holder | Annual review |
| Sealed envelope #2 (different off-site location) | Identical replica of envelope #1 | Founder | Annual review (regenerate if envelope #1 contents change) |
| 1Password Founder vault (cloud) | All secrets in digital form | Founder | Daily — automatic |
| GitHub (cloud) | Code + signed releases | Org members | Continuous |

**Off-site** means physically distinct from the founder's home **and**
the office (if/when an office exists). The acceptable distance is
"different building, ideally different postcode, accessible without
the founder being present (e.g., a parent's safe or a bank
safe-deposit box)".

---

## Rotation cadence

| Cadence | What rotates | Owner |
|---|---|---|
| **Per-incident only** | GPG passphrase, kill-switch TOTP secret, monitor TOTP secret, GPG private key (revoke + reissue) | Founder |
| **Per offboarding** | All shared TOTP secrets (kill-switch, monitor) — defensive, even if departed engineer didn't have access | Founder |
| **Annually** | CF API tokens, GH PATs, account passwords (CF, GH, registrar), Resend API key | On-call engineer |
| **Optional annual** | Per-firm telemetry tokens (run `scripts/onboard_firm.sh` on each profile to rotate) | On-call engineer |
| **Calendar event** | Domain auto-renewal, Apple Dev membership (when enrolled), CF DNS cert if not auto-managed | On-call engineer |
| **Quarterly off-site backup rotation** | Time Machine disk swap; sealed envelope contents review | Founder |

---

## Discovery commands

If you've inherited the vendor seat and are auditing what's where, run
these from the founder's daily-driver Mac (or recovered backup):

```sh
# What gh accounts are configured
gh auth status

# What CF Workers exist (needs wrangler authed)
cd docs/monitor/cloudflare-worker && npx wrangler deployments list
cd ../../kill-switch/cloudflare-worker && npx wrangler deployments list

# What's in the local firm registry
ls -la ~/.locallyai/vendor/
cat ~/.locallyai/vendor/firms-registry.json | python3 -m json.tool

# What GPG keys are imported
gpg --list-secret-keys --keyid-format LONG

# What's in vendor-records
ls ~/locallyai-vendor-records/firms/
tail -20 ~/locallyai-vendor-records/firms-issued.log
```

If any command above produces no output where there should be data,
that is itself a finding — escalate per [V5](vendor-incidents-own-infra.md).

---

## When the inventory is wrong

If you find this document out of date during an incident, **fix it in
the same commit as the incident response**. The CHANGELOG entry should
say "discovered X was actually at Y, not Z as documented" so future-you
knows the registry was unreliable on that date.

If you find this document out of date during a routine task, fix it as
a standalone commit with a one-line message: `docs: refresh inventory
— <thing> moved/added/removed`.
