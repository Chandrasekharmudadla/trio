"""Streamlit interface: user portal, analyst console and approver view, behind a sign-in.

Run:  streamlit run app.py
Every action is checked again in engine.py against the signed-in account, so hiding a button is never the only control.
"""
import json
import os

import pandas as pd
import streamlit as st

import engine

st.set_page_config(page_title="IT Ticket Assistant", layout="wide")
engine.init_db()
engine.auto_close_resolved()


@st.cache_resource(show_spinner="Training models on the ticket history...", max_entries=2)
def _assistant(version):
    return engine.build_assistant()


def assistant():
    v = engine.query("SELECT COUNT(*) AS n, SUM(CASE WHEN resolved_at != '' THEN 1 ELSE 0 END) AS r FROM tickets").iloc[0]
    return _assistant((int(v["n"]), int(v["r"] or 0)))


def distinct(col):
    df = engine.query("SELECT DISTINCT %s AS v FROM tickets WHERE %s != '' ORDER BY 1" % (col, col))
    return list(df["v"])


def num(x, default=0.0):
    try:
        f = float(x)
        return default if f != f else f
    except (TypeError, ValueError):
        return default


def flash(msg, kind="success", slot="main"):
    st.session_state["flash_" + slot] = (kind, msg)


def show_flash(slot="main"):
    item = st.session_state.pop("flash_" + slot, None)
    if item:
        getattr(st, item[0])(item[1])


def attempt(fn, *args, **kwargs):
    """Run an engine action and show a refusal as a message instead of a crash."""
    try:
        return True, fn(*args, **kwargs)
    except (engine.PermissionDenied, ValueError) as err:
        st.error(str(err))
        return False, None


# =========================================================================== sign-in
def login_screen():
    st.title("IT Ticket Assistant")
    col, _ = st.columns([1, 1.3])
    with col:
        with st.form("login"):
            st.subheader("Sign in")
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            user = engine.authenticate(username, password)
            if user:
                st.session_state.clear()
                st.session_state["user"] = user
                st.rerun()
            st.error("Wrong username or password.")
        with st.expander("Demo accounts for this prototype"):
            st.dataframe(pd.DataFrame([{"Username": u, "Name": n, "Role": engine.ROLE_LABELS[r],
                                        "Restricted tickets": "Yes" if c else "No"} for u, n, r, c, _ in engine.DEMO_USERS]),
                         hide_index=True)
            st.caption("Every demo account uses the password %s. Passwords are stored as salted PBKDF2 hashes. "
                       "A production deployment would use the company's single sign-on instead." % engine.DEMO_PASSWORD)


ME = st.session_state.get("user")
if not ME:
    login_screen()
    st.stop()
ROLE = ME["role"]
PRIV = bool(ME["restricted_clearance"])
CATEGORIES = distinct("category")
SOPS = engine.load_sops()
SOP_TITLE = dict(zip(SOPS["sop_id"], SOPS["title"]))
SOP_APPROVAL = dict(zip(SOPS["sop_id"], SOPS["requires_approval"].astype(int)))
SOP_STEPS = dict(zip(SOPS["sop_id"], SOPS["steps"]))

with st.sidebar:
    st.title("IT Ticket Assistant")
    st.write("Signed in as **%s** (%s)" % (ME["display_name"], ME["username"]))
    st.caption(engine.ROLE_LABELS[ROLE] + (" with restricted-ticket clearance" if PRIV else ""))
    if st.button("Sign out"):
        st.session_state.clear()
        st.rerun()
    st.divider()
    st.caption("Prototype on synthetic data. The assistant recommends; people decide. It never closes a ticket "
               "or changes a system on its own.")


@st.cache_data(show_spinner=False, max_entries=4)
def _recurring(version, priv):
    t = engine.load_tickets()
    return engine.recurring_problems(t if priv else t[t["restricted"] == 0])


def recurring():
    return _recurring(int(engine.query("SELECT COUNT(*) AS n FROM tickets").iloc[0]["n"]), PRIV)


