"""What-if simulator. Pure: never modifies the real state or the database. Costs are ASSUMPTIONS."""
import copy

from . import rules

DEFAULT_ASSUMPTIONS = {"expedite_fee": 150_000, "idle_cost_per_day": 80_000}   # DZD, editable, NOT project data


def simulate(state, scenario=None):
    s = copy.deepcopy(state)
    if scenario:
        if scenario["kind"] == "expedite_delivery":
            s.deliveries[scenario["item"]]["date"] = scenario["new_date"]
        elif scenario["kind"] == "parallelize":
            s.deps = [d for d in s.deps if tuple(d) != tuple(scenario["edge"])]
    risks = [f for f in rules.run_all(s) if f.kind == "risk"]
    slips = rules.combined_forecast(s, risks)
    return {"activities_slipping": len(slips), "worst_slip": max(slips.values(), default=0),
            "open_risks": len(risks), "slips": slips}


def compare(state, scenarios, assumptions=None):
    a = {**DEFAULT_ASSUMPTIONS, **(assumptions or {})}
    rows = []
    labels = {"expedite_delivery": "A: Expedite delivery", "parallelize": "B: Run in parallel"}
    for sc in [None] + list(scenarios):
        name = "Baseline (no action)" if sc is None else sc.get("name") or labels[sc["kind"]]
        r = simulate(state, sc)
        cost = r["worst_slip"] * a["idle_cost_per_day"] + (a["expedite_fee"] if sc and sc["kind"] == "expedite_delivery" else 0)
        rows.append({"scenario": name, "activities_slipping": r["activities_slipping"], "worst_slip": r["worst_slip"],
                     "open_risks": r["open_risks"], "assumed_cost": int(cost)})
    return rows
