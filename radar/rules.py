"""Deterministic rules. Pure functions: State in, Findings out. No database, no AI."""
from dataclasses import dataclass, field
from datetime import date, timedelta
import networkx as nx


@dataclass
class State:
    activities: dict      # id -> {id,name,stage,start,finish,actual_start,status}
    deps: list            # [(predecessor_id, successor_id)]
    reqs: list            # [{id, activity_id, item, quantity}]
    deliveries: dict      # item -> {date, supplier, source, confirmed}  (latest version)
    inspections: list     # [{id, stage_id, name, required, status}]
    stages: dict          # id -> {id, name, status}
    specs: dict = field(default_factory=dict)       # item -> {qty, unit, source, page}
    estimates: dict = field(default_factory=dict)   # item -> {qty, unit, source, confirmed} (latest)


@dataclass
class Finding:
    fingerprint: str
    rule_id: str
    kind: str             # 'risk' or 'data_gap'
    severity: str         # low / medium / high / critical
    title: str
    evidence: dict
    affected: list = field(default_factory=list)
    forced: dict = field(default_factory=dict)   # {activity_id: earliest feasible start} (not stored)


def build_graph(state):
    g = nx.DiGraph()
    g.add_nodes_from(state.activities)
    g.add_edges_from(state.deps)
    return g


def propagate(state, forced):
    """R4 core. Forward pass over the dependency graph.
    forced = {activity_id: earliest feasible start}. Durations are kept.
    A successor's lag to each predecessor is preserved but capped at 1 day, so spare float is not
    counted as a delay. Returns {activity_id: finish_slip_days} for activities that slip."""
    g = build_graph(state)
    if not nx.is_directed_acyclic_graph(g):
        return {}
    A, new_finish = state.activities, {}
    for a in nx.topological_sort(g):
        act = A[a]
        dur = act["finish"] - act["start"]
        s = max(act["start"], forced.get(a, act["start"]))
        for p in g.predecessors(a):
            lag = min((act["start"] - A[p]["finish"]).days, 1)
            s = max(s, new_finish[p] + timedelta(days=lag))
        new_finish[a] = s + dur
    return {a: (new_finish[a] - A[a]["finish"]).days for a in A if new_finish[a] > A[a]["finish"]}


def _affected(state, forced):
    slips = propagate(state, forced)
    order = sorted(slips, key=lambda a: (state.activities[a]["start"], a))
    return [{"id": a, "name": state.activities[a]["name"], "slip_days": slips[a]} for a in order]


def data_quality(state):
    out = []
    g = build_graph(state)
    if not nx.is_directed_acyclic_graph(g):
        cyc = [c for c in nx.simple_cycles(g)][:1]
        out.append(Finding("DQ-CYCLE", "DQ-CYCLE", "data_gap", "critical",
                           "Dependency cycle: schedule logic is invalid, impact analysis disabled",
                           {"cycle": cyc[0] if cyc else []}))
    for a in state.activities.values():
        if a["finish"] < a["start"]:
            out.append(Finding(f"DQ-DATES:{a['id']}", "DQ-DATES", "data_gap", "high",
                               f"{a['name']}: finish date is before start date",
                               {"activity": a["id"], "start": a["start"].isoformat(), "finish": a["finish"].isoformat()}))
    needs = {r["activity_id"] for r in state.reqs}
    for a in state.activities.values():
        if a["status"] != "complete" and a["id"] not in needs:
            out.append(Finding(f"DQ-NOREQ:{a['id']}", "DQ-NOREQ", "data_gap", "low",
                               f"{a['name']}: no equipment or material requirement identified",
                               {"activity": a["id"]}))
    for r in state.reqs:
        if r["item"] not in state.deliveries and state.activities[r["activity_id"]]["status"] != "complete":
            a = state.activities[r["activity_id"]]
            out.append(Finding(f"DQ-NODELIVERY:{a['id']}:{r['item']}", "DQ-NODELIVERY", "data_gap", "medium",
                               f"{r['item']} (needed by {a['name']}): no delivery date on record",
                               {"activity": a["id"], "item": r["item"]}))
    return out


def check_delivery_vs_start(state):
    """R1: activity starts before a required item is delivered."""
    out = []
    for r in state.reqs:
        act, d = state.activities[r["activity_id"]], state.deliveries.get(r["item"])
        if act["status"] == "complete" or not d:
            continue
        delta = (d["date"] - act["start"]).days
        if delta <= 0:
            continue
        forced = {act["id"]: d["date"]}
        out.append(Finding(
            f"R1:{act['id']}:{r['item']}", "R1", "risk",
            "high" if delta >= 5 else "medium" if delta >= 2 else "low",
            f"{act['name']} starts {delta} day(s) before {r['item']} arrives",
            {"item": r["item"], "supplier": d["supplier"], "source": d["source"], "verified": d["confirmed"],
             "activity": act["id"], "activity_start": act["start"].isoformat(),
             "delivery_date": d["date"].isoformat(), "delta_days": delta},
            _affected(state, forced), forced))
    return out