# =========================================================================== employee
def user_portal():
    st.header("User portal")
    tab_new, tab_mine = st.tabs(["Raise a ticket", "My tickets"])
    with tab_new:
        show_flash()
        result = st.session_state.pop("submitted", None)
        if result:
            st.success("Ticket %s created with priority %s." % (result["ticket_id"], result["priority"]))
            if result["suggested_duplicate"]:
                st.info("Others have reported a similar problem. An analyst will check whether it is the same issue; "
                        "if it is, your ticket will be handled together with theirs.")
            st.caption("You can follow progress under My tickets.")
        locs = distinct("location")
        with st.form("raise", clear_on_submit=True):
            c1, c2 = st.columns(2)
            c1.text_input("Raised by", value="%s (%s)" % (ME["display_name"], ME["username"]), disabled=True)
            location = c2.selectbox("Your location", locs, index=locs.index(ME["location"]) if ME["location"] in locs else 0)
            title = st.text_input("Short title", placeholder="For example: VPN not connecting")
            desc = st.text_area("Describe the problem", height=140,
                                placeholder="What happens, any error message, and when it started.")
            c1, c2, c3 = st.columns(3)
            app = c1.selectbox("Affected application or service", ["Other / not sure"] + distinct("application"))
            env = c2.selectbox("Environment", ["Production", "UAT", "Development"])
            cat = c3.selectbox("Category", ["Not sure"] + CATEGORIES, help="What kind of problem it is.")
            c1, c2 = st.columns(2)
            impact = c1.radio("How many people are affected?", list(engine.IMPACT_LABELS), format_func=engine.IMPACT_LABELS.get)
            urgency = c2.radio("How badly is work affected?", list(engine.URGENCY_LABELS), format_func=engine.URGENCY_LABELS.get)
            st.caption("Priority is calculated from these two answers. Category and priority are separate.")
            sensitive = st.checkbox("This ticket contains sensitive information (salary, health, legal, confidential). "
                                    "Only cleared analysts will see it.")
            if st.form_submit_button("Submit ticket", type="primary"):
                if not title.strip() or len(desc.strip()) < 10:
                    flash("Please enter a title and a description of at least 10 characters.", "error")
                else:
                    ok, res = attempt(engine.submit_ticket, assistant(), {
                        "title": title.strip(), "description": desc.strip(),
                        "application": "" if app.startswith("Other") else app, "environment": env, "location": location,
                        "user_category": cat, "impact": impact, "urgency": urgency, "restricted": int(sensitive)}, ME)
                    if ok:
                        st.session_state["submitted"] = res
                st.rerun()
    with tab_mine:
        show_flash("mine")
        mine = engine.query("SELECT ticket_id, title, category, priority, status, created_at, resolved_at, resolution_notes "
                            "FROM tickets WHERE requester_username = ? ORDER BY created_at DESC", (ME["username"],))
        if mine.empty:
            st.info("You have not raised any tickets yet.")
            return
        st.dataframe(mine, hide_index=True)
        resolved = mine[mine["status"] == "Resolved"]
        if len(resolved):
            st.markdown("#### Did the fix work?")
            st.caption("Resolved tickets close automatically after %d days unless you reopen them." % engine.REOPEN_DAYS)
            labels = dict(zip(resolved["ticket_id"], resolved["ticket_id"] + " | " + resolved["title"]))
            pick = st.selectbox("Ticket", list(labels), format_func=labels.get, key="confirm_pick")
            comment = st.text_input("If it is still not working, tell us what you see", key="reopen_comment")
            c1, c2, _ = st.columns([1, 1.2, 4])
            if c1.button("Yes, it is fixed"):
                ok, _ = attempt(engine.user_confirm_fixed, pick, ME)
                if ok:
                    flash("Thanks. Ticket %s is closed." % pick, slot="mine")
                    st.rerun()
            if c2.button("No, still not working"):
                ok, _ = attempt(engine.user_reopen, pick, ME, comment)
                if ok:
                    flash("Ticket %s is reopened and back with the service desk." % pick, "warning", slot="mine")
                    st.rerun()
        with st.expander("Ticket history"):
            pick = st.selectbox("Show history for", list(mine["ticket_id"]), key="hist_pick")
            st.dataframe(engine.ticket_history(pick), hide_index=True)


# =========================================================================== analyst: ticket view
def render_recommendation(i, rec, ticket, audit_id):
    label = "%d. %s %s - %d of %d similar resolved tickets (%.0f%% of evidence)" % (
        i + 1, rec["sop_id"], rec["title"], rec["support"], rec["evidence_total"], rec["share"] * 100)
    with st.expander(label, expanded=(i == 0)):
        st.write("**Risk:** %s" % rec["risk_level"])
        if rec.get("first_time_fix_rate") is not None:
            st.write("**Track record:** fixed the problem at the first attempt in %.0f%% of %d past uses."
                     % (rec["first_time_fix_rate"] * 100, rec["uses"]))
        if rec["requires_approval"]:
            st.warning("Needs an approved change request before it is carried out. Request it in the decision section below.")
        st.markdown("\n".join("%d. %s" % (k + 1, s) for k, s in enumerate(rec["steps"])))
        st.write("**Why this is recommended:** " + rec["reason"])
        st.dataframe(pd.DataFrame([{"Source ticket": e["ticket_id"], "Similarity": e["text_similarity"],
                                    "Shared terms": ", ".join(e["shared_terms"]), "Relevant excerpt": e["excerpt"],
                                    "How it was resolved": e["resolution_notes"]} for e in rec["evidence"]]), hide_index=True)
        c1, c2, _ = st.columns([1, 1, 4])
        if c1.button("Useful", key="u_%s_%s" % (ticket["ticket_id"], rec["sop_id"])):
            engine.add_feedback(audit_id, ticket["ticket_id"], "recommendation", rec["sop_id"], "useful", ME["username"])
            st.toast("Feedback saved: useful")
        if c2.button("Incorrect", key="i_%s_%s" % (ticket["ticket_id"], rec["sop_id"])):
            engine.add_feedback(audit_id, ticket["ticket_id"], "recommendation", rec["sop_id"], "incorrect", ME["username"])
            st.toast("Feedback saved: incorrect")


