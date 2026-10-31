# Project Readiness Radar: Next Steps and Prompt Pack

Put this file in your project as `docs/NEXT_STEPS.md`. Each prompt below is written to be pasted into Roo Code (or any coding assistant) **one at a time**.

## 1. Status

| Area | Status |
|---|---|
| Database schema, CSV import with validation, synthetic data | Done |
| Rules R1, R2, R3, R4 + data-quality checks | Done (tested) |
| Findings lifecycle (confirm / false positive / needs info / auto-resolve / reopen) | Done (tested) |
| Roles, permission checks in the service layer, separation of duties | Done (tested) |
| Hash-chained, append-only audit log + CSV/JSON export | Done (tested) |
| Streamlit UI: Dashboard, Findings, Stages, Audit log | Done |
| AI explainer (Gemini/Groq) | **Not done** |
| PDF verification record | **Not done** |
| Excel/CSV upload page | **Not done** |
| Director approval of schedule changes | **Not done** |
| What-If simulator | **Not done** (stretch) |
| Simulated offline sync | **Not done** (stretch) |
| Steel quantity reconciliation (rule R5) | **Not done** |
| PDF extraction with human confirmation | **Not done** (future work) |

**Recommended order:** 3 (upload) → 4 (schedule-change approval) → 7 (quantity rule) → 2 (PDF record) → 1 (AI explainer) → 5 (what-if) → 6 (offline) → 8 (PDF extraction).
Reason: items 3, 4 and 7 strengthen the core story (rules + human approval). Items 1 and 2 are visible extras. Items 5, 6 and 8 are optional.

## 2. How to use these prompts well (free-tier friendly)

- **One prompt = one feature = one session.** Start a new task for each, so context stays small and you spend less quota.
- **Always paste the Master Context (section 3) first.**
- After each feature: run `python -m pytest -q`, then `streamlit run app.py` and click through it yourself.
- Commit after each working feature (`git init` once, then `git add . && git commit -m "feature: ..."`). If the AI breaks something, you can go back.
- Read every diff. Ask: "what does this function receive, what does it return, which test fails if it's wrong?"
- Use **synthetic data only** with any free-tier API.

## 3. Master Context (paste at the start of every session)

```
You are working on "Project Readiness Radar", a Python/Streamlit/SQLite app: a human-in-the-loop
consistency checker for industrial project plans (plan vs deliveries vs inspections).

ARCHITECTURE (strict layering, do not break it):
- radar/rules.py    : PURE functions. State in, Findings out. No database, no network, no AI.
- radar/services.py : all business actions. ALL permission checks live here (raise PermissionDenied).
                      Every state-changing action must write an audit entry via radar/audit.py.
- radar/db.py       : schema, CSV import, demo reset.
- radar/audit.py    : append-only hash-chained log. Never UPDATE or DELETE audit rows.
- app.py            : Streamlit UI only. No business logic, no SQL.
- tests/            : pytest. Every new rule or action needs tests, including failure cases.

DOMAIN RULES:
- Two separate human actions: operational confirmation (role field_engineer) and formal
  authorization (role project_director). An engineer must never be able to authorize.
- Facts are versioned: a confirmation INSERTS a new row; never overwrite history.
- AI/LLM output is never a source of truth. Rules run only on confirmed data. AI may only
  summarize or propose; a human confirms before anything enters the database.
- Dates are ISO strings (YYYY-MM-DD) in the database, datetime.date in Python.
- Login switcher is a simulated demo device, not authentication; label it so.

CONSTRAINTS:
- Python 3.10+, Windows/PowerShell friendly. Do not add dependencies without asking and listing them.
- Do not change the behaviour of existing rules or existing tests. Keep diffs small.
- Use parameterized SQL (?), never string-built SQL.
- Before declaring done: run `python -m pytest -q` and report the result.
- Use synthetic data only.
```

---

## 4. Prompts

### Prompt 1: AI explainer (Gemini / Groq)

