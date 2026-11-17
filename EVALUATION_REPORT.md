# Evaluation Report: IT Ticket Analysis and Resolution Assistant

All figures below were produced by `python evaluate.py` on the data in `data/`. Re-running the script regenerates this file.

## 1. Dataset

The dataset is **synthetic**: `generate_data.py` wrote every ticket, label, timestamp, handling time and resolution. No real ticket data was used. Each row carries `label_source = synthetic-generated`.

| Item | Value |
| --- | --- |
| Tickets | 1230 |
| Categories / subcategories | 8 / 22 |
| SOPs in the knowledge base | 30 |
| Duplicate clusters (several users, one incident) | 37 clusters, 234 tickets |
| Restricted (sensitive) tickets | 58 |
| Train / test split | 984 / 246 (80/20, stratified by subcategory, seed 42) |

The data is built to behave like a real service desk history, so that the assistant has something meaningful to learn:

- **Root causes:** each subcategory has one or two distinct root causes with their own symptoms and their own SOP, so the right fix depends on the wording, not only on the category.
- **Incidents:** 37 clusters where several users report one fault within about an hour, mentioning the same triggering event.
- **Recurring problems:** five faults return on the same service and site every 6 to 12 days and get the same temporary fix each time.
- **Failed first fixes:** in 81 resolved tickets the usual SOP was tried, failed, and the ticket was reopened and fixed another way.
- **Time patterns:** Mondays and mid-mornings are busiest and weekends are quiet.
- **Restricted tickets:** about 5% contain sensitive content and are hidden from analysts without clearance; none appear in the tables of this report.
- **Messy input:** about 5% of tickets are vague one-liners, 8% use wording that fits two categories, 10% contain a typo, and 14% have a wrong or "Not sure" user-selected category.

## 2. Evaluation method

- **Classification:** the models are trained on the training split and scored on the test split against the generated labels.
- **Duplicate retrieval:** for each test ticket that belongs to a duplicate cluster, we check where the first ticket from the same cluster appears in the ranked results.
- **Recommendation:** for each resolved test ticket, the recommended SOPs are compared with the SOP that resolved it. This stands in for analyst review, which needs real analysts.
- **Fallback:** 12 hand-written vague, out-of-scope and contradictory tickets should all be routed to human review.
- **MTTR:** simulated from the generated handling times (assumptions in section 3.5).

## 3. Results

### 3.1 Classification

| Field | Accuracy | Macro F1 |
| --- | --- | --- |
| Category | 94.3% | 0.945 |
| Subcategory | 91.1% | 0.913 |
| Priority (text model only) | 68.3% | 0.607 |

For comparison, the category the user picked on the form matched the true category in 86.2% of test tickets, so the classifier is the more reliable source. In the live app, priority comes from the impact and urgency matrix; the text model is only a cross-check, and its lower score shows why.

Category accuracy this high is an artefact of template-generated text, where each category has its own vocabulary. Real tickets would score lower; treat these numbers as a check that the pipeline works, not as expected production accuracy.

Category confusion matrix (rows = true, columns = predicted):

|  | Access | Application | Email | Hardware | Printer | Server | VPN | Wi-Fi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Access | 26 | 0 | 2 | 0 | 0 | 0 | 0 | 0 |
| Application | 4 | 36 | 0 | 0 | 0 | 1 | 0 | 0 |
| Email | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 0 |
| Hardware | 0 | 0 | 0 | 22 | 0 | 0 | 0 | 0 |
| Printer | 0 | 1 | 0 | 0 | 18 | 0 | 0 | 0 |
| Server | 0 | 2 | 0 | 0 | 0 | 34 | 0 | 0 |
| VPN | 0 | 0 | 0 | 0 | 0 | 0 | 34 | 1 |
| Wi-Fi | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 30 |

Priority confusion matrix for the text model (rows = true, columns = predicted):

|  | P1 | P2 | P3 | P4 |
| --- | --- | --- | --- | --- |
| P1 | 17 | 4 | 13 | 1 |
| P2 | 6 | 19 | 25 | 1 |
| P3 | 3 | 12 | 118 | 1 |
| P4 | 0 | 1 | 11 | 14 |

Operationally important confusion: 14 of 35 true P1 tickets were predicted as P3 or P4 by the text model.

Most frequent subcategory confusions:

| True | Predicted | Count |
| --- | --- | --- |
| Hardware - Laptop won't boot | Hardware - Peripheral not working | 2 |
| Application - Slow performance | Application - Login error | 2 |
| Server - Service down | Application - Login error | 2 |
| Wi-Fi - Cannot connect | Hardware - Peripheral not working | 2 |
| Application - Crash on launch | Server - Service down | 1 |