def duplicates_section(ticket, a):
    tid = ticket["ticket_id"]
    st.markdown("#### Duplicates and incident")
    if not engine.can_group(ticket):
        why = ("restricted tickets are never grouped with other tickets" if int(ticket["restricted"])
               else "%s issues affect one person, so they are never grouped into incidents" % ticket["category"])
        st.caption("Handled on its own: " + why + ".")
        return
    if ticket["incident_id"]:
        linked = engine.query("SELECT ticket_id, title, requester, location, status, linked_by, linked_at FROM tickets "
                              "WHERE incident_id=? ORDER BY created_at", (ticket["incident_id"],))
        st.info("Part of incident %s with %d tickets. Every link below was confirmed by the analyst named in linked_by."
                % (ticket["incident_id"], len(linked)))
        st.dataframe(linked, hide_index=True)
        reason = st.text_input("Reason, if this ticket is not the same problem", key="unl_r_" + tid)
        if st.button("Unlink this ticket from the incident", key="unlink_" + tid):
            ok, _ = attempt(engine.unlink_ticket, tid, ME, reason)
            if ok:
                flash("%s unlinked; its own priority is restored." % tid)
                st.rerun()
        return
    cand = ticket.get("dup_candidate") or ""
    if cand:
        if cand.startswith("INC-"):
            info = engine.query("SELECT title, (SELECT COUNT(*) FROM tickets t WHERE t.incident_id = i.incident_id) AS n "
                                "FROM incidents i WHERE incident_id=?", (cand,))
            target = "incident %s (%s, %d tickets)" % (cand, info["title"].iloc[0], info["n"].iloc[0]) if len(info) else cand
        else:
            o = engine.get_ticket(cand)
            target = "%s (%s, %s, raised %s)" % (cand, o["title"], o["location"], o["created_at"]) if o else cand
        st.warning("The assistant suggests this may be the same problem as %s, text similarity %.2f. "
                   "Nothing is linked until you confirm." % (target, num(ticket.get("dup_score"))))
        c1, c2, _ = st.columns([1.3, 1.3, 3])
        if c1.button("Confirm: same problem", key="dupok_" + tid, type="primary"):
            ok, r = attempt(engine.confirm_duplicate, tid, ME)
            if ok:
                flash("Linked to %s. Incident now has %d tickets; priority %s." % (r["incident_id"], r["linked"], r["priority"]))
                st.rerun()
        if c2.button("Not the same problem", key="dupno_" + tid):
            ok, _ = attempt(engine.reject_duplicate, tid, ME, "Rejected in ticket view")
            if ok:
                flash("Suggestion rejected and recorded as feedback.", "info")
                st.rerun()
        return
    dups = engine.find_open_duplicates(a, ticket, ticket["category"], ticket["subcategory"], exclude_id=tid)
    if not dups:
        st.caption("No open ticket for the same problem at the same site or service in the last %d hours."
                   % engine.DUP_WINDOW_HOURS)
        return
    st.write("Open tickets that may be the same problem:")
    st.dataframe(pd.DataFrame(dups)[["ticket_id", "title", "incident_id", "location", "application", "reasons"]], hide_index=True)
    pick = st.selectbox("Link this ticket to", [d["ticket_id"] for d in dups], key="lk_" + tid)
    if st.button("Link as the same problem", key="link_" + tid):
        ok, r = attempt(engine.link_tickets, tid, pick, ME)
        if ok:
            flash("Linked to incident %s." % r["incident_id"])
            st.rerun()


def approval_block(tid, sop):
    st.markdown("**%s %s is high risk. It needs an approved change request before the ticket can be resolved.**"
                % (sop, SOP_TITLE.get(sop, "")))
    state = engine.approval_state(tid, sop)
    if state and state["valid"]:
        st.success("Change request #%d approved by %s at %s. %s" % (state["approval_id"], state["decided_by"],
                                                                     state["decided_at"], state["decision_comment"]))
        return True
    if state and state["status"] == "Pending":
        st.info("Change request #%d is waiting for an approver (requested by %s at %s)."
                % (state["approval_id"], state["requested_by"], state["requested_at"]))
        return False
    if state and state["status"] == "Rejected":
        st.error("Change request #%d was rejected by %s: %s" % (state["approval_id"], state["decided_by"], state["decision_comment"]))
    elif state and state["status"] == "Approved":
        st.warning("Change request #%d has expired (approvals are valid for %d hours). Request a new one."
                   % (state["approval_id"], engine.APPROVAL_VALID_HOURS))
    plan = st.text_area("Change request: what you will do, the expected impact, and the rollback plan", key="plan_%s_%s" % (tid, sop))
    if st.button("Request approval", key="req_%s_%s" % (tid, sop)):
        ok, aid = attempt(engine.request_approval, tid, sop, plan, ME)
        if ok:
            flash("Change request #%d sent to the approvers." % aid, "info")
            st.rerun()
    return False