```
TASK: Add a grounded AI explainer for findings, with a guaranteed non-AI fallback.

GOAL: In the Findings page, an engineer clicks "Explain" and gets a short executive summary plus
exactly two concrete frontline actions, generated ONLY from the finding's stored facts.

FILES: create radar/explainer.py; touch services.py (one function), app.py (one button),
requirements.txt (ask before adding), tests/test_explainer.py, .gitignore, .env.example.

REQUIREMENTS:
1. explainer.explain_finding(finding: dict) -> {"summary": str, "actions": [str, str], "source": "ai"|"template"}
   - Input is ONLY: title, severity, evidence, affected (list of activity id/name/slip_days).
2. Provider abstraction: a function chosen by env var EXPLAINER_PROVIDER ("gemini" | "groq" | "none").
   Read keys from GEMINI_API_KEY / GROQ_API_KEY via environment (.env loaded with python-dotenv if present).
   Model name must be configurable via env (EXPLAINER_MODEL); do not hardcode, free-tier model names change.
3. System prompt must instruct: use only the provided JSON; do not add numbers, dates, names or causes
   that are not in it; max 80 words for the summary; output strict JSON {"summary","actions"}.
4. Robustness: 10 s timeout, one retry, parse JSON defensively (strip code fences).
5. Grounding check: reject the AI result and use the template if the output contains any number or
   ISO date that does not appear in the input JSON.
6. Template fallback: deterministic string built from the finding (always works with no API key/offline).
7. Cache results in a new table explanations(fingerprint, signature, summary, actions_json, source, created_at)
   keyed by (fingerprint, signature) so the same finding never costs a second API call.
8. UI: show result with the caption "AI-generated summary. Verify against the evidence above." and show
   whether the source was ai or template.
9. Audit: services.explain_finding(conn, user, fingerprint) writes "explanation_generated" with source.
   The explainer must never modify findings or any other fact.
10. Never log or commit API keys. Add .env to .gitignore and provide .env.example.

TESTS (no real network; inject a fake provider function):
- valid AI JSON accepted; invalid JSON -> template; timeout/exception -> template;
- output with an invented number -> template; cache hit makes no second provider call;
- provider "none" -> template.

DONE WHEN: pytest passes and the feature works with and without an API key.
```

### Prompt 2: PDF verification record

```
TASK: Generate a downloadable "Verification record (DRAFT)" PDF for a finding.

FILES: create radar/pdf_record.py; touch services.py, app.py, requirements.txt (reportlab; pypdf for tests),
tests/test_pdf_record.py.

REQUIREMENTS:
1. pdf_record.build_record(data: dict) -> bytes using reportlab (pure function, no database access).
2. services.generate_record(conn, user, fingerprint, site_location: str) -> bytes
   - role: field_engineer only (raise PermissionDenied otherwise).
   - gathers: finding (title, severity, status, evidence), affected activities, full review history,
     the latest verified delivery facts related to the finding, current stage status.
   - writes audit entry "record_generated" with the SHA-256 of the produced PDF bytes.
3. PDF content: title "Verification record (DRAFT)", project name, generated-at UTC timestamp,
   finding id/title/severity, evidence table, affected-activity table, review history table,
   "Site location (entered by user)" field showing the typed text (never imply it was captured by GPS),
   a sign-off block with lines for Field Engineer and Project Director (name, date, signature),
   and a footer: page number + the document's SHA-256 + the notice
   "Draft record. A typed name or timestamp is not a legally binding signature."
4. UI: in the Findings expander, a text input for site location and a download_button for the PDF.
5. Keep Arabic/French characters safe: register a TTF font (DejaVu Sans) if available, otherwise
   fall back to Helvetica and strip unsupported characters rather than crash.

TESTS: output starts with b"%PDF"; extracted text (pypdf) contains finding title and the DRAFT notice;
director cannot generate (PermissionDenied); audit row created with matching hash; empty review history
renders "No reviews yet" instead of failing.

DONE WHEN: pytest passes and the PDF opens correctly in a viewer.
```

### Prompt 3: Excel/CSV upload page

