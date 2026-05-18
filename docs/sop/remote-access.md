# Remote staff access

> **Audience:** firm IT + LocallyAI vendor (during onboarding advisory).
>
> **Scope:** how lawyers and staff reach the office Mac's worker UI
> from outside the firm's office LAN — home, multi-office, on the road.
> Vendor never appears on the access path; this is firm-managed
> network plumbing.

---

## The default: office-LAN only

LocallyAI ships configured for staff laptops to reach the office Mac
only when both are on the firm's office network — Wi-Fi or wired LAN.
The launcher binds the API to `0.0.0.0` in fleet mode and the Tauri
Worker app resolves the office Mac via `office-mac.local` (mDNS) or by
LAN IP.

**For many firms this is the right answer**: partners work in the
office, sensitive work stays on-prem in the literal sense, no extra
infrastructure to maintain. If this fits the firm's working patterns,
stop reading and don't add remote access — every additional path is
attack surface.

---

## When remote access becomes necessary

- **Hybrid working** is the default at most UK firms now
- **Multi-office firms** with a single office Mac (the others need to reach it)
- **Travelling fee-earners** (court appearances, client visits, conferences)
- **Out-of-hours work** from home

If any of these apply to fee-earners (not just admin staff), the firm
needs an answer.

---

## The four real options

### Option A — On-LAN only (the default)

- **Setup**: nothing
- **Who manages**: nobody — it's the absence of a remote path
- **Cost**: £0
- **Attack surface**: only the firm's office LAN; vendor's egress
  allowlist already covers the outbound side
- **Auditability**: all access already in `logs/audit.log` from inside
  the office network
- **Pick when**: partners are office-first; the firm has no acute
  pressure for remote access; the firm's regulator views off-LAN
  access poorly

### Option B — Tailscale (recommended where remote access is needed)

A WireGuard-based overlay network with a hosted control plane.
Tailscale Free covers up to 100 devices per network — easily enough
for a small/medium law firm. Each device gets a stable hostname like
`office-mac.tailfirm.ts.net`.

- **Setup** (firm IT): 30–60 min one-time
  1. Create a Tailscale account at `tailscale.com` (firm-controlled — use a shared mailbox)
  2. Install Tailscale on the office Mac, log in with the firm's
     Tailscale account. `tailscale up --hostname office-mac`.
  3. Install Tailscale on each staff laptop (Mac/Win/iOS/Android).
     Authenticate with the same Tailscale account.
  4. Configure Tailscale ACLs (free-tier feature) to allow staff
     laptops to reach port 8000 on the office Mac — and **nothing else**.
  5. Optionally enable MagicDNS so the office Mac is reachable at
     `office-mac` (no `.local` / no IP).
  6. In the Tauri Worker app on each staff laptop, set the office Mac
     URL to `https://office-mac.tailfirm.ts.net:8000` (or whatever
     Tailscale assigned).
- **Who manages**: firm IT. **Vendor is not on the tailnet.** This
  is non-negotiable — vendor's presence would re-create the
  no-vendor-data-access break that the rest of this SOP prevents.
- **Cost**: £0 on Tailscale Free for ≤100 devices, ≤3 admin users
- **Attack surface**: Tailscale's control plane (well-audited, used by
  Cloudflare, Mozilla, etc.); WireGuard cryptographic envelope
  end-to-end; ACL gates per-port access
- **Auditability**: Tailscale's "Network log" feature shows every
  connection (free tier includes 30 days)
- **Pick when**: firm wants the simplest off-LAN solution and doesn't
  already run a corporate VPN

#### Minimum ACL for LocallyAI

In Tailscale Admin → Access Controls, replace the default ACL with:

```jsonc
{
  "tagOwners": {
    "tag:locallyai-office": ["autogroup:admin"]
  },
  "acls": [
    // Staff laptops can reach the LocallyAI office Mac on port 8000 only.
    {
      "action": "accept",
      "src":    ["autogroup:member"],
      "dst":    ["tag:locallyai-office:8000"]
    }
    // No other internal traffic permitted between staff devices.
  ]
}
```

Then in the office Mac's Tailscale CLI:

```sh
sudo tailscale set --advertise-tags=tag:locallyai-office
```

This pinns the policy so a compromised staff laptop can't reach
arbitrary services on the office Mac (e.g., SSH, Time Machine ports).

---

### Option C — Firm's existing corporate VPN

If the firm already runs a VPN for other reasons (Microsoft 365,
file shares, legacy apps), reuse it. Configure staff laptops to reach
the office Mac's LAN IP over VPN.

- **Setup**: zero new infrastructure if VPN already exists
- **Who manages**: firm IT (existing VPN team)
- **Cost**: typically already in IT budget
- **Attack surface**: existing VPN's threat model; adding LocallyAI as
  one more service on an existing protected segment is low-marginal-risk
