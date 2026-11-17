"""Core logic for the IT Ticket Analysis and Resolution Assistant.

Storage  : one SQLite file (data/tickets.db) with tables tickets, incidents, sops, users, approvals,
           ticket_events, audit_log and feedback.
Access   : every action takes the signed-in actor and checks role and restricted clearance here, not only in the UI.
Analysis : TF-IDF + logistic regression classification, cosine-similarity retrieval,
           duplicate linking into incidents, SOP recommendation with evidence, and a confidence gate.

The assistant only analyses and recommends. It never closes a ticket or changes a system by itself.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics.pairwise import linear_kernel

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
DB_PATH = os.environ.get("TICKET_DB", os.path.join(DATA_DIR, "tickets.db"))
FMT = "%Y-%m-%d %H:%M:%S"

IMPACT_LABELS = {1: "Just me", 2: "My team", 3: "My department or site", 4: "Whole organisation"}
URGENCY_LABELS = {"Blocked": "Work is blocked", "Degraded": "Work is slowed", "Minor": "Minor inconvenience"}
PRIORITY_MATRIX = {("Blocked", 4): "P1", ("Blocked", 3): "P1", ("Blocked", 2): "P2", ("Blocked", 1): "P3",
                   ("Degraded", 4): "P1", ("Degraded", 3): "P2", ("Degraded", 2): "P3", ("Degraded", 1): "P3",
                   ("Minor", 4): "P2", ("Minor", 3): "P3", ("Minor", 2): "P4", ("Minor", 1): "P4"}
URGENCY_RANK = {"Blocked": 3, "Degraded": 2, "Minor": 1}
SLA_HOURS = {"P1": 4, "P2": 8, "P3": 24, "P4": 72}      # resolution target per priority
PER_USER_CATEGORIES = ("Access", "Hardware")            # one-person issues; not candidates for problem records
OPEN_STATUSES = ("Open", "In Progress", "Escalated")
DONE_STATUSES = ("Resolved", "Closed")

# Thresholds (tuned on the held-out split by evaluate.py; see EVALUATION_REPORT.md)
CAT_CONF_MIN = 0.50      # below this the category prediction is treated as low confidence
SIM_MIN = 0.30           # a resolved ticket must be at least this similar to count as evidence
SOP_SHARE_MIN = 0.50     # the leading SOP must hold at least this share of the evidence weight
HIGH_SIM = 0.45          # top similarity needed for a "high" confidence result
DUP_SIM = 0.25           # text similarity for a duplicate SUGGESTION; low on purpose because an analyst confirms
                         # every link (catches 91% of real duplicates; see EVALUATION_REPORT.md section 3.2)
DUP_WINDOW_HOURS = 48
TOP_K_EVIDENCE = 8
MIN_EVIDENCE = 2         # at least this many similar resolved tickets must support a recommendation
VOCAB_COVERAGE_MIN = 0.5 # share of the ticket's words that must have been seen in past tickets
INCIDENT_IMPACT_STEPS = [(15, 4), (6, 3), (3, 2)]   # linked tickets -> minimum impact level


def now_str():
    return datetime.now().strftime(FMT)


def clean_text(text):
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def priority_for(urgency, impact):
    return PRIORITY_MATRIX.get((urgency, int(impact)), "P3")


# --------------------------------------------------------------------------- learning from the history
def sop_effectiveness(tickets):
    """How often each SOP fixed the problem at the first attempt, from the resolution history."""
    t = tickets
    first = t["first_attempt_sop"].fillna("") if "first_attempt_sop" in t else pd.Series("", index=t.index)
    reopened = pd.to_numeric(t["reopen_count"], errors="coerce").fillna(0) if "reopen_count" in t else pd.Series(0, index=t.index)
    final = t["resolution_sop"].fillna("")
    worked = final[(final != "") & (reopened == 0)].value_counts()
    failed = first[first != ""].value_counts()
    rows = []
    for sop in sorted(set(worked.index) | set(failed.index)):
        w, f = int(worked.get(sop, 0)), int(failed.get(sop, 0))
        rows.append({"sop_id": sop, "uses": w + f, "fixed_first_time": w, "failed_first_attempt": f,
                     "first_time_fix_rate": round(w / float(w + f), 3) if (w + f) else None})
    return pd.DataFrame(rows, columns=["sop_id", "uses", "fixed_first_time", "failed_first_attempt", "first_time_fix_rate"])


def recurring_problems(tickets, min_occurrences=8, min_lift=1.8):
    """The same fault returning on the same service and site far more often than at other sites.

    These are candidates for a permanent fix (a problem record) instead of repeating the same SOP.
    """
    cols = ["problem", "application", "location", "occurrences", "vs_other_sites", "typical_gap_days", "last_seen",
            "usual_fix", "times_fixed", "analyst_hours_spent"]
    t = tickets[~tickets["category"].isin(PER_USER_CATEGORIES)].copy()
    t["day"] = t["created_at"].str[:10]
    # several users reporting one incident on one day count as one occurrence
    days = t.drop_duplicates(["subcategory", "application", "location", "day"])
    n_sites = max(t["location"].nunique(), 2)
    counts = days.groupby(["subcategory", "application", "location"]).size()
    totals = days.groupby(["subcategory", "application"]).size()
    rows = []
    for (sub, app, loc), n in counts.items():
        others = (totals[(sub, app)] - n) / float(n_sites - 1)
        lift = n / max(others, 0.5)
        if n < min_occurrences or lift < min_lift:
            continue
        g = t[(t["subcategory"] == sub) & (t["application"] == app) & (t["location"] == loc)]
        d = pd.to_datetime(g["day"].drop_duplicates()).sort_values()
        fixes = g.loc[g["resolution_sop"].fillna("") != "", "resolution_sop"]
        mins = sum(pd.to_numeric(g[c], errors="coerce").fillna(0) for c in ("triage_minutes", "lookup_minutes", "fix_minutes"))
        usual = fixes.mode().iloc[0] if len(fixes) else ""
        rows.append({"problem": sub, "application": app, "location": loc, "occurrences": int(n),
                     "vs_other_sites": "%.1fx" % lift, "typical_gap_days": round(float(d.diff().dt.days.median()), 1),
                     "last_seen": str(d.iloc[-1].date()), "usual_fix": usual, "times_fixed": int((fixes == usual).sum()),
                     "analyst_hours_spent": round(float(mins.sum()) / 60.0, 1)})
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values("occurrences", ascending=False).reset_index(drop=True)


def sla_compliance(tickets):
    """Share of resolved tickets that met the resolution target for their priority."""
    t = tickets[tickets["resolved_at"].fillna("") != ""].copy()
    t["hours"] = (pd.to_datetime(t["resolved_at"]) - pd.to_datetime(t["created_at"])).dt.total_seconds() / 3600.0
    t["target"] = t["priority"].map(SLA_HOURS)
    t["met"] = t["hours"] <= t["target"]
    g = t.groupby("priority").agg(resolved=("met", "size"), met=("met", "sum"), avg_hours=("hours", "mean")).reset_index()
    g["target_hours"] = g["priority"].map(SLA_HOURS)
    g["met_pct"] = (100.0 * g["met"] / g["resolved"]).round(1)
    g["avg_hours"] = g["avg_hours"].round(1)
    return g[["priority", "target_hours", "resolved", "met_pct", "avg_hours"]]


def sla_status(ticket, now=None):
    """Due time and state of the resolution target for one ticket."""
    created = datetime.strptime(ticket["created_at"], FMT)
    due = created + timedelta(hours=SLA_HOURS.get(ticket["priority"], 24))
    end = datetime.strptime(ticket["resolved_at"], FMT) if ticket.get("resolved_at") else (now or datetime.now())
    left = (due - end).total_seconds() / 3600.0
    if ticket.get("resolved_at"):
        label = "Met" if left >= 0 else "Breached by %.1f h" % -left
    else:
        label = "Due in %.1f h" % left if left >= 0 else "BREACHED %.1f h ago" % -left
    return {"due": due.strftime(FMT), "hours_left": round(left, 1), "label": label}


# --------------------------------------------------------------------------- analysis
class Assistant:
    """Classifier + retriever + recommender built from a ticket table and an SOP table."""

    def __init__(self, tickets, sops):
        self.sops = sops.set_index("sop_id").to_dict("index")
        t = tickets.copy().reset_index(drop=True)
        for col in ("title", "description", "resolution_sop", "resolution_notes", "application", "location"):
            t[col] = t[col].fillna("")
        t["text"] = (t["title"] + ". " + t["description"]).map(clean_text)
        self.tickets = t
        self.vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True, stop_words="english")
        self.M = self.vec.fit_transform(t["text"])
        self.terms = np.array(self.vec.get_feature_names_out())
        self._analyzer = TfidfVectorizer(stop_words="english").build_analyzer()   # single words, stop words removed
        labelled = t[t["category"].fillna("") != ""]
        idx = labelled.index.values
        self.models = {}
        for field in ("category", "subcategory", "priority"):
            clf = LogisticRegression(max_iter=3000, C=10.0)
            clf.fit(self.M[idx], labelled[field].values)
            self.models[field] = clf
        self.is_done = (t["status"].isin(DONE_STATUSES) & (t["resolution_sop"] != "")).values
        self.sop_stats = sop_effectiveness(t).set_index("sop_id").to_dict("index")

    # ---- FR-01 classification
    def classify(self, text):
        q = self.vec.transform([clean_text(text)])
        out = {}
        for field, clf in self.models.items():
            p = clf.predict_proba(q)[0]
            classes = clf.classes_
            if field == "subcategory" and "category" in out:
                # keep the subcategory consistent with the predicted category
                keep = np.array([c.startswith(out["category"]["label"] + " - ") for c in classes])
                if keep.any() and p[keep].sum() > 0:
                    p = np.where(keep, p, 0.0)
                    p = p / p.sum()
            order = np.argsort(p)[::-1]
            best = int(order[0])
            contrib = q.multiply(clf.coef_[best]).toarray().ravel()
            top = [self.terms[i] for i in np.argsort(contrib)[::-1][:5] if contrib[i] > 0]
            out[field] = {"label": str(classes[best]), "confidence": round(float(p[best]), 3),
                          "alternatives": [{"label": str(classes[i]), "confidence": round(float(p[i]), 3)} for i in order[1:3]],
                          "top_terms": top}
        return out

    # ---- FR-02 / FR-07 similarity search with filters
    def similar(self, text, application=None, category=None, filters=None, include_restricted=False,
                top_k=10, resolved_only=False, exclude_ids=None, min_text_sim=0.05, location=None):
        t = self.tickets
        q = self.vec.transform([clean_text(text)])
        cos = linear_kernel(q, self.M).ravel()
        mask = cos >= min_text_sim
        if not include_restricted:
            mask &= (t["restricted"].values == 0)
        if resolved_only:
            mask &= self.is_done
        if exclude_ids:
            mask &= ~t["ticket_id"].isin(list(exclude_ids)).values
        for key, val in (filters or {}).items():
            if val in (None, "", [], "All"):
                continue
            if key == "date_from":
                mask &= (t["created_at"].values >= str(val))
            elif key == "date_to":
                mask &= (t["created_at"].values <= str(val) + " 23:59:59")
            elif isinstance(val, (list, tuple, set)):
                mask &= t[key].isin(list(val)).values
            else:
                mask &= (t[key].values == val)
        boost = np.zeros(len(t))
        if category:
            boost += 0.10 * (t["category"].values == category)
        if application:
            boost += 0.05 * (t["application"].values == application)
        if location:
            boost += 0.05 * (t["location"].values == location)
        score = 0.80 * cos + boost
        cand = np.where(mask)[0]
        order = cand[np.argsort(-score[cand])][:top_k]
        qv = q.toarray().ravel()
        results = []
        for i in order:
            row = t.iloc[i]
            prod = qv * self.M[i].toarray().ravel()
            shared = [self.terms[j] for j in np.argsort(prod)[::-1][:6] if prod[j] > 0]
            results.append({
                "ticket_id": row["ticket_id"], "title": row["title"], "status": row["status"],
                "category": row["category"], "subcategory": row["subcategory"], "priority": row["priority"],
                "application": row["application"], "location": row["location"], "environment": row["environment"],
                "assignment_group": row["assignment_group"], "created_at": row["created_at"],
                "text_similarity": round(float(cos[i]), 3), "score": round(float(score[i]), 3),
                "shared_terms": shared, "excerpt": self._excerpt(row["description"], shared),
                "resolution_sop": row["resolution_sop"], "resolution_notes": row["resolution_notes"],
                "dup_group": row.get("dup_group", "") or "",
                "restricted": int(row["restricted"]),
            })
        return results

    @staticmethod
    def _excerpt(description, shared):
        words = set(w for term in shared for w in term.split())
        best, best_n = "", -1
        for s in re.split(r"(?<=[.!?])\s+", str(description)):
            n = sum(1 for w in re.findall(r"\w+", s.lower()) if w in words)
            if n > best_n:
                best, best_n = s, n
        return best[:240]

    # ---- FR-03 / FR-04 recommendation with evidence
    def recommend(self, resolved_matches):
        evidence = [m for m in resolved_matches if m["text_similarity"] >= SIM_MIN][:TOP_K_EVIDENCE]
        total = sum(m["text_similarity"] for m in evidence)
        groups = {}
        for m in evidence:
            groups.setdefault(m["resolution_sop"], []).append(m)
        recs = []
        for sop_id, items in groups.items():
            sop = self.sops.get(sop_id, {})
            weight = sum(m["text_similarity"] for m in items)
            recs.append({
                "sop_id": sop_id, "title": sop.get("title", sop_id), "risk_level": sop.get("risk_level", ""),
                "requires_approval": int(sop.get("requires_approval", 0) or 0),
                "steps": [s.strip() for s in str(sop.get("steps", "")).split("|") if s.strip()],
                "support": len(items), "evidence_total": len(evidence),
                "share": round(weight / total, 3) if total else 0.0,
                "reason": "%d of the %d most similar resolved tickets were fixed with this procedure." % (len(items), len(evidence)),
                "uses": int(self.sop_stats.get(sop_id, {}).get("uses", 0)),
                "first_time_fix_rate": self.sop_stats.get(sop_id, {}).get("first_time_fix_rate"),
                "evidence": [{k: m[k] for k in ("ticket_id", "title", "text_similarity", "shared_terms", "excerpt", "resolution_notes")}
                             for m in items[:3]],
            })
        recs.sort(key=lambda r: -r["share"])
        return recs[:3]

    # ---- FR-05 confidence and fallback
    @staticmethod
    def gate(classification, resolved_matches, recs, coverage=1.0):
        reasons = []
        if coverage < VOCAB_COVERAGE_MIN:
            reasons.append("Only %.0f%% of the ticket's wording has been seen in past tickets; it may be a new or out-of-scope issue."
                           % (coverage * 100))
        cat_conf = classification["category"]["confidence"]
        top_sim = max([m["text_similarity"] for m in resolved_matches], default=0.0)
        if cat_conf < CAT_CONF_MIN:
            reasons.append("Low classification confidence (%.0f%%)." % (cat_conf * 100))
        if top_sim < SIM_MIN or not recs:
            reasons.append("No sufficiently similar resolved ticket (best match %.2f, needs %.2f)." % (top_sim, SIM_MIN))
        elif recs[0]["evidence_total"] < MIN_EVIDENCE:
            reasons.append("Only one similar resolved ticket was found; that is too little evidence.")
        elif recs[0]["share"] < SOP_SHARE_MIN:
            reasons.append("Similar tickets were fixed in conflicting ways (leading procedure has only %.0f%% of the evidence)."
                           % (recs[0]["share"] * 100))
        if recs and top_sim >= SIM_MIN:
            match_cats = [m["category"] for m in resolved_matches if m["text_similarity"] >= SIM_MIN][:TOP_K_EVIDENCE]
            agree = match_cats.count(classification["category"]["label"]) / float(len(match_cats))
            if agree < 0.5:
                reasons.append("Predicted category disagrees with the categories of the most similar tickets.")
        if reasons:
            status = "low"
        elif top_sim >= HIGH_SIM and recs[0]["share"] >= 0.7 and cat_conf >= 0.7:
            status = "high"
        else:
            status = "medium"
        return {"status": status, "reasons": reasons, "top_similarity": round(top_sim, 3), "vocabulary_coverage": round(coverage, 2),
                "action": "Route to human review or escalate" if status == "low" else "Analyst to review the recommendation"}

    def analyze(self, ticket, include_restricted=False, exclude_ids=None):
        text = "%s. %s" % (ticket.get("title", ""), ticket.get("description", ""))
        cls = self.classify(text)
        cat = cls["category"]["label"]
        app = ticket.get("application") or None
        loc = ticket.get("location") or None
        similar = self.similar(text, app, cat, include_restricted=include_restricted, top_k=8, exclude_ids=exclude_ids,
                               location=loc)
        resolved = self.similar(text, app, cat, include_restricted=include_restricted, top_k=TOP_K_EVIDENCE,
                                resolved_only=True, exclude_ids=exclude_ids, location=loc)
        recs = self.recommend(resolved)
        words = [w for w in self._analyzer(clean_text(text))]
        coverage = sum(1 for w in words if w in self.vec.vocabulary_) / float(len(words)) if words else 0.0
        gate = self.gate(cls, resolved, recs, coverage)
        return {"classification": cls, "similar": similar, "resolved_matches": resolved,
                "recommendations": recs, "confidence": gate}


# --------------------------------------------------------------------------- storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id TEXT PRIMARY KEY, source TEXT, title TEXT, description TEXT, requester TEXT, requester_username TEXT,
    application TEXT, environment TEXT, location TEXT, user_category TEXT, category TEXT, subcategory TEXT,
    impact INTEGER, urgency TEXT, priority TEXT, priority_reason TEXT, status TEXT, assignment_group TEXT,
    created_at TEXT, recommended_at TEXT, resolved_at TEXT, resolution_sop TEXT, resolution_notes TEXT,
    restricted INTEGER DEFAULT 0, incident_id TEXT, linked_by TEXT, linked_at TEXT, dup_candidate TEXT, dup_score REAL,
    dup_group TEXT, wait_minutes REAL, triage_minutes REAL, lookup_minutes REAL, fix_minutes REAL,
    first_attempt_sop TEXT, reopen_count INTEGER DEFAULT 0, label_source TEXT);
CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY, parent_ticket_id TEXT, title TEXT, category TEXT, subcategory TEXT, status TEXT,
    created_at TEXT, created_by TEXT, resolved_at TEXT, effective_impact INTEGER, priority TEXT);
CREATE TABLE IF NOT EXISTS sops (
    sop_id TEXT PRIMARY KEY, title TEXT, category TEXT, risk_level TEXT, requires_approval INTEGER, steps TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, display_name TEXT, role TEXT, restricted_clearance INTEGER DEFAULT 0, location TEXT,
    salt TEXT, password_hash TEXT);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, sop_id TEXT, justification TEXT, requested_by TEXT,
    requested_at TEXT, status TEXT, decided_by TEXT, decided_at TEXT, decision_comment TEXT);
CREATE TABLE IF NOT EXISTS ticket_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, at TEXT, actor TEXT, event TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, analysed_at TEXT, analysed_by TEXT, input_json TEXT,
    classification_json TEXT, evidence_json TEXT, recommendation_json TEXT, confidence_status TEXT,
    fallback_reason TEXT, contains_restricted INTEGER DEFAULT 0, reviewer_decision TEXT, reviewer TEXT,
    decided_at TEXT, chosen_sop TEXT);
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT, audit_id INTEGER, ticket_id TEXT, target_type TEXT,
    target_id TEXT, verdict TEXT, comment TEXT, reviewer TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_incident ON tickets(incident_id);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON ticket_events(ticket_id);
"""