def decision_section(ticket, cls, gate, recs, audit_id):
    tid = ticket["ticket_id"]
    st.markdown("#### Analyst decision")
    rec_ids = [r["sop_id"] for r in recs] if gate["status"] != "low" else []
    options = rec_ids + [s for s in SOPS["sop_id"] if s not in rec_ids]
    c1, c2 = st.columns(2)
    sop = c1.selectbox("Procedure used", options, key="sop_" + tid,
                       format_func=lambda s: "%s %s%s" % (s, SOP_TITLE.get(s, ""), "  (recommended)" if s in rec_ids else ""))
    default_cat = cls["category"]["label"] if cls["category"]["label"] in CATEGORIES else CATEGORIES[0]
    final_cat = c2.selectbox("Confirmed category", CATEGORIES, index=CATEGORIES.index(default_cat), key="cat_" + tid)
    approved = approval_block(tid, sop) if SOP_APPROVAL.get(sop) else True
    also = []
    if ticket["incident_id"]:
        linked = engine.query("SELECT ticket_id, requester, location, title FROM tickets WHERE incident_id=? AND ticket_id != ? "
                              "AND status IN ('Open','In Progress','Escalated') ORDER BY created_at", (ticket["incident_id"], tid))
        if len(linked):
            labels = dict(zip(linked["ticket_id"], linked["ticket_id"] + " | " + linked["requester"] + " | " + linked["location"]))
            also = st.multiselect("Also resolve these linked tickets with the same fix", list(labels), default=list(labels),
                                  format_func=labels.get, key="also_" + tid)
            st.caption("Keep only the tickets this fix covers. Removed tickets are unlinked and stay open. Each person "
                       "can reopen within %d days if the problem is still there." % engine.REOPEN_DAYS)
    notes = st.text_area("Resolution or escalation notes", key="notes_" + tid)
    b1, b2, b3, _ = st.columns([1.2, 1.6, 1.4, 3])
    if b1.button("Resolve ticket", type="primary", key="res_" + tid, disabled=not approved):
        sub = cls["subcategory"]["label"] if final_cat == cls["category"]["label"] else ""
        ok, res = attempt(engine.resolve_ticket, tid, sop, notes, ME, final_cat, sub, also)
        if ok:
            engine.record_decision(audit_id, "accepted" if sop in rec_ids else "overridden", ME["username"], sop)
            msg = "Resolved %d ticket(s): %s." % (len(res["resolved"]), ", ".join(res["resolved"]))
            if res["unlinked"]:
                msg += " Left open and unlinked: %s." % ", ".join(res["unlinked"])
            flash(msg)
            st.rerun()
    if b2.button("Recommendation did not fix it", key="unres_" + tid):
        engine.add_feedback(audit_id, tid, "recommendation", rec_ids[0] if rec_ids else "", "unresolved", ME["username"], notes)
        engine.record_decision(audit_id, "unresolved", ME["username"])
        ok, _ = attempt(engine.mark_unresolved, tid, ME, notes)
        if ok:
            flash("Marked unresolved. The ticket stays in progress.", "warning")
            st.rerun()
    if b3.button("Escalate to L3", key="esc_" + tid):
        engine.record_decision(audit_id, "escalated", ME["username"])
        ok, _ = attempt(engine.escalate_ticket, tid, notes or "Low confidence or unresolved", ME)
        if ok:
            flash("Ticket %s escalated to L3." % tid, "warning")
            st.rerun()


