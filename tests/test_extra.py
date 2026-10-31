import csv
import copy
from datetime import date
import pytest
from radar import audit, db, explainer, rules, services, whatif

ENG, DIR = "Karim B. (Field Engineer)", "Samira L. (Project Director)"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.reset_demo(c)
    services.recalc(c)
    return c


def fps(conn):
    return {f["fingerprint"]: f for f in services.list_findings(conn)}


def seed_frames():
    out = {}
    for t in db.REQUIRED.keys() | set(db.OPTIONAL):
        with open(db.SEED_DIR / f"{t}.csv", newline="", encoding="utf-8") as fh:
            out[t] = list(csv.DictReader(fh))
    return out


# ---- R5 quantity ----
def test_steel_mismatch_flagged_and_concrete_within_tolerance_not(conn):
    f = fps(conn)
    assert f["R5:Rebar B500 steel"]["evidence"]["deviation_pct"] == -16.3
    assert f["R5:Rebar B500 steel"]["severity"] == "high"
    assert "R5:Concrete C30" not in f


def test_quantity_tolerance_boundary():
    st = rules.State({}, [], [], {}, [], {}, specs={"X": {"qty": 100, "unit": "t", "source": "s", "page": None}},
                     estimates={"X": {"qty": 105, "unit": "t", "source": "e", "confirmed": False}})
    assert rules.check_quantity(st) == []                      # exactly 5% is not flagged
    st.estimates["X"]["qty"] = 105.1
    assert len(rules.check_quantity(st)) == 1


def test_unit_mismatch_and_zero_spec_are_data_gaps_not_crashes():
    st = rules.State({}, [], [], {}, [], {}, specs={"X": {"qty": 100, "unit": "kg", "source": "s", "page": None},
                                                    "Y": {"qty": 0, "unit": "t", "source": "s", "page": None}},
                     estimates={"X": {"qty": 1, "unit": "t", "source": "e", "confirmed": False},
                                "Y": {"qty": 1, "unit": "t", "source": "e", "confirmed": False}})
    assert {f.rule_id for f in rules.check_quantity(st)} == {"DQ-UNIT", "DQ-SPEC0"}


def test_confirming_quantity_resolves_finding(conn):
    services.confirm_quantity_estimate(conn, ENG, "Rebar B500 steel", 21.5, "QTO rev C")
    assert fps(conn)["R5:Rebar B500 steel"]["status"] == "resolved"
    with pytest.raises(services.PermissionDenied):
        services.confirm_quantity_estimate(conn, DIR, "Rebar B500 steel", 20, "x")


# ---- Import ----
def test_import_valid_roundtrip_preserves_audit(conn):
    n_before = audit.verify(conn)[1]
    services.import_project_data(conn, ENG, seed_frames(), {"activities.csv": b"x"})
    ok, n_after = audit.verify(conn)
    assert ok and n_after > n_before
    assert "R1:A4:Dewatering pump P-101" in fps(conn)


def test_import_reports_exact_row_for_bad_date_and_unknown_refs_and_cycle():
    fr = seed_frames()
    fr["activities"][2]["planned_start"] = "31/31/2026"
    _, errors, _ = db.validate_dataset(fr)
    assert any("activities line 4" in e for e in errors)
    fr = seed_frames()
    fr["requirements"][0]["activity_id"] = "ZZ"
    assert any("unknown activity ZZ" in e for e in db.validate_dataset(fr)[1])
    fr = seed_frames()
    fr["dependencies"].append({"predecessor_id": "A9", "successor_id": "A1"})
    assert any("cycle" in e for e in db.validate_dataset(fr)[1])


def test_import_converts_french_dates():
    fr = seed_frames()
    fr["activities"][0]["planned_start"] = "19/10/2026"
    clean, errors, notes = db.validate_dataset(fr)
    assert not errors and clean["activities"][0]["planned_start"] == "2026-10-19" and notes


def test_rejected_import_leaves_data_intact(conn):
    fr = seed_frames()
    fr["dependencies"].append({"predecessor_id": "A9", "successor_id": "A1"})
    with pytest.raises(services.RadarError):
        services.import_project_data(conn, ENG, fr, {})
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 9
    with pytest.raises(services.PermissionDenied):
        services.import_project_data(conn, DIR, seed_frames(), {})


# ---- Schedule change workflow ----
def test_change_request_approval_flow(conn):
    services.request_schedule_change(conn, ENG, "A4", date(2026, 10, 29), "Pump arrives 29 Oct")
    ch = services.list_changes(conn)[0]
    assert ch["status"] == "pending" and len(ch["impact"]) == 6
    with pytest.raises(services.PermissionDenied):
        services.decide_schedule_change(conn, ENG, ch["id"], "approved", "ok")
    services.decide_schedule_change(conn, DIR, ch["id"], "approved", "Approved, supplier confirmed")
    start = conn.execute("SELECT planned_start FROM activities WHERE id='A4'").fetchone()[0]
    assert start == "2026-10-29"
    assert fps(conn)["R1:A4:Dewatering pump P-101"]["status"] == "resolved"
    assert conn.execute("SELECT COUNT(*) FROM activity_history").fetchone()[0] == 6
    with pytest.raises(services.RadarError):                       # no double decision
        services.decide_schedule_change(conn, DIR, ch["id"], "rejected", "again")
    assert audit.verify(conn)[0]