HISTORY = "history import"
REOPEN_DAYS = 3                # a user can reopen a resolved ticket for this long; after that it closes
APPROVAL_VALID_HOURS = 24      # an approved change request must be used within this time
ROLE_LABELS = {"user": "Employee", "analyst": "Service desk analyst", "approver": "Change approver"}
DEMO_PASSWORD = "demo123"
DEMO_USERS = [
    # username, display name, role, restricted clearance, home location
    ("priya", "Priya Sharma", "user", 0, "Hyderabad"),
    ("rahul", "Rahul Verma", "user", 0, "Pune"),
    ("analyst1", "Anil Kumar", "analyst", 0, ""),
    ("lead1", "Leela Menon", "analyst", 1, ""),
    ("cab1", "Chandra Rao", "approver", 0, ""),
]


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql, params=()):
    conn = connect()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


def execute(sql, params=()):
    conn = connect()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _hash_password(password, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 100000).hex()


def init_db():
    """Create the tables and, on first run, load the seed CSV files and demo accounts."""
    conn = connect()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tickets)").fetchall()]
        if cols and "requester_username" not in cols:
            # database from an earlier version of the prototype: rebuild it from the seed files
            for table in ("tickets", "incidents", "sops", "audit_log", "feedback", "users", "approvals", "ticket_events"):
                conn.execute("DROP TABLE IF EXISTS %s" % table)
            conn.commit()
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            for username, name, role, clearance, loc in DEMO_USERS:
                salt = secrets.token_hex(16)
                conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                             (username, name, role, clearance, loc, salt, _hash_password(DEMO_PASSWORD, salt)))
            conn.commit()
        seeded = False
        if conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0:
            t = pd.read_csv(os.path.join(DATA_DIR, "tickets.csv"), dtype=str, keep_default_na=False)
            t["source"] = "historical"
            t["priority_reason"] = "Impact and urgency matrix"
            for col in ("recommended_at", "incident_id", "requester_username", "linked_by", "linked_at", "dup_candidate"):
                t[col] = ""
            for col in ("impact", "restricted", "reopen_count"):
                t[col] = t[col].astype(int)
            for col in ("wait_minutes", "triage_minutes", "lookup_minutes", "fix_minutes"):
                t[col] = pd.to_numeric(t[col], errors="coerce")
            t.to_sql("tickets", conn, if_exists="append", index=False)
            pd.read_csv(os.path.join(DATA_DIR, "sops.csv"), dtype=str, keep_default_na=False).to_sql(
                "sops", conn, if_exists="append", index=False)
            conn.commit()
            seeded = True
    finally:
        conn.close()
    if seeded:
        # open tickets that the history already records as one event form one incident
        groups = query("SELECT dup_group, ticket_id FROM tickets WHERE dup_group != '' AND status IN ('Open','In Progress') "
                       "AND restricted = 0 ORDER BY created_at")
        for _, ids in groups.groupby("dup_group")["ticket_id"]:
            ids = list(ids)
            inc = _create_incident(ids[0], HISTORY)
            for tid in ids[1:]:
                execute("UPDATE tickets SET incident_id=?, linked_by=?, linked_at=? WHERE ticket_id=?", (inc, HISTORY, now_str(), tid))
            refresh_incident(inc, HISTORY)


