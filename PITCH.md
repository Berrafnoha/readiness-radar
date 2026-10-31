# Project Readiness Radar: what it is and how to pitch it

## What the project does, exactly

**One line:** an app that automatically cross-checks a project's plan against its deliveries, quantities and inspection records, flags inconsistencies with evidence, and makes sure a human verifies each fact and the right person authorizes each decision.

**The problem.** On industrial projects, the schedule, supplier deliveries, quantity estimates and inspection records live in separate files owned by different people. Nobody systematically checks that they agree until something goes wrong on site: a pump arrives after the crew needs it, a stage is declared ready while an inspection is still open, a steel estimate doesn't match the specification.

**What the app does.**
1. **Imports** exports from existing planning sheets (CSV/Excel). It does not replace the planner: it sits on top of it.
2. **Checks** consistency with explicit rules (no guessing):
   - R1: an activity starts before a required item is delivered
   - R2: an activity starts before its predecessor is finished
   - R3: a stage is declared ready with a required inspection still open
   - R4: if something slips, which later activities slip and by how much (dependency graph)
   - R5: an estimated quantity (e.g. steel) differs from the specified quantity beyond a tolerance
   - Data gaps: missing deliveries, missing requirements, cycles, bad dates, unit mismatches (reported separately, so gaps are never mistaken for a safe plan)
3. **Shows the evidence** for every alert: values, source documents, affected activities.
4. **Waits for a human.** The Field Engineer verifies facts (new delivery date, closed inspection, corrected quantity) or marks the alert as a false positive. Every decision needs a comment.
5. **Recalculates** after each confirmed fact. Risks that no longer hold close automatically; false positives stay suppressed until the facts change.
6. **Separates authority.** Only the Project Director can authorize a stage or approve a schedule change. The check lives in the code, not just the buttons.
7. **Records everything** in an append-only, hash-chained audit log, exportable as CSV/JSON, and can produce a draft verification record (PDF).

**Extras:** a What-if simulator (compare two mitigations; costs are editable assumptions), an AI explainer (only rephrases computed facts; falls back to a template), and a simulated offline mode for field updates.

## Pitch script (about 4 minutes)

**Opening (20 s).** "On a project, the plan, the supplier deliveries, the quantity estimates and the inspection records live in different files. Nobody checks that they agree until something fails on site. Project Readiness Radar is that missing check."

**Problem (30 s).** "Picture an excavation stage. The schedule says dewatering starts on October 26. The supplier quotation says the pump arrives on October 29. Three days of idle crew, and six downstream activities slip. The information existed. It was just never compared."

**Solution (30 s).** "We don't replace planning tools. We import their exports and run explicit rules: delivery versus start, predecessor readiness, stage gates, quantity versus specification, and downstream impact through the dependency graph. No black box. Every alert shows its evidence."

**Live demo (2 min).**
1. Dashboard: "4 risks, 6 activities forecast to slip." Open the pump alert: evidence and affected activities. Click Explain.
2. Steel quantity: "18 tonnes estimated, 21.5 specified, 16% under." "The concrete estimate is 3% off, inside tolerance, so it stays quiet. That's how we limit alert noise."
3. As the engineer, enter the supplier's verified date. "The alert closes itself and the forecast recalculates."
4. Submit the stage as ready: the gate rule fires on the open inspection. As the director, try to authorize: blocked. Close the inspection, authorize: allowed.
5. Show the audit log and click Verify integrity.

**Why it's different (30 s).** "Two actions stay separate: an engineer confirms facts, only a director authorizes. It's enforced in the service layer and covered by tests. AI is never a source of truth: it only rephrases facts the rules already computed, and its output is rejected if it contains a number that isn't in those facts."

**Honest limits and next steps (20 s).** "Today it runs on synthetic data, with simulated login and simulated offline sync. Next: extraction from supplier PDFs with human confirmation, real authentication, and direct import of planning-tool formats."

**Close (10 s).** "A late pump is only expensive if you find out on site. We find out from the data."

## Questions you will probably get

- **Why not just use Primavera or MS Project?** They answer "what is the schedule?" We answer "is the schedule consistent with everything else we know?" We import from them.
- **Is the data real?** No. It is synthetic, modelled on a typical excavation and foundation sequence. The import page works with any exports in the template format.
- **Where does AI actually matter?** It phrases the explanation and trade-off summary. It never decides, never changes data, and is checked against the computed facts. The app works fully without it.
- **Can an engineer approve a stage?** No. The permission check is in the service layer, and the tests prove it fails.
- **Is the audit log really tamper-proof?** Tamper-evident. Database triggers block edits, and every row stores the previous row's hash, so a modified row breaks the chain. Someone with raw database access could rebuild it, so it is not tamper-proof.
- **What about false alarms?** Findings have fingerprints: they update instead of duplicating, tolerances avoid noise, and an engineer-marked false positive stays quiet until its evidence changes.
- **Is the offline mode real?** It's a simulation of queue-and-sync with conflict detection, labelled as such. Real field use would need a proper local store.
- **What are the costs in What-if?** Editable assumptions shown on screen, not project data. The schedule impact comes from the engine.
- **What did you not build?** Extraction from PDFs, real authentication, planning-tool file formats. They are listed as future work.

## Slide outline (8 slides)

1. Title + one-line value proposition
2. The problem: four disconnected sources, found out on site
3. Example: pump arrives 3 days after the crew needs it, six activities slip
4. How it works: import, rules, evidence, human verification, recalculation
5. Two actions, two roles: confirmation vs authorization (diagram)
6. Responsible AI: grounded, optional, with a fallback
7. Demo (screenshots of dashboard, steel finding, blocked authorization, audit log)
8. Limits and next steps; what's simulated; thank you
