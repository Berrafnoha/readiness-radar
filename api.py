"""JSON API over radar/services + the web UI. Run: python -m uvicorn api:app --reload"""
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from radar import audit, db, services

app = FastAPI(title="Project Readiness Radar")
WEB = Path(__file__).parent / "web"


def conn():
    c = db.connect()
    if not db.is_seeded(c):
        db.reset_demo(c)
        services.recalc(c)
    return c


def call(fn, user, *args):
    """All business rules and permissions live in services; this only maps errors to HTTP codes."""
    try:
        return fn(conn(), user, *args) or {"ok": True}
    except services.PermissionDenied as e:
        raise HTTPException(403, str(e))
    except services.Blocked as e:
        raise HTTPException(409, str(e))
    except services.RadarError as e:
        raise HTTPException(400, str(e))


class Review(BaseModel):
    fingerprint: str
    decision: str
    comment: str = ""


class Confirm(BaseModel):
    item: str
    date: date
    note: str = ""


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/users")
def users():
    return services.USERS


@app.get("/api/dashboard")
def dashboard():
    c = conn()
    state, slips = services.forecast(c)
    active = [f for f in services.list_findings(c) if f["status"] in services.ACTIVE]
    acts = [{"id": a["id"], "name": a["name"], "stage": a["stage"], "status": a["status"], "start": a["start"].isoformat(),
             "finish": a["finish"].isoformat(), "slip": slips.get(a["id"], 0)}
            for a in sorted(state.activities.values(), key=lambda x: (x["start"], x["id"]))]
    return {"risks": sum(f["kind"] == "risk" for f in active), "gaps": sum(f["kind"] == "data_gap" for f in active),
            "slipping": len(slips), "worst": max(slips.values(), default=0), "activities": acts}


@app.get("/api/findings")
def findings():
    return services.list_findings(conn())


@app.post("/api/review")
def review(b: Review, x_user: str = Header("")):
    return call(services.review_finding, x_user, b.fingerprint, b.decision, b.comment)


@app.post("/api/confirm")
def confirm(b: Confirm, x_user: str = Header("")):
    return call(services.confirm_delivery_date, x_user, b.item, b.date, b.note)


@app.get("/api/stages")
def stages():
    c = conn()
    return [{**dict(s), "inspections": [dict(i) for i in c.execute("SELECT id,name,status,closed_by FROM inspections WHERE stage_id=?", (s["id"],))]}
            for s in c.execute("SELECT * FROM stages ORDER BY id")]


@app.post("/api/stages/{sid}/ready")
def ready(sid: str, x_user: str = Header("")):
    return call(services.request_stage_ready, x_user, sid)


@app.post("/api/stages/{sid}/authorize")
def authorize(sid: str, x_user: str = Header("")):
    return call(services.authorize_stage, x_user, sid)


@app.post("/api/inspections/{iid}/close")
def close(iid: str, x_user: str = Header("")):
    return call(services.close_inspection, x_user, iid, "Closed via web app")


@app.get("/api/audit")
def audit_log():
    c = conn()
    ok, info = audit.verify(c)
    return {"ok": ok, "info": info, "rows": audit.rows(c)}


@app.post("/api/reset")
def reset():
    c = conn()
    db.reset_demo(c)
    services.recalc(c)
    return {"ok": True}
