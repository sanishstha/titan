# Phase 12: Portfolio constraints and scenarios (paper)

**Week:** Week 4  
**Estimated effort:** 16-22h  
**OPTIONAL:** skip unless the user explicitly asks.

**Goal:** Turn evidence into bounded portfolio actions. Treat as a second-tier deliverable: the research desk is complete without it.

## Tasks

- Paper portfolio model: positions, cash, cost basis, daily marks from stored prices (point-in-time aware).
- Risk constraints as code: max position size, sector caps, max turnover, concentration limits, liquidity floor. Constraints are enforced deterministically, never by the LLM.
- Scenario engine: deterministic valuation scenarios (growth, margin, multiple ranges) with the thesis assumptions as inputs; outputs distributions and sensitivities.
- Exposure analysis: how a thesis change affects portfolio exposure by position, sector and factor proxy.
- Allocation proposal: from thesis conviction, scenarios and constraints, produce a proposed action with a rationale linking to evidence. A deterministic optimizer picks sizes, the LLM explains.

## Deliverables

- `portfolio/` module, constraint tests, scenario engine, proposal generator

## Exit criteria (all must pass)

- [ ] Property tests: no proposal ever violates a constraint, across randomized portfolios.
- [ ] Each proposal links to the thesis version and evidence that produced it.

---
When done: write `docs/notes/phase-12.md` (built, measured, broke), then stop and wait for `START PHASE 13`.
