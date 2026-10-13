# Phase 14: Frontend: investment terminal

**Week:** Week 4-5  
**Estimated effort:** 24-32h

**Goal:** The showcase. Built around two views: what changed, and interrogating competing hypotheses.

## Tasks

- Terminal layout: universe list, company workspace, investigation feed, command palette.
- "What changed" view: ranked changes since a chosen date with before/after text diff and the claims behind each.
- Evidence graph (React Flow): thesis, claims, facts, contradictions; click to open the exact filing span.
- Hypothesis view: competing theses side by side, challenger objections, status (addressed, unresolved), and the falsifiers.
- Live investigations: stream agent steps over SSE (tool calls, partial findings, cost counter), with pause and resume.
- Inference panel: live latency, tokens/s, cache hit rate, route taken per call, cost per investigation.
- If Phases 12-13 were built: portfolio view, scenario sliders, approval inbox. Otherwise skip.
- Two workflow presets (fundamental analyst, portfolio manager) as configuration, not forks.

## Deliverables

- Next.js app, typed API client, component tests, a short screen recording script for the demo

## Exit criteria (all must pass)

- [ ] Every number and claim in the UI opens its source.
- [ ] A live investigation streams to completion in the UI without a page refresh.
- [ ] Lighthouse and type checks pass; no unhandled error states for empty or failed investigations.

---
When done: write `docs/notes/phase-14.md` (built, measured, broke), then stop and wait for `START PHASE 15`.
