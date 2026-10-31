from fastapi.testclient import TestClient
import api
from radar import db

ENG, DIR = "Karim B. (Field Engineer)", "Samira L. (Project Director)"


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(db, "connect", lambda p=tmp_path / "t.db": db.sqlite3.connect(p, **{}) and _c(p))
    return TestClient(api.app)


def _c(p):
    c = db.sqlite3.connect(p); c.row_factory = db.sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c


def test_api_flow_and_permissions(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    assert c.get("/").status_code == 200
    d = c.get("/api/dashboard").json()
    assert d["risks"] == 4 and d["slipping"] == 6
    fp = next(f["fingerprint"] for f in c.get("/api/findings").json() if f["rule_id"] == "R1" and "P-101" in f["fingerprint"])
    r = c.post("/api/review", json={"fingerprint": fp, "decision": "confirmed", "comment": "ok"}, headers={"X-User": DIR})
    assert r.status_code == 403                                   # director cannot do engineer actions
    r = c.post("/api/confirm", json={"item": "Dewatering pump P-101", "date": "2026-10-25", "note": "email"}, headers={"X-User": ENG})
    assert r.status_code == 200 and c.get("/api/dashboard").json()["risks"] == 3
    assert c.post("/api/stages/S1/ready", headers={"X-User": ENG}).status_code == 200
    assert c.post("/api/stages/S1/authorize", headers={"X-User": DIR}).status_code == 409   # open inspection blocks
    assert c.get("/api/audit").json()["ok"] is True
