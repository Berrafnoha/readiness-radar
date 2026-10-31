"""SQLite schema, CSV import with validation, demo reset."""
import csv
import pathlib
import sqlite3
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "radar.db"
SEED_DIR = ROOT / "data" / "seed"

SCHEMA = """
CREATE TABLE stages (id TEXT PRIMARY KEY, name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','ready_requested','authorized')),
  authorized_by TEXT, authorized_at TEXT);
CREATE TABLE activities (id TEXT PRIMARY KEY, name TEXT NOT NULL, work_package TEXT,
  stage_id TEXT REFERENCES stages(id), planned_start TEXT NOT NULL, planned_finish TEXT NOT NULL,
  actual_start TEXT, status TEXT NOT NULL DEFAULT 'planned');
CREATE TABLE dependencies (predecessor_id TEXT REFERENCES activities(id),
  successor_id TEXT REFERENCES activities(id), PRIMARY KEY (predecessor_id, successor_id));
CREATE TABLE requirements (id INTEGER PRIMARY KEY, activity_id TEXT REFERENCES activities(id),
  item TEXT NOT NULL, quantity TEXT);
-- Deliveries are versioned facts: a confirmation ADDS a row; the latest row per item is current.
CREATE TABLE deliveries (id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT NOT NULL, supplier TEXT,
  promised_date TEXT NOT NULL, source_doc TEXT, confirmed INTEGER NOT NULL DEFAULT 0,
  confirmed_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE inspections (id TEXT PRIMARY KEY, stage_id TEXT REFERENCES stages(id), name TEXT NOT NULL,
  required INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  closed_by TEXT, closed_at TEXT);
CREATE TABLE findings (fingerprint TEXT PRIMARY KEY, rule_id TEXT, kind TEXT, severity TEXT, title TEXT,
  evidence TEXT, affected TEXT, status TEXT NOT NULL, signature TEXT, first_seen TEXT, updated_at TEXT);
CREATE TABLE reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT, user TEXT,
  decision TEXT, comment TEXT, ts TEXT);
CREATE TABLE specifications (item TEXT PRIMARY KEY, spec_quantity REAL, unit TEXT, source_doc TEXT, page TEXT);
CREATE TABLE estimates (id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT NOT NULL, quantity REAL, unit TEXT,
  source_doc TEXT, confirmed INTEGER NOT NULL DEFAULT 0, confirmed_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE schedule_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, activity_id TEXT, old_start TEXT, new_start TEXT,
  reason TEXT, requested_by TEXT, requested_at TEXT, status TEXT NOT NULL DEFAULT 'pending', decided_by TEXT,
  decided_at TEXT, decision_note TEXT, impact_json TEXT);
CREATE TABLE activity_history (id INTEGER PRIMARY KEY AUTOINCREMENT, activity_id TEXT, old_start TEXT, old_finish TEXT,
  new_start TEXT, new_finish TEXT, change_id INTEGER, changed_at TEXT);
CREATE TABLE explanations (fingerprint TEXT, signature TEXT, summary TEXT, actions_json TEXT, source TEXT,
  created_at TEXT, PRIMARY KEY (fingerprint, signature));
CREATE TABLE pending_sync (id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT, action TEXT, args_json TEXT,
  base_fact_json TEXT, queued_at TEXT, status TEXT NOT NULL DEFAULT 'queued', result_note TEXT, synced_at TEXT);
CREATE TABLE audit_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, user TEXT NOT NULL,
  role TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT,'audit log is append-only'); END;
"""

TABLES = ["pending_sync", "explanations", "activity_history", "schedule_changes", "estimates", "specifications",
          "audit_log", "reviews", "findings", "inspections", "deliveries",
          "requirements", "dependencies", "activities", "stages"]
IMPORT_ORDER = ["stages", "activities", "dependencies", "requirements", "deliveries", "inspections",
                "specifications", "estimates"]
DATE_COLS = {"planned_start", "planned_finish", "actual_start", "promised_date", "closed_at"}


def connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def import_csv(conn, table, path):
    """Import one CSV. Raises ValueError with the row number on bad dates."""
    with open(path, newline="", encoding="utf-8") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            row = {k: (v.strip() or None) for k, v in row.items()}
            for col in DATE_COLS & row.keys():
                if row[col]:
                    try:
                        date.fromisoformat(row[col])
                    except ValueError:
                        raise ValueError(f"{path.name} line {n}: '{row[col]}' is not YYYY-MM-DD ({col})")
            cols = ",".join(row)
            conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({','.join('?' * len(row))})", list(row.values()))


def reset_demo(conn, seed_dir=SEED_DIR):
    conn.executescript("PRAGMA foreign_keys=OFF;" + "".join(f"DROP TABLE IF EXISTS {t};" for t in TABLES))
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys=ON")
    for t in IMPORT_ORDER:
        import_csv(conn, t, seed_dir / f"{t}.csv")
    conn.commit()