def ticket_detail(ticket):
    if not engine.can_view(ME, ticket):
        st.error("You do not have access to this ticket.")
        return
    tid = ticket["ticket_id"]
    a = assistant()
    analysis = a.analyze(ticket, include_restricted=PRIV, exclude_ids=[tid])
    audit_id = engine.ensure_audit(ticket, analysis, ME["username"])
    cls, gate, recs = analysis["classification"], analysis["confidence"], analysis["recommendations"]

    st.subheader("%s: %s" % (tid, ticket["title"]))
    if int(ticket["restricted"]):
        st.warning("Restricted ticket. Only analysts with restricted clearance can see it, and it is never used as "
                   "evidence for other analysts or grouped into incidents.")
    c = st.columns(6)
    c[0].metric("Priority", ticket["priority"])
    c[1].metric("Status", ticket["status"])
    c[2].metric("Impact", engine.IMPACT_LABELS.get(int(ticket["impact"]), "-"))
    c[3].metric("Urgency", ticket["urgency"])
    c[4].metric("Application", ticket["application"] or "-")
    c[5].metric("Location", ticket["location"] or "-")
    st.caption("Raised by %s on %s | Environment: %s | Group: %s | Priority basis: %s" % (
        ticket["requester"], ticket["created_at"], ticket["environment"], ticket["assignment_group"], ticket["priority_reason"]))
    sla = engine.sla_status(ticket)
    target = "Resolution target for %s: %d hours, due %s. Status: %s." % (
        ticket["priority"], engine.SLA_HOURS.get(ticket["priority"], 24), sla["due"], sla["label"])
    (st.error if sla["hours_left"] < 0 and not ticket["resolved_at"] else st.caption)(target)
    st.write(ticket["description"])
    rp = recurring()
    hit = rp[(rp["problem"] == ticket["subcategory"]) & (rp["application"] == ticket["application"]) & (rp["location"] == ticket["location"])]
    if len(hit):
        h = hit.iloc[0]
        st.warning("Recurring problem: this fault has occurred %d times at this site (%s the rate of other sites), about every "
                   "%.0f days, and %s has been applied %d times. Fix this ticket, then raise a problem record for a permanent fix."
                   % (h["occurrences"], h["vs_other_sites"], h["typical_gap_days"], h["usual_fix"], h["times_fixed"]))

    st.markdown("#### Classification")
    c = st.columns(3)
    c[0].metric("Category", cls["category"]["label"], "%.0f%% confidence" % (cls["category"]["confidence"] * 100), delta_color="off")
    c[1].metric("Subcategory", cls["subcategory"]["label"].split(" - ")[-1],
                "%.0f%% confidence" % (cls["subcategory"]["confidence"] * 100), delta_color="off")
    c[2].metric("Priority suggested by text", cls["priority"]["label"],
                "%.0f%% confidence" % (cls["priority"]["confidence"] * 100), delta_color="off")
    st.caption("Terms that drove the category: %s. Priority in force (%s) comes from the impact and urgency matrix; "
               "the text suggestion is only a cross-check." % (", ".join(cls["category"]["top_terms"]) or "none", ticket["priority"]))
    user_cat = ticket.get("user_category") or "Not sure"
    if user_cat not in ("Not sure", cls["category"]["label"]):
        st.warning("The user selected category \"%s\" but the model predicts \"%s\". Please confirm the category when resolving."
                   % (user_cat, cls["category"]["label"]))

    duplicates_section(ticket, a)

    st.markdown("#### Recommended resolution")
    if gate["status"] == "low":
        st.error("Low confidence. No recommendation is made; route this ticket to human review or escalate.\n\n- "
                 + "\n- ".join(gate["reasons"]))
        if recs:
            with st.expander("Unverified leads (weak evidence, for the reviewer only)"):
                for r in recs:
                    st.write("%s %s - %d similar tickets, %.0f%% of evidence" % (r["sop_id"], r["title"], r["support"], r["share"] * 100))
    else:
        msg = "Confidence: %s. Best match similarity %.2f." % (gate["status"], gate["top_similarity"])
        (st.success if gate["status"] == "high" else st.info)(msg + " Review the evidence before acting.")
        for i, rec in enumerate(recs):
            render_recommendation(i, rec, ticket, audit_id)

    st.markdown("#### Similar historical tickets")
    sim = analysis["similar"]
    if sim:
        st.dataframe(pd.DataFrame([{"Ticket": m["ticket_id"], "Title": m["title"], "Status": m["status"],
                                    "Text similarity": m["text_similarity"], "Score": m["score"],
                                    "Shared terms": ", ".join(m["shared_terms"]), "Excerpt": m["excerpt"],
                                    "Fix used": m["resolution_sop"]} for m in sim]), hide_index=True)
        c1, c2, c3 = st.columns([2, 2, 1])
        target = c1.selectbox("Rate a match", [m["ticket_id"] for m in sim], key="fm_" + tid)
        verdict = c2.radio("Verdict", ["useful", "incorrect"], horizontal=True, key="fv_" + tid)
        if c3.button("Save", key="fs_" + tid):
            engine.add_feedback(audit_id, tid, "match", target, verdict, ME["username"])
            st.toast("Feedback saved")
    else:
        st.caption("No similar ticket found.")
    with st.expander("Ticket history"):
        st.dataframe(engine.ticket_history(tid), hide_index=True)

    if ticket["status"] in engine.DONE_STATUSES:
        st.success("%s on %s with %s. %s" % (ticket["status"], ticket["resolved_at"], ticket["resolution_sop"], ticket["resolution_notes"]))
        return
    decision_section(ticket, cls, gate, recs, audit_id)


# =========================================================================== analyst tabs
def tab_queue():
    show_flash()
    c1, c2, c3 = st.columns(3)
    statuses = c1.multiselect("Status", list(engine.OPEN_STATUSES) + list(engine.DONE_STATUSES), default=list(engine.OPEN_STATUSES))
    cats = c2.multiselect("Category", CATEGORIES)
    pris = c3.multiselect("Priority", ["P1", "P2", "P3", "P4"])
    df = engine.query("SELECT ticket_id, priority, status, category, title, requester, application, location, incident_id, "
                      "dup_candidate AS suggested_duplicate_of, created_at, resolved_at, restricted FROM tickets "
                      "ORDER BY priority, created_at DESC")
    df = df[df["status"].isin(statuses or list(engine.OPEN_STATUSES))]
    if cats:
        df = df[df["category"].isin(cats)]
    if pris:
        df = df[df["priority"].isin(pris)]
    hidden = 0 if PRIV else int((df["restricted"] == 1).sum())
    if not PRIV:
        df = df[df["restricted"] == 0]
    df = df.head(300)
    df.insert(2, "sla", [engine.sla_status(r)["label"] for r in df.to_dict("records")])
    st.caption("%d tickets in view.%s" % (len(df), " %d restricted tickets are hidden; they need restricted clearance." % hidden if hidden else ""))
    st.dataframe(df.drop(columns=["restricted", "resolved_at"]), hide_index=True, height=260)
    if df.empty:
        return
    labels = dict(zip(df["ticket_id"], df["ticket_id"] + " | " + df["priority"] + " | " + df["title"]))
    tid = st.selectbox("Open a ticket", list(labels), format_func=labels.get)
    st.divider()
    ticket_detail(engine.get_ticket(tid))