def load_tickets():
    return query("SELECT * FROM tickets ORDER BY created_at")


def load_sops():
    return query("SELECT * FROM sops ORDER BY sop_id")


def build_assistant():
    return Assistant(load_tickets(), load_sops())


def get_ticket(ticket_id):
    df = query("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
    return df.iloc[0].to_dict() if len(df) else None


def _next_id(table, col, prefix, width):
    df = query("SELECT %s AS v FROM %s WHERE %s LIKE ?" % (col, table, col), (prefix + "%",))
    nums = [int(v[len(prefix):]) for v in df["v"] if v[len(prefix):].isdigit()]
    return prefix + str(max(nums + [0]) + 1).zfill(width)


def log_event(ticket_id, actor_name, event, detail=""):
    execute("INSERT INTO ticket_events (ticket_id, at, actor, event, detail) VALUES (?,?,?,?,?)",
            (ticket_id, now_str(), actor_name, event, detail))


def ticket_history(ticket_id):
    return query("SELECT at, actor, event, detail FROM ticket_events WHERE ticket_id=? ORDER BY event_id", (ticket_id,))


# ---- access control
class PermissionDenied(Exception):
    """The signed-in person is not allowed to do this."""


def authenticate(username, password):
    df = query("SELECT * FROM users WHERE username=?", ((username or "").strip().lower(),))
    if df.empty:
        _hash_password(password or "", "00" * 16)     # same work either way, so timing does not reveal valid names
        return None
    u = df.iloc[0].to_dict()
    if not hmac.compare_digest(_hash_password(password or "", u["salt"]), u["password_hash"]):
        return None
    return {"username": u["username"], "display_name": u["display_name"], "role": u["role"],
            "restricted_clearance": int(u["restricted_clearance"]), "location": u["location"] or ""}


def _require(actor, *roles):
    if not actor or actor.get("role") not in roles:
        raise PermissionDenied("This action needs the %s role." % " or ".join(ROLE_LABELS.get(r, r) for r in roles))


def can_view(actor, ticket):
    """Employees see only their own tickets; restricted tickets need restricted clearance."""
    if not actor or not ticket:
        return False
    if actor["role"] == "user":
        return ticket.get("requester_username") == actor["username"]
    return not int(ticket.get("restricted") or 0) or bool(actor.get("restricted_clearance"))


def _visible_ticket(actor, ticket_id):
    t = get_ticket(ticket_id)
    if t is None or not can_view(actor, t):
        raise PermissionDenied("Ticket %s is not available to you." % ticket_id)
    return t


# ---- duplicates and incidents
def can_group(ticket):
    """Only shared-service problems are grouped. Per-person issues and restricted tickets are always handled one by one."""
    return ticket.get("category") not in PER_USER_CATEGORIES and not int(ticket.get("restricted") or 0)


def _create_incident(parent_ticket_id, actor_name):
    p = get_ticket(parent_ticket_id)
    inc = _next_id("incidents", "incident_id", "INC-", 4)
    stamp = now_str()
    execute("INSERT INTO incidents (incident_id, parent_ticket_id, title, category, subcategory, status, created_at, "
            "created_by, resolved_at, effective_impact, priority) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (inc, parent_ticket_id, p["title"], p["category"], p["subcategory"], "Open", stamp, actor_name, "",
             int(p["impact"]), p["priority"]))
    execute("UPDATE tickets SET incident_id=?, linked_by=?, linked_at=?, dup_candidate='', dup_score=NULL WHERE ticket_id=?",
            (inc, actor_name, stamp, parent_ticket_id))
    log_event(parent_ticket_id, actor_name, "Incident created", "%s opened with this ticket as the parent." % inc)
    return inc