def check_predecessor_readiness(state):
    """R2: successor starts (or is planned to start) before its predecessor is finished."""
    out = []
    for p, s in state.deps:
        P, S = state.activities[p], state.activities[s]
        if S["status"] == "complete" or P["status"] == "complete":
            continue
        started_early = S["actual_start"] is not None
        overlap = (P["finish"] - S["start"]).days
        if not started_early and overlap <= 0:
            continue
        forced = {s: P["finish"] + timedelta(days=1)}
        out.append(Finding(
            f"R2:{p}:{s}", "R2", "risk", "high" if started_early else "medium",
            f"{S['name']} " + ("has started" if started_early else f"is planned {overlap} day(s) early")
            + f" but predecessor '{P['name']}' is not complete",
            {"predecessor": p, "successor": s, "predecessor_finish": P["finish"].isoformat(),
             "successor_start": S["start"].isoformat(), "overlap_days": max(overlap, 0),
             "successor_actual_start": S["actual_start"].isoformat() if started_early else None,
             "predecessor_status": P["status"]},
            _affected(state, forced), forced))
    return out


def check_stage_gate(state):
    """R3: stage marked ready/authorized while a required inspection is still open."""
    out = []
    for st in state.stages.values():
        if st["status"] not in ("ready_requested", "authorized"):
            continue
        open_i = [i for i in state.inspections if i["stage_id"] == st["id"] and i["required"] and i["status"] != "closed"]
        if open_i:
            out.append(Finding(
                f"R3:{st['id']}", "R3", "risk", "critical" if st["status"] == "authorized" else "high",
                f"Stage {st['name']} is '{st['status']}' with {len(open_i)} required inspection(s) still open",
                {"stage": st["id"], "stage_status": st["status"],
                 "open_inspections": [{"id": i["id"], "name": i["name"]} for i in open_i]}))
    return out


QUANTITY_TOLERANCE_PCT = 5.0


def check_quantity(state, tolerance=QUANTITY_TOLERANCE_PCT):
    """R5: estimated quantity deviates from the specified quantity by more than the tolerance.
    Unit mismatches and zero specifications are reported as data gaps, never silently converted."""
    out = []
    for item, e in state.estimates.items():
        sp = state.specs.get(item)
        if not sp:
            continue
        if (e["unit"] or "").strip().lower() != (sp["unit"] or "").strip().lower():
            out.append(Finding(f"DQ-UNIT:{item}", "DQ-UNIT", "data_gap", "medium",
                               f"{item}: estimate unit '{e['unit']}' differs from specification unit '{sp['unit']}', not compared",
                               {"item": item, "estimate_unit": e["unit"], "spec_unit": sp["unit"]}))
            continue
        if not sp["qty"]:
            out.append(Finding(f"DQ-SPEC0:{item}", "DQ-SPEC0", "data_gap", "medium",
                               f"{item}: specified quantity is zero or empty, not compared", {"item": item}))
            continue
        dev = round((e["qty"] - sp["qty"]) / sp["qty"] * 100, 6)
        if abs(dev) <= tolerance:
            continue
        direction = "under" if dev < 0 else "over"
        out.append(Finding(
            f"R5:{item}", "R5", "risk", "high" if abs(dev) > 15 else "medium",
            f"{item}: estimate {e['qty']:g} {e['unit']} is {abs(dev):.1f}% {'below' if dev < 0 else 'above'} the specified {sp['qty']:g} {sp['unit']}",
            {"item": item, "estimate": e["qty"], "specified": sp["qty"], "unit": sp["unit"],
             "deviation_pct": round(dev, 1), "direction": direction, "tolerance_pct": tolerance,
             "estimate_source": e["source"], "spec_source": f"{sp['source']} {sp['page'] or ''}".strip(),
             "activities": sorted({r["activity_id"] for r in state.reqs if r["item"] == item})}))
    return out


def run_all(state):
    return (data_quality(state) + check_delivery_vs_start(state)
            + check_predecessor_readiness(state) + check_stage_gate(state) + check_quantity(state))


def combined_forecast(state, findings):
    """Forecast with ALL given findings applied together -> {activity_id: finish_slip_days}."""
    forced = {}
    for f in findings:
        for a, d in f.forced.items():
            forced[a] = max(d, forced.get(a, d))
    return propagate(state, forced)