def tab_search():
    q = st.text_input("Search by problem description (leave empty to list by filters only)")
    c = st.columns(5)
    f = {"application": c[0].multiselect("Application", distinct("application")),
         "category": c[1].multiselect("Category ", CATEGORIES),
         "status": c[2].multiselect("Status ", distinct("status")),
         "assignment_group": c[3].multiselect("Assignment group", distinct("assignment_group")),
         "environment": c[4].multiselect("Environment", distinct("environment"))}
    c1, c2 = st.columns(2)
    d1 = c1.date_input("Created from", value=None)
    d2 = c2.date_input("Created to", value=None)
    if d1:
        f["date_from"] = d1.strftime("%Y-%m-%d")
    if d2:
        f["date_to"] = d2.strftime("%Y-%m-%d")
    if q.strip():
        res = assistant().similar(q, filters=f, include_restricted=PRIV, top_k=30)
        df = pd.DataFrame([{"Ticket": m["ticket_id"], "Similarity": m["text_similarity"], "Title": m["title"],
                            "Status": m["status"], "Category": m["category"], "Application": m["application"],
                            "Environment": m["environment"], "Group": m["assignment_group"], "Created": m["created_at"],
                            "Shared terms": ", ".join(m["shared_terms"]), "Fix used": m["resolution_sop"]} for m in res])
    else:
        df = engine.load_tickets()
        if not PRIV:
            df = df[df["restricted"] == 0]
        for k in ("application", "category", "status", "assignment_group", "environment"):
            if f[k]:
                df = df[df[k].isin(f[k])]
        if "date_from" in f:
            df = df[df["created_at"] >= f["date_from"]]
        if "date_to" in f:
            df = df[df["created_at"] <= f["date_to"] + " 23:59:59"]
        df = df[["ticket_id", "title", "status", "category", "subcategory", "priority", "application", "environment",
                 "assignment_group", "created_at", "resolution_sop"]].sort_values("created_at", ascending=False).head(300)
    st.caption("%d results%s" % (len(df), "" if PRIV else " (restricted tickets excluded)"))
    st.dataframe(df, hide_index=True)


def tab_incidents():
    show_flash("inc")
    st.caption("An incident groups tickets from different people that an analyst has confirmed are the same problem. "
               "Access and hardware issues, and restricted tickets, are always handled one by one.")
    st.markdown("#### Duplicate suggestions waiting for an analyst")
    pend = engine.query("SELECT ticket_id, title, requester, location, application, dup_candidate AS suggested_duplicate_of, "
                        "dup_score AS similarity, created_at FROM tickets WHERE dup_candidate != '' AND restricted = 0 "
                        "AND status IN ('Open','In Progress','Escalated') ORDER BY dup_candidate, created_at")
    if pend.empty:
        st.caption("None.")
    else:
        st.dataframe(pend, hide_index=True)
        pick = st.multiselect("Select suggestions to act on", list(pend["ticket_id"]), key="pend_pick")
        c1, c2, _ = st.columns([1.3, 1.3, 4])
        for label, fn, verb in (("Confirm selected", engine.confirm_duplicate, "Linked"),
                                ("Reject selected", engine.reject_duplicate, "Rejected")):
            if (c1 if verb == "Linked" else c2).button(label, disabled=not pick):
                done = [tid for tid in pick if attempt(fn, tid, ME)[0]]
                if done and len(done) == len(pick):
                    flash("%s: %s" % (verb, ", ".join(done)), slot="inc")
                    st.rerun()
                elif done:
                    st.success("%s: %s" % (verb, ", ".join(done)))
    st.markdown("#### Incidents")
    inc = engine.query(
        "SELECT i.incident_id, i.status, i.priority, i.effective_impact, i.title, i.parent_ticket_id, i.created_by, i.created_at, "
        "i.resolved_at, (SELECT COUNT(*) FROM tickets t WHERE t.incident_id = i.incident_id) AS linked_tickets, "
        "(SELECT COUNT(*) FROM ticket_events e WHERE e.event = 'Reopened by user' AND e.detail LIKE '%' || i.incident_id || '%') "
        "AS reopened_after_fix FROM incidents i ORDER BY CASE i.status WHEN 'Open' THEN 0 ELSE 1 END, i.created_at DESC")
    if inc.empty:
        st.info("No incidents yet.")
        return
    inc["effective_impact"] = inc["effective_impact"].map(engine.IMPACT_LABELS)
    st.dataframe(inc, hide_index=True)
    st.caption("reopened_after_fix counts people who said the incident fix did not work for them; a high number means "
               "tickets were grouped that should not have been.")
    pick = st.selectbox("Show linked tickets for", list(inc["incident_id"]))
    linked = engine.query("SELECT ticket_id, title, requester, location, priority, status, linked_by, linked_at, restricted "
                          "FROM tickets WHERE incident_id=? ORDER BY created_at", (pick,))
    if not PRIV:
        linked = linked[linked["restricted"] == 0]
    st.dataframe(linked.drop(columns=["restricted"]), hide_index=True)


