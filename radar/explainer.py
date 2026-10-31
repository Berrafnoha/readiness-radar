"""Grounded AI explainer. The LLM only PHRASES facts that rules already computed.
Any failure, missing key, or ungrounded output falls back to a deterministic template."""
import json
import os
import re
import urllib.request


def provider():
    p = os.environ.get("EXPLAINER_PROVIDER", "").lower()
    if p in ("gemini", "groq", "none"):
        return p
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    return "none"


def describe():
    p = provider()
    return "template fallback (no API key set)" if p == "none" else f"{p} (falls back to template on any error)"


def _post(url, payload, headers):
    req = urllib.request.Request(url, json.dumps(payload).encode(), {"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode())


def _call_gemini(prompt):
    model = os.environ.get("EXPLAINER_MODEL", "gemini-3.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={os.environ['GEMINI_API_KEY']}"
    d = _post(url, {"contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json"}}, {})
    return d["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq(prompt):
    model = os.environ.get("EXPLAINER_MODEL", "llama-3.3-70b-versatile")
    d = _post("https://api.groq.com/openai/v1/chat/completions",
              {"model": model, "messages": [{"role": "user", "content": prompt}],
               "response_format": {"type": "json_object"}},
              {"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
    return d["choices"][0]["message"]["content"]


def _ask(prompt):
    fn = {"gemini": _call_gemini, "groq": _call_groq}.get(provider())
    if fn is None:
        raise RuntimeError("no provider configured")
    try:
        return fn(prompt)
    except Exception:           # one retry
        return fn(prompt)


def _parse(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text)


def _numbers(x):
    return set(re.findall(r"\d+", x if isinstance(x, str) else json.dumps(x, default=str)))


def grounded(output_text, source):
    """Every number in the AI text must already exist in the source facts."""
    return _numbers(output_text) <= _numbers(source)


RULES = ("Use ONLY the facts in the JSON. Do not add numbers, dates, names, causes or costs that are not in it. "
         "Do not use numbered lists. Plain professional English.")


def template_explanation(f):
    ev, aff, rid = f["evidence"], f["affected"], f["rule_id"]
    n = len(aff)
    worst = max((a["slip_days"] for a in aff), default=0)
    impact = f" If unresolved, {n} activities slip, the worst by {worst} day(s)." if n else ""
    if rid == "R1":
        return {"summary": f"{f['title']}. The delivery date comes from {ev['source']} (supplier: {ev['supplier']}).{impact}",
                "actions": [f"Contact {ev['supplier']} to confirm or expedite {ev['item']}.",
                            "If the date cannot move, submit a schedule change request for the affected activity."], "source": "template"}
    if rid == "R2":
        return {"summary": f"{f['title']}.{impact}",
                "actions": ["Check on site whether the predecessor is really finished.",
                            "Resequence the activities or request a schedule change."], "source": "template"}
    if rid == "R3":
        names = ", ".join(i["name"] for i in ev["open_inspections"])
        return {"summary": f"{f['title']}. Open: {names}.",
                "actions": ["Complete and record the open inspection(s).", "Resubmit the stage for authorization once closed."], "source": "template"}
    if rid == "R5":
        return {"summary": f"{f['title']}. Sources: {ev['estimate_source']} vs {ev['spec_source']}.",
                "actions": ["Re-check the quantity take-off against the specification.",
                            "Record the verified quantity, or confirm that the deviation is intended."], "source": "template"}
    return {"summary": f["title"] + ".", "actions": ["Verify the source data.", "Record your decision with a comment."], "source": "template"}


def explain_finding(f, llm=None):
    """f: {title, severity, rule_id, evidence, affected}. llm: optional callable(prompt)->str (tests)."""
    if llm is None and provider() == "none":
        return template_explanation(f)
    facts = {k: f[k] for k in ("title", "severity", "rule_id", "evidence", "affected")}
    prompt = (f"{RULES}\nFINDING JSON:\n{json.dumps(facts, default=str)}\n"
              'Return JSON: {"summary": "max 70 words", "actions": ["action 1", "action 2"]}. Exactly 2 actions.')
    try:
        d = _parse((llm or _ask)(prompt))
        summary, actions = str(d["summary"]).strip(), [str(a).strip() for a in d["actions"]][:2]
        if not summary or len(actions) < 2 or not grounded(summary + " " + " ".join(actions), facts):
            raise ValueError("empty or ungrounded")
        return {"summary": summary, "actions": actions, "source": "ai"}
    except Exception:
        return template_explanation(f)


def narrate_whatif(rows, llm=None):
    """rows: computed scenario results. Returns 3 bullet strings (AI phrasing or template)."""
    base = rows[0]
    template = [f"Baseline: {base['activities_slipping']} activities slip, worst {base['worst_slip']} day(s)."]
    template += [f"{r['scenario']}: {r['activities_slipping']} activities slip, worst {r['worst_slip']} day(s); "
                 f"assumed cost {r['assumed_cost']:,} DZD." for r in rows[1:3]]
    template = template[:3]
    template[-1] += " Costs are editable assumptions, not project data. Verify feasibility with the supplier and planner."
    if llm is None and provider() == "none":
        return template_explanation(f)
    facts = {k: f[k] for k in ("title", "severity", "rule_id", "evidence", "affected")}
    prompt = (f"{RULES}\nFINDING JSON:\n{json.dumps(facts, default=str)}\n"
              'Return JSON: {"summary": "max 70 words", "actions": ["action 1", "action 2"]}. Exactly 2 actions.')
    try:
        d = _parse((llm or _ask)(prompt))
        summary, actions = str(d["summary"]).strip(), [str(a).strip() for a in d["actions"]][:2]
        if not summary or len(actions) < 2 or not grounded(summary + " " + " ".join(actions), facts):
            raise ValueError("empty or ungrounded")
        return {"summary": summary, "actions": actions, "source": "ai"}
    except Exception:
        return template_explanation(f)


def narrate_whatif(rows, llm=None):
    """rows: computed scenario results. Returns 3 bullet strings (AI phrasing or template)."""
    base = rows[0]
    template = [f"Baseline: {base['activities_slipping']} activities slip, worst {base['worst_slip']} day(s)."]
    template += [f"{r['scenario']}: {r['activities_slipping']} activities slip, worst {r['worst_slip']} day(s); "
                 f"assumed cost {r['assumed_cost']:,} DZD." for r in rows[1:3]]
    template = (template + ["Costs are editable assumptions, not project data."])[:3] if len(rows) < 3 else template[:2] + [template[2]]
    template[-1] = template[-1] + " Costs are editable assumptions, not project data. Verify feasibility with the supplier and planner."
    if llm is None and provider() == "none":
        return template
    prompt = (f"{RULES}\nSCENARIO RESULTS JSON:\n{json.dumps(rows, default=str)}\n"
              'Write a 3-bullet cost/schedule trade-off. Return JSON: {"bullets": ["...","...","..."]}')
    try:
        bullets = [str(b) for b in _parse((llm or _ask)(prompt))["bullets"]][:3]
        if len(bullets) < 3 or not grounded(" ".join(bullets), rows):
            raise ValueError
        return bullets
    except Exception:
        return template
