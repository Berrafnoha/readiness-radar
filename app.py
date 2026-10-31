"""Project Readiness Radar - Streamlit UI. Run: streamlit run app.py"""
import json
import os
from datetime import date

import pandas as pd
import streamlit as st

from radar import audit, db, explainer, services, whatif

st.set_page_config(page_title="Project Readiness Radar", layout="wide")

# Streamlit Cloud secrets -> environment variables (the explainer reads os.environ). Optional.
try:
    for _k in ("GEMINI_API_KEY", "GROQ_API_KEY", "EXPLAINER_PROVIDER", "EXPLAINER_MODEL"):
        if _k in st.secrets and _k not in os.environ:
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass
conn = db.connect()
if not db.is_seeded(conn):
    db.reset_demo(conn)
    services.recalc(conn)

# ---------------- Sidebar ----------------
st.sidebar.title("Readiness Radar")
user = st.sidebar.selectbox("Simulated login", list(services.USERS))
role = services.USERS[user]
st.sidebar.caption(f"Role: **{role.replace('_', ' ').title()}**  \nDemo device only, not real authentication.")
offline = st.sidebar.toggle("Simulate offline (field mode)", key="offline")
st.sidebar.caption("Simulated offline sync: field updates are queued locally and replayed on sync.")
st.sidebar.caption(f"AI explainer: {explainer.describe()}")
page = st.sidebar.radio("View", ["Dashboard", "Findings", "Stages", "Change requests", "What-if", "Import data", "Sync queue", "Audit log"])
if st.sidebar.button("Reset demo data"):
    db.reset_demo(conn)
    services.recalc(conn)
    st.rerun()

if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))
if offline:
    n = sum(1 for p in services.list_pending(conn) if p["status"] == "queued")
    st.warning(f"SIMULATED OFFLINE: showing data as of the last sync. {n} update(s) waiting to sync. "
               "Formal authorization and approvals are disabled offline.")


def act(fn, *args, msg="Done."):
    """Run a service action. Errors are shown, never swallowed. Offline: operational actions are queued."""
    name = fn.__name__
    try:
        if offline and name in services.QUEUEABLE:
            services.queue_action(conn, user, name, list(args))
            msg = "Saved offline. It will be applied when you sync."
        elif offline and name in services.ONLINE_ONLY:
            raise services.Blocked("This action needs a connection (simulated offline mode).")
        else:
            fn(conn, user, *args)
    except services.RadarError as e:
        st.error(str(e))
        return
    st.session_state["flash"] = msg
    st.rerun()


def table(rows, **kw):
    st.dataframe(pd.DataFrame(rows).astype(str), hide_index=True, width="stretch", **kw)


# ---------------- Dashboard ----------------
if page == "Dashboard":
    st.title("Project readiness dashboard")
    state, slips = services.forecast(conn)
    active = [f for f in services.list_findings(conn) if f["status"] in services.ACTIVE]
    c = st.columns(4)
    c[0].metric("Active risks", sum(f["kind"] == "risk" for f in active))
    c[1].metric("Data gaps", sum(f["kind"] == "data_gap" for f in active))
    c[2].metric("Activities forecast to slip", len(slips))
    c[3].metric("Worst slip (days)", max(slips.values(), default=0))
    rows = []
    for a in sorted(state.activities.values(), key=lambda x: (x["start"], x["id"])):
        s = slips.get(a["id"], 0)
        rows.append({"ID": a["id"], "Activity": a["name"], "Stage": a["stage"], "Status": a["status"],
                     "Planned start": a["start"], "Planned finish": a["finish"], "Slip (days)": s,
                     "Forecast finish": a["finish"] + pd.Timedelta(days=s)})
    st.subheader("Plan vs forecast")
    table(rows)
    st.caption("Forecast applies all active (not false-positive) findings together, keeping durations and dependency lags.")

