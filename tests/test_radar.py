import sqlite3
from datetime import date, timedelta
import pytest
from radar import audit, db, rules, services

ENG, DIR = "Karim B. (Field Engineer)", "Samira L. (Project Director)"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.reset_demo(c)
    services.recalc(c)
    return c


def fps(conn, status=None):
    return {f["fingerprint"]: f for f in services.list_findings(conn) if status is None or f["status"] in status}


def test_late_pump_detected_with_downstream(conn):
    f = fps(conn)["R1:A4:Dewatering pump P-101"]
    assert f["evidence"]["delta_days"] == 3
    assert [a["id"] for a in f["affected"]] == ["A4", "A5", "A6", "A7", "A8", "A9"]
    assert {a["slip_days"] for a in f["affected"]} == {3}


def test_same_day_delivery_is_not_a_risk(conn):
    state = services.load_state(conn)
    state.deliveries["Dewatering pump P-101"]["date"] = state.activities["A4"]["start"]
    assert not [f for f in rules.check_delivery_vs_start(state) if "P-101" in f.fingerprint]


def test_missing_delivery_is_data_gap_not_risk(conn):
    f = fps(conn)["DQ-NODELIVERY:A8:Concrete C30"]
    assert f["kind"] == "data_gap"


def test_float_absorbs_small_delay(conn):
    state = services.load_state(conn)
    # A2 -> A3 has 1 spare day: a 1-day slip on A2 must not move A3.
    assert "A3" not in rules.propagate(state, {"A2": state.activities["A2"]["start"] + timedelta(days=1)})


def test_cycle_is_reported_and_does_not_crash(conn):
    conn.execute("INSERT INTO dependencies VALUES ('A9','A1')")
    services.recalc(conn)
    assert "DQ-CYCLE" in fps(conn)


def test_stage_gate_fires_when_ready_with_open_inspection(conn):
    assert "R3:S1" not in fps(conn)
    services.request_stage_ready(conn, ENG, "S1")
    assert fps(conn)["R3:S1"]["severity"] == "high"


def test_engineer_confirming_date_auto_resolves_finding(conn):
    services.confirm_delivery_date(conn, ENG, "Dewatering pump P-101", date(2026, 10, 25), "Email ref 12/10")
    assert fps(conn)["R1:A4:Dewatering pump P-101"]["status"] == "resolved"


def test_engineer_cannot_authorize(conn):
    services.request_stage_ready(conn, ENG, "S1")
    with pytest.raises(services.PermissionDenied):
        services.authorize_stage(conn, ENG, "S1")


def test_director_blocked_while_inspection_open_then_allowed(conn):
    services.request_stage_ready(conn, ENG, "S1")
    with pytest.raises(services.Blocked):
        services.authorize_stage(conn, DIR, "S1")
    services.close_inspection(conn, ENG, "I2", "survey signed")
    assert fps(conn)["R3:S1"]["status"] == "resolved"
    services.authorize_stage(conn, DIR, "S1")
    assert conn.execute("SELECT status FROM stages WHERE id='S1'").fetchone()[0] == "authorized"


def test_director_cannot_do_engineer_actions(conn):
    with pytest.raises(services.PermissionDenied):
        services.close_inspection(conn, DIR, "I2", "x")


def test_false_positive_stays_suppressed_until_facts_change(conn):
    fp = "R1:A4:Dewatering pump P-101"
    services.review_finding(conn, ENG, fp, "false_positive", "Supplier confirmed by phone, awaiting email")
    services.recalc(conn)
    assert fps(conn)[fp]["status"] == "false_positive"
    services.confirm_delivery_date(conn, ENG, "Dewatering pump P-101", date(2026, 11, 2), "Revised quotation")
    assert fps(conn)[fp]["status"] == "open"      # facts changed -> reopened


def test_review_requires_comment(conn):
    with pytest.raises(services.RadarError):
        services.review_finding(conn, ENG, "R1:A4:Dewatering pump P-101", "confirmed", "  ")


def test_audit_chain_is_tamper_evident_and_append_only(conn):
    ok, n = audit.verify(conn)
    assert ok and n > 0
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE audit_log SET user='x' WHERE seq=1")
    conn.execute("DROP TRIGGER audit_no_update")          # an attacker with DB access
    conn.execute("UPDATE audit_log SET user='mallory' WHERE seq=1")
    assert audit.verify(conn) == (False, 1)


def test_bad_csv_date_reports_line(tmp_path):
    bad = tmp_path / "activities.csv"
    bad.write_text("id,name,planned_start,planned_finish\nX1,Test,12/11/2026,2026-11-13\n")
    c = db.connect(tmp_path / "x.db")
    c.executescript(db.SCHEMA)
    with pytest.raises(ValueError, match="line 2"):
        db.import_csv(c, "activities", bad)
