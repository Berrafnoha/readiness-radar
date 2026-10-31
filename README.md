# Project Readiness Radar

Human-in-the-loop consistency checker that sits on top of existing planning sheets (P6 / MS Project / Excel exports).
It cross-checks plan vs. deliveries vs. quantities vs. inspections with **deterministic rules**, shows the evidence,
and keeps **operational confirmation** (Field Engineer) separate from **formal authorization** (Project Director).
All demo data is synthetic.

## Run
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m pytest -q              # 34 tests should pass
python -m uvicorn api:app --reload
```
Optional AI explainer (works without it, using a template fallback):
```powershell
$env:GEMINI_API_KEY="your_key"         
$env:EXPLAINER_MODEL="gemini-3.5-flash"
streamlit run app.py
```

## Architecture
```
app.py (UI) -> radar/services.py (permissions + actions) -> radar/rules.py (pure rules) -> SQLite
                  |-> audit.py (hash-chained log)  |-> explainer.py (AI, grounded)  |-> pdf_record.py  |-> whatif.py
```

## Rules
| ID | What it detects |
|----|-----------------|
| R1 | Activity starts before a required item is delivered |
| R2 | Successor starts / planned before predecessor is complete |
| R3 | Stage marked ready/authorized with a required inspection open |
| R4 | Downstream impact: dependency-graph forward pass (NetworkX), enriches R1-R3 and drives the forecast |
| R5 | Estimated quantity differs from the specified quantity beyond a tolerance (default 5%) |
| DQ | Data gaps: dependency cycle, bad dates, no requirement, no delivery, unit mismatch, zero spec |

## Features
- Dashboard: plan vs forecast; Findings with evidence and review workflow (valid / false positive / needs info)
- Stages: engineer submits, director authorizes (blocked while inspections are open; separation of duties)
- Change requests: engineer requests a schedule change with impact preview; only the director approves; history kept
- What-if: compares two mitigation scenarios; schedule impact computed by the engine, costs are editable assumptions
- Import: upload CSV/XLSX with dry-run validation (row-level errors), atomic replace, audit log preserved
- Simulated offline sync: engineer updates queue in SQLite, conflicts detected on sync, director actions disabled offline
- AI explainer (Gemini/Groq): sees only computed finding facts; output rejected if it contains numbers not in the facts
- PDF "Verification record (DRAFT)" with evidence, review history, sign-off block and content hash
- Audit log: append-only (DB triggers) and hash-chained; verify button; CSV/JSON export

## Honest limitations
- Login switcher is a demo device, not authentication. Offline mode is a simulation of queue-and-sync, not network detection.
- Site location on the PDF is typed by the user, not captured by GPS. A typed name is not a legal signature.
- Dependencies are finish-to-start only, calendar days, no resource levelling: a consistency checker, not a planner.
- Approving a change shifts the activity and its downstream chain by the stored impact snapshot.
- Not built: PDF/quotation extraction with human confirmation (see docs/NEXT_STEPS.md, prompt 8), real authentication, P6 XER import.
- Audit chain is tamper-evident, not tamper-proof (someone with raw DB access could rebuild it).

## Demo script (6 minutes)
1. Dashboard: 4 risks, 4 data gaps, 6 activities forecast to slip.
2. Findings > pump (R1): evidence + 6 affected activities. Click "Explain this finding".
3. Findings > steel (R5): estimate 18 t vs specified 21.5 t (-16.3%). Concrete (-3.4%) is correctly NOT flagged.
4. As Karim (engineer): record verified pump date 2026-10-25 + source -> finding auto-resolves, forecast shrinks.
5. Stages: submit S1 as ready -> R3 fires. As Samira (director): authorize fails. Karim closes I2, Samira authorizes.
6. Change requests: Karim requests A4 start 2026-10-29 with impact preview; Samira approves.
7. Offline: toggle offline, record an update, show the queue, toggle back, Sync now.
8. Generate the PDF record. Audit log: Verify integrity, export.

## Web UI (FastAPI + single-page app)
```powershell
python -m uvicorn api:app --reload      # then open http://127.0.0.1:8000
```
`api.py` only maps HTTP to `radar/services.py` (permissions stay in services). The page is `web/index.html` (no build step).
The Streamlit app (`streamlit run app.py`) remains and has the extra pages: What-if, Import, Change requests, Sync queue, PDF.