### 3.2 Duplicate retrieval

| Measure | Value |
| --- | --- |
| Test tickets with a known duplicate in the history | 40 |
| Duplicate found at rank 1 | 55.0% |
| Duplicate found in top 3 | 80.0% |
| Duplicate found in top 5 | 82.5% |
| Mean reciprocal rank | 0.676 |
| Top-5 results sharing the true subcategory (all test tickets) | 87.5% |
| Top-5 results from a different category (superficial matches) | 5.5% |
| Restricted tickets shown in any result | 0 |

**Duplicate suggestions in the live flow.** Each historical ticket is replayed as if it had just arrived: the candidates are tickets raised in the previous 48 hours with the same subcategory and the same site or service. 185 tickets had an earlier report of the same incident; 751 did not.

| Similarity threshold | Real duplicates suggested with the right target | Non-duplicates given a wrong suggestion | Suggestions that are right |
| --- | --- | --- | --- |
| 0.15 | 97.3% | 24.4% | 49.0% |
| 0.20 | 94.6% | 19.2% | 54.2% |
| 0.25 (in use) | 89.2% | 14.6% | 59.1% |
| 0.30 | 82.7% | 12.3% | 61.4% |
| 0.35 | 77.3% | 10.3% | 63.8% |
| 0.45 | 64.9% | 8.4% | 64.2% |
| 0.55 | 56.8% | 6.8% | 66.5% |

The threshold is set low on purpose. Every suggestion is confirmed or rejected by an analyst, so a missed duplicate (lost grouping, slower response) costs more than a wrong suggestion (one click to reject).

### 3.3 Resolution recommendation

| Measure | Value |
| --- | --- |
| Resolved test tickets evaluated | 240 |
| Recommendation shown (confidence high or medium) | 216 (90.0%) |
| Top recommendation matched the actual fix | 89.8% |
| Actual fix within the top 3 recommendations | 92.6% |

By confidence level:

| Confidence | Tickets | Top-1 correct | Top-3 correct |
| --- | --- | --- | --- |
| high | 191 | 92.1% | 93.7% |
| medium | 25 | 72.0% | 84.0% |
| low | 24 | 16.7% | 25.0% |

The "low" row shows what the hidden recommendations would have scored; it is the cost and the benefit of the gate.

### 3.4 Confidence and fallback

11 of 12 challenge tickets were flagged low confidence and routed to human review (vague: 4 of 4, out of scope: 5 of 5, contradictory: 2 of 3).

On ordinary in-scope test tickets, 10.0% were routed to human review.

### 3.5 MTTR (simulated)

| Measure | Manual baseline | With assistant | Reduction |
| --- | --- | --- | --- |
| MTTR, creation to resolution (minutes) | 294 | 257 | 12.8% |
| Hands-on handling time, excluding queue wait (minutes) | 108 | 71 | 34.7% |

This is a simulation, not a measurement. Assumptions: a correct recommendation replaces triage and lookup with a 2-minute review; a wrong recommendation costs the review plus 10 minutes before the analyst falls back to manual handling; a low-confidence ticket is handled manually. Queue wait and fix time are unchanged. The saving from resolving all linked duplicates at once is not included, so the effect on a real major incident would be larger. The app's MTTR tab also reports real timestamps for tickets handled in the app.

### 3.6 What the assistant learns from the history

Recurring problems detected (same fault, same service and site, at 1.8 times or more the rate of other sites):

| problem | application | location | occurrences | vs_other_sites | typical_gap_days | last_seen | usual_fix | times_fixed | analyst_hours_spent |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Printer - Cannot print | Print Service | Bengaluru | 21 | 4.0x | 5.0 | 2026-11-16 | SOP-028 | 18 | 31.8 |
| Wi-Fi - Intermittent drops | Corporate Wi-Fi | Noida | 19 | 3.5x | 6.0 | 2026-11-14 | SOP-007 | 24 | 42.2 |
| Server - Disk space full | Tableau | Pune | 12 | 20.0x | 9.0 | 2026-11-15 | SOP-027 | 12 | 27.3 |
| Application - Slow performance | Salesforce CRM | Chennai | 11 | 11.0x | 10.0 | 2026-11-15 | SOP-023 | 11 | 23.5 |
| Email - Mailbox full | Outlook | Hyderabad | 11 | 2.6x | 6.5 | 2026-11-03 | SOP-010 | 14 | 19.4 |
| VPN - Slow over VPN | GlobalProtect VPN | Hyderabad | 11 | 2.5x | 10.5 | 2026-11-16 | SOP-004 | 17 | 21.9 |
| Email - Phishing or spam report | Outlook | Hyderabad | 8 | 2.0x | 7.0 | 2026-10-27 | SOP-011 | 8 | 11.6 |
| Server - High CPU or memory | SAP ERP | Hyderabad | 8 | 16.0x | 12.0 | 2026-11-14 | SOP-026 | 7 | 18.6 |