def refresh_incident(incident_id, actor_name="system"):
    """Recalculate an incident's impact and priority from its confirmed, linked tickets."""
    linked = query("SELECT ticket_id, impact, urgency, priority, status FROM tickets WHERE incident_id=?", (incident_id,))
    if linked.empty:
        execute("UPDATE incidents SET status='Closed', resolved_at=? WHERE incident_id=? AND status='Open'", (now_str(), incident_id))
        return None
    n = len(linked)
    impact = int(linked["impact"].max())
    for threshold, level in INCIDENT_IMPACT_STEPS:
        if n >= threshold:
            impact = max(impact, level)
            break
    urgency = max(linked["urgency"], key=lambda u: URGENCY_RANK.get(u, 0))
    priority = priority_for(urgency, impact)
    execute("UPDATE incidents SET effective_impact=?, priority=? WHERE incident_id=?", (impact, priority, incident_id))
    for _, row in linked.iterrows():
        if row["status"] in OPEN_STATUSES and priority < row["priority"]:
            execute("UPDATE tickets SET priority=?, priority_reason=? WHERE ticket_id=?",
                    (priority, "Raised by incident %s: %d confirmed tickets" % (incident_id, n), row["ticket_id"]))
            log_event(row["ticket_id"], actor_name, "Priority raised",
                      "%s to %s: incident %s now has %d confirmed tickets." % (row["priority"], priority, incident_id, n))
    return {"incident_id": incident_id, "linked": n, "effective_impact": impact, "priority": priority}


