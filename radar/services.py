"""Business actions. ALL permission checks live here (never only in the UI)."""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from . import audit, rules
from . import db as _db
from . import explainer, pdf_record

# Simulated login: a demo device, NOT real authentication.
USERS = {"Karim B. (Field Engineer)": "field_engineer", "Samira L. (Project Director)": "project_director"}
DECISIONS = {"investigating": "Under investigation", "confirmed": "Valid risk confirmed",
             "false_positive": "False positive", "needs_info": "Needs more information"}
ACTIVE = ("open", "investigating", "confirmed", "needs_info")


class RadarError(Exception):
    """Base error for rule violations shown to the user."""


class PermissionDenied(RadarError):
    pass


class Blocked(RadarError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _role(user):
    if user not in USERS:
        raise PermissionDenied(f"Unknown user '{user}'")
    return USERS[user]


def _require(user, *roles):
    role = _role(user)
    if role not in roles:
        raise PermissionDenied(f"{user} has role '{role}' and cannot perform this action "
                               f"(requires: {', '.join(roles)}).")
    return role


def _log(conn, user, action, payload):
    audit.append(conn, user, USERS.get(user, "system"), action, payload)


def load_state(conn):
    d = date.fromisoformat
    acts = {r["id"]: {"id": r["id"], "name": r["name"], "stage": r["stage_id"], "start": d(r["planned_start"]),
                      "finish": d(r["planned_finish"]), "status": r["status"],
                      "actual_start": d(r["actual_start"]) if r["actual_start"] else None}
            for r in conn.execute("SELECT * FROM activities")}
    deliveries = {r["item"]: {"date": d(r["promised_date"]), "supplier": r["supplier"], "source": r["source_doc"],
                              "confirmed": bool(r["confirmed"])}
                  for r in conn.execute("SELECT * FROM deliveries ORDER BY id")}   # later rows overwrite earlier
    return rules.State(
        activities=acts,
        deps=[(r[0], r[1]) for r in conn.execute("SELECT predecessor_id, successor_id FROM dependencies")],
        reqs=[dict(r) for r in conn.execute("SELECT * FROM requirements")],
        deliveries=deliveries,
        inspections=[dict(r) for r in conn.execute("SELECT * FROM inspections")],
        stages={r["id"]: dict(r) for r in conn.execute("SELECT * FROM stages")},
        specs={r["item"]: {"qty": r["spec_quantity"], "unit": r["unit"], "source": r["source_doc"], "page": r["page"]}
               for r in conn.execute("SELECT * FROM specifications")},
        estimates={r["item"]: {"qty": r["quantity"], "unit": r["unit"], "source": r["source_doc"],
                               "confirmed": bool(r["confirmed"])}
                   for r in conn.execute("SELECT * FROM estimates ORDER BY id")})


def _signature(f):
    return hashlib.sha256(json.dumps(f.evidence, sort_keys=True).encode()).hexdigest()[:16]


def recalc(conn):
    """Re-run all rules on current facts and reconcile with stored findings.
    New -> open. Disappeared -> auto-resolved. False positives stay suppressed until the facts change."""
    detected = {f.fingerprint: f for f in rules.run_all(load_state(conn))}
    existing = {r["fingerprint"]: r for r in conn.execute("SELECT * FROM findings")}
    now = _now()
    for fp, f in detected.items():
        sig, row = _signature(f), existing.get(fp)
        ev, aff = json.dumps(f.evidence), json.dumps(f.affected)
        if row is None:
            conn.execute("INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (fp, f.rule_id, f.kind, f.severity, f.title, ev, aff, "open", sig, now, now))
            _log(conn, "system", "finding_opened", {"fingerprint": fp, "severity": f.severity})
            continue
        status = row["status"]
        if status == "resolved":
            status = "open"
            _log(conn, "system", "finding_reopened", {"fingerprint": fp})
        elif status == "false_positive" and row["signature"] != sig:
            status = "open"
            _log(conn, "system", "finding_reopened_facts_changed", {"fingerprint": fp})
        conn.execute("UPDATE findings SET severity=?,title=?,evidence=?,affected=?,status=?,signature=?,updated_at=? "
                     "WHERE fingerprint=?", (f.severity, f.title, ev, aff, status, sig, now, fp))
    for fp, row in existing.items():
        if fp not in detected and row["status"] != "resolved":
            conn.execute("UPDATE findings SET status='resolved', updated_at=? WHERE fingerprint=?", (now, fp))
            _log(conn, "system", "finding_auto_resolved", {"fingerprint": fp, "was": row["status"]})
    conn.commit()


def list_findings(conn):
    out = []
    for r in conn.execute("SELECT * FROM findings ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                          "WHEN 'medium' THEN 2 ELSE 3 END, fingerprint"):
        d = dict(r)
        d["evidence"], d["affected"] = json.loads(d["evidence"]), json.loads(d["affected"])
        out.append(d)
    return out


def reviews_for(conn, fingerprint):
    return [dict(r) for r in conn.execute("SELECT user,decision,comment,ts FROM reviews WHERE fingerprint=? ORDER BY id",
                                          (fingerprint,))]


def forecast(conn):
    """Forecast using findings that are still considered real (not false positive / resolved)."""
    state = load_state(conn)
    active = {r["fingerprint"] for r in conn.execute("SELECT fingerprint FROM findings WHERE status IN "
                                                      "('open','investigating','confirmed','needs_info')")}
    fs = [f for f in rules.run_all(state) if f.fingerprint in active]
    return state, rules.combined_forecast(state, fs)


# ---- Operational actions (Field Engineer) ------------------------------------------------------
def review_finding(conn, user, fingerprint, decision, comment):
    _require(user, "field_engineer")
    if decision not in DECISIONS:
        raise RadarError("Invalid decision.")
    if not comment or not comment.strip():
        raise RadarError("A comment is required so the decision can be audited.")
    row = conn.execute("SELECT status FROM findings WHERE fingerprint=?", (fingerprint,)).fetchone()
    if row is None or row["status"] == "resolved":
        raise RadarError("Finding not found or already resolved.")
    conn.execute("INSERT INTO reviews (fingerprint,user,decision,comment,ts) VALUES (?,?,?,?,?)",
                 (fingerprint, user, decision, comment.strip(), _now()))
    conn.execute("UPDATE findings SET status=?, updated_at=? WHERE fingerprint=?", (decision, _now(), fingerprint))
    _log(conn, user, "finding_reviewed", {"fingerprint": fingerprint, "decision": decision, "comment": comment.strip()})
    conn.commit()


def confirm_delivery_date(conn, user, item, new_date, note):
    _require(user, "field_engineer")
    if not note or not note.strip():
        raise RadarError("Cite the source of verification (e.g. supplier email reference).")
    last = conn.execute("SELECT * FROM deliveries WHERE item=? ORDER BY id DESC LIMIT 1", (item,)).fetchone()
    if last is None:
        raise RadarError(f"No delivery record exists for '{item}'.")
    conn.execute("INSERT INTO deliveries (item,supplier,promised_date,source_doc,confirmed,confirmed_by) VALUES (?,?,?,?,1,?)",
                 (item, last["supplier"], new_date.isoformat(), f"Verified by engineer: {note.strip()}", user))
    _log(conn, user, "delivery_date_confirmed",
         {"item": item, "old": last["promised_date"], "new": new_date.isoformat(), "source": note.strip()})
    conn.commit()
    recalc(conn)


def close_inspection(conn, user, inspection_id, note):
    _require(user, "field_engineer")
    row = conn.execute("SELECT * FROM inspections WHERE id=?", (inspection_id,)).fetchone()
    if row is None or row["status"] == "closed":
        raise RadarError("Inspection not found or already closed.")
    conn.execute("UPDATE inspections SET status='closed', closed_by=?, closed_at=? WHERE id=?",
                 (user, date.today().isoformat(), inspection_id))
    _log(conn, user, "inspection_closed", {"inspection": inspection_id, "note": (note or "").strip()})
    conn.commit()
    recalc(conn)


def request_stage_ready(conn, user, stage_id):
    _require(user, "field_engineer")
    st = conn.execute("SELECT status FROM stages WHERE id=?", (stage_id,)).fetchone()
    if st is None or st["status"] != "planned":
        raise RadarError("Only a planned stage can be submitted as ready.")
    conn.execute("UPDATE stages SET status='ready_requested' WHERE id=?", (stage_id,))
    _log(conn, user, "stage_ready_requested", {"stage": stage_id})
    conn.commit()
    recalc(conn)


# ---- Formal authorization (Project Director) ---------------------------------------------------
def authorize_stage(conn, user, stage_id):
    _require(user, "project_director")
    st = conn.execute("SELECT * FROM stages WHERE id=?", (stage_id,)).fetchone()
    if st is None or st["status"] != "ready_requested":
        raise Blocked("Stage must be submitted as ready by an engineer before it can be authorized.")
    open_i = conn.execute("SELECT id,name FROM inspections WHERE stage_id=? AND required=1 AND status!='closed'",
                          (stage_id,)).fetchall()
    if open_i:
        raise Blocked("Open required inspections: " + ", ".join(f"{r['id']} {r['name']}" for r in open_i))
    if conn.execute("SELECT 1 FROM inspections WHERE stage_id=? AND closed_by=?", (stage_id, user)).fetchone():
        raise Blocked("Separation of duties: you cannot authorize a stage whose inspections you closed.")
    conn.execute("UPDATE stages SET status='authorized', authorized_by=?, authorized_at=? WHERE id=?",
                 (user, _now(), stage_id))
    _log(conn, user, "stage_authorized", {"stage": stage_id})
    conn.commit()
    recalc(conn)


# ================================================================================================
# Quantity reconciliation (R5)
# ================================================================================================
def confirm_quantity_estimate(conn, user, item, new_quantity, note):
    _require(user, "field_engineer")
    if not note or not note.strip():
        raise RadarError("Cite the source of verification (e.g. updated take-off reference).")
    if new_quantity is None or new_quantity < 0:
        raise RadarError("Quantity must be zero or positive.")
    last = conn.execute("SELECT * FROM estimates WHERE item=? ORDER BY id DESC LIMIT 1", (item,)).fetchone()
    if last is None:
        raise RadarError(f"No estimate exists for '{item}'.")
    conn.execute("INSERT INTO estimates (item,quantity,unit,source_doc,confirmed,confirmed_by) VALUES (?,?,?,?,1,?)",
                 (item, float(new_quantity), last["unit"], f"Verified by engineer: {note.strip()}", user))
    _log(conn, user, "quantity_confirmed", {"item": item, "old": last["quantity"], "new": float(new_quantity), "source": note.strip()})
    conn.commit()
    recalc(conn)


# ================================================================================================
# Schedule change requests: engineer requests, director approves
# ================================================================================================
def preview_change_impact(conn, activity_id, new_start):
    state = load_state(conn)
    a = state.activities.get(activity_id)
    if a is None:
        raise RadarError("Unknown activity.")
    if a["status"] == "complete":
        raise RadarError("A completed activity cannot be rescheduled.")
    if new_start == a["start"]:
        raise RadarError("The new start date equals the current one.")
    slips = rules.propagate(state, {activity_id: new_start}) if new_start > a["start"] else {}
    order = sorted(slips, key=lambda x: (state.activities[x]["start"], x))
    return [{"id": x, "name": state.activities[x]["name"], "slip_days": slips[x]} for x in order]


def request_schedule_change(conn, user, activity_id, new_start, reason):
    _require(user, "field_engineer")
    if not reason or not reason.strip():
        raise RadarError("A reason is required.")
    impact = preview_change_impact(conn, activity_id, new_start)
    old = conn.execute("SELECT planned_start FROM activities WHERE id=?", (activity_id,)).fetchone()[0]
    cur = conn.execute("INSERT INTO schedule_changes (activity_id,old_start,new_start,reason,requested_by,requested_at,status,impact_json) "
                       "VALUES (?,?,?,?,?,?,'pending',?)",
                       (activity_id, old, new_start.isoformat(), reason.strip(), user, _now(), json.dumps(impact)))
    _log(conn, user, "change_requested", {"change": cur.lastrowid, "activity": activity_id, "new_start": new_start.isoformat(),
                                          "reason": reason.strip(), "downstream": len(impact)})
    conn.commit()


def list_changes(conn):
    out = []
    for r in conn.execute("SELECT * FROM schedule_changes ORDER BY id DESC"):
        d = dict(r)
        d["impact"] = json.loads(d.pop("impact_json") or "[]")
        out.append(d)
    return out


def decide_schedule_change(conn, user, change_id, decision, note):
    _require(user, "project_director")
    if decision not in ("approved", "rejected"):
        raise RadarError("Decision must be approved or rejected.")
    if not note or not note.strip():
        raise RadarError("A decision note is required.")
    ch = conn.execute("SELECT * FROM schedule_changes WHERE id=?", (change_id,)).fetchone()
    if ch is None or ch["status"] != "pending":
        raise RadarError("Change request not found or already decided.")
    if ch["requested_by"] == user:
        raise Blocked("Separation of duties: you cannot decide your own request.")
    if decision == "approved":
        impact = json.loads(ch["impact_json"] or "[]")
        shifts = {a["id"]: a["slip_days"] for a in impact if a["id"] != ch["activity_id"]}
        shifts[ch["activity_id"]] = (date.fromisoformat(ch["new_start"]) - date.fromisoformat(ch["old_start"])).days
        for aid, days in shifts.items():
            row = conn.execute("SELECT planned_start, planned_finish FROM activities WHERE id=?", (aid,)).fetchone()
            ns = (date.fromisoformat(row["planned_start"]) + timedelta(days=days)).isoformat()
            nf = (date.fromisoformat(row["planned_finish"]) + timedelta(days=days)).isoformat()
            conn.execute("INSERT INTO activity_history (activity_id,old_start,old_finish,new_start,new_finish,change_id,changed_at) "
                         "VALUES (?,?,?,?,?,?,?)", (aid, row["planned_start"], row["planned_finish"], ns, nf, change_id, _now()))
            conn.execute("UPDATE activities SET planned_start=?, planned_finish=? WHERE id=?", (ns, nf, aid))
    conn.execute("UPDATE schedule_changes SET status=?, decided_by=?, decided_at=?, decision_note=? WHERE id=?",
                 (decision, user, _now(), note.strip(), change_id))
    _log(conn, user, f"change_{decision}", {"change": change_id, "note": note.strip()})
    conn.commit()
    if decision == "approved":
        recalc(conn)


# ================================================================================================
# Simulated offline mode (queue persisted in SQLite)
# ================================================================================================
QUEUEABLE = {"confirm_delivery_date", "review_finding", "close_inspection"}
ONLINE_ONLY = {"authorize_stage", "decide_schedule_change", "request_schedule_change", "confirm_quantity_estimate",
               "import_project_data", "request_stage_ready"}


def _base_fact(conn, action, args):
    q = {"confirm_delivery_date": "SELECT promised_date FROM deliveries WHERE item=? ORDER BY id DESC LIMIT 1",
         "review_finding": "SELECT status FROM findings WHERE fingerprint=?",
         "close_inspection": "SELECT status FROM inspections WHERE id=?"}[action]
    r = conn.execute(q, (args[0],)).fetchone()
    return r[0] if r else None


def queue_action(conn, user, action, args):
    _require(user, "field_engineer")
    if action not in QUEUEABLE:
        raise Blocked("This action needs a connection (simulated offline mode).")
    args = [a.isoformat() if isinstance(a, date) else a for a in args]
    conn.execute("INSERT INTO pending_sync (user,action,args_json,base_fact_json,queued_at,status) VALUES (?,?,?,?,?,'queued')",
                 (user, action, json.dumps(args), json.dumps(_base_fact(conn, action, args)), _now()))
    _log(conn, user, "offline_action_queued", {"action": action, "args": args, "device_time": _now()})
    conn.commit()


def list_pending(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM pending_sync ORDER BY id DESC")]


def _execute_queued(conn, row):
    a, u = json.loads(row["args_json"]), row["user"]
    if row["action"] == "confirm_delivery_date":
        confirm_delivery_date(conn, u, a[0], date.fromisoformat(a[1]), a[2])
    elif row["action"] == "review_finding":
        review_finding(conn, u, *a)
    elif row["action"] == "close_inspection":
        close_inspection(conn, u, *a)


def _mark(conn, rid, status, note=""):
    conn.execute("UPDATE pending_sync SET status=?, result_note=?, synced_at=? WHERE id=?", (status, note, _now(), rid))


def sync_pending(conn, user):
    """Replay queued actions in order through the normal services (permissions + audit still apply)."""
    _require(user, "field_engineer")
    res = {"synced": 0, "conflict": 0, "failed": 0}
    for row in conn.execute("SELECT * FROM pending_sync WHERE status='queued' ORDER BY id").fetchall():
        a = json.loads(row["args_json"])
        current, base = _base_fact(conn, row["action"], a), json.loads(row["base_fact_json"])
        if current != base:
            _mark(conn, row["id"], "conflict", f"value when queued: {base}; value now: {current}")
            _log(conn, user, "offline_conflict", {"id": row["id"], "queued": base, "now": current})
            res["conflict"] += 1
            continue
        try:
            _execute_queued(conn, row)
            _mark(conn, row["id"], "synced")
            _log(conn, user, "offline_action_synced", {"id": row["id"], "action": row["action"], "performed_at_device": row["queued_at"]})
            res["synced"] += 1
        except Exception as e:
            conn.rollback()
            _mark(conn, row["id"], "failed", str(e))
            res["failed"] += 1
    conn.commit()
    recalc(conn)
    return res


def resolve_conflict(conn, user, pending_id, apply_mine):
    _require(user, "field_engineer")
    row = conn.execute("SELECT * FROM pending_sync WHERE id=? AND status='conflict'", (pending_id,)).fetchone()
    if row is None:
        raise RadarError("No such conflict.")
    if apply_mine:
        try:
            _execute_queued(conn, row)
        except RadarError as e:
            _mark(conn, pending_id, "failed", str(e))
            conn.commit()
            raise
        _mark(conn, pending_id, "synced", "applied by engineer after conflict")
    else:
        _mark(conn, pending_id, "discarded", "discarded by engineer")
    _log(conn, user, "offline_conflict_resolved", {"id": pending_id, "applied": bool(apply_mine)})
    conn.commit()


# ================================================================================================
# AI explainer (cached) and PDF record
# ================================================================================================
def get_explanation(conn, fingerprint):
    r = conn.execute("SELECT e.* FROM explanations e JOIN findings f ON f.fingerprint=e.fingerprint AND f.signature=e.signature "
                     "WHERE e.fingerprint=?", (fingerprint,)).fetchone()
    return None if r is None else {"summary": r["summary"], "actions": json.loads(r["actions_json"]), "source": r["source"]}


def explain_finding(conn, user, fingerprint, llm=None):
    _require(user, "field_engineer", "project_director")
    row = conn.execute("SELECT * FROM findings WHERE fingerprint=?", (fingerprint,)).fetchone()
    if row is None:
        raise RadarError("Finding not found.")
    cached = get_explanation(conn, fingerprint)
    if cached:
        return cached
    f = {"title": row["title"], "severity": row["severity"], "rule_id": row["rule_id"],
         "evidence": json.loads(row["evidence"]), "affected": json.loads(row["affected"])}
    res = explainer.explain_finding(f, llm=llm)
    conn.execute("INSERT OR REPLACE INTO explanations VALUES (?,?,?,?,?,?)",
                 (fingerprint, row["signature"], res["summary"], json.dumps(res["actions"]), res["source"], _now()))
    _log(conn, user, "explanation_generated", {"fingerprint": fingerprint, "source": res["source"]})
    conn.commit()
    return res


def generate_record(conn, user, fingerprint, site_location=""):
    _require(user, "field_engineer")
    row = conn.execute("SELECT * FROM findings WHERE fingerprint=?", (fingerprint,)).fetchone()
    if row is None:
        raise RadarError("Finding not found.")
    data = {"project": "Excavation and Foundations (synthetic demo data)", "generated_at": _now(), "generated_by": user,
            "site_location": (site_location or "").strip() or "(not provided)",
            "finding": {"fingerprint": fingerprint, "severity": row["severity"], "title": row["title"], "status": row["status"],
                        "evidence": json.loads(row["evidence"])},
            "affected": json.loads(row["affected"]), "reviews": reviews_for(conn, fingerprint)}
    data["content_hash"] = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    pdf = pdf_record.build_record(data)
    _log(conn, user, "record_generated", {"fingerprint": fingerprint, "pdf_sha256": hashlib.sha256(pdf).hexdigest()})
    conn.commit()
    return pdf


# ================================================================================================
# Data import (CSV/XLSX frames already parsed into row dicts)
# ================================================================================================
def import_project_data(conn, user, raw_frames, file_bytes):
    _require(user, "field_engineer")
    clean, errors, _ = _db.validate_dataset(raw_frames)
    if errors:
        raise RadarError("Import rejected: " + "; ".join(errors[:5]))
    _db.import_dataset(conn, clean)
    _log(conn, user, "data_imported", {"rows": {t: len(r) for t, r in clean.items()},
                                       "files_sha256": {n: hashlib.sha256(b).hexdigest() for n, b in file_bytes.items()}})
    conn.commit()
    recalc(conn)
