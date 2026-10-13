# Phase 13: Approvals, paper execution and decision tracking

**Week:** Week 4  
**Estimated effort:** 12-16h  
**OPTIONAL:** skip unless the user explicitly asks.

**Goal:** Approved actions only, fully logged, scored against what happened later.

## Tasks

- Approval workflow: proposed, approved or rejected by a human, executed on the paper book. No auto-execution.
- Execution simulator: fills at next available price with a simple slippage and fee model, all assumptions explicit.
- Decision ledger: every decision with timestamp, thesis version, evidence ids, constraints checked, and later outcome marks at 1, 3, 6, 12 months.
- Scoreboard: hit rate, return versus benchmark, and calibration of stated conviction. Report small-sample caveats on the scoreboard itself.

## Deliverables

- Approval API, execution simulator, ledger, scoreboard

## Exit criteria (all must pass)

- [ ] Convert one thesis change into a bounded, approved paper action end to end.
- [ ] Rejected and constraint-failing proposals are logged and never executed.

---
When done: write `docs/notes/phase-13.md` (built, measured, broke), then stop and wait for `START PHASE 14`.