def tab_approvals():
    show_flash("appr")
    ap = engine.list_approvals()
    ap["title"] = [t if (r == 0 or PRIV) else "[restricted ticket]" for t, r in zip(ap["title"], ap["restricted"])]
    if ROLE == "approver":
        st.markdown("#### Change requests waiting for your decision")
        pending = ap[ap["status"] == "Pending"]
        if pending.empty:
            st.info("Nothing waiting.")
        for _, r in pending.iterrows():
            aid = int(r["approval_id"])
            with st.container(border=True):
                st.write("**#%d | %s %s** for ticket %s (%s): %s" % (aid, r["sop_id"], SOP_TITLE.get(r["sop_id"], ""),
                                                                    r["ticket_id"], r["priority"], r["title"]))
                st.caption("Requested by %s at %s" % (r["requested_by"], r["requested_at"]))
                st.write("**Plan:** " + r["justification"])
                with st.expander("Procedure steps"):
                    st.markdown("\n".join("%d. %s" % (i + 1, s.strip()) for i, s in enumerate(SOP_STEPS.get(r["sop_id"], "").split("|"))))
                comment = st.text_input("Comment (required to reject)", key="ac_%d" % aid)
                c1, c2, _ = st.columns([1, 1, 4])
                for col, decision in ((c1, "Approved"), (c2, "Rejected")):
                    if col.button("Approve" if decision == "Approved" else "Reject", key="ad_%s_%d" % (decision, aid),
                                  type="primary" if decision == "Approved" else "secondary"):
                        ok, _ = attempt(engine.decide_approval, aid, decision, comment, ME)
                        if ok:
                            flash("Change request #%d %s." % (aid, decision.lower()), slot="appr")
                            st.rerun()
    st.markdown("#### All change requests")
    st.dataframe(ap.drop(columns=["restricted"]), hide_index=True)
    st.caption("Approvals are valid for %d hours. Nobody can approve a change they requested, and the ticket cannot be "
               "resolved with a high-risk procedure until an approver has approved it." % engine.APPROVAL_VALID_HOURS)


def tab_sops():
    cat = st.selectbox("Category", ["All"] + sorted(SOPS["category"].unique()))
    for _, s in SOPS.iterrows():
        if cat not in ("All", s["category"]):
            continue
        with st.expander("%s %s | risk %s%s" % (s["sop_id"], s["title"], s["risk_level"],
                                                 " | approval required" if int(s["requires_approval"]) else "")):
            st.markdown("\n".join("%d. %s" % (i + 1, x.strip()) for i, x in enumerate(s["steps"].split("|"))))


def tab_insights():
    t = engine.load_tickets()
    if not PRIV:
        t = t[t["restricted"] == 0]
    st.caption("What the ticket history shows. All figures are computed from the tickets table%s."
               % ("" if PRIV else ", excluding restricted tickets"))
    st.markdown("#### Recurring problems: candidates for a permanent fix")
    rp = recurring()
    if rp.empty:
        st.info("No recurring problem detected.")
    else:
        st.dataframe(rp, hide_index=True)
        st.caption("A fault is listed when it occurred on at least 8 separate days on the same service and site, at 1.8 times "
                   "or more the rate of other sites. Analyst hours spent is the handling time already used on repeat fixes.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### SLA compliance by priority")
        st.dataframe(engine.sla_compliance(t), hide_index=True)
    with c2:
        st.markdown("#### SOPs that most often fail at the first attempt")
        eff = engine.sop_effectiveness(t)
        eff = eff[eff["uses"] >= 10].sort_values("first_time_fix_rate").head(8)
        eff.insert(1, "title", eff["sop_id"].map(SOP_TITLE))
        eff["first_time_fix_rate"] = (eff["first_time_fix_rate"] * 100).round(0).astype(int).astype(str) + "%"
        st.dataframe(eff[["sop_id", "title", "uses", "failed_first_attempt", "first_time_fix_rate"]], hide_index=True)
    created = pd.to_datetime(t["created_at"])
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Tickets by day of week")
        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        by_day = created.dt.day_name().value_counts().reindex(order).fillna(0)
        by_day.index = ["%d %s" % (i + 1, d[:3]) for i, d in enumerate(order)]
        st.bar_chart(by_day)
    with c2:
        st.markdown("#### Weekly ticket volume by category")
        week = created.dt.to_period("W").dt.start_time.dt.strftime("%Y-%m-%d")
        st.line_chart(t.assign(week=week).pivot_table(index="week", columns="category", values="ticket_id", aggfunc="count").fillna(0))
    st.markdown("#### Busiest services in the last 30 days")
    recent = t[created >= created.max() - pd.Timedelta(days=30)]
    hot = recent.groupby(["application", "category"]).size().reset_index(name="tickets").sort_values("tickets", ascending=False).head(10)
    st.dataframe(hot, hide_index=True)


