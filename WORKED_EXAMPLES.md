# Worked Examples

Three tickets taken from the evaluation run (`python evaluate.py`). Data is synthetic.

## Example 1: Service outage, approval-gated SOP

**Input ticket**

| Field | Value |
| --- | --- |
| Title | File Server server not responding |
| Description | File Server is completely unavailable, the application service looks hung and health check is failing. Not sure what changed. My whole team is facing this. |
| Application | File Server |
| User-selected category | Server |
| Impact / urgency | My team / Minor |

**Classification**

| Field | Prediction | Confidence | Terms that drove it |
| --- | --- | --- | --- |
| Category | Server | 99% | server, service, file server, unavailable, failing |
| Subcategory | Server - Service down | 99% | service, server, unavailable, failing, responding |
| Priority | P4 | from impact and urgency matrix | - |

**Similar historical tickets**

| Ticket | Status | Text similarity | Shared terms | Excerpt |
| --- | --- | --- | --- | --- |
| TKT-11002 | Resolved | 0.819 | file server, file, server, server completely | Hi team, File Server is completely unavailable, the application service looks hung and health check is failing. |
| TKT-10642 | Closed | 0.736 | file server, file, server completely, server | File Server is completely unavailable, the application service looks hung and health check is failing. |
| TKT-10426 | Resolved | 0.692 | file server, file, server completely, server | File Server is completely unavailable, the application service looks hung and health check is failing. |

**Confidence:** high. All checks passed.

**Recommended resolution:** SOP-024 Controlled application server restart (risk High, approval required)

1. Confirm the service is down from monitoring and capture the error (for example HTTP 503).
2. Check active user sessions and running batch jobs on the server.
3. Notify affected stakeholders and post an outage notice.
4. Obtain approval from the change manager or on-call lead.
5. Take a configuration backup or VM snapshot.
6. Restart the application service first; restart the server only if the service restart fails.
7. Verify health checks, sign-in and one key transaction.
8. Rollback: if the service does not recover in 15 minutes, restore the snapshot and escalate to L3.

**Source evidence:** 7 of the 8 most similar resolved tickets were fixed with this procedure.

| Source ticket | Similarity | Why it supports this | How it was resolved |
| --- | --- | --- | --- |
| TKT-11002 | 0.819 | Shares: file server, file, server, server completely | Approval taken, snapshot done, application service restarted. Verified sign-in and a transaction. |
| TKT-10642 | 0.736 | Shares: file server, file, server completely, server | Followed controlled restart with approval. Service restart restored File Server, health checks green. |
| TKT-10426 | 0.692 | Shares: file server, file, server completely, server | Approval taken, snapshot done, application service restarted. Verified sign-in and a transaction. |

Other paths found: SOP-030 Escalate to vendor or L3 with diagnostics (12.6% of evidence)

Outcome: the ticket was actually resolved with SOP-024, which matches the top recommendation. The SOP requires approval and a backup before any restart, so the analyst cannot simply restart the server.

## Example 2: Duplicate of a known incident

**Input ticket**

| Field | Value |
| --- | --- |
| Title | Git Enterprise pages take minutes to load |
| Description | Git Enterprise performance is terrible since morning, searches spin for a long time before showing results. Seems related to the switch replacement in Noida. I cannot do any work until this is fixed. My colleague suggested raising a ticket. |
| Application | Git Enterprise |
| User-selected category | Not sure |
| Impact / urgency | Whole organisation / Blocked |

**Classification**

| Field | Prediction | Confidence | Terms that drove it |
| --- | --- | --- | --- |
| Category | Application | 96% | git, enterprise, git enterprise, minutes load, pages minutes |
| Subcategory | Application - Slow performance | 99% | minutes load, git, load, enterprise, git enterprise |
| Priority | P1 | from impact and urgency matrix | - |

**Similar historical tickets**

| Ticket | Status | Text similarity | Shared terms | Excerpt |
| --- | --- | --- | --- | --- |
| TKT-10334 | Resolved | 0.678 | git, git enterprise, enterprise, enterprise performance | Git Enterprise performance is terrible since morning, searches spin for a long time before showing results. |
| TKT-10344 | Resolved | 0.662 | enterprise, git enterprise, git, enterprise performance | Hi, Git Enterprise performance is terrible since morning, searches spin for a long time before showing results. |
| TKT-10340 | Resolved | 0.62 | git, morning searches, performance terrible, performance | Urgent - Git Enterprsie performance is terrible since morning, searches spin for a long time before showing results. |

**Confidence:** high. All checks passed.

**Recommended resolution:** SOP-023 Application performance triage (risk Medium)

1. Confirm whether slowness affects one user or many.
2. Check application server and database response times on the monitoring dashboard.
3. Review the database connection pool and long-running queries.
4. Recycle the application pool in a quiet period if connections are exhausted.
5. Confirm page and report load times are back within the normal range.

**Source evidence:** 6 of the 8 most similar resolved tickets were fixed with this procedure.

| Source ticket | Similarity | Why it supports this | How it was resolved |
| --- | --- | --- | --- |
| TKT-10334 | 0.678 | Shares: git, git enterprise, enterprise, enterprise performance | Connection pool exhausted. Recycled app pool in quiet period, response normal. |
| TKT-10344 | 0.662 | Shares: enterprise, git enterprise, git, enterprise performance | Long-running query killed and pool recycled, load times normal. |
| TKT-10340 | 0.62 | Shares: git, morning searches, performance terrible, performance | Long-running query killed and pool recycled, load times normal. |

Other paths found: SOP-030 Escalate to vendor or L3 with diagnostics (12.8% of evidence); SOP-026 Identify runaway process and recycle it (10.3% of evidence)

Outcome: the first similar ticket belongs to the same incident cluster (HG-031) as this ticket, and the actual fix was SOP-023. The user selected "Not sure" as the category; the true category is Application, which the classifier predicted.

## Example 3: Vague ticket, routed to human review

**Input ticket**

| Field | Value |
| --- | --- |
| Title | Help |
| Description | It is not working. Please fix it as soon as possible. |
| Application | - |
| User-selected category | Not sure |
| Impact / urgency | - / - |

**Classification**

| Field | Prediction | Confidence | Terms that drove it |
| --- | --- | --- | --- |
| Category | Wi-Fi | 25% | help |
| Subcategory | Wi-Fi - Intermittent drops | 90% | help |
| Priority | - | from impact and urgency matrix | - |

**Similar historical tickets**

| Ticket | Status | Text similarity | Shared terms | Excerpt |
| --- | --- | --- | --- | --- |
| TKT-11030 | Resolved | 0.499 | help | wifi problem, please help |
| TKT-10512 | Closed | 0.312 | help, working | vpn not working pls help |
| TKT-11120 | Resolved | 0.272 | help, working | My laptop is not working properly, please help. |

**Confidence:** low. Low classification confidence (25%).

**Recommended resolution:** none. The assistant withholds a recommendation and routes the ticket to human review.


Outcome: the text gives no usable symptom, so the assistant declines to recommend a fix.
