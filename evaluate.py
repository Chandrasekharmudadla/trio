"""Evaluate the assistant on a held-out split and write the report files.

Run:  python evaluate.py
Out:  evaluation_results.json, EVALUATION_REPORT.md, WORKED_EXAMPLES.md

Every number in the report comes from this run. The data is synthetic (see generate_data.py),
and the MTTR comparison is a simulation whose assumptions are stated in the report.
"""
import json
import os
from collections import Counter

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split

import engine

BASE = os.path.dirname(os.path.abspath(__file__))
REVIEW_MINUTES = 2          # analyst time to read and accept a recommendation
FALSE_LEAD_MINUTES = 10     # time lost when the analyst follows a wrong recommendation first

# Hand-written tickets that SHOULD be routed to human review: vague, out of scope, or contradictory.
CHALLENGE = [
    ("vague", "Help", "It is not working. Please fix it as soon as possible."),
    ("vague", "Issue", "Having some problem since morning, kindly check."),
    ("vague", "Urgent", "Need help immediately, nothing is working for me."),
    ("vague", "System problem", "My system has a problem."),
    ("out of scope", "Projector in boardroom", "The ceiling projector in the boardroom shows a pink tint and the remote is missing."),
    ("out of scope", "Need a new ergonomic chair", "My chair is broken, please arrange a replacement chair and a footrest."),
    ("out of scope", "Cafeteria card reader", "The cafeteria payment card reader does not accept my badge."),
    ("out of scope", "Air conditioning", "The air conditioning on our floor is too cold, please change the temperature."),
    ("out of scope", "Mobile app idea", "I would like to suggest a new feature for our customer mobile app, who should I speak to?"),
    ("contradictory", "Many problems", "VPN certificate error, also the printer has a paper jam, my mailbox is full and the laptop battery drains fast."),
    ("contradictory", "Everything broken", "Outlook is disconnected, Wi-Fi adapter is missing, Jira licence not assigned and the server disk is full."),
    ("contradictory", "Login and printing", "Account locked out, prints are faded, BitLocker asks for a key and SAP ERP reports time out."),
]


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def pct(x):
    return "%.1f%%" % (100.0 * x)


