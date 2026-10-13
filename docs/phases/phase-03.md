# Phase 3: First research agent end to end and bare terminal skeleton

**Week:** Week 1  
**Estimated effort:** 12-16h

**Goal:** One agent, one company, evidence-backed report, visible in a minimal UI. This proves the whole pipeline before any depth is added.

## Tasks

- `ModelClient` interface with backends: frontier API, OpenAI-compatible (for vLLM), mock. Tracing of every call into `traces`.
- Tool registry with strict JSON schemas: `get_facts`, `get_ratios`, `search_filing_text` (pgvector), `get_filing_section`. All tools take `as_of`.
- Deterministic ratio module (margins, growth, leverage, cash conversion, etc). The agent calls it; it never computes.
- Research agent: plan, call tools within a step/token/dollar budget, write a report where every sentence carrying a figure cites a fact id or claim id.
- Report verifier: parse the report, reject any number with no source.
- Frontend skeleton (Next.js): a company page with a button that triggers an investigation and a report view with clickable citations. Minimal styling only.

## Deliverables

- Agent, tools, verifier, API endpoints (`POST /investigations`, `GET /investigations/{id}`), frontend skeleton

## Exit criteria (all must pass)

- [ ] Open a company in the UI, trigger an investigation, receive a cited report.
- [ ] Verifier rejects a report with an injected unsourced number (tested).
- [ ] Same investigation with `as_of` set in the past uses only data known then (test).

---
When done: write `docs/notes/phase-03.md` (built, measured, broke), then stop and wait for `START PHASE 4`.