```
TASK: Add an "Import" page so users can load their own project data (CSV or XLSX) instead of editing seed files.

FILES: touch db.py (transactional import function), services.py, app.py, requirements.txt (openpyxl),
create data/templates/*.csv (empty templates with headers + one example row), tests/test_import.py.

REQUIREMENTS:
1. Accept 5 files: activities, dependencies, requirements, deliveries, inspections (CSV or XLSX; for XLSX
   read the first sheet). Stages are derived from distinct stage_id values if no stages file is given.
2. Provide download buttons for each template.
3. VALIDATION FIRST (dry run), never touch the database until the user confirms. Report errors with
   file name and row number:
   - required columns present; no duplicate IDs; dates are YYYY-MM-DD (also accept DD/MM/YYYY and
     convert, showing the conversion);
   - every dependency/requirement/inspection references an existing activity/stage;
   - finish >= start; dependency graph has no cycle (show the cycle path);
   - warnings (not errors): activities without requirements, requirements without deliveries.
4. Show a preview (row counts per table + first 5 rows) and a button "Commit import".
5. db.import_dataset(conn, frames) runs in ONE transaction: on any error, rollback and leave data untouched.
   Importing REPLACES project data (activities, dependencies, requirements, deliveries, inspections, stages,
   findings, reviews) but MUST PRESERVE audit_log. Do not reuse reset_demo (it drops the audit log).
6. services.import_project_data(conn, user, frames, filenames): role field_engineer; writes audit
   "data_imported" with file names, SHA-256 of each file and row counts; then calls recalc().
7. UI warns before commit: "This replaces current project data. The audit log is kept."

TESTS: valid import works and creates findings; bad date reports the exact row; unknown activity id is
rejected; cycle is rejected; failed import leaves previous data intact (rollback); audit log survives
import and verify() still returns True; director role is refused.

DONE WHEN: pytest passes and you can import the template files back successfully.
```

### Prompt 4: Director approval of schedule changes

```
TASK: Implement a change-request workflow: engineers REQUEST schedule changes, only the Project Director
APPROVES them. Approved changes update the plan; rejected ones change nothing.

FILES: touch db.py (tables), services.py, app.py (new page "Change requests"), tests/test_changes.py.

DATA MODEL:
- schedule_changes(id INTEGER PK, activity_id, new_start, new_finish, reason, requested_by, requested_at,
  status CHECK IN ('pending','approved','rejected'), decided_by, decided_at, decision_note, impact_json)
- activity_history(id, activity_id, old_start, old_finish, new_start, new_finish, change_id, changed_at)

REQUIREMENTS:
1. services.preview_change_impact(conn, activity_id, new_start, new_finish) -> list of affected
   activities with slip days, computed by rules.propagate (reuse it; do not reimplement).
2. services.request_schedule_change(...): role field_engineer; reason required; new_finish >= new_start;
   stores the impact snapshot in impact_json; audit "change_requested".
3. services.decide_schedule_change(conn, user, change_id, decision, note): role project_director only.
   - separation of duties: the approver must not be the requester;
   - decision note required;
   - status must be 'pending' (no double decision);
   - on approval, in ONE transaction: write activity_history, update the activity dates, audit
     "change_approved", then recalc(); on rejection only audit "change_rejected".
4. UI page: engineers see a request form with a live impact preview; directors see pending requests
   with the stored impact table and Approve/Reject buttons plus a note field. Everyone sees history.
5. Edge cases: requesting a change for a completed activity is rejected; requesting identical dates is rejected.

TESTS: engineer cannot approve (PermissionDenied); director cannot request; self-approval blocked;
approval changes dates and records history; approving a pump-related move resolves the R1 finding after
recalc; rejection leaves dates unchanged; double decision rejected; audit chain still verifies.

DONE WHEN: pytest passes and the full request -> approve flow works in the UI.
```

### Prompt 5: What-If simulator (stretch)