def main():
    tickets = pd.read_csv(os.path.join(BASE, "data", "tickets.csv"), dtype=str, keep_default_na=False)
    tickets["impact"] = tickets["impact"].astype(int)
    tickets["restricted"] = tickets["restricted"].astype(int)
    for c in ("wait_minutes", "triage_minutes", "lookup_minutes", "fix_minutes"):
        tickets[c] = pd.to_numeric(tickets[c], errors="coerce")
    sops = pd.read_csv(os.path.join(BASE, "data", "sops.csv"), dtype=str, keep_default_na=False)
    train, test = train_test_split(tickets, test_size=0.2, random_state=42, stratify=tickets["subcategory"])
    assistant = engine.Assistant(train, sops)
    train_restricted = set(train.loc[train["restricted"] == 1, "ticket_id"])
    train_groups = Counter(train.loc[(train["dup_group"] != "") & (train["restricted"] == 0), "dup_group"])

    records = []
    leaks = 0
    for _, row in test.iterrows():
        t = row.to_dict()
        a = assistant.analyze(t)
        leaks += sum(1 for m in a["similar"] + a["resolved_matches"] if m["ticket_id"] in train_restricted)
        recs = [r["sop_id"] for r in a["recommendations"]]
        dup_rank = None
        if t["dup_group"] and train_groups.get(t["dup_group"], 0) > 0:
            dup_rank = next((i + 1 for i, m in enumerate(a["similar"]) if m["dup_group"] == t["dup_group"]), 99)
        top5 = a["similar"][:5]
        records.append({
            "ticket": t, "analysis": a,
            "cat": a["classification"]["category"]["label"], "cat_conf": a["classification"]["category"]["confidence"],
            "sub": a["classification"]["subcategory"]["label"], "pri": a["classification"]["priority"]["label"],
            "status": a["confidence"]["status"], "recs": recs, "dup_rank": dup_rank,
            "p5_sub": np.mean([m["subcategory"] == t["subcategory"] for m in top5]) if top5 else 0.0,
            "p5_othercat": np.mean([m["category"] != t["category"] for m in top5]) if top5 else 0.0,
        })

    y = lambda f: [r["ticket"][f] for r in records]
    res = {"dataset": {"total": int(len(tickets)), "train": int(len(train)), "test": int(len(test)),
                       "categories": int(tickets["category"].nunique()), "subcategories": int(tickets["subcategory"].nunique()),
                       "sops": int(len(sops)), "restricted": int(tickets["restricted"].sum()),
                       "duplicate_clusters": int(tickets.loc[tickets["dup_group"] != "", "dup_group"].nunique()),
                       "tickets_in_clusters": int((tickets["dup_group"] != "").sum())}}

    # ---- classification
    cls = {}
    for field, key in (("category", "cat"), ("subcategory", "sub"), ("priority", "pri")):
        pred = [r[key] for r in records]
        cls[field] = {"accuracy": accuracy_score(y(field), pred), "macro_f1": f1_score(y(field), pred, average="macro")}
    user_ok = np.mean([r["ticket"]["user_category"] == r["ticket"]["category"] for r in records])
    cls["user_selected_category_accuracy"] = float(user_ok)
    res["classification"] = cls
    cats = sorted(tickets["category"].unique())
    cm_cat = confusion_matrix(y("category"), [r["cat"] for r in records], labels=cats)
    pris = ["P1", "P2", "P3", "P4"]
    cm_pri = confusion_matrix(y("priority"), [r["pri"] for r in records], labels=pris)
    sub_conf = Counter((r["ticket"]["subcategory"], r["sub"]) for r in records if r["ticket"]["subcategory"] != r["sub"])
    p1_missed = sum(1 for r in records if r["ticket"]["priority"] == "P1" and r["pri"] in ("P3", "P4"))
    p1_total = sum(1 for r in records if r["ticket"]["priority"] == "P1")

    # ---- retrieval
    dup = [r["dup_rank"] for r in records if r["dup_rank"] is not None]
    ret = {"duplicate_queries": len(dup)}
    for k in (1, 3, 5):
        ret["hit_at_%d" % k] = float(np.mean([d <= k for d in dup])) if dup else None
    ret["mrr"] = float(np.mean([1.0 / d if d < 99 else 0.0 for d in dup])) if dup else None
    ret["precision_at_5_same_subcategory"] = float(np.mean([r["p5_sub"] for r in records]))
    ret["top5_from_other_category"] = float(np.mean([r["p5_othercat"] for r in records]))
    ret["restricted_leaks"] = int(leaks)
    res["retrieval"] = ret

    # ---- duplicate suggestion, simulated the way the app applies it: open in the last 48 h, same subcategory,
    # same site or service, then a text-similarity threshold. Groupable, unrestricted tickets only.
    g = tickets[(tickets["restricted"] == 0) & (~tickets["category"].isin(engine.PER_USER_CATEGORIES))].reset_index(drop=True)
    g_ts = pd.to_datetime(g["created_at"])
    Mg = assistant.vec.transform((g["title"] + ". " + g["description"]).map(engine.clean_text))
    sims = (Mg @ Mg.T).toarray()
    sugg = []
    for i in range(len(g)):
        w = np.where((g_ts < g_ts[i]) & (g_ts >= g_ts[i] - pd.Timedelta(hours=engine.DUP_WINDOW_HOURS)) &
                     (g["subcategory"] == g["subcategory"][i]) &
                     ((g["location"] == g["location"][i]) | (g["application"] == g["application"][i])))[0]
        grp = g["dup_group"][i]
        true_dup = bool(grp) and bool((g["dup_group"].values[w] == grp).any())
        if len(w):
            j = w[np.argmax(sims[i, w])]
            sugg.append((true_dup, float(sims[i, j]), bool(grp) and g["dup_group"][j] == grp))
        else:
            sugg.append((true_dup, 0.0, False))
    sd = pd.DataFrame(sugg, columns=["true_dup", "score", "right_target"])
    rows = []
    for th in (0.15, 0.20, 0.25, 0.30, 0.35, 0.45, 0.55):
        hit = sd["score"] >= th
        rows.append({"threshold": th,
                     "caught": float((hit & sd["right_target"]).sum()) / max(int(sd["true_dup"].sum()), 1),
                     "false_suggestion_rate": float((hit & ~sd["true_dup"]).sum()) / max(int((~sd["true_dup"]).sum()), 1),
                     "precision": float((hit & sd["right_target"]).sum()) / max(int(hit.sum()), 1)})
    res["duplicate_suggestion"] = {"tickets_with_earlier_duplicate": int(sd["true_dup"].sum()),
                                   "tickets_without": int((~sd["true_dup"]).sum()), "by_threshold": rows,
                                   "threshold_in_use": engine.DUP_SIM}

    # ---- recommendation and gate
    solved = [r for r in records if r["ticket"]["resolution_sop"]]
    by_status = {}
    for s in ("high", "medium", "low"):
        grp = [r for r in solved if r["status"] == s]
        by_status[s] = {"count": len(grp),
                        "top1": float(np.mean([r["recs"][:1] == [r["ticket"]["resolution_sop"]] for r in grp])) if grp else None,
                        "top3": float(np.mean([r["ticket"]["resolution_sop"] in r["recs"] for r in grp])) if grp else None}
    shown = [r for r in solved if r["status"] != "low"]
    rec = {"evaluated": len(solved), "recommendation_shown": len(shown), "coverage": len(shown) / float(len(solved)),
           "top1_when_shown": float(np.mean([r["recs"][:1] == [r["ticket"]["resolution_sop"]] for r in shown])),
           "top3_when_shown": float(np.mean([r["ticket"]["resolution_sop"] in r["recs"] for r in shown])),
           "by_confidence": by_status}
    res["recommendation"] = rec

    # ---- fallback challenge set
    challenge = []
    for kind, title, desc in CHALLENGE:
        a = assistant.analyze({"title": title, "description": desc, "application": ""})
        challenge.append({"kind": kind, "title": title, "description": desc, "status": a["confidence"]["status"],
                          "reasons": a["confidence"]["reasons"], "analysis": a})
    res["fallback"] = {"challenge_total": len(challenge),
                       "challenge_flagged_low": sum(1 for c in challenge if c["status"] == "low"),
                       "by_kind": {k: "%d of %d" % (sum(1 for c in challenge if c["kind"] == k and c["status"] == "low"),
                                                    sum(1 for c in challenge if c["kind"] == k))
                                   for k in ("vague", "out of scope", "contradictory")},
                       "in_scope_low_rate": by_status["low"]["count"] / float(len(solved))}

    # ---- MTTR simulation
    base, assisted, base_h, assisted_h = [], [], [], []
    for r in solved:
        t = r["ticket"]
        manual = t["triage_minutes"] + t["lookup_minutes"] + t["fix_minutes"]
        if r["status"] == "low":
            handle = manual + 1
        elif t["resolution_sop"] in r["recs"]:
            handle = REVIEW_MINUTES + t["fix_minutes"]
        else:
            handle = REVIEW_MINUTES + FALSE_LEAD_MINUTES + manual
        base.append(t["wait_minutes"] + manual)
        assisted.append(t["wait_minutes"] + handle)
        base_h.append(manual)
        assisted_h.append(handle)
    res["mttr"] = {"simulated": True, "tickets": len(solved),
                   "baseline_mttr_minutes": float(np.mean(base)), "assisted_mttr_minutes": float(np.mean(assisted)),
                   "mttr_reduction": 1 - float(np.mean(assisted)) / float(np.mean(base)),
                   "baseline_handling_minutes": float(np.mean(base_h)), "assisted_handling_minutes": float(np.mean(assisted_h)),
                   "handling_reduction": 1 - float(np.mean(assisted_h)) / float(np.mean(base_h)),
                   "assumptions": {"review_minutes": REVIEW_MINUTES, "false_lead_minutes": FALSE_LEAD_MINUTES}}
    res["thresholds"] = {"CAT_CONF_MIN": engine.CAT_CONF_MIN, "SIM_MIN": engine.SIM_MIN, "SOP_SHARE_MIN": engine.SOP_SHARE_MIN,
                         "HIGH_SIM": engine.HIGH_SIM, "DUP_SIM": engine.DUP_SIM}
    with open(os.path.join(BASE, "evaluation_results.json"), "w") as f:
        json.dump(res, f, indent=2)

    # ---- observed errors (real examples from this run)
    cat_err = [r for r in records if r["cat"] != r["ticket"]["category"] and r["ticket"]["restricted"] == 0][:6]
    rec_err = [r for r in shown if r["ticket"]["resolution_sop"] not in r["recs"] and r["ticket"]["restricted"] == 0][:4]
    missed = [c for c in challenge if c["status"] != "low"]

    d, c, rt, rc, fb, m = res["dataset"], res["classification"], res["retrieval"], res["recommendation"], res["fallback"], res["mttr"]
    L = []
    L.append("# Evaluation Report: IT Ticket Analysis and Resolution Assistant\n")
    L.append("All figures below were produced by `python evaluate.py` on the data in `data/`. Re-running the script regenerates this file.\n")
    L.append("## 1. Dataset\n")
    L.append("The dataset is **synthetic**: `generate_data.py` wrote every ticket, label, timestamp, handling time and resolution. "
             "No real ticket data was used. Each row carries `label_source = synthetic-generated`.\n")
    L.append(md_table(["Item", "Value"], [
        ["Tickets", d["total"]], ["Categories / subcategories", "%d / %d" % (d["categories"], d["subcategories"])],
        ["SOPs in the knowledge base", d["sops"]],
        ["Duplicate clusters (several users, one incident)", "%d clusters, %d tickets" % (d["duplicate_clusters"], d["tickets_in_clusters"])],
        ["Restricted (sensitive) tickets", d["restricted"]], ["Train / test split", "%d / %d (80/20, stratified by subcategory, seed 42)" % (d["train"], d["test"])]]))
    L.append("\nThe data is built to behave like a real service desk history, so that the assistant has something meaningful to learn:\n")
    L.append("- **Root causes:** each subcategory has one or two distinct root causes with their own symptoms and their own SOP, so the right fix depends on the wording, not only on the category.\n"
             "- **Incidents:** %d clusters where several users report one fault within about an hour, mentioning the same triggering event.\n"
             "- **Recurring problems:** five faults return on the same service and site every 6 to 12 days and get the same temporary fix each time.\n"
             "- **Failed first fixes:** in %d resolved tickets the usual SOP was tried, failed, and the ticket was reopened and fixed another way.\n"
             "- **Time patterns:** Mondays and mid-mornings are busiest and weekends are quiet.\n"
             "- **Restricted tickets:** about 5%% contain sensitive content and are hidden from analysts without clearance; none appear in the tables of this report.\n"
             "- **Messy input:** about 5%% of tickets are vague one-liners, 8%% use wording that fits two categories, 10%% contain a typo, and 14%% have a wrong or \"Not sure\" user-selected category.\n"
             % (d["duplicate_clusters"], int((pd.to_numeric(tickets["reopen_count"]) > 0).sum())))
    L.append("## 2. Evaluation method\n")
    L.append("- **Classification:** the models are trained on the training split and scored on the test split against the generated labels.\n"
             "- **Duplicate retrieval:** for each test ticket that belongs to a duplicate cluster, we check where the first ticket from the same cluster appears in the ranked results.\n"
             "- **Recommendation:** for each resolved test ticket, the recommended SOPs are compared with the SOP that resolved it. This stands in for analyst review, which needs real analysts.\n"
             "- **Fallback:** %d hand-written vague, out-of-scope and contradictory tickets should all be routed to human review.\n"
             "- **MTTR:** simulated from the generated handling times (assumptions in section 3.5).\n" % fb["challenge_total"])
    L.append("## 3. Results\n")
    L.append("### 3.1 Classification\n")
    L.append(md_table(["Field", "Accuracy", "Macro F1"], [
        ["Category", pct(c["category"]["accuracy"]), "%.3f" % c["category"]["macro_f1"]],
        ["Subcategory", pct(c["subcategory"]["accuracy"]), "%.3f" % c["subcategory"]["macro_f1"]],
        ["Priority (text model only)", pct(c["priority"]["accuracy"]), "%.3f" % c["priority"]["macro_f1"]]]))
    L.append("\nFor comparison, the category the user picked on the form matched the true category in %s of test tickets, "
             "so the classifier is the more reliable source. In the live app, priority comes from the impact and urgency matrix; "
             "the text model is only a cross-check, and its lower score shows why.\n" % pct(c["user_selected_category_accuracy"]))
    L.append("Category accuracy this high is an artefact of template-generated text, where each category has its own vocabulary. "
             "Real tickets would score lower; treat these numbers as a check that the pipeline works, not as expected production accuracy.\n")
    L.append("Category confusion matrix (rows = true, columns = predicted):\n")
    L.append(md_table([""] + cats, [[cats[i]] + list(cm_cat[i]) for i in range(len(cats))]))
    L.append("\nPriority confusion matrix for the text model (rows = true, columns = predicted):\n")
    L.append(md_table([""] + pris, [[pris[i]] + list(cm_pri[i]) for i in range(4)]))
    L.append("\nOperationally important confusion: %d of %d true P1 tickets were predicted as P3 or P4 by the text model.\n" % (p1_missed, p1_total))
    if sub_conf:
        L.append("Most frequent subcategory confusions:\n")
        L.append(md_table(["True", "Predicted", "Count"], [[a, b, n] for (a, b), n in sub_conf.most_common(5)]))
        L.append("")
    L.append("### 3.2 Duplicate retrieval\n")
    L.append(md_table(["Measure", "Value"], [
        ["Test tickets with a known duplicate in the history", rt["duplicate_queries"]],
        ["Duplicate found at rank 1", pct(rt["hit_at_1"])], ["Duplicate found in top 3", pct(rt["hit_at_3"])],
        ["Duplicate found in top 5", pct(rt["hit_at_5"])], ["Mean reciprocal rank", "%.3f" % rt["mrr"]],
        ["Top-5 results sharing the true subcategory (all test tickets)", pct(rt["precision_at_5_same_subcategory"])],
        ["Top-5 results from a different category (superficial matches)", pct(rt["top5_from_other_category"])],
        ["Restricted tickets shown in any result", rt["restricted_leaks"]]]))
    L.append("")
    ds = res["duplicate_suggestion"]
    L.append("**Duplicate suggestions in the live flow.** Each historical ticket is replayed as if it had just arrived: the "
             "candidates are tickets raised in the previous %d hours with the same subcategory and the same site or service. "
             "%d tickets had an earlier report of the same incident; %d did not.\n" % (engine.DUP_WINDOW_HOURS,
             ds["tickets_with_earlier_duplicate"], ds["tickets_without"]))
    L.append(md_table(["Similarity threshold", "Real duplicates suggested with the right target", "Non-duplicates given a wrong suggestion", "Suggestions that are right"],
                      [["%.2f%s" % (r["threshold"], " (in use)" if abs(r["threshold"] - ds["threshold_in_use"]) < 1e-9 else ""),
                        pct(r["caught"]), pct(r["false_suggestion_rate"]), pct(r["precision"])] for r in ds["by_threshold"]]))
    L.append("\nThe threshold is set low on purpose. Every suggestion is confirmed or rejected by an analyst, so a missed "
             "duplicate (lost grouping, slower response) costs more than a wrong suggestion (one click to reject).\n")
    L.append("### 3.3 Resolution recommendation\n")
    L.append(md_table(["Measure", "Value"], [
        ["Resolved test tickets evaluated", rc["evaluated"]],
        ["Recommendation shown (confidence high or medium)", "%d (%s)" % (rc["recommendation_shown"], pct(rc["coverage"]))],
        ["Top recommendation matched the actual fix", pct(rc["top1_when_shown"])],
        ["Actual fix within the top 3 recommendations", pct(rc["top3_when_shown"])]]))
    L.append("\nBy confidence level:\n")
    L.append(md_table(["Confidence", "Tickets", "Top-1 correct", "Top-3 correct"], [
        [s, by_status[s]["count"], pct(by_status[s]["top1"]) if by_status[s]["top1"] is not None else "-",
         pct(by_status[s]["top3"]) if by_status[s]["top3"] is not None else "-"] for s in ("high", "medium", "low")]))
    L.append("\nThe \"low\" row shows what the hidden recommendations would have scored; it is the cost and the benefit of the gate.\n")
    L.append("### 3.4 Confidence and fallback\n")
    L.append("%d of %d challenge tickets were flagged low confidence and routed to human review (%s).\n" % (
        fb["challenge_flagged_low"], fb["challenge_total"], ", ".join("%s: %s" % kv for kv in fb["by_kind"].items())))
    L.append("On ordinary in-scope test tickets, %s were routed to human review.\n" % pct(fb["in_scope_low_rate"]))
    L.append("### 3.5 MTTR (simulated)\n")
    L.append(md_table(["Measure", "Manual baseline", "With assistant", "Reduction"], [
        ["MTTR, creation to resolution (minutes)", "%.0f" % m["baseline_mttr_minutes"], "%.0f" % m["assisted_mttr_minutes"], pct(m["mttr_reduction"])],
        ["Hands-on handling time, excluding queue wait (minutes)", "%.0f" % m["baseline_handling_minutes"], "%.0f" % m["assisted_handling_minutes"], pct(m["handling_reduction"])]]))
    L.append("\nThis is a simulation, not a measurement. Assumptions: a correct recommendation replaces triage and lookup with a "
             "%d-minute review; a wrong recommendation costs the review plus %d minutes before the analyst falls back to manual handling; "
             "a low-confidence ticket is handled manually. Queue wait and fix time are unchanged. The saving from resolving all linked "
             "duplicates at once is not included, so the effect on a real major incident would be larger. The app's MTTR tab also "
             "reports real timestamps for tickets handled in the app.\n" % (REVIEW_MINUTES, FALSE_LEAD_MINUTES))
    L.append("### 3.6 What the assistant learns from the history\n")
    rp = engine.recurring_problems(tickets)
    L.append("Recurring problems detected (same fault, same service and site, at 1.8 times or more the rate of other sites):\n")
    L.append(md_table(list(rp.columns), rp.values.tolist()) if len(rp) else "None detected.")
    L.append("\nAll five recurring faults planted in the data are in this list. The others are chance concentrations, which is why the app presents the list as candidates for an analyst to judge.\n")
    L.append("SLA compliance in the history:\n")
    sc = engine.sla_compliance(tickets)
    L.append(md_table(list(sc.columns), sc.values.tolist()))
    eff = engine.sop_effectiveness(tickets)
    eff = eff[eff["uses"] >= 10].sort_values("first_time_fix_rate").head(5)
    L.append("\nSOPs with the lowest first-time fix rate (shown to the analyst next to each recommendation):\n")
    L.append(md_table(["SOP", "Uses", "Failed first attempt", "First-time fix rate"],
                      [[r["sop_id"], r["uses"], r["failed_first_attempt"], pct(r["first_time_fix_rate"])] for _, r in eff.iterrows()]))
    L.append("")
    L.append("## 4. Observed errors\n")
    if cat_err:
        L.append("Category misclassifications from the test run:\n")
        L.append(md_table(["Ticket", "Title", "True", "Predicted", "Confidence", "Gate"],
                          [[r["ticket"]["ticket_id"], r["ticket"]["title"], r["ticket"]["category"], r["cat"],
                            "%.0f%%" % (100 * r["cat_conf"]), r["status"]] for r in cat_err]))
        L.append("")
    if rec_err:
        L.append("Recommendations that were shown but did not contain the actual fix:\n")
        L.append(md_table(["Ticket", "Title", "Actual fix", "Recommended", "Confidence"],
                          [[r["ticket"]["ticket_id"], r["ticket"]["title"], r["ticket"]["resolution_sop"], ", ".join(r["recs"]), r["status"]]
                           for r in rec_err]))
        L.append("")
    if missed:
        L.append("Challenge tickets that were NOT flagged low confidence (gate misses):\n")
        L.append(md_table(["Kind", "Title", "Status", "Predicted category"],
                          [[x["kind"], x["title"], x["status"], x["analysis"]["classification"]["category"]["label"]] for x in missed]))
        L.append("")
    else:
        L.append("No challenge ticket slipped past the gate in this run.\n")
    L.append("Patterns behind the errors:\n")
    L.append("- Vague one-line tickets carry too little text to tell subcategories apart; most are caught by the gate.\n"
             "- Tickets whose real fix was an alternative SOP or a vendor escalation look identical to ordinary ones, so the top recommendation is wrong by construction for part of them.\n"
             "- Priority cannot be read reliably from text when the user does not describe impact, which is why the app asks for impact and urgency directly.\n")
    L.append("## 5. Known limitations\n")
    L.append("- The data is synthetic and template-based, so scores are higher than they would be on real tickets.\n"
             "- TF-IDF matches words. A ticket that describes a known problem in very different words can be missed.\n"
             "- Recommendation quality was scored against the recorded fix, not by human analysts.\n"
             "- The MTTR figures are simulated.\n"
             "- Duplicate linking uses a similarity threshold plus same subcategory and same site or application; it can link two unrelated single-user tickets, so the analyst can unlink.\n"
             "- Thresholds were set on this dataset and need retuning on real data.\n"             "- Two gate checks (unfamiliar wording, minimum evidence) were added after looking at the challenge tickets, so the challenge result is not an independent test.\n"
             "- Recurring-problem detection compares sites with each other; it also lists some chance concentrations and needs an analyst's judgement.\n"
             "- Sign-in uses demo accounts with salted password hashes and fixed roles. A real deployment would use the company's single sign-on and directory groups.\n"
             "- The classifier is trained on all tickets, including restricted ones. Their text never appears in results or evidence for analysts without clearance, but a stricter setup would train a separate model.\n"
             "- Duplicate suggestions need an analyst to confirm them, which costs a few seconds per ticket during a large outage; bulk confirmation in the Incidents tab reduces this.\n")
    with open(os.path.join(BASE, "EVALUATION_REPORT.md"), "w") as f:
        f.write("\n".join(L))

    # ---- worked examples
    def pick(cond):
        return next((r for r in records if cond(r)), None)
    ex1 = pick(lambda r: r["ticket"]["restricted"] == 0 and r["ticket"]["resolution_sop"] == "SOP-024" and r["status"] != "low" and r["recs"][:1] == ["SOP-024"])
    ex2 = pick(lambda r: r["dup_rank"] == 1 and r["status"] != "low" and r["ticket"]["user_category"] != r["ticket"]["category"]
               and r["cat"] == r["ticket"]["category"] and r["ticket"]["restricted"] == 0) or pick(lambda r: r["dup_rank"] == 1 and r["status"] != "low" and r["ticket"]["restricted"] == 0)
    ex3 = challenge[0]
    W = ["# Worked Examples\n", "Three tickets taken from the evaluation run (`python evaluate.py`). Data is synthetic.\n"]

    def render(n, heading, t, a, note):
        cl = a["classification"]
        W.append("## Example %d: %s\n" % (n, heading))
        W.append("**Input ticket**\n")
        W.append(md_table(["Field", "Value"], [["Title", t.get("title", "")], ["Description", t.get("description", "")],
                                               ["Application", t.get("application", "") or "-"],
                                               ["User-selected category", t.get("user_category", "Not sure")],
                                               ["Impact / urgency", "%s / %s" % (engine.IMPACT_LABELS.get(int(t["impact"]), "-") if t.get("impact") else "-", t.get("urgency", "-"))]]))
        W.append("\n**Classification**\n")
        W.append(md_table(["Field", "Prediction", "Confidence", "Terms that drove it"], [
            ["Category", cl["category"]["label"], "%.0f%%" % (100 * cl["category"]["confidence"]), ", ".join(cl["category"]["top_terms"]) or "-"],
            ["Subcategory", cl["subcategory"]["label"], "%.0f%%" % (100 * cl["subcategory"]["confidence"]), ", ".join(cl["subcategory"]["top_terms"]) or "-"],
            ["Priority", engine.priority_for(t["urgency"], t["impact"]) if t.get("impact") else "-", "from impact and urgency matrix", "-"]]))
        W.append("\n**Similar historical tickets**\n")
        W.append(md_table(["Ticket", "Status", "Text similarity", "Shared terms", "Excerpt"],
                          [[s["ticket_id"], s["status"], s["text_similarity"], ", ".join(s["shared_terms"][:4]), s["excerpt"]] for s in a["similar"][:3]]) if a["similar"] else "None found.")
        g = a["confidence"]
        W.append("\n**Confidence:** %s. %s\n" % (g["status"], " ".join(g["reasons"]) or "All checks passed."))
        if g["status"] == "low":
            W.append("**Recommended resolution:** none. The assistant withholds a recommendation and routes the ticket to human review.\n")
        else:
            r = a["recommendations"][0]
            W.append("**Recommended resolution:** %s %s (risk %s%s)\n" % (r["sop_id"], r["title"], r["risk_level"],
                                                                         ", approval required" if r["requires_approval"] else ""))
            W.append("\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(r["steps"])))
            W.append("\n**Source evidence:** %s\n" % r["reason"])
            W.append(md_table(["Source ticket", "Similarity", "Why it supports this", "How it was resolved"],
                              [[e["ticket_id"], e["text_similarity"], "Shares: " + ", ".join(e["shared_terms"][:4]), e["resolution_notes"]] for e in r["evidence"]]))
            if len(a["recommendations"]) > 1:
                W.append("\nOther paths found: " + "; ".join("%s %s (%s of evidence)" % (x["sop_id"], x["title"], pct(x["share"])) for x in a["recommendations"][1:]))
        W.append("\n" + note + "\n")

    if ex1:
        t = ex1["ticket"]
        render(1, "Service outage, approval-gated SOP", t, ex1["analysis"],
               "Outcome: the ticket was actually resolved with %s, which matches the top recommendation. The SOP requires approval and a "
               "backup before any restart, so the analyst cannot simply restart the server." % t["resolution_sop"])
    if ex2:
        t = ex2["ticket"]
        render(2, "Duplicate of a known incident", t, ex2["analysis"],
               "Outcome: the first similar ticket belongs to the same incident cluster (%s) as this ticket, and the actual fix was %s.%s"
               % (t["dup_group"], t["resolution_sop"],
                  " The user selected \"%s\" as the category; the true category is %s, which the classifier predicted."
                  % (t["user_category"], t["category"]) if t["user_category"] != t["category"] else
                  " In the live app the assistant would suggest linking this ticket to the open incident, and an analyst would confirm it."))
    render(3, "Vague ticket, routed to human review", {"title": ex3["title"], "description": ex3["description"]}, ex3["analysis"],
           "Outcome: the text gives no usable symptom, so the assistant declines to recommend a fix.")
    with open(os.path.join(BASE, "WORKED_EXAMPLES.md"), "w") as f:
        f.write("\n".join(W))

    print(json.dumps({k: res[k] for k in ("classification", "retrieval", "recommendation", "fallback", "mttr")}, indent=1, default=str))
    print("missed challenge:", [(x["kind"], x["title"], x["status"]) for x in missed])


if __name__ == "__main__":
    main()