# ---------------- Findings ----------------
elif page == "Findings":
    st.title("Findings")
    view = st.radio("Show", ["Risks", "Data gaps", "Closed"], horizontal=True)
    findings = services.list_findings(conn)
    if view == "Risks":
        shown = [f for f in findings if f["kind"] == "risk" and f["status"] in services.ACTIVE]
    elif view == "Data gaps":
        shown = [f for f in findings if f["kind"] == "data_gap" and f["status"] in services.ACTIVE]
    else:
        shown = [f for f in findings if f["status"] not in services.ACTIVE]
    if not shown:
        st.info("Nothing here.")
    for f in shown:
        fp, ev = f["fingerprint"], f["evidence"]
        with st.expander(f"[{f['severity'].upper()}] {f['title']}  ·  {f['status']}"):
            left, right = st.columns(2)
            left.markdown("**Evidence**")
            with left:
                table([{"field": k, "value": json.dumps(v) if isinstance(v, (list, dict)) else v} for k, v in ev.items()])
            right.markdown("**Downstream activities affected**")
            with right:
                table(f["affected"]) if f["affected"] else st.write("None identified.")
            hist = services.reviews_for(conn, fp)
            if hist:
                st.markdown("**Review history**")
                table(hist)
            # AI explainer (grounded, template fallback)
            ex = services.get_explanation(conn, fp)
            if st.button("Explain this finding", key=f"ex-{fp}"):
                try:
                    services.explain_finding(conn, user, fp)
                    st.rerun()
                except services.RadarError as e:
                    st.error(str(e))
            if ex:
                st.info(f"**Summary:** {ex['summary']}\n\n**Suggested actions:**\n\n- {ex['actions'][0]}\n- {ex['actions'][1]}")
                st.caption(f"Source: {ex['source']}. AI-generated text is a phrasing aid: verify against the evidence above.")
            if f["status"] not in services.ACTIVE:
                continue
            if role != "field_engineer":
                st.info("Read-only for this role. Only a Field Engineer verifies facts and reviews findings.")
                continue
            if f["rule_id"] == "R1":
                with st.form(f"date-{fp}"):
                    st.markdown("**Verified a different delivery date?**")
                    nd = st.date_input("Verified delivery date", value=date.fromisoformat(ev["delivery_date"]), key=f"nd-{fp}")
                    src = st.text_input("Source of verification (e.g. supplier email ref)", key=f"src-{fp}")
                    if st.form_submit_button("Record verified date"):
                        act(services.confirm_delivery_date, ev["item"], nd, src, msg="Verified date recorded; risks recalculated.")
            if f["rule_id"] == "R5":
                with st.form(f"qty-{fp}"):
                    st.markdown("**Verified a corrected quantity?**")
                    nq = st.number_input(f"Verified quantity ({ev['unit']})", min_value=0.0, value=float(ev["estimate"]), key=f"nq-{fp}")
                    src = st.text_input("Source of verification (e.g. take-off revision)", key=f"qs-{fp}")
                    if st.form_submit_button("Record verified quantity"):
                        act(services.confirm_quantity_estimate, ev["item"], nq, src, msg="Quantity recorded; risks recalculated.")
            with st.form(f"rev-{fp}"):
                st.markdown("**Review this finding**")
                dec = st.selectbox("Decision", list(services.DECISIONS), format_func=services.DECISIONS.get, key=f"dec-{fp}")
                com = st.text_input("Comment (required)", key=f"com-{fp}")
                if st.form_submit_button("Record review"):
                    act(services.review_finding, fp, dec, com, msg="Review recorded.")
            if f["kind"] == "risk":
                loc = st.text_input("Site location (typed, not GPS)", key=f"loc-{fp}")
                if st.button("Generate verification record (PDF, draft)", key=f"pdf-{fp}"):
                    try:
                        st.session_state[f"pdfdata-{fp}"] = services.generate_record(conn, user, fp, loc)
                    except services.RadarError as e:
                        st.error(str(e))
                if f"pdfdata-{fp}" in st.session_state:
                    st.download_button("Download PDF", st.session_state[f"pdfdata-{fp}"], f"verification_{fp.replace(':', '_')}.pdf",
                                       "application/pdf", key=f"dl-{fp}")