```
TASK: Add a what-if simulator that compares two mitigation scenarios against the baseline. All numbers
come from deterministic computation; an LLM may only phrase the result.

FILES: create radar/whatif.py (pure), data/seed/assumptions.csv, touch app.py (page "What-If"),
tests/test_whatif.py. Reuse radar/explainer.py provider if it exists; otherwise use a template narrator.

REQUIREMENTS:
1. whatif.py defines a Scenario dataclass with kinds:
   - "expedite_delivery": item, new_date, assumed expedite cost
   - "shift_activity_start": activity_id, new_start
   - "remove_dependency": predecessor_id, successor_id (models running activities in parallel)
2. whatif.simulate(state, scenario) -> {slips: {activity: days}, affected_count, worst_slip, project_finish_delta,
   assumed_cost, assumptions_used}. It MUST deep-copy state; it must never modify the original or the database.
3. whatif.compare(state, scenario_a, scenario_b) returns baseline, A and B side by side.
4. Costs come ONLY from assumptions.csv (expedite_fee_per_item, idle_crew_cost_per_day, ...). Each use is
   labelled "ASSUMPTION (editable)" in the UI. Never let an LLM invent a cost figure. If no assumption
   exists for something, show "no cost assumption" instead of a number.
5. UI: pick two scenarios from dropdowns, show a comparison table + st.bar_chart of worst slip, then a
   3-bullet trade-off summary. Narration receives only the computed JSON and may not add numbers
   (apply the same grounding check as the explainer). Fallback to a template on any failure.
6. The simulator must not write findings or facts. Audit entry "whatif_run" is optional but recommended.

TESTS: baseline equals services.forecast; expediting the pump to the activity start gives zero slip;
the original state object is unchanged after simulate (compare deep copies); removing a dependency
shortens or equals the slip, never lengthens it; missing assumption does not crash.

DONE WHEN: pytest passes and two scenarios produce visibly different results on the seed data.
```

### Prompt 6: Simulated offline sync (stretch)

```
TASK: Add a clearly-labelled SIMULATED offline mode for field engineers. It is a UI/queue simulation,
not real network detection.

FILES: touch db.py (table pending_sync), services.py, app.py (sidebar toggle), tests/test_offline.py.

DATA MODEL: pending_sync(id INTEGER PK, user, action, args_json, base_fact_json, queued_at,
status CHECK IN ('queued','synced','conflict','failed'), result_note, synced_at)

REQUIREMENTS:
1. Sidebar toggle "Simulate offline (field mode)". Label: "Simulated offline sync".
2. While offline, ONLY operational engineer actions are queued instead of executed:
   confirm_delivery_date, review_finding, close_inspection. Director actions (authorize_stage,
   decide_schedule_change) are NOT available offline: formal authorization requires being online. Show why.
3. Queue persists in SQLite (not st.session_state) so a browser refresh does not lose it.
4. Each queued item stores the device time (queued_at) and a snapshot of the fact it depends on
   (e.g. the delivery date it saw) as base_fact_json.
5. Dashboard banner while offline: "Showing data as of last sync. N updates pending." No recalculation runs offline.
6. On going online, sync replays queued items IN ORDER through the normal services functions (so all
   permission checks and audit entries still apply):
   - if the underlying fact changed since queued (base_fact differs), mark 'conflict' and do NOT apply;
     show both values and let the engineer choose "apply mine" or "discard";
   - if a service call raises, mark 'failed' with the message and continue with the next item.
   Then run recalc() once at the end.
7. Audit: "offline_action_queued" (with device time), "offline_action_synced", "offline_conflict".
   The synced action's audit payload must include performed_at_device.

TESTS: queued action does not change facts until sync; sync applies in order; conflict detected when
another change happened in between; failed item does not block the rest; director action refused offline;
queue survives a new connection; audit contains device time.

DONE WHEN: pytest passes and the offline -> queue -> online -> sync loop works in the UI.
```

### Prompt 7: Steel quantity reconciliation (rule R5)

