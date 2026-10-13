# Phase 7: Persistent agents: theses, challenger, checkpoints and recovery

**Week:** Week 2  
**Estimated effort:** 20-28h

**Goal:** Agents that keep a view over time, attack their own views, and survive crashes.

## Tasks

- Thesis model: versioned thesis with drivers, assumptions, claims backing each, and falsifiers (what would prove it wrong).
- Thesis agent: builds the initial thesis, and on a material change proposes a revision with a diff against the previous version.
- Challenger agent: independent prompt and context, attacks assumptions, looks for contradicting evidence in the graph, and must cite. Produces a ranked objection list.
- Arbiter step: decide whether each objection is addressed, requires revision, or is unresolved. Unresolved objections stay visible.
- Durable execution: job queue on Postgres, checkpoint after every agent step, resume on restart, idempotency keys on tool calls.
- Failure drills: kill the worker mid-investigation and confirm it resumes without duplicating work.

## Deliverables

- Thesis and challenger agents, job runner with checkpointing, failure-drill tests

## Exit criteria (all must pass)

- [ ] Crash-recovery test passes: kill at 5 different steps, each resumes and finishes with identical conclusions.
- [ ] A material change on a seeded company produces a thesis revision with a visible diff and at least one cited objection.

---
When done: write `docs/notes/phase-07.md` (built, measured, broke), then stop and wait for `START PHASE 8`.
