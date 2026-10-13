# Phase 8: Agent evals and early point-in-time backtest harness

**Week:** Week 2  
**Estimated effort:** 14-18h

**Goal:** Make agent quality measurable before optimizing anything, including a first look at whether decisions are any good.

## Tasks

- Eval harness over a fixed set of (company, as_of) cases: citation validity, unsupported-number rate, contradiction-catch rate, thesis-revision correctness on known events, cost and steps per investigation.
- Adversarial cases: a company with no material change (the agent must not invent one), contradictory filings, missing data, prompt-injection text inside a filing.
- Point-in-time backtest harness: replay history from `as_of = t` forward, run the pipeline using only data known at `t`, record the thesis direction and compare with later returns.
- Leakage canary: a test where a fact from the future is planted; the backtest must never see it.
- Regression gate: `make eval` fails when any tracked metric drops past its threshold.

## Deliverables

- `evals/agents/`, `evals/backtest/`, baseline scores, backtest report template

## Exit criteria (all must pass)

- [ ] Baseline stable across 3 runs (variance documented).
- [ ] Leakage canary never leaks.
- [ ] No-change company produces zero fabricated changes.

---
When done: write `docs/notes/phase-08.md` (built, measured, broke), then stop and wait for `START PHASE 9`.