def tab_audit():
    df = engine.export_audit(include_restricted=PRIV)
    st.caption("%d audit records%s. Each record keeps the input, classification, evidence, recommendation and reviewer decision."
               % (len(df), "" if PRIV else ". Records about, or citing, restricted tickets are excluded from view and export"))
    st.dataframe(df.drop(columns=["input_json", "classification_json", "evidence_json", "recommendation_json"]), hide_index=True)
    st.download_button("Download audit log (CSV)", df.to_csv(index=False), "audit_log.csv", "text/csv")
    if len(df):
        pick = st.selectbox("Inspect a record", list(df["audit_id"]))
        row = df[df["audit_id"] == pick].iloc[0]
        c1, c2 = st.columns(2)
        for col, label, key in ((c1, "Input", "input_json"), (c1, "Classification", "classification_json"),
                                (c2, "Evidence", "evidence_json"), (c2, "Recommendation", "recommendation_json")):
            col.write(label)
            col.json(json.loads(row[key]), expanded=False)
    st.markdown("#### Analyst feedback")
    restricted_filter = "" if PRIV else ("WHERE t.restricted = 0 AND f.target_id NOT IN "
                                         "(SELECT ticket_id FROM tickets WHERE restricted = 1)")
    fb = engine.query("SELECT f.* FROM feedback f JOIN tickets t ON t.ticket_id = f.ticket_id %s ORDER BY feedback_id DESC"
                      % restricted_filter)
    if fb.empty:
        st.caption("No feedback recorded yet.")
    else:
        st.dataframe(fb, hide_index=True)
        st.bar_chart(fb["verdict"].value_counts())


def tab_mttr():
    m = engine.mttr_summary()
    st.markdown("#### Manual baseline (historical tickets, generated handling times)")
    c = st.columns(3)
    c[0].metric("Baseline MTTR", "%.0f min" % m["baseline_mttr"])
    c[1].metric("Of which triage and lookup", "%.0f min" % m["baseline_triage_lookup"])
    c[2].metric("Tickets in baseline", m["baseline_count"])
    st.bar_chart(pd.Series(m["baseline_by_priority"], name="Baseline MTTR (minutes)"))
    st.markdown("#### Tickets resolved in this app (real timestamps)")
    if m["live_count"]:
        c = st.columns(3)
        c[0].metric("Tickets resolved with the assistant", m["live_count"])
        c[1].metric("MTTR, creation to resolution", "%.1f min" % m["live_mttr"])
        if m["live_time_to_recommendation"] is not None:
            c[2].metric("Creation to recommendation", "%.1f sec" % m["live_time_to_recommendation"])
        st.caption("Tickets that were already waiting in the seeded queue include their waiting time, so this figure is only "
                   "comparable with the baseline for tickets raised through the portal.")
    else:
        st.info("Resolve a ticket in the Queue tab to start measuring.")
    path = os.path.join(engine.BASE, "evaluation_results.json")
    st.markdown("#### Offline evaluation (held-out test split)")
    if not os.path.exists(path):
        st.info("Run `python evaluate.py` to produce the evaluation results.")
        return
    r = json.load(open(path))
    s = r["mttr"]
    c = st.columns(4)
    c[0].metric("Simulated MTTR, manual", "%.0f min" % s["baseline_mttr_minutes"])
    c[1].metric("Simulated MTTR, assisted", "%.0f min" % s["assisted_mttr_minutes"], "-%.0f%%" % (100 * s["mttr_reduction"]), delta_color="inverse")
    c[2].metric("Handling time, manual", "%.0f min" % s["baseline_handling_minutes"])
    c[3].metric("Handling time, assisted", "%.0f min" % s["assisted_handling_minutes"], "-%.0f%%" % (100 * s["handling_reduction"]), delta_color="inverse")
    st.caption("Simulated: a correct recommendation replaces triage and lookup with a %d-minute review; a wrong one costs %d extra minutes."
               % (s["assumptions"]["review_minutes"], s["assumptions"]["false_lead_minutes"]))
    c = st.columns(4)
    c[0].metric("Category accuracy", "%.1f%%" % (100 * r["classification"]["category"]["accuracy"]))
    c[1].metric("Subcategory accuracy", "%.1f%%" % (100 * r["classification"]["subcategory"]["accuracy"]))
    c[2].metric("Duplicate in top 5", "%.1f%%" % (100 * r["retrieval"]["hit_at_5"]))
    c[3].metric("Correct fix in top 3", "%.1f%%" % (100 * r["recommendation"]["top3_when_shown"]))
    st.caption("Full details, confusion matrices and observed errors are in EVALUATION_REPORT.md.")


# =========================================================================== routing by role
def run_tabs(spec):
    tabs = st.tabs([name for name, _ in spec])
    for tab, (_, fn) in zip(tabs, spec):
        with tab:
            fn()


if ROLE == "user":
    user_portal()
elif ROLE == "analyst":
    st.header("Analyst console")
    run_tabs([("Queue and analysis", tab_queue), ("Search", tab_search), ("Incidents", tab_incidents),
              ("Insights", tab_insights), ("Change requests", tab_approvals), ("SOP library", tab_sops),
              ("Audit log", tab_audit), ("MTTR and evaluation", tab_mttr)])
elif ROLE == "approver":
    st.header("Change approvals")
    run_tabs([("Change requests", tab_approvals), ("SOP library", tab_sops), ("Audit log", tab_audit)])
else:
    st.error("Your account has no role in this application.")