def find_open_duplicates(assistant, ticket, category, subcategory, exclude_id=None):
    """Open, unrestricted tickets from the last 48 hours that may be the same shared-service problem.

    This only produces suggestions. Nothing is linked until an analyst confirms it.
    """
    if not subcategory or category in PER_USER_CATEGORIES or int(ticket.get("restricted") or 0):
        return []
    since = (datetime.now() - timedelta(hours=DUP_WINDOW_HOURS)).strftime(FMT)
    cand = query("SELECT * FROM tickets WHERE status IN ('Open','In Progress','Escalated') AND created_at >= ? "
                 "AND restricted = 0 AND subcategory = ?", (since, subcategory))
    if exclude_id:
        cand = cand[cand["ticket_id"] != exclude_id]
    if cand.empty:
        return []
    text = clean_text("%s. %s" % (ticket.get("title", ""), ticket.get("description", "")))
    texts = (cand["title"].fillna("") + ". " + cand["description"].fillna("")).map(clean_text)
    cos = linear_kernel(assistant.vec.transform([text]), assistant.vec.transform(texts)).ravel()
    out = []
    for sim, (_, row) in zip(cos, cand.iterrows()):
        why = []
        if row["location"] and row["location"] == ticket.get("location"):
            why.append("same site (%s)" % row["location"])
        if row["application"] and row["application"] == ticket.get("application"):
            why.append("same service (%s)" % row["application"])
        if sim >= DUP_SIM and why:
            if row["incident_id"]:
                why.append("already part of confirmed incident %s" % row["incident_id"])
            # rank: wording first; then prefer an ongoing confirmed incident and a match on both site and service
            rank = sim + 0.10 * bool(row["incident_id"]) + 0.05 * (len(why) - bool(row["incident_id"]) - 1)
            out.append({"ticket_id": row["ticket_id"], "title": row["title"], "text_similarity": round(float(sim), 3),
                        "rank": round(float(rank), 3), "incident_id": row["incident_id"] or "",
                        "dup_candidate": row["dup_candidate"] or "", "location": row["location"],
                        "application": row["application"], "created_at": row["created_at"],
                        "reasons": "same problem type, %s, text similarity %.2f, raised within %d hours"
                                   % (", ".join(why), sim, DUP_WINDOW_HOURS)})
    out.sort(key=lambda d: -d["rank"])
    return out


def link_tickets(ticket_id, target_id, actor):
    """Analyst confirms that ticket_id is the same problem as target_id (a ticket or an incident)."""
    _require(actor, "analyst")
    t = _visible_ticket(actor, ticket_id)
    if str(target_id).startswith("INC-"):
        df = query("SELECT * FROM incidents WHERE incident_id=?", (target_id,))
        if df.empty:
            raise ValueError("Unknown incident %s." % target_id)
        if df["status"].iloc[0] != "Open":
            raise ValueError("Incident %s is already %s; handle this ticket on its own." % (target_id, df["status"].iloc[0].lower()))
        inc, anchor = target_id, get_ticket(df["parent_ticket_id"].iloc[0])
    else:
        anchor = _visible_ticket(actor, target_id)
        inc = anchor["incident_id"] or None
    for x in (t, anchor):
        if not can_group(x):
            raise ValueError("%s cannot be grouped: access and hardware issues and restricted tickets are always "
                             "handled one by one." % x["ticket_id"])
        if x["status"] not in OPEN_STATUSES:
            raise ValueError("%s is %s, so it cannot be linked." % (x["ticket_id"], x["status"].lower()))
    if t["subcategory"] != anchor["subcategory"]:
        raise ValueError("%s is '%s' but %s is '%s'. Correct the category first if they really are the same problem."
                         % (ticket_id, t["subcategory"], anchor["ticket_id"], anchor["subcategory"]))
    if t["incident_id"]:
        raise ValueError("%s is already in incident %s." % (ticket_id, t["incident_id"]))
    if inc is None:
        inc = _create_incident(anchor["ticket_id"], actor["username"])
    execute("UPDATE tickets SET incident_id=?, linked_by=?, linked_at=?, dup_candidate='', dup_score=NULL WHERE ticket_id=?",
            (inc, actor["username"], now_str(), ticket_id))
    log_event(ticket_id, actor["username"], "Linked to incident",
              "Analyst confirmed this is the same problem as %s (incident %s)." % (anchor["ticket_id"], inc))
    return refresh_incident(inc, actor["username"])