def test_rejection_changes_nothing_and_requires_note(conn):
    services.request_schedule_change(conn, ENG, "A4", date(2026, 10, 29), "why")
    cid = services.list_changes(conn)[0]["id"]
    with pytest.raises(services.RadarError):
        services.decide_schedule_change(conn, DIR, cid, "rejected", " ")
    services.decide_schedule_change(conn, DIR, cid, "rejected", "Not justified")
    assert conn.execute("SELECT planned_start FROM activities WHERE id='A4'").fetchone()[0] == "2026-10-26"


def test_cannot_reschedule_completed_activity_or_to_same_date(conn):
    with pytest.raises(services.RadarError):
        services.request_schedule_change(conn, ENG, "A1", date(2026, 10, 30), "x")
    with pytest.raises(services.RadarError):
        services.request_schedule_change(conn, ENG, "A4", date(2026, 10, 26), "x")


# ---- Offline ----
def test_offline_queue_does_not_change_facts_until_sync(conn):
    services.queue_action(conn, ENG, "confirm_delivery_date", ["Dewatering pump P-101", date(2026, 10, 25), "email"])
    assert fps(conn)["R1:A4:Dewatering pump P-101"]["status"] == "open"
    res = services.sync_pending(conn, ENG)
    assert res["synced"] == 1
    assert fps(conn)["R1:A4:Dewatering pump P-101"]["status"] == "resolved"
    assert audit.verify(conn)[0]


def test_offline_conflict_detected_and_resolvable(conn):
    services.queue_action(conn, ENG, "confirm_delivery_date", ["Dewatering pump P-101", date(2026, 10, 25), "my email"])
    services.confirm_delivery_date(conn, ENG, "Dewatering pump P-101", date(2026, 10, 27), "colleague update")
    assert services.sync_pending(conn, ENG)["conflict"] == 1
    pid = services.list_pending(conn)[0]["id"]
    services.resolve_conflict(conn, ENG, pid, apply_mine=False)
    assert services.list_pending(conn)[0]["status"] == "discarded"


def test_director_actions_not_queueable_and_queue_persists(conn, tmp_path):
    with pytest.raises(services.Blocked):
        services.queue_action(conn, ENG, "authorize_stage", ["S1"])
    services.queue_action(conn, ENG, "close_inspection", ["I2", "done"])
    c2 = db.connect(tmp_path / "t.db")
    assert len(services.list_pending(c2)) == 1


# ---- Explainer (no network: fake provider) ----
def finding(conn, fp="R1:A4:Dewatering pump P-101"):
    return fps(conn)[fp]


def test_template_used_without_provider(conn, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False); monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("EXPLAINER_PROVIDER", raising=False)
    assert explainer.explain_finding(finding(conn))["source"] == "template"


def test_valid_ai_output_accepted_invented_number_rejected(conn):
    ok = '{"summary": "The pump arrives 3 days after the dewatering start.", "actions": ["Call the supplier.", "Request a schedule change."]}'
    assert explainer.explain_finding(finding(conn), llm=lambda p: ok)["source"] == "ai"
    bad = '{"summary": "The delay will cost 5000000 dinars.", "actions": ["a", "b"]}'
    assert explainer.explain_finding(finding(conn), llm=lambda p: bad)["source"] == "template"


def test_provider_error_and_bad_json_fall_back(conn):
    def boom(p): raise TimeoutError()
    assert explainer.explain_finding(finding(conn), llm=boom)["source"] == "template"
    assert explainer.explain_finding(finding(conn), llm=lambda p: "not json")["source"] == "template"


def test_explanation_cached_and_audited(conn):
    calls = []
    llm = lambda p: calls.append(1) or '{"summary": "Pump late by 3 days.", "actions": ["x", "y"]}'
    services.explain_finding(conn, ENG, "R1:A4:Dewatering pump P-101", llm=llm)
    services.explain_finding(conn, ENG, "R1:A4:Dewatering pump P-101", llm=llm)
    assert len(calls) == 1


# ---- PDF ----
def test_pdf_record_and_permissions(conn):
    pdf = services.generate_record(conn, ENG, "R1:A4:Dewatering pump P-101", "Hassi Messaoud (typed)")
    assert pdf.startswith(b"%PDF") and b"DRAFT" in pdf and b"Hassi Messaoud" in pdf
    with pytest.raises(services.PermissionDenied):
        services.generate_record(conn, DIR, "R1:A4:Dewatering pump P-101")
    assert any(r["action"] == "record_generated" for r in audit.rows(conn))


# ---- What-if ----
def test_whatif_does_not_mutate_and_scenarios_differ(conn):
    st = services.load_state(conn)
    before = copy.deepcopy(st)
    rows = whatif.compare(st, [{"kind": "expedite_delivery", "item": "Dewatering pump P-101", "new_date": date(2026, 10, 26)},
                               {"kind": "parallelize", "edge": ("A4", "A5")}])
    assert st.deliveries == before.deliveries and st.deps == before.deps
    assert rows[0]["activities_slipping"] == 6
    assert rows[1]["activities_slipping"] < rows[0]["activities_slipping"]
    assert rows[2]["activities_slipping"] < rows[0]["activities_slipping"]
    assert len(explainer.narrate_whatif(rows)) == 3