All five recurring faults planted in the data are in this list. The others are chance concentrations, which is why the app presents the list as candidates for an analyst to judge.

SLA compliance in the history:

| priority | target_hours | resolved | met_pct | avg_hours |
| --- | --- | --- | --- | --- |
| P1 | 4 | 190 | 91.6 | 2.4 |
| P2 | 8 | 231 | 97.8 | 3.0 |
| P3 | 24 | 615 | 100.0 | 4.8 |
| P4 | 72 | 159 | 100.0 | 13.4 |

SOPs with the lowest first-time fix rate (shown to the analyst next to each recommendation):

| SOP | Uses | Failed first attempt | First-time fix rate |
| --- | --- | --- | --- |
| SOP-025 | 29 | 5 | 82.8% |
| SOP-021 | 45 | 7 | 84.4% |
| SOP-002 | 39 | 5 | 87.2% |
| SOP-026 | 40 | 5 | 87.5% |
| SOP-005 | 49 | 6 | 87.8% |

## 4. Observed errors

Category misclassifications from the test run:

| Ticket | Title | True | Predicted | Confidence | Gate |
| --- | --- | --- | --- | --- | --- |
| TKT-10543 | Problem with Tableau | Application | Server | 50% | low |
| TKT-10872 | Cannot connect | VPN | Wi-Fi | 60% | low |
| TKT-10275 | Laptop not connecting | Wi-Fi | Hardware | 83% | low |
| TKT-10341 | Mail login problem | Access | Email | 68% | low |
| TKT-10060 | Cannot log in to Jira | Application | Access | 58% | medium |
| TKT-11153 | Laptop not connecting | Wi-Fi | Hardware | 62% | low |

Recommendations that were shown but did not contain the actual fix:

| Ticket | Title | Actual fix | Recommended | Confidence |
| --- | --- | --- | --- | --- |
| TKT-11110 | Jira login loop | SOP-030 | SOP-021 | high |
| TKT-11138 | Mailbox quota exceeded | SOP-030 | SOP-010 | high |
| TKT-10312 | VPN not connecting - certificate error | SOP-002 | SOP-001 | high |
| TKT-11130 | Jira not working | SOP-023 | SOP-021, SOP-022 | medium |

Challenge tickets that were NOT flagged low confidence (gate misses):

| Kind | Title | Status | Predicted category |
| --- | --- | --- | --- |
| contradictory | Everything broken | medium | Wi-Fi |

Patterns behind the errors:

- Vague one-line tickets carry too little text to tell subcategories apart; most are caught by the gate.
- Tickets whose real fix was an alternative SOP or a vendor escalation look identical to ordinary ones, so the top recommendation is wrong by construction for part of them.
- Priority cannot be read reliably from text when the user does not describe impact, which is why the app asks for impact and urgency directly.

## 5. Known limitations

- The data is synthetic and template-based, so scores are higher than they would be on real tickets.
- TF-IDF matches words. A ticket that describes a known problem in very different words can be missed.
- Recommendation quality was scored against the recorded fix, not by human analysts.
- The MTTR figures are simulated.
- Duplicate linking uses a similarity threshold plus same subcategory and same site or application; it can link two unrelated single-user tickets, so the analyst can unlink.
- Thresholds were set on this dataset and need retuning on real data.
- Two gate checks (unfamiliar wording, minimum evidence) were added after looking at the challenge tickets, so the challenge result is not an independent test.
- Recurring-problem detection compares sites with each other; it also lists some chance concentrations and needs an analyst's judgement.
- Sign-in uses demo accounts with salted password hashes and fixed roles. A real deployment would use the company's single sign-on and directory groups.
- The classifier is trained on all tickets, including restricted ones. Their text never appears in results or evidence for analysts without clearance, but a stricter setup would train a separate model.
- Duplicate suggestions need an analyst to confirm them, which costs a few seconds per ticket during a large outage; bulk confirmation in the Incidents tab reduces this.
