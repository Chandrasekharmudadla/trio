"""Checks for access control, restricted data, duplicate handling and approvals.

Run:  python test_workflows.py      (uses a temporary database; data/tickets.db is not touched)
"""
import os
import sys
import tempfile

os.environ["TICKET_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as e  # noqa: E402
e.init_db(); a = e.build_assistant()
FAILED = []


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILED.append(msg)



def raises(fn, *args, **kw):
    try: fn(*args, **kw); return None
    except (e.PermissionDenied, ValueError) as x: return str(x)

# 1. authentication and roles
ok(e.authenticate("priya", "wrong") is None, "wrong password rejected")
ok(e.authenticate("nobody", "demo123") is None, "unknown user rejected")
P, R, A, L, C = (e.authenticate(u, "demo123") for u in ("priya", "rahul", "analyst1", "lead1", "cab1"))
ok(P and P["role"] == "user" and L["restricted_clearance"] == 1, "demo accounts sign in with roles")
ok(e.query("select password_hash from users where username='priya'").iloc[0,0] != "demo123", "passwords stored hashed")
form = dict(title="VPN certificate error", description="VPN client shows certificate validation failure and will not connect since the certificate rotation.",
            application="GlobalProtect VPN", environment="Production", location="Hyderabad", user_category="Wi-Fi", impact=1, urgency="Blocked", restricted=0)
ok(raises(e.submit_ticket, a, form, A) is not None, "analyst cannot raise a ticket as a user")

# 3. duplicate is suggested, not linked
s = e.submit_ticket(a, form, P); t = e.get_ticket(s["ticket_id"])
ok(t["incident_id"] == "" and t["dup_candidate"] == "INC-0001" and t["priority"] == "P3", "duplicate suggested only, priority unchanged (%s, %s, %s)" % (t["incident_id"], t["dup_candidate"], t["priority"]))
ok(raises(e.confirm_duplicate, s["ticket_id"], P) is not None, "employee cannot confirm duplicates")
r = e.confirm_duplicate(s["ticket_id"], A); t = e.get_ticket(s["ticket_id"])
ok(t["incident_id"] == "INC-0001" and t["linked_by"] == "analyst1" and t["priority"] == "P1", "analyst confirms -> linked, priority %s" % t["priority"])

# per-person issues never grouped
pw = dict(form, title="Forgot my password", description="I forgot my Windows password after leave and cannot log in, please reset it.", application="Active Directory", user_category="Access")
x1 = e.submit_ticket(a, pw, P); x2 = e.submit_ticket(a, pw, R)
ok(x2["suggested_duplicate"] is None, "two people's password resets are not suggested as duplicates")
ok(raises(e.link_tickets, x2["ticket_id"], x1["ticket_id"], A) is not None, "and cannot be linked manually")

# 2. restricted exposure
rs = e.submit_ticket(a, dict(form, restricted=1, location="Pune"), R)
ok(rs["suggested_duplicate"] is None, "restricted ticket never suggested for grouping")
ok(not e.can_view(A, e.get_ticket(rs["ticket_id"])) and e.can_view(L, e.get_ticket(rs["ticket_id"])), "restricted ticket: analyst1 no, lead1 yes")
ok(not e.can_view(P, e.get_ticket(rs["ticket_id"])) and e.can_view(R, e.get_ticket(rs["ticket_id"])), "employees see only their own tickets")
ok(raises(e.resolve_ticket, rs["ticket_id"], "SOP-001", "x", A) is not None, "analyst without clearance cannot act on restricted ticket")
inc_restricted = e.query("select count(*) n from tickets where incident_id != '' and restricted = 1").iloc[0, 0]
ok(inc_restricted == 0, "no restricted ticket inside any incident")
# privileged analyst views a normal ticket whose evidence includes restricted tickets -> audit flagged
hr = e.query("select * from tickets where restricted=1 and status in ('Open','In Progress') limit 1")
src = e.query("select * from tickets where restricted=0 and status='Open' and subcategory=(select subcategory from tickets where restricted=1 and resolution_sop!='' limit 1) limit 1")
if len(src):
    tk = src.iloc[0].to_dict(); an = a.analyze(tk, include_restricted=True, exclude_ids=[tk["ticket_id"]])
    had = any(m["restricted"] for m in an["similar"] + an["resolved_matches"])
    e.ensure_audit(tk, an, "lead1")
    vis = e.export_audit(False); ok((tk["ticket_id"] not in set(vis["ticket_id"])) == had, "audit record citing restricted evidence hidden from non-cleared export (cited restricted: %s)" % had)

# 4. selective resolution + reopen
linked = e.query("select ticket_id from tickets where incident_id='INC-0001' and ticket_id != ?", (s["ticket_id"],))["ticket_id"].tolist()
res = e.resolve_ticket(s["ticket_id"], "SOP-001", "Renewed certificate", A, also_resolve=linked[:3])
ok(len(res["resolved"]) == 4 and len(res["unlinked"]) == len(linked) - 3, "resolved %d, left open and unlinked %d" % (len(res["resolved"]), len(res["unlinked"])))
st = e.query("select status, incident_id from tickets where ticket_id in (%s)" % ",".join("'%s'" % x for x in res["unlinked"]))
ok((st["status"] == "Open").all() and (st["incident_id"] == "").all(), "unselected tickets stay open, outside the incident")
ok(raises(e.user_reopen, s["ticket_id"], R, "still broken") is not None, "another user cannot reopen priya's ticket")
e.user_reopen(s["ticket_id"], P, "Still getting the certificate error")
t = e.get_ticket(s["ticket_id"])
ok(t["status"] == "Open" and t["incident_id"] == "" and int(t["reopen_count"]) == 1 and t["first_attempt_sop"] == "SOP-001", "user reopen -> back in queue, out of incident, first attempt recorded")

# 5. approvals
srv = e.submit_ticket(a, dict(form, title="Jira is down", description="Jira shows 503 service unavailable for everyone and the health check is failing.",
                              application="Jira", location="Pune", user_category="Server", impact=4), R)
tid = srv["ticket_id"]
ok(raises(e.resolve_ticket, tid, "SOP-024", "restart", A) is not None, "high-risk SOP cannot be resolved without approval")
ok(raises(e.request_approval, tid, "SOP-024", "short", A) is not None, "approval request needs a real plan")
aid = e.request_approval(tid, "SOP-024", "Restart the Jira application service after notifying users; restore snapshot if not healthy in 15 minutes.", A)
ok(raises(e.decide_approval, aid, "Approved", "", A) is not None, "analyst cannot approve")
ok(raises(e.resolve_ticket, tid, "SOP-024", "restart", A) is not None, "still blocked while pending")
ok(raises(e.decide_approval, aid, "Rejected", "", C) is not None, "rejection needs a reason")
e.decide_approval(aid, "Approved", "Go ahead in the next 30 minutes", C)
ok(raises(e.decide_approval, aid, "Approved", "", C) is not None, "cannot decide twice")
res = e.resolve_ticket(tid, "SOP-024", "Service restarted, health checks green", A)
ok(res["approval_id"] == aid, "resolved under approval #%s" % res["approval_id"])
print("\n%d checks failed" % len(FAILED) if FAILED else "\nAll checks passed")
sys.exit(1 if FAILED else 0)