def confirm_duplicate(ticket_id, actor):
    _require(actor, "analyst")
    t = _visible_ticket(actor, ticket_id)
    if not t.get("dup_candidate"):
        raise ValueError("Ticket %s has no duplicate suggestion." % ticket_id)
    return link_tickets(ticket_id, t["dup_candidate"], actor)


def reject_duplicate(ticket_id, actor, reason=""):
    _require(actor, "analyst")
    t = _visible_ticket(actor, ticket_id)
    cand = t.get("dup_candidate") or ""
    if not cand:
        raise ValueError("Ticket %s has no duplicate suggestion." % ticket_id)
    execute("UPDATE tickets SET dup_candidate='', dup_score=NULL WHERE ticket_id=?", (ticket_id,))
    log_event(ticket_id, actor["username"], "Duplicate suggestion rejected", "Not the same problem as %s. %s" % (cand, reason))
    add_feedback(latest_audit_id(ticket_id), ticket_id, "duplicate", cand, "incorrect", actor["username"], reason)


def _detach(ticket_id, actor_name, event, detail):
    t = get_ticket(ticket_id)
    inc = t["incident_id"]
    execute("UPDATE tickets SET incident_id='', linked_by='', linked_at='', priority=?, priority_reason=? WHERE ticket_id=?",
            (priority_for(t["urgency"], t["impact"]), "Impact and urgency matrix", ticket_id))
    log_event(ticket_id, actor_name, event, detail)
    if inc:
        remaining = query("SELECT ticket_id FROM tickets WHERE incident_id=? ORDER BY created_at", (inc,))["ticket_id"].tolist()
        parent = query("SELECT parent_ticket_id FROM incidents WHERE incident_id=?", (inc,))["parent_ticket_id"].iloc[0]
        if remaining and parent not in remaining:
            execute("UPDATE incidents SET parent_ticket_id=? WHERE incident_id=?", (remaining[0], inc))
        refresh_incident(inc, actor_name)


def unlink_ticket(ticket_id, actor, reason=""):
    _require(actor, "analyst")
    t = _visible_ticket(actor, ticket_id)
    if not t["incident_id"]:
        raise ValueError("Ticket %s is not linked to an incident." % ticket_id)
    if t["status"] not in OPEN_STATUSES:
        raise ValueError("Ticket %s is %s and cannot be unlinked." % (ticket_id, t["status"].lower()))
    _detach(ticket_id, actor["username"], "Unlinked from incident", "Removed from %s by the analyst. %s" % (t["incident_id"], reason))


# ---- intake (user portal)
def submit_ticket(assistant, form, actor):
    """Store a new ticket, classify it and suggest (never apply) a duplicate link."""
    _require(actor, "user")
    tid = _next_id("tickets", "ticket_id", "TKT-", 5)
    analysis = assistant.analyze(form, include_restricted=False)
    cls = analysis["classification"]
    user_cat = form.get("user_category") or "Not sure"
    if cls["category"]["confidence"] >= CAT_CONF_MIN or user_cat == "Not sure":
        category, subcategory, cat_source = cls["category"]["label"], cls["subcategory"]["label"], "model"
    else:
        category, subcategory, cat_source = user_cat, "", "user (model confidence too low)"
    cls["category_source"] = cat_source
    cls["user_category"] = user_cat
    impact, urgency = int(form["impact"]), form["urgency"]
    priority = priority_for(urgency, impact)
    cls["priority_matrix"] = {"impact": impact, "urgency": urgency, "priority": priority}
    group_df = query("SELECT assignment_group FROM tickets WHERE category=? AND source='historical' LIMIT 1", (category,))
    group = group_df["assignment_group"].iloc[0] if len(group_df) else "Service Desk"
    execute("INSERT INTO tickets (ticket_id, source, title, description, requester, requester_username, application, "
            "environment, location, user_category, category, subcategory, impact, urgency, priority, priority_reason, status, "
            "assignment_group, created_at, recommended_at, resolved_at, resolution_sop, resolution_notes, restricted, "
            "incident_id, linked_by, linked_at, dup_candidate, dup_group, reopen_count, label_source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, "portal", form["title"], form["description"], actor["display_name"], actor["username"],
             form.get("application", ""), form.get("environment", "Production"), form.get("location", ""), user_cat, category,
             subcategory, impact, urgency, priority, "Impact and urgency matrix", "Open", group, now_str(), "", "", "", "",
             int(form.get("restricted", 0)), "", "", "", "", "", 0, "user-reported + model"))
    log_event(tid, actor["username"], "Created", "Priority %s from impact '%s' and urgency '%s'."
              % (priority, IMPACT_LABELS[impact], URGENCY_LABELS[urgency]))
    dups = find_open_duplicates(assistant, form, category, subcategory, exclude_id=tid)
    suggestion = None
    if dups:
        top = dups[0]
        # if the best match is itself waiting for confirmation, point at the same target so one outage gives one incident
        suggestion = top["incident_id"] or top["dup_candidate"] or top["ticket_id"]
        execute("UPDATE tickets SET dup_candidate=?, dup_score=? WHERE ticket_id=?", (suggestion, dups[0]["text_similarity"], tid))
        log_event(tid, "assistant", "Duplicate suggested",
                  "May be the same problem as %s: %s. Waiting for analyst confirmation." % (suggestion, dups[0]["reasons"]))
    analysis["duplicates"] = dups
    audit_id = write_audit(tid, form, analysis, "assistant")
    execute("UPDATE tickets SET recommended_at=? WHERE ticket_id=?", (now_str(), tid))
    return {"ticket_id": tid, "priority": priority, "category": category, "suggested_duplicate": suggestion,
            "duplicates": dups, "audit_id": audit_id, "analysis": analysis}


# ---- FR-08 audit, FR-06 feedback
def write_audit(ticket_id, ticket_input, analysis, actor_name):
    keep = ("title", "description", "requester", "application", "environment", "location", "user_category", "impact", "urgency")
    shown = analysis["similar"] + analysis.get("resolved_matches", [])
    contains = int(any(m.get("restricted") for m in shown))
    evidence = {"similar": [{k: m[k] for k in ("ticket_id", "status", "text_similarity", "score", "shared_terms", "excerpt")}
                            for m in analysis["similar"][:5]],
                "duplicates": analysis.get("duplicates", [])}
    gate = analysis["confidence"]
    return execute(
        "INSERT INTO audit_log (ticket_id, analysed_at, analysed_by, input_json, classification_json, evidence_json, "
        "recommendation_json, confidence_status, fallback_reason, contains_restricted, reviewer_decision, reviewer, decided_at, "
        "chosen_sop) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ticket_id, now_str(), actor_name, json.dumps({k: ticket_input.get(k) for k in keep}, default=str),
         json.dumps(analysis["classification"], default=str), json.dumps(evidence, default=str),
         json.dumps(analysis["recommendations"], default=str), gate["status"], " ".join(gate["reasons"]), contains,
         "pending", "", "", ""))