```
TASK: Add rule R5 "Quantity mismatch": compare the estimated quantity of an item against the quantity
specified in the technical documents, within a tolerance.

FILES: touch rules.py, db.py (schema + import), services.py, app.py, data/seed/specifications.csv,
tests/test_quantity.py.

DATA MODEL:
- specifications(item, spec_quantity REAL, unit, source_doc, page)
- requirements gets numeric columns: quantity REAL, unit (migrate the seed CSV: Rebar B500 steel = 18 t)
- estimates are versioned facts: table quantity_estimates(id, item, quantity, unit, source, confirmed,
  confirmed_by, created_at); the latest row per item is current (same pattern as deliveries).
- settings(key, value): quantity_tolerance_pct default 5.

RULE R5 (pure function in rules.py, State gets a `specs` dict):
- deviation_pct = (estimate - spec) / spec * 100
- flag when abs(deviation_pct) > tolerance. Severity: > 15% high, > 5% medium, else low.
  Exactly at the tolerance is NOT flagged.
- evidence: item, estimate, spec, unit, deviation_pct (1 decimal), direction ("under"/"over"),
  both source documents with page numbers.
- fingerprint: "R5:{activity_id}:{item}".
- data gaps (not risks): spec missing; unit mismatch (do NOT convert silently); spec_quantity == 0
  (avoid division by zero).
- seed data: set steel spec to 21.5 t so the estimate of 18 t shows -16.3% (high, under-estimated).

SERVICES: confirm_quantity_estimate(conn, user, item, new_quantity, note): role field_engineer,
note required, inserts a new estimate row, audit "quantity_confirmed", then recalc().

UI: R5 findings show estimate vs specification side by side and a form to record a verified quantity.

TESTS: boundary exactly at tolerance not flagged; just above flagged; over vs under direction; unit mismatch
is a data gap; zero spec handled; confirming the corrected quantity auto-resolves the finding; false
positive reopens when the estimate changes; all existing tests still pass.

DONE WHEN: pytest passes and the demo shows the steel mismatch with evidence.
```

### Prompt 8: PDF extraction with human confirmation (future work)

```
TASK: Let an engineer upload a supplier quotation PDF, propose structured delivery facts using an LLM,
and require explicit human confirmation before anything enters the database.

FILES: create radar/extraction.py; touch db.py (table proposed_facts), services.py, app.py (page
"Review extractions"), requirements.txt (pdfplumber; ask first), tests/test_extraction.py.

DATA MODEL: proposed_facts(id, file_name, file_sha256, page, item, supplier, promised_date, quantity, unit,
quote, status CHECK IN ('proposed','accepted','rejected'), proposed_at, decided_by, decided_at)

REQUIREMENTS:
1. extraction.extract_pages(pdf_bytes) -> list of (page_number, text) using pdfplumber. If a page has no
   text (scanned PDF), report "No text layer; OCR is not supported" and skip it. No OCR in this task.
2. extraction.propose(pages, llm_call) -> list of proposals. llm_call is an injected function so tests can
   fake it. The prompt requires strict JSON: [{item, supplier, promised_date (ISO), quantity, unit, page,
   quote (max 25 words copied verbatim from the page), confidence (0-1)}]. Instruct: never guess;
   omit anything not explicitly present.
3. GROUNDING CHECK (mandatory): discard a proposal if its quote is not found verbatim in the text of the
   stated page, or if its promised_date/quantity is not present in that quote. Report how many were discarded.
4. services.store_proposals(...) saves as status 'proposed'. RULES AND FORECASTS NEVER READ proposed_facts.
5. services.decide_proposal(conn, user, id, decision, edited_values=None): role field_engineer.
   - Accept (optionally after edits) inserts into deliveries with confirmed=1, source_doc "file.pdf p.3",
     confirmed_by user; then recalc(). Reject only records the decision.
   - Audit: "extraction_proposed", "extraction_accepted"/"extraction_rejected" including file SHA-256.
6. UI: upload, then show each proposal next to the source page text with the quote highlighted;
   buttons Accept / Edit & accept / Reject. Show a warning banner: "Free-tier AI APIs may use prompts for
   training. Do not upload confidential documents."
7. Files are processed in memory; do not store the PDF unless the user opts in.

TESTS (fake llm_call): hallucinated quote is discarded; accepted proposal creates a confirmed delivery
and triggers recalc; rejected proposal changes no facts; scanned page handled gracefully; director cannot
accept; malformed LLM JSON -> clear error, no crash.

DONE WHEN: pytest passes and a sample synthetic quotation PDF flows through propose -> review -> accept.
```

---

## 5. Definition of done (for every feature)

- [ ] `python -m pytest -q` passes, including the old tests
- [ ] New permission checks are in `services.py`, not just the UI
- [ ] Every state change writes an audit entry, and `audit.verify()` still returns True
- [ ] No API keys or real company data in the repository
- [ ] README updated (one paragraph + limitations)
- [ ] You can explain the new code in one minute without looking at it