def is_seeded(conn):
    try:
        conn.execute("SELECT 1 FROM pending_sync LIMIT 1")      # detects an old database without the new tables
        conn.execute("SELECT 1 FROM specifications LIMIT 1")
        return conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False


# ---------------------------------------------------------------------------------------------
# User data import: validate first (dry run), then replace project data in ONE transaction.
# The audit log is never touched.
# ---------------------------------------------------------------------------------------------
import networkx as nx

REQUIRED = {"activities": ["id", "name", "stage_id", "planned_start", "planned_finish"],
            "dependencies": ["predecessor_id", "successor_id"],
            "requirements": ["id", "activity_id", "item"],
            "deliveries": ["item", "promised_date"],
            "inspections": ["id", "stage_id", "name"]}
OPTIONAL = ["stages", "specifications", "estimates"]
INSERT_ORDER = ["stages", "activities", "dependencies", "requirements", "deliveries", "inspections",
                "specifications", "estimates"]
CLEAR_ORDER = ["findings", "reviews", "explanations", "pending_sync", "schedule_changes", "activity_history",
               "estimates", "specifications", "inspections", "deliveries", "requirements", "dependencies",
               "activities", "stages"]


def _norm_date(v):
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def validate_dataset(frames):
    """frames: {table: [row dicts]}. Returns (clean_frames, errors, notes). Nothing is written."""
    errors, notes, clean = [], [], {}
    for t in list(REQUIRED) + OPTIONAL:
        rows = frames.get(t)
        if rows is None:
            if t in REQUIRED:
                errors.append(f"{t}: file is missing")
            continue
        cols = set(rows[0]) if rows else set()
        miss = [c for c in REQUIRED.get(t, []) if c not in cols]
        if miss:
            errors.append(f"{t}: missing column(s) {', '.join(miss)}")
            continue
        out, converted = [], 0
        for n, r in enumerate(rows, start=2):
            r = {str(k).strip(): (str(v).strip() if v is not None and str(v).strip() != "" else None) for k, v in r.items()}
            for col in DATE_COLS & r.keys():
                if r[col]:
                    nd = _norm_date(r[col])
                    if nd is None:
                        errors.append(f"{t} line {n}: '{r[col]}' is not a valid date ({col}); use YYYY-MM-DD")
                    else:
                        converted += nd != r[col]
                        r[col] = nd
            out.append(r)
        if converted:
            notes.append(f"{t}: {converted} date(s) converted from DD/MM/YYYY to YYYY-MM-DD")
        clean[t] = out
    if errors:
        return clean, errors, notes
    acts = clean["activities"]
    ids = [a["id"] for a in acts]
    for dup in {i for i in ids if ids.count(i) > 1}:
        errors.append(f"activities: duplicate id {dup}")
    if "stages" not in clean:
        clean["stages"] = [{"id": s, "name": s} for s in sorted({a["stage_id"] for a in acts if a["stage_id"]})]
        notes.append("stages: derived from activities.stage_id")
    stage_ids = {s["id"] for s in clean["stages"]}
    for n, a in enumerate(acts, start=2):
        if a["stage_id"] not in stage_ids:
            errors.append(f"activities line {n}: unknown stage {a['stage_id']}")
        if a["planned_start"] and a["planned_finish"] and a["planned_finish"] < a["planned_start"]:
            errors.append(f"activities line {n}: finish is before start ({a['id']})")
        a["status"] = a.get("status") or "planned"
    idset = set(ids)
    for n, d in enumerate(clean["dependencies"], start=2):
        for k in ("predecessor_id", "successor_id"):
            if d[k] not in idset:
                errors.append(f"dependencies line {n}: unknown activity {d[k]}")
    for n, r in enumerate(clean["requirements"], start=2):
        if r["activity_id"] not in idset:
            errors.append(f"requirements line {n}: unknown activity {r['activity_id']}")
    for n, i in enumerate(clean["inspections"], start=2):
        if i["stage_id"] not in stage_ids:
            errors.append(f"inspections line {n}: unknown stage {i['stage_id']}")
        i["required"] = 1 if str(i.get("required") or "1").lower() in ("1", "true", "yes", "oui") else 0
        i["status"] = i.get("status") or "open"
    g = nx.DiGraph([(d["predecessor_id"], d["successor_id"]) for d in clean["dependencies"]])
    if not nx.is_directed_acyclic_graph(g):
        errors.append("dependencies: cycle detected " + " -> ".join(next(nx.simple_cycles(g))))
    return clean, errors, notes


def import_dataset(conn, clean):
    """Replace project data atomically. audit_log is preserved."""
    try:
        for t in CLEAR_ORDER:
            conn.execute(f"DELETE FROM {t}")
        for t in INSERT_ORDER:
            cols_ok = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
            for row in clean.get(t, []):
                row = {k: v for k, v in row.items() if k in cols_ok}
                conn.execute(f"INSERT INTO {t} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