def latest_audit_id(ticket_id):
    df = query("SELECT MAX(audit_id) AS a FROM audit_log WHERE ticket_id=?", (ticket_id,))
    return None if df["a"].isna().all() else int(df["a"].iloc[0])


def ensure_audit(ticket, analysis, actor_name):
    audit_id = latest_audit_id(ticket["ticket_id"])
    if audit_id is None:
        audit_id = write_audit(ticket["ticket_id"], ticket, analysis, actor_name)
        if not ticket.get("recommended_at"):
            execute("UPDATE tickets SET recommended_at=? WHERE ticket_id=?", (now_str(), ticket["ticket_id"]))
    return audit_id


def record_decision(audit_id, decision, actor_name, chosen_sop=""):
    execute("UPDATE audit_log SET reviewer_decision=?, reviewer=?, decided_at=?, chosen_sop=? WHERE audit_id=?",
            (decision, actor_name, now_str(), chosen_sop, audit_id))


def add_feedback(audit_id, ticket_id, target_type, target_id, verdict, actor_name, comment=""):
    execute("INSERT INTO feedback (audit_id, ticket_id, target_type, target_id, verdict, comment, reviewer, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)", (audit_id, ticket_id, target_type, target_id, verdict, comment, actor_name, now_str()))


# ---- change approvals for high-risk SOPs
def sop_requires_approval(sop_id):
    df = query("SELECT requires_approval FROM sops WHERE sop_id=?", (sop_id,))
    return bool(len(df)) and int(df["requires_approval"].iloc[0]) == 1


def approval_state(ticket_id, sop_id):
    df = query("SELECT * FROM approvals WHERE ticket_id=? AND sop_id=? ORDER BY approval_id DESC LIMIT 1", (ticket_id, sop_id))
    if df.empty:
        return None
    a = df.iloc[0].to_dict()
    a["approval_id"] = int(a["approval_id"])
    a["valid"] = bool(a["status"] == "Approved" and a["decided_at"] and
                      datetime.strptime(a["decided_at"], FMT) >= datetime.now() - timedelta(hours=APPROVAL_VALID_HOURS))
    return a


def request_approval(ticket_id, sop_id, justification, actor):
    _require(actor, "analyst")
    t = _visible_ticket(actor, ticket_id)
    if t["status"] not in OPEN_STATUSES:
        raise ValueError("Ticket %s is not open." % ticket_id)
    if not sop_requires_approval(sop_id):
        raise ValueError("%s does not need approval." % sop_id)
    if len((justification or "").strip()) < 20:
        raise ValueError("Describe the planned action, the expected impact and the rollback plan (at least 20 characters).")
    current = approval_state(ticket_id, sop_id)
    if current and current["status"] == "Pending":
        raise ValueError("Change request #%d is already waiting for a decision." % current["approval_id"])
    aid = execute("INSERT INTO approvals (ticket_id, sop_id, justification, requested_by, requested_at, status, decided_by, "
                  "decided_at, decision_comment) VALUES (?,?,?,?,?,?,?,?,?)",
                  (ticket_id, sop_id, justification.strip(), actor["username"], now_str(), "Pending", "", "", ""))
    log_event(ticket_id, actor["username"], "Approval requested", "Change request #%d for %s." % (aid, sop_id))
    return aid


def decide_approval(approval_id, decision, comment, actor):
    _require(actor, "approver")
    df = query("SELECT * FROM approvals WHERE approval_id=?", (int(approval_id),))
    if df.empty:
        raise ValueError("Unknown change request #%s." % approval_id)
    a = df.iloc[0].to_dict()
    if a["status"] != "Pending":
        raise ValueError("Change request #%d was already %s." % (int(a["approval_id"]), a["status"].lower()))
    if decision not in ("Approved", "Rejected"):
        raise ValueError("The decision must be Approved or Rejected.")
    t = get_ticket(a["ticket_id"])
    if actor["username"] in (a["requested_by"], t.get("requester_username")):
        raise PermissionDenied("You cannot decide on a change you requested or on your own ticket.")
    if decision == "Rejected" and not (comment or "").strip():
        raise ValueError("Give a reason when rejecting a change request.")
    execute("UPDATE approvals SET status=?, decided_by=?, decided_at=?, decision_comment=? WHERE approval_id=?",
            (decision, actor["username"], now_str(), (comment or "").strip(), int(approval_id)))
    log_event(a["ticket_id"], actor["username"], "Change request " + decision.lower(),
              "#%d for %s. %s" % (int(a["approval_id"]), a["sop_id"], comment or ""))


def list_approvals():
    return query("SELECT a.*, t.title, t.priority, t.restricted FROM approvals a JOIN tickets t ON t.ticket_id = a.ticket_id "
                 "ORDER BY CASE a.status WHEN 'Pending' THEN 0 ELSE 1 END, a.approval_id DESC")


# ---- analyst actions
def resolve_ticket(ticket_id, sop_id, notes, actor, category=None, subcategory=None, also_resolve=()):
    """Resolve a ticket. For an incident, only the linked tickets the analyst selects are resolved with it;
    the rest are unlinked and stay open. High-risk SOPs need a valid approved change request."""
    _require(actor, "analyst")
    t = _visible_ticket(actor, ticket_id)
    if t["status"] not in OPEN_STATUSES:
        raise ValueError("Ticket %s is already %s." % (ticket_id, t["status"].lower()))
    approval = None
    if sop_requires_approval(sop_id):
        approval = approval_state(ticket_id, sop_id)
        if not approval or not approval["valid"]:
            raise PermissionDenied("%s is a high-risk procedure. It needs an approved change request (valid for %d hours) "
                                   "before this ticket can be resolved." % (sop_id, APPROVAL_VALID_HOURS))
    user, stamp = actor["username"], now_str()
    resolved, unlinked = [ticket_id], []
    inc = t["incident_id"]
    if inc:
        linked = query("SELECT ticket_id FROM tickets WHERE incident_id=? AND ticket_id != ? AND "
                       "status IN ('Open','In Progress','Escalated') ORDER BY created_at", (inc, ticket_id))["ticket_id"].tolist()
        keep = set(also_resolve or ())
        for tid in linked:
            if tid not in keep:
                _detach(tid, user, "Unlinked from incident",
                        "Not covered by the fix applied on %s; left open for separate handling." % ticket_id)
                unlinked.append(tid)
        note = ("Resolved by the fix applied on %s for incident %s (%s). If the problem is still there, reopen this ticket "
                "within %d days." % (ticket_id, inc, sop_id, REOPEN_DAYS))
        for tid in linked:
            if tid in keep:
                execute("UPDATE tickets SET status='Resolved', resolved_at=?, resolution_sop=?, resolution_notes=? WHERE ticket_id=?",
                        (stamp, sop_id, note, tid))
                log_event(tid, user, "Resolved", "Kept in the incident fix by the analyst. " + note)
                resolved.append(tid)
        execute("UPDATE incidents SET status='Resolved', resolved_at=? WHERE incident_id=?", (stamp, inc))
    execute("UPDATE tickets SET status='Resolved', resolved_at=?, resolution_sop=?, resolution_notes=? WHERE ticket_id=?",
            (stamp, sop_id, notes or "Resolved using " + sop_id, ticket_id))
    if category:
        execute("UPDATE tickets SET category=?, subcategory=? WHERE ticket_id=?", (category, subcategory or "", ticket_id))
    log_event(ticket_id, user, "Resolved", "%s applied%s. %s" % (
        sop_id, " under change request #%d" % approval["approval_id"] if approval else "", notes or ""))
    return {"resolved": resolved, "unlinked": unlinked, "approval_id": approval["approval_id"] if approval else None}


