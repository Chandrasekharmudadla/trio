# Intelligent IT Ticket Analysis and Resolution Assistant

HCLTech Future Ready AI Engineer Hackathon prototype.

An employee raises a ticket. The assistant classifies it, suggests whether it is the same problem as other open
tickets, finds similar resolved tickets and recommends a standard operating procedure (SOP) with the evidence behind
it. A service desk analyst reviews and decides. High-risk procedures need a change approver's sign-off. The assistant
never closes a ticket or changes a system on its own.

> All data is synthetic. `generate_data.py` wrote every ticket, label, timestamp and resolution.

## Quick start (macOS)

```bash
cd ~/Desktop/hcl_tech
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501. On first run it creates `data/tickets.db` from the CSV files and the demo accounts.

| Username | Role | Restricted tickets |
| --- | --- | --- |
| `priya`, `rahul` | Employee: raise tickets, see only their own, confirm or reopen | Own only |
| `analyst1` | Service desk analyst | No |
| `lead1` | Senior analyst | Yes |
| `cab1` | Change approver | No |

Password for every demo account: `demo123`.

Other commands:

```bash
python test_workflows.py  # 27 checks for access control, restricted data, duplicates and approvals
python evaluate.py        # re-run the evaluation; rewrites EVALUATION_REPORT.md and WORKED_EXAMPLES.md
python generate_data.py   # regenerate the synthetic data with fresh timestamps
rm data/tickets.db        # reset the database; it is rebuilt on the next app start
```

The seeded open tickets are dated relative to when `generate_data.py` ran. If more than two days have passed, run
`python generate_data.py` and delete `data/tickets.db` so the live-incident demo below still works.

## Demo script

1. **Sign in as `priya`.** Raise: title `VPN certificate error`, description
   `VPN client shows certificate validation failure and will not connect since the certificate rotation.`,
   application GlobalProtect VPN, category Wi-Fi (wrong on purpose), Just me, Work is blocked.
   She is told others reported something similar and an analyst will check. Her priority stays P3.
2. **Sign out, sign in as `analyst1`.** Open the ticket. The console flags the category mismatch and shows the
   suggestion: possibly the same problem as incident INC-0001. Click **Confirm: same problem**. The ticket joins the
   incident and its priority rises to P1 because six people are now affected.
3. **Resolve.** The SOP-001 recommendation shows its steps, source tickets and first-time fix rate. Under
   "Also resolve these linked tickets", remove two tickets, then resolve. Three tickets are resolved; the two removed
   ones are unlinked and stay open.
4. **Sign in as `priya`, My tickets.** Click **No, still not working**. The ticket reopens, leaves the incident and
   goes back to the queue. The Incidents tab now shows one reopen for INC-0001.
5. **Server outage with approval.** As `rahul`, raise `Jira is down` /
   `Jira shows 503 service unavailable for everyone and the health check is failing.`, application Jira, Whole
   organisation. As `analyst1`, the recommended SOP-024 is a controlled restart; **Resolve** stays disabled. Write
   the change request (plan, impact, rollback) and click **Request approval**. As `cab1`, approve it in Change
   requests. As `analyst1`, the ticket can now be resolved, and its history shows who approved what and when.
6. **Restricted data.** As `analyst1`, the queue says one restricted ticket is hidden. As `lead1`, it is visible.
7. **Insights tab.** Recurring problems, SLA compliance, SOPs that often fail first time, volume patterns.

## How duplicate tickets are handled

```
New ticket ──► Can it be grouped? ── no ──► handled on its own
                 │  (not Access/Hardware,      (password resets, laptops,
                 │   not restricted)            restricted tickets)
                 ▼ yes
         Open tickets in the last 48 h with the same subcategory,
         same site or same service, and text similarity >= 0.55?
                 │ no ──► handled on its own (analyst can still link manually)
                 ▼ yes
         SUGGESTION stored on the ticket. Nothing linked, priority unchanged.
                 │
         Analyst: Confirm ─────────────► linked to incident, linked_by recorded,
                 │                        incident priority recalculated
         Analyst: Not the same ────────► suggestion cleared, recorded as feedback
                 ▼
         Resolve: analyst chooses which linked tickets the fix covers.
         Unselected tickets are unlinked and stay open.
                 ▼
         Each person: "Yes, fixed" ──► Closed
                      "Still broken" ─► reopened, removed from the incident, back in the queue
                      no answer in 3 days ─► Closed automatically