# ---------------- Stages ----------------
elif page == "Stages":
    st.title("Stage readiness")
    for s in conn.execute("SELECT * FROM stages ORDER BY id").fetchall():
        st.subheader(f"{s['id']} · {s['name']}  -  {s['status'].replace('_', ' ')}")
        if s["authorized_by"]:
            st.caption(f"Authorized by {s['authorized_by']} at {s['authorized_at']}")
        insp = [dict(r) for r in conn.execute("SELECT id,name,status,closed_by,closed_at FROM inspections WHERE stage_id=?", (s["id"],))]
        if insp:
            table(insp)
        cols = st.columns(3)
        with cols[0]:
            if s["status"] == "planned" and st.button("Submit stage as ready (engineer)", key=f"rdy-{s['id']}"):
                act(services.request_stage_ready, s["id"], msg="Stage submitted. Checking gate rules.")
        with cols[1]:
            for i in insp:
                if i["status"] == "open" and st.button(f"Close inspection {i['id']} (engineer)", key=f"ci-{i['id']}"):
                    act(services.close_inspection, i["id"], "Closed via app", msg=f"Inspection {i['id']} closed.")
        with cols[2]:
            if s["status"] == "ready_requested" and st.button("Authorize stage (director)", key=f"auth-{s['id']}"):
                act(services.authorize_stage, s["id"], msg=f"Stage {s['id']} authorized.")

# ---------------- Change requests ----------------
elif page == "Change requests":
    st.title("Schedule change requests")
    st.caption("Engineers request a change; only the Project Director approves. Approval updates the plan and keeps history.")
    state = services.load_state(conn)
    if role == "field_engineer":
        movable = [a for a in state.activities.values() if a["status"] != "complete"]
        aid = st.selectbox("Activity", [a["id"] for a in movable], format_func=lambda x: f"{x} · {state.activities[x]['name']}")
        ns = st.date_input("New start date", value=state.activities[aid]["start"], key=f"cs-{aid}")
        reason = st.text_input("Reason (required)")
        try:
            impact = services.preview_change_impact(conn, aid, ns)
            st.markdown(f"**Impact preview:** {len(impact)} activities slip" if impact else "**Impact preview:** no downstream slip")
            if impact:
                table(impact)
        except services.RadarError as e:
            st.caption(str(e))
        if st.button("Submit change request"):
            act(services.request_schedule_change, aid, ns, reason, msg="Change request submitted for director approval.")
    else:
        st.info("Directors review and decide pending requests below.")
    st.subheader("Requests")
    for ch in services.list_changes(conn):
        with st.expander(f"#{ch['id']} · {ch['activity_id']} {ch['old_start']} → {ch['new_start']} · {ch['status']}"):
            st.write(f"Reason: {ch['reason']}  \nRequested by {ch['requested_by']} at {ch['requested_at']}")
            if ch["impact"]:
                table(ch["impact"])
            if ch["status"] != "pending":
                st.write(f"Decision by {ch['decided_by']}: {ch['decision_note']}")
            elif role == "project_director":
                with st.form(f"dec-{ch['id']}"):
                    d = st.selectbox("Decision", ["approved", "rejected"], key=f"d-{ch['id']}")
                    n = st.text_input("Decision note (required)", key=f"n-{ch['id']}")
                    if st.form_submit_button("Record decision"):
                        act(services.decide_schedule_change, ch["id"], d, n, msg=f"Request #{ch['id']} {d}.")

# ---------------- What-if ----------------
elif page == "What-if":
    st.title("What-if simulator")
    st.caption("Schedule impact is computed by the rule engine. Costs are editable ASSUMPTIONS, not project data. Nothing here changes the plan.")
    state = services.load_state(conn)
    items = sorted(state.deliveries)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("Scenario A: expedite")
        item = st.selectbox("Item", items, index=items.index("Dewatering pump P-101") if "Dewatering pump P-101" in items else 0)
        nd = st.date_input("New delivery date", value=date(2026, 10, 26))
    with c2:
        st.subheader("Scenario B: run in parallel")
        edge = st.selectbox("Remove dependency", state.deps, index=state.deps.index(("A4", "A5")) if ("A4", "A5") in state.deps else 0,
                            format_func=lambda e: f"{e[0]} → {e[1]}")
    with c3:
        st.subheader("Assumptions (DZD)")
        fee = st.number_input("Expedite fee (assumed)", 0, value=whatif.DEFAULT_ASSUMPTIONS["expedite_fee"], step=10000)
        idle = st.number_input("Idle cost per day of delay (assumed)", 0, value=whatif.DEFAULT_ASSUMPTIONS["idle_cost_per_day"], step=10000)
    rows = whatif.compare(state, [{"kind": "expedite_delivery", "item": item, "new_date": nd},
                                  {"kind": "parallelize", "edge": edge}], {"expedite_fee": fee, "idle_cost_per_day": idle})
    table(rows)
    st.bar_chart(pd.DataFrame(rows).set_index("scenario")[["activities_slipping"]])
    st.markdown("**Trade-off summary**")
    for b in explainer.narrate_whatif(rows):
        st.markdown(f"- {b}")