def mark_unresolved(ticket_id, actor, notes=""):
    _require(actor, "analyst")
    _visible_ticket(actor, ticket_id)
    execute("UPDATE tickets SET status='In Progress' WHERE ticket_id=?", (ticket_id,))
    log_event(ticket_id, actor["username"], "Recommendation did not fix it", notes)


def escalate_ticket(ticket_id, reason, actor):
    _require(actor, "analyst")
    _visible_ticket(actor, ticket_id)
    execute("UPDATE tickets SET status='Escalated', assignment_group='L3 Escalation' WHERE ticket_id=?", (ticket_id,))
    log_event(ticket_id, actor["username"], "Escalated to L3", reason)


# ---- employee actions after resolution
def user_confirm_fixed(ticket_id, actor):
    _require(actor, "user")
    t = _visible_ticket(actor, ticket_id)
    if t["status"] != "Resolved":
        raise ValueError("Only resolved tickets can be confirmed.")
    execute("UPDATE tickets SET status='Closed' WHERE ticket_id=?", (ticket_id,))
    log_event(ticket_id, actor["username"], "Confirmed fixed by user")


def user_reopen(ticket_id, actor, comment):
    """The user says the fix did not work for them. The ticket leaves its incident and goes back to the queue."""
    _require(actor, "user")
    t = _visible_ticket(actor, ticket_id)
    if t["status"] != "Resolved":
        raise ValueError("Only resolved tickets can be reopened. Closed tickets need a new ticket.")
    if len((comment or "").strip()) < 5:
        raise ValueError("Tell us what is still not working.")
    inc = t["incident_id"]
    execute("UPDATE tickets SET status='Open', resolved_at='', first_attempt_sop=resolution_sop, resolution_sop='', "
            "resolution_notes=?, reopen_count=COALESCE(reopen_count,0)+1, incident_id='', linked_by='', linked_at='', "
            "priority=?, priority_reason='Impact and urgency matrix (reopened)' WHERE ticket_id=?",
            ("Reopened by the user: " + comment.strip(), priority_for(t["urgency"], t["impact"]), ticket_id))
    log_event(ticket_id, actor["username"], "Reopened by user",
              comment.strip() + (" (was resolved with incident %s)" % inc if inc else ""))


def auto_close_resolved():
    """Resolved tickets that nobody reopened within the window are closed."""
    cutoff = (datetime.now() - timedelta(days=REOPEN_DAYS)).strftime(FMT)
    df = query("SELECT ticket_id FROM tickets t WHERE status='Resolved' AND resolved_at != '' AND resolved_at < ? AND "
               "EXISTS (SELECT 1 FROM ticket_events e WHERE e.ticket_id = t.ticket_id AND e.event = 'Resolved')", (cutoff,))
    for tid in df["ticket_id"]:
        execute("UPDATE tickets SET status='Closed' WHERE ticket_id=?", (tid,))
        log_event(tid, "system", "Closed automatically", "Not reopened within %d days of resolution." % REOPEN_DAYS)


# ---- FR-09 MTTR
def _minutes(a, b):
    return (pd.to_datetime(b) - pd.to_datetime(a)).dt.total_seconds() / 60.0


def mttr_summary():
    hist = query("SELECT priority, created_at, resolved_at, triage_minutes, lookup_minutes FROM tickets "
                 "WHERE resolved_at != '' AND triage_minutes IS NOT NULL")
    hist["mttr"] = _minutes(hist["created_at"], hist["resolved_at"])
    live = query("SELECT ticket_id, priority, created_at, recommended_at, resolved_at FROM tickets "
                 "WHERE resolved_at != '' AND triage_minutes IS NULL")
    out = {"baseline_count": int(len(hist)), "baseline_mttr": float(hist["mttr"].mean()),
           "baseline_triage_lookup": float((hist["triage_minutes"] + hist["lookup_minutes"]).mean()),
           "baseline_by_priority": hist.groupby("priority")["mttr"].mean().round(1).to_dict(),
           "live_count": int(len(live)), "live_mttr": None, "live_time_to_recommendation": None}
    if len(live):
        out["live_mttr"] = float(_minutes(live["created_at"], live["resolved_at"]).mean())
        rec = live[live["recommended_at"] != ""]
        if len(rec):
            out["live_time_to_recommendation"] = float(_minutes(rec["created_at"], rec["recommended_at"]).mean() * 60)
    return out


def export_audit(include_restricted=False):
    """Audit log joined to ticket fields. Records about, or citing, restricted tickets need restricted clearance."""
    sql = ("SELECT a.audit_id, a.ticket_id, t.title, t.category, t.subcategory, t.priority, t.status, t.created_at, "
           "a.analysed_at, a.analysed_by, t.resolved_at, a.confidence_status, a.fallback_reason, a.reviewer_decision, "
           "a.reviewer, a.decided_at, a.chosen_sop, a.input_json, a.classification_json, a.evidence_json, a.recommendation_json "
           "FROM audit_log a JOIN tickets t ON t.ticket_id = a.ticket_id")
    if not include_restricted:
        sql += " WHERE t.restricted = 0 AND a.contains_restricted = 0"
    return query(sql + " ORDER BY a.audit_id DESC")
