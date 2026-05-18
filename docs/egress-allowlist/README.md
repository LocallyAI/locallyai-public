# Egress allowlist — what LocallyAI is allowed to talk to

Network-layer enforcement of the data-isolation guarantee documented
in [docs/sop/data-isolation.md](../sop/data-isolation.md). The lists
in this folder let IT block every other outbound connection from the
office Mac, so even a compromised dependency or a developer mistake
can't accidentally exfiltrate firm data.

Two enforcement levels (use either or both):

1. **Audit-only** — `scripts/audit_egress.sh` reports what's actually
   connecting. Run weekly; investigate anything outside the list.
2. **Active block** — install LuLu (free, Objective-See) and import
   the rules in `lulu-rules.json`. LuLu prompts for any new outbound
   connection and lets you allow/deny per-process.

## The allowlist

| Host | Why | Used by |
|---|---|---|
| `github.com` | Source repo + signed releases | git, gh CLI |
| `api.github.com` | Release metadata + Contents API | gh CLI |
| `raw.githubusercontent.com` | Public file fetch (release manifest, signing key) | system_updates.py |
| `objects.githubusercontent.com` | Release artefact downloads | gh release download |
| `huggingface.co` | LLM + embedder model downloads | huggingface_hub |
| `cdn-lfs.huggingface.co` | Large model files (LFS) | huggingface_hub |
| `*.workers.dev` (your specific Worker URL) | Kill-switch poll | kill_switch.py |
| `localhost`, `127.0.0.1`, `::1` | Loopback | API ↔ Ollama (if used), API ↔ Qdrant |
| **LAN subnet** (e.g. 192.168.1.0/24) | Staff laptops reaching the office Mac | Browser → API |

Things that should NEVER appear in outbound:
- Telemetry / analytics endpoints (we don't ship any)
- Model APIs other than HuggingFace (no OpenAI, Anthropic, etc. — on-prem only)
- Cloud document stores (no S3, GCS, Dropbox, etc.)
- Any IP that's not in the table above + your LAN

## Audit script

```bash
bash scripts/audit_egress.sh
```

Lists every TCP connection LocallyAI processes currently have open,
flags anything outside the allowlist in red, and prints a summary
the operator can paste into a compliance ticket.

Run it weekly. If you see anything unexpected, kill the process and
investigate before the audit-log review.

## Enforcement via LuLu (free, Objective-She)

LuLu is a free open-source application firewall by Patrick Wardle.
Best fit for our needs: per-process outbound filtering with a
human-readable rule format.

### Install

```bash
brew install --cask lulu
# OR download from https://objective-see.org/products/lulu.html
```

### Import the LocallyAI rules

LuLu's UI: Preferences → Rules → Import → pick `lulu-rules.json`.

The rules pre-allow:
- Python (the venv binary specifically) → all hosts in the allowlist
- node (vite + Tauri runtime) → all hosts in the allowlist
- gh CLI → github.com + api.github.com
- huggingface_hub helper → huggingface.co + cdn-lfs.huggingface.co
- curl + git → all hosts in the allowlist (used by sentinel + scripts)

Anything else from these processes prompts the operator. Anything
from a process that's NOT in our allowlist is blocked silently.

### Verify rules are active

```bash
sudo defaults read /Library/Objective-See/LuLu/rules.plist | head -50
```

## Enforcement via macOS pf (built-in, no install)

`pf` is the kernel-level packet filter that ships with macOS. It can
filter outbound by destination but **not by process** — so this is
all-or-nothing rather than per-process. If you go this route, you're
opting into "this whole Mac is dedicated to LocallyAI" mode.

```bash
# Generate a pf.conf snippet for the current allowlist
bash docs/egress-allowlist/generate_pf_conf.sh > /etc/pf.anchors/locallyai
sudo cp docs/egress-allowlist/pf.conf.template /etc/pf.conf.locallyai
sudo pfctl -f /etc/pf.conf.locallyai -e
```

(Recommended only if the office Mac is single-purpose. For mixed-use
machines, LuLu is the right tool.)

## When the allowlist needs to change

If a future LocallyAI release adds a new outbound destination (e.g.
a new model registry), it'll be called out in the release manifest's
`changelog_summary` field. Update both:
- This file (the allowlist table above)
- `lulu-rules.json` (add the new host)
- The pf rules if you use them