# ---------------- Import ----------------
elif page == "Import data":
    st.title("Import project data")
    st.caption("Sits on top of your planning sheets: export from P6 / MS Project / Excel, then import CSV or XLSX here.")
    with st.expander("Download templates (current demo data)"):
        for t in db.INSERT_ORDER:
            st.download_button(f"{t}.csv", (db.SEED_DIR / f"{t}.csv").read_bytes(), f"{t}.csv", "text/csv", key=f"tpl-{t}")
    files = st.file_uploader("Upload files named activities, dependencies, requirements, deliveries, inspections "
                             "(optional: stages, specifications, estimates)", type=["csv", "xlsx"], accept_multiple_files=True)
    if files:
        raw, blobs = {}, {}
        for f in files:
            stem = f.name.rsplit(".", 1)[0].lower()
            blobs[f.name] = f.getvalue()
            f.seek(0)
            df = pd.read_csv(f, dtype=str, keep_default_na=False) if f.name.lower().endswith(".csv") else pd.read_excel(f, dtype=str).fillna("")
            raw[stem] = df.to_dict("records")
        clean, errors, notes = db.validate_dataset(raw)
        for e in errors:
            st.error(e)
        for n in notes:
            st.info(n)
        if not errors:
            st.success("Validation passed (dry run). Nothing has been changed yet.")
            table([{"table": t, "rows": len(r)} for t, r in clean.items()])
            st.warning("Committing REPLACES current project data. The audit log is kept.")
            if role != "field_engineer":
                st.info("Only a Field Engineer can commit an import.")
            elif st.checkbox("I understand") and st.button("Commit import"):
                act(services.import_project_data, raw, blobs, msg="Import committed; rules re-run.")

# ---------------- Sync queue ----------------
elif page == "Sync queue":
    st.title("Offline sync queue")
    pend = services.list_pending(conn)
    if pend:
        table([{k: p[k] for k in ("id", "user", "action", "args_json", "queued_at", "status", "result_note")} for p in pend])
    else:
        st.info("Queue is empty. Turn on 'Simulate offline' in the sidebar, then record an engineer update.")
    if role == "field_engineer":
        if st.button("Sync now"):
            try:
                r = services.sync_pending(conn, user)
                st.session_state["flash"] = f"Sync finished: {r['synced']} applied, {r['conflict']} conflict(s), {r['failed']} failed."
                st.rerun()
            except services.RadarError as e:
                st.error(str(e))
        for p in [p for p in pend if p["status"] == "conflict"]:
            st.warning(f"Conflict #{p['id']}: {p['result_note']}")
            c1, c2 = st.columns(2)
            if c1.button("Apply mine", key=f"am-{p['id']}"):
                act(services.resolve_conflict, p["id"], True, msg="Conflict resolved: yours applied.")
            if c2.button("Discard mine", key=f"dm-{p['id']}"):
                act(services.resolve_conflict, p["id"], False, msg="Conflict resolved: discarded.")
        st.caption("Turn the sidebar toggle off to work online again.")

# ---------------- Audit ----------------
else:
    st.title("Audit log")
    ok, info = audit.verify(conn)
    st.success(f"Hash chain intact ({info} entries).") if ok else st.error(f"Chain broken at entry #{info}.")
    data = audit.rows(conn)
    df = pd.DataFrame(data)
    st.dataframe(df, width="stretch", hide_index=True)
    c1, c2 = st.columns(2)
    c1.download_button("Export CSV", df.to_csv(index=False), "audit_log.csv", "text/csv")
    c2.download_button("Export JSON", json.dumps(data, indent=2), "audit_log.json", "application/json")
