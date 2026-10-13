# Phase 15: Deployment, point-in-time evaluation, external test and write-up

**Week:** Week 5  
**Estimated effort:** 20-28h

**Goal:** Show it works, show how well, and show it to someone who is not you.

## Tasks

- Deploy: docker-compose to a single VM, GPU endpoint attached, secrets handling, health checks, simple auth.
- Full point-in-time backtest across the universe over several years: thesis direction versus subsequent returns, with confidence intervals and honest sample-size caveats. Compare against naive baselines (always long, momentum, random).
- Final agent and extraction evals on the deployed stack; performance pass on the slowest endpoints.
- External user test: one or two people (an analyst or PM-type user if possible) run it on a company they know. Record what confused them and what they wanted.
- Write-up: architecture, evals, the inference results table, break-even analysis, backtest results with limitations, and a plain statement of what the system does not show.

## Deliverables

- Deployed system, `docs/results.md`, demo recording, README with architecture diagram

## Exit criteria (all must pass)

- [ ] Fresh clone plus documented commands reproduces the demo.
- [ ] Backtest report includes baselines and states plainly whether the system shows any edge.
- [ ] External tester completes an investigation unaided.

---
When done: write `docs/notes/phase-15.md` (built, measured, broke), then stop and wait for `START PHASE 16`.