```

- Incident priority rises with the number of **confirmed** tickets: 3, 6 and 15 tickets raise impact to team,
  department and organisation level.
- The Incidents tab lists pending suggestions with bulk confirm and reject, for large outages.
- `reopened_after_fix` per incident shows when a group fix did not work for everyone.
- Every step is written to `ticket_events` and shown as the ticket history.

## Access control and restricted data

- Sign-in with salted PBKDF2 password hashes (`users` table). Three roles: employee, analyst, change approver.
  Restricted clearance is a property of the account, not a switch in the UI.
- Every action in `engine.py` takes the signed-in account and checks role, ticket ownership and clearance, so a
  hidden button is never the only control.
- Employees see only tickets they raised.
- Restricted tickets are excluded, for analysts without clearance, from: the queue, search, similar tickets,
  recommendation evidence, incident lists, insights, the audit log view and its CSV export, and feedback.
- Restricted tickets are never grouped into incidents, so a group fix or an incident list cannot reveal them.
- An audit record whose evidence cited a restricted ticket is flagged and excluded from exports for analysts without
  clearance.
- The evaluation report and worked examples contain no restricted tickets.

## Change approval for high-risk procedures

SOPs marked `requires_approval` (controlled server restart, deployment rollback, access grants):

1. The analyst writes a change request: planned action, impact and rollback plan.
2. A change approver approves or rejects it. Rejection needs a reason. Nobody can decide on a request they made or a
   ticket they raised.
3. The ticket can be resolved with that SOP only under an approval less than 24 hours old. `resolve_ticket` refuses
   otherwise, whatever the UI shows.
4. The approval number is written into the ticket history and resolution.

## Architecture

```
Employee portal ──► ticket form (category, impact, urgency, sensitive flag)
                     │
                     ▼
              ┌─────────────── engine.py ───────────────────────────────┐
              │ Access control  role, ownership, restricted clearance    │
              │ Priority        impact x urgency -> P1..P4, SLA target   │
              │ Classifier      TF-IDF + logistic regression             │
              │ Duplicates      suggest -> analyst confirms -> incident  │
              │ Retrieval       cosine similarity + metadata filters     │
              │ Recommender     SOP from similar resolved tickets        │
              │ Confidence gate recommend, or route to human review      │
              │ Approvals       change request -> approver -> resolve    │
              └──────────────────────────────────────────────────────────┘
                     │                                   ▲
                     ▼                                   │
Analyst console / approver view ───────────────► SQLite: data/tickets.db
```

| File | Purpose |
| --- | --- |
| `app.py` | Streamlit interface: sign-in, employee portal, analyst console, approver view |
| `engine.py` | Storage, access control, analysis, duplicates, approvals, audit, MTTR |
| `generate_data.py` | Synthetic ticket and SOP generator |
| `evaluate.py` | Held-out evaluation; writes the report and worked examples |
| `test_workflows.py` | Checks for the access, duplicate and approval rules |
| `EVALUATION_REPORT.md`, `WORKED_EXAMPLES.md` | Results, observed errors, limitations, three worked examples |

## How data is stored and retrieved

One SQLite file, `data/tickets.db`. SQLite ships with Python and macOS, so there is no server to install.

| Table | Holds |
| --- | --- |
| `tickets` | Every ticket: text, category, priority, status, timestamps, restricted flag, incident link, duplicate suggestion |
| `incidents` | Confirmed groups of tickets with the same problem |
| `sops` | Approved procedures with ordered steps, risk level and approval flag |
| `users` | Accounts, roles, clearance, password hashes |
| `approvals` | Change requests and approver decisions |
| `ticket_events` | Every action on every ticket: who, when, what |
| `audit_log` | One record per analysis: input, classification, evidence, recommendation, reviewer decision |
| `feedback` | Useful, incorrect and unresolved marks from analysts |

Filters use SQL. Similarity uses a TF-IDF index built in memory from the `tickets` table and rebuilt when tickets
are added or resolved. To browse: `sqlite3 data/tickets.db` then `.tables`, or open the file in DB Browser for SQLite.

## What the synthetic data models

| Pattern in the data | How the assistant uses it |
| --- | --- |
| Distinct root causes per subcategory, each with its own SOP | The recommended SOP depends on the ticket's wording, not only its category |
| Incidents: several users report one fault within an hour | Duplicate suggestions, confirmed by an analyst |
| Recurring faults on one service and site every 6 to 12 days | Insights tab and a warning on the ticket |
| Failed first fixes and reopened tickets | First-time fix rate shown with each recommendation |
| Priority-based handling times | SLA due times, breach flags and compliance |
| Vague, ambiguous, misspelt and mis-categorised tickets | Confidence gate and the user-versus-model category check |
| Restricted tickets with sensitive content | Access rules above |

## Requirement coverage

| ID | How it is met |
| --- | --- |
| FR-01 Classification | Category, subcategory and a text-based priority suggestion, each with confidence and the terms behind it. Priority in force comes from impact and urgency. |
| FR-02 Similarity | Ranked similar tickets; open near-duplicates suggested for analyst confirmation. |
| FR-03 Recommendation | Up to three SOPs ranked by how many similar resolved tickets each fixed, with first-time fix rate. |
| FR-04 Evidence | Source ticket IDs, similarity, shared terms, excerpt and how each source was resolved. |
| FR-05 Fallback | Low confidence, unfamiliar wording, too little or conflicting evidence, or category disagreement suppress the recommendation. |
| FR-06 Feedback | Useful or incorrect per recommendation and match; unresolved; rejected duplicate suggestions; user reopen. |
| FR-07 Search | Filters for application, category, status, assignment group, environment and date. |
| FR-08 Audit | `audit_log` and `ticket_events`, viewable and exportable, with restricted records filtered. |
| FR-09 MTTR | Created, recommended and resolved timestamps; baseline versus assisted comparison. |

## Design trade-offs

- **No generative model in the core flow.** Every recommendation traces to resolved tickets and an approved SOP.
- **Suggest, don't auto-link.** A wrong automatic link would raise priorities and close other people's tickets.
  The cost is one analyst click per duplicate, reduced by bulk confirmation.
- **TF-IDF instead of embeddings.** Fast and explainable; misses heavily reworded tickets.
- **SQLite instead of a server database.** Right-sized for a prototype; the SQL is standard.
- **Demo sign-in instead of single sign-on.** Real password hashing and server-side checks, but fixed demo accounts.

Known limitations are in section 5 of `EVALUATION_REPORT.md`.