- **Auditability**: VPN logs + LocallyAI's `audit.log`
- **Pick when**: firm already has a VPN; IT prefers existing tooling

**Watch out for**: many corporate VPNs route all traffic through HQ
(full-tunnel), which means LocallyAI queries traverse the firm's
corporate gateway. Latency is usually fine (LLM inference is many
seconds; an extra 50ms VPN hop is invisible), but verify with a real
query before declaring it done.

---

### Option D — Cloudflare Tunnel (firm's own CF account)

Cloudflare Tunnel + Cloudflare Access provides authenticated public
URLs for internal services without inbound firewall holes. Office Mac
opens an outbound tunnel to Cloudflare; staff laptops reach
`locallyai.firm.com` after SSO authentication.

- **Setup** (firm IT): 1–2 hours
  1. Firm creates own Cloudflare account (NOT the vendor's — preserves
     isolation between LocallyAI's monitor Worker and the firm's tunnel)
  2. Install `cloudflared` on the office Mac
  3. Create a tunnel pointing at `https://localhost:8000`
  4. Configure CF Access policy: only the firm's identity provider
     emails (Google Workspace / Microsoft / Okta) can authenticate
  5. Staff hit the public URL, authenticate via the firm's IdP, get
     access
- **Who manages**: firm IT
- **Cost**: £0 (CF Tunnel is free; CF Access is free for up to 50
  users)
- **Attack surface**: Cloudflare control plane; well-audited; firm's
  IdP is on the critical path
- **Auditability**: CF Access logs every authenticated session
- **Pick when**: firm already uses Cloudflare for DNS / DDOS / pages
  and prefers a public-URL UX

**Watch out for**: this is the most complex option. Don't pick it just
because it's free — Tailscale is also free and is 10× simpler.
Cloudflare Tunnel makes sense when the firm has an SSO provider they
want to gate access through.

---

## Comparison table

| | A: On-LAN only | B: Tailscale | C: Firm VPN | D: CF Tunnel |
|---|---|---|---|---|
| Setup time | 0 min | 30–60 min | 0 (if VPN exists) | 1–2 hr |
| New vendors firm depends on | none | Tailscale | none | Cloudflare |
| Cost | £0 | £0 (≤100 devs) | already paid | £0 (≤50 users) |
| Off-office access | ❌ | ✅ | ✅ | ✅ |
| Per-device auth | n/a | Tailscale account | VPN credential | firm IdP SSO |
| Audit log | local only | + Tailscale net log | + VPN log | + CF Access log |
| Vendor on the path? | no | **no** | no | no (firm's CF account) |
| Recommended for | office-first | most firms | firms with existing VPN | firms wanting SSO |

---

## Vendor's role

The vendor:

- **Advises** during onboarding (Phase 0 discovery: "how do your fee-earners
  work — office-only, hybrid, road warriors?") and Phase 7 handover
  ("here are the four options; let's pick before you grant external access").
- **Records the firm's choice** in `vendor-records/firms/<slug>.md`
  under a new section, so future support calls don't need to re-ask.
  The intake form's *Remote-staff access plan* field captures this.
- **Does NOT** join the firm's tailnet / VPN / CF Access policy.
  The vendor's only path to the firm Mac is the anonymised heartbeat
  going OUT to the monitor Worker. There is no inbound path, by design.
- **Refuses** firm requests to "set up Tailscale for us so you can
  help debug remotely". The vendor cites this SOP chapter and offers
  screen-share (Zoom/Teams, firm-initiated, per-incident) as the
  sanctioned alternative.

---

## What to tell firms (one paragraph for the onboarding call)

> By default, your LocallyAI office Mac is reachable only from the firm's
> office network — quotes a typical "all on-prem" posture. If your
> fee-earners work from home or on the road, you have three real ways
> to extend access without compromising the per-firm isolation we're
> committing to in the DPA. Easiest is Tailscale (free, ~30 min setup,
> ~100 device cap). If you already run a corporate VPN, just point it
> at the office Mac. If you use Cloudflare for SSO already, Cloudflare
> Tunnel + Access works too. In all three cases, LocallyAI as a vendor
> is **not** on the access path — you (firm IT) configure and manage
> it, and we never get tunnel credentials. Pick what fits; we'll record
> the choice in your firm profile.

---

## Decommission

When a firm winds down (per [decommission.md](decommission.md)):

- **Tailscale**: firm revokes the office Mac's auth key and removes
  staff laptops from the tailnet
- **Firm VPN**: firm IT removes the LocallyAI host from VPN routing
- **CF Tunnel**: firm IT deletes the tunnel + CF Access policy

LocallyAI's `decommission.md` already covers the office Mac side
(wiping audit log, removing the firm from the monitor Worker
`FIRM_TOKENS`, etc.) — no vendor-side change needed for remote-access
decommission because the vendor was never on the access path.
