"""Append-only, hash-chained audit log (tamper-evident)."""
import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 64


def _hash(prev, ts, user, role, action, payload):
    return hashlib.sha256(f"{prev}|{ts}|{user}|{role}|{action}|{payload}".encode()).hexdigest()


def append(conn, user, role, action, payload=None):
    payload = json.dumps(payload or {}, sort_keys=True, default=str)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else GENESIS
    conn.execute("INSERT INTO audit_log (ts,user,role,action,payload,prev_hash,hash) VALUES (?,?,?,?,?,?,?)",
                 (ts, user, role, action, payload, prev, _hash(prev, ts, user, role, action, payload)))


def verify(conn):
    """Recompute the chain. Returns (True, n_rows) or (False, seq_of_first_bad_row)."""
    prev = GENESIS
    rows = conn.execute("SELECT * FROM audit_log ORDER BY seq").fetchall()
    for r in rows:
        if r["prev_hash"] != prev or r["hash"] != _hash(prev, r["ts"], r["user"], r["role"], r["action"], r["payload"]):
            return False, r["seq"]
        prev = r["hash"]
    return True, len(rows)


def rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY seq")]
