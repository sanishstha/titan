# Phase 6: Change detection: what changed

**Week:** Week 2  
**Estimated effort:** 16-22h

**Goal:** Detect and rank material changes between filings and events, with evidence for each.

## Tasks

- Filing diff engine: section-level alignment between consecutive filings of the same type, then claim-level diff (added, removed, softened, strengthened, reversed).
- Quantitative change detection on facts: margin shifts, working-capital changes, unusual accruals, guidance changes, with deterministic thresholds.
- Materiality scorer: rank changes by type, magnitude and relevance to open theses. Deterministic features first, LLM only for explanation.
- Event monitor: poll EDGAR for new filings on the universe, enqueue investigations as jobs.
- Labeled change set: 60+ known material changes (from real filings) used as ground truth.

## Deliverables

- Diff engine, change scorer, monitor job, labeled set in `evals/extraction/changes.json`

## Exit criteria (all must pass)

- [ ] Recall of labeled material changes at least 80% in the top-10 ranked changes per filing.
- [ ] Every surfaced change links to before and after evidence.

---
When done: write `docs/notes/phase-06.md` (built, measured, broke), then stop and wait for `START PHASE 7`.
