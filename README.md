# TITAN: Autonomous Investment Research and Capital Allocation Engine

An AI-native investment operations system built to learn **agentic engineering**, **inference engineering** and **serious frontend work** on real public data.

Observes filings and events, detects what changed, investigates competing theses, challenges them, and (optionally, paper only) turns evidence into bounded portfolio actions. Every agent action and model decision is measurable.

> Research tool only. Not investment advice. Paper trading only.

## How to build this with Cursor

1. Open this folder in Cursor. Rules in `.cursor/rules/project.mdc` load automatically.
2. Say: `START PHASE 1`
3. Cursor implements only that phase, runs checks and measurements, writes `docs/notes/phase-NN.md`, then stops.
4. Verify the exit criteria in the phase file, then say `START PHASE 2`, and so on.

Set `SEC_USER_AGENT` (for example `"Your Name your@email.com"`) before Phase 2. SEC requires a descriptive User-Agent.

## Phases

| # | Week | Phase | Effort |
|---|---|---|---|
| 1 | Week 1 | [Foundations and point-in-time data model](docs/phases/phase-01.md) | 10-14h |
| 2 | Week 1 | [EDGAR ingestion](docs/phases/phase-02.md) | 14-18h |
| 3 | Week 1 | [First research agent end to end and bare terminal skeleton](docs/phases/phase-03.md) | 12-16h |
| 4 | Week 1 | [GPU spike: self-hosted model baseline](docs/phases/phase-04.md) | 4-6h |
| 5 | Week 2 | [Claim and evidence store with extraction evals](docs/phases/phase-05.md) | 18-24h |
| 6 | Week 2 | [Change detection: what changed](docs/phases/phase-06.md) | 16-22h |
| 7 | Week 2 | [Persistent agents: theses, challenger, checkpoints and recovery](docs/phases/phase-07.md) | 20-28h |
| 8 | Week 2 | [Agent evals and early point-in-time backtest harness](docs/phases/phase-08.md) | 14-18h |
| 9 | Week 3 | [Inference lab: benchmark harness and baselines](docs/phases/phase-09.md) | 16-22h |
| 10 | Week 3 | [Inference lab: optimization, one lever at a time](docs/phases/phase-10.md) | 22-30h |
| 11 | Week 3 | [Routing, observability and break-even](docs/phases/phase-11.md) | 12-16h |
| 12 | Week 4 | [Portfolio constraints and scenarios (paper)](docs/phases/phase-12.md) (optional) | 16-22h |
| 13 | Week 4 | [Approvals, paper execution and decision tracking](docs/phases/phase-13.md) (optional) | 12-16h |
| 14 | Week 4-5 | [Frontend: investment terminal](docs/phases/phase-14.md) | 24-32h |
| 15 | Week 5 | [Deployment, point-in-time evaluation, external test and write-up](docs/phases/phase-15.md) | 20-28h |
| 16 | Parallel, finishes after 15 | [Real users and customer deployment track (FDE evidence)](docs/phases/phase-16.md) | 25-35h |

**Committed core (phases 1-11, 14, 15, 16): roughly 230-300 hours.** Phases 12-13 (portfolio, paper execution) are optional and recommended to SKIP, since they matter least for the FDE goal. Phase 16 (real users and customer deployment) starts in parallel after Phase 3 and finishes after Phase 15. Spend any spare hours on evals and inference benchmarks, not extra features.

## Layout

```
backend/src/titan/   ingest, pit (point-in-time), evidence, agents, models, tasks, api, portfolio, monitor
frontend/            Next.js terminal
evals/               extraction, agents, backtest
bench/               inference harness and results
docs/phases/         one spec per phase
docs/notes/          per-phase notes written by Cursor
docs/fde/            templates for real-user sessions, scope, feedback, postmortem, demos
data/                raw cache and fixtures
docker/              compose and vLLM images
```

Setup: `cd backend && uv sync --extra dev`, `docker compose up -d db`, then `make check`.
