# Operator runbooks

**These runbooks exist because the SOP chapters are reference material, not action material.** When something is wrong or you need to do a specific operation, open the runbook for that operation, follow it top-to-bottom, and verify each step before moving to the next.

## When to use a runbook vs the SOP

| You're doing | Use |
|---|---|
| Fixing a broken thing | Runbook |
| A scheduled operation (monthly snapshot, model swap) | Runbook |
| Onboarding a firm | Runbook |
| Decommissioning a firm | Runbook |
| Understanding **why** something works the way it does | SOP chapter (`docs/sop/`) |
| Reading the regulatory mapping | SOP `compliance.md` / `compliance-saudi.md` |
| Understanding the architecture | SOP `data-isolation.md` |

If a runbook doesn't exist for what you're doing, **stop and call the founder**. Inventing the procedure on the spot is how data gets lost.

## Available runbooks

| File | When to use |
|---|---|
| [`dpo-monthly-snapshot.md`](dpo-monthly-snapshot.md) | DPO needs to file the monthly compliance evidence |
| [`api-down.md`](api-down.md) | `/healthz` is failing or the firm reports the app is dead |
| [`add-new-firm.md`](add-new-firm.md) | A new firm has signed and you need to install |
| [`remove-firm.md`](remove-firm.md) | A firm is leaving and you need to decommission |
| [`audit-chain-broken.md`](audit-chain-broken.md) | `/admin/audit-verify` returns TAMPERED |
| [`dashboard-locked-out.md`](dashboard-locked-out.md) | The vendor monitor dashboard is rejecting your TOTP |
| [`conflict-check.md`](conflict-check.md) | Run a conflict-of-interest check before opening a new matter |

## How a runbook is structured

Every runbook in this folder follows the same shape so you can navigate them without re-reading the conventions each time:

- **When** — the symptom or trigger that brought you here
- **Time budget** — if you've been here longer than this, escalate
- **Risk if you stop midway** — partial state matters; this tells you when to commit vs back out
- **Prerequisites** — credentials, access, tools you must have before starting
- **Decision tree** — the first thing you read; tells you which procedure applies
- **Procedure** — exact commands, with the **expected output** for each one
- **Things that go wrong** — error messages → what they actually mean
- **When to escalate** — explicit triggers, not "when you're stuck"

If a runbook doesn't fit this shape, that's a bug. File it.

## Escalation chain

| Severity | Contact | Within |
|---|---|---|
| Service down + firm calling | Founder by phone | 15 min |
| Audit chain TAMPERED | Founder by phone | 1 hour |
| Suspected data leak | Founder + DPO of affected firm | Immediately |
| Routine "I'm not sure" | Founder by Slack | Same business day |
| Roadmap question | Founder when convenient | — |

The founder phone number is in `vendor-records/operator-contacts.md` (private repo, not this one).

## Confidentiality

These runbooks are **internal vendor documentation**. They reference the structure of every firm's deployment but never the contents of any firm's data. Do not share them with firms. Firms see the SOP set; they do not see this folder.
