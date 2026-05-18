# Customer success cadence

> Proactive engagement with firms, beyond just incident response. The
> goal is to catch issues before firms have to call us, and to surface
> renewal / expansion signals early.

---

## Cadence by firm age

| Firm age | Cadence | Format | Owner |
|---|---|---|---|
| Day 0–7 | Daily dashboard watch + 1 mid-week phone check-in | Phone (5 min) | On-call engineer |
| Day 8–30 | Weekly check-in | Email or phone | On-call engineer |
| Month 2–6 | Monthly check-in | Email | Customer success role |
| Month 7+ | Quarterly health review | Video call (30 min) | Customer success role |
| Month 11 | Annual renewal conversation | Video call (45 min) | Founder + customer success |

For single-person team: founder fills all roles.

---

## Day 0–7 hyper-watch

The first week after install is when most issues surface. Vendor
expectation:

- Dashboard checked twice daily (start-of-shift + mid-afternoon)
- Any non-green dot escalated to the on-call engineer regardless of
  formal alert tier
- Mid-week (Wed) phone check-in to firm IT primary: 5 min, three
  questions:
  1. "Is anything not working as you expected?"
  2. "Have your users had any feedback?"
  3. "Is the install audit (`bash scripts/audit_install.sh`) still
     passing?"

If the answer to (1) or (2) is yes: add the issue to the firm's
record in vendor-records and either resolve immediately or schedule.

If the answer to (3) is "I haven't run it" — coach them through
running it. The audit script is the firm-side health check we want
them comfortable running themselves.

---

## Day 8–30 weekly check-in

One email per week to firm IT primary + cc DPO. Template:

> **Subject:** LocallyAI — week N check-in
>
> Hi [name],
>
> Quick check-in for week N of your LocallyAI deployment:
>
> **From our monitoring:**
> - [N] heartbeats received this week, all green
> - [N] queries served (anonymised; we see counts only)
> - Latest applied release: [version]
>
> **Anything we should be aware of?**
> - Any feedback from users or partners?
> - Any concerns from the DPO?
> - Any planned office moves, IT changes, hardware refreshes?
>
> Reply when you have a moment — no urgency.
>
> Thanks.

The point of these emails is **to give the firm a comfortable channel
to surface issues before they escalate**. Many firms don't proactively
report; the weekly nudge converts unspoken friction into something we
can fix.

---

## Monthly check-in (months 2–6)

Email-only. Brief:

> **Subject:** LocallyAI — month N
>
> Hi [name],
>
> Month N of your deployment is complete. Quick summary from our side:
>
> - Uptime: [N]% / SLA met / N incidents
> - Releases applied: [list]
> - Self-heals fired: [N] (no impact to users)
>
> Anything you'd like to discuss? Happy to schedule a call if useful;
> otherwise, see you in month N+1.

---

## Quarterly health review (month 7+)

30-min video call. Agenda:

1. **Operational review (10 min)**: dashboard heatmap of last quarter,
   alert summary, self-heal stats, any incidents, releases applied.

2. **Compliance review (10 min)**: audit-export cadence (are they
   running them?), retention pruning fired (yes/no/error), any
   subject-access requests handled? Run the install audit + breach
   detector live on the call to demonstrate.

3. **Strategic discussion (10 min)**: any new initiatives at the firm
   that need our input? Office moves, additional offices, language
   coverage (KSA expansion, EU expansion), hardware refresh planning.

Send the dashboard screenshots + summary to the firm afterwards as a
written record.

---

## Annual renewal

Month 11 video call (a month before the contract anniversary):

1. **Performance recap**: dashboard + incidents over the full year.
2. **Renewal pricing**: confirm next-year fee. If we're proposing a
   change, justify it (more firms = better support coverage / new
   features released).
3. **DPA refresh**: we typically don't re-sign the DPA annually unless
   regulations changed materially. Confirm any changes.
4. **Onboarding intake re-run**: send the intake URL with subject
   "annual confirmation" so we can refresh contacts, hardware, etc.
   See [onboarding.md phase 8](../sop/onboarding.md#phase-8--steady-state--annual-review).
5. **Expansion discussion**: more users? more offices? new use cases?

Aim to have the renewal signed before the contract anniversary so
there's no gap.

---

## Health metrics per firm

Beyond dashboard health, watch these per-firm signals:

| Signal | What it means | When to act |
|---|---|---|
| Query volume drops sharply | Users may be drifting away | Reach out — ask about user experience |
| Increase in failed-auth events (audit log) | Users forgetting keys / sharing keys | Coach IT on key hygiene |
| sources_retrieved=0 rate creeps up | Corpus may be incomplete / queries shifting | Suggest a retrieval audit |
| Self-heal frequency creeps up | Hardware degrading / disk filling / ollama instability | Schedule a hardware health review |
| Update lag (firm pinned to old version) | IT may have lost confidence in updates | Personal conversation about the cause |
| Slow response to emails | Maybe a contact left and no one updated us | Ask about contact list refresh |

These are not dashboard alerts — they require human-judgement watching.
Customer success role does this monthly per firm.

---

## Renewal / expansion signals

Positive:

- Firm asks about adding more users (likely partner buy-in growing)
- Firm asks about adding a second office
- Firm asks about Arabic / other-language support (jurisdiction expansion)
- Firm DPO asks for a sub-processor review (preparing for their own
  audit — engagement is strong)

Negative (churn risk):

- Sustained drop in query volume + slow response to outreach
- Repeated tier-B updates declined / postponed
- New IT director who hasn't been briefed
- Cost pressure mentioned in passing
- Reference to "evaluating alternatives"

When a negative signal appears: founder-level conversation within 14
days. Address head-on, don't wait for them to issue notice.

---

## Customer success log

Per firm, append to `vendor-records/firms/<slug>-cs-log.md`:

```markdown
## 2026-06-12 — month-2 check-in

Email sent. Reply same day:
- Users happy. One ask: can the worker-ui composer support paste-from-Word?
  → Filed as feature request RA-23. Quoted "next minor release" (~1 month).
- DPO planning quarterly audit; asked for SOP PDF refresh — sent latest dist/.
- No operational issues.

## 2026-09-05 — quarterly health review (call)

Attended: Jane Smith (IT lead), David Chen (DPO), Emanuel
Highlights:
- 99.7% uptime over Q3, no incidents
- 4 releases applied, all clean
- Considering adding 5 more users in Q4 — contracted Annex A allows up to 30
- DPO asked about right-to-portability tooling — promised research, will follow up
Action items:
- [ ] Emanuel to research portability export by 2026-09-20
- [ ] Jane to send hardware-refresh budget request internally
```

This log is the institutional memory across the relationship; it
survives staff turnover on either side.
