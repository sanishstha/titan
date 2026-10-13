# TITAN: all phases in one file

The same content is split per phase in `docs/phases/phase-01.md` to `phase-16.md`.

# Phase 1: Foundations and point-in-time data model

**Week:** Week 1  
**Estimated effort:** 10-14h

**Goal:** A database where every fact knows when it became public, and queries cannot see the future.

## Tasks

- Repo setup: backend package, docker-compose (Postgres + pgvector), Makefile, CI-style `make check` (ruff, mypy, pytest).
- Schema: `companies`, `documents` (filing metadata, accession no., form type, `filed_at`, `known_at`), `facts` (XBRL concept, value, unit, period, `known_at`, source document), `claims` (empty for now), `jobs`, `traces`.
- The `as_of` query layer: a single repository module through which all reads go, taking `as_of` and filtering on `known_at`. No raw reads elsewhere.
- Restatement handling: when a later filing restates a value, keep both versions with their own `known_at` so the as-of view returns what was known then.
- Leakage test suite: seed data with future-dated facts and assert they are invisible to earlier `as_of` queries.

## Deliverables

- Migrations, repository layer, leakage tests, `docs/data-model.md`

## Exit criteria (all must pass)

- [x] Leakage tests pass, including restated-value cases.
- [x] No code outside the repository layer reads `facts` or `documents` directly (enforced by a test or lint rule).

---
When done: write `docs/notes/phase-01.md` (built, measured, broke), then stop and wait for `START PHASE 2`.

========================================

# Phase 2: EDGAR ingestion

**Week:** Week 1  
**Estimated effort:** 14-18h

**Goal:** Pull real filings and structured financial facts for a starter universe, correctly timestamped.

## Tasks

- EDGAR client: descriptive User-Agent from env, rate limiter (max 5 req/s), retry with backoff, on-disk cache of raw responses in `data/raw/`.
- Ingest submissions index, 10-K, 10-Q and 8-K filings, plus the XBRL company-facts API into `facts`. Use the acceptance datetime as `known_at`.
- Parse filing text into sections (Business, Risk Factors, MD&A, Notes) with stable section ids and character offsets, so claims can cite exact spans.
- Starter universe config: 10 companies across 3 sectors, committed in `universe.yaml`.
- Ingestion CLI: `titan ingest --ticker NVDA --since 2019`. Idempotent: re-running changes nothing.

## Deliverables

- Ingestion pipeline, section parser, universe config, ingestion tests on recorded fixtures

## Exit criteria (all must pass)

- [ ] Re-running ingestion is a no-op (row counts unchanged).
- [ ] Spot check of 20 facts against the SEC website: values and `known_at` match.
- [ ] Section parser round-trips: every section offset slices back to the exact filing text.

---
When done: write `docs/notes/phase-02.md` (built, measured, broke), then stop and wait for `START PHASE 3`.

========================================

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

========================================

# Phase 4: GPU spike: self-hosted model baseline

**Week:** Week 1  
**Estimated effort:** 4-6h

**Goal:** Remove the biggest unknown early. Get one open model served on a rented GPU and measured crudely.

## Tasks

- Docker image and run script to serve one 7-8B instruction model with vLLM (OpenAI-compatible). Pin vLLM version and model revision.
- Point `ModelClient` at it and run the Phase 3 agent against it end to end.
- Crude benchmark: TTFT, tokens/s, p50/p95 latency at concurrency 1, 8, 32. Record GPU model, memory use and hourly cost.
- Write down what surprised you and what the real constraints are (context length, KV cache size, load time).

## Deliverables

- `docker/vllm/`, `bench/harness/quick.py`, first numbers in `bench/results/`, `docs/notes/gpu-spike.md`

## Exit criteria (all must pass)

- [ ] The agent completes an investigation using the self-hosted model.
- [ ] Numbers are recorded with exact config so they can be re-run.

---
When done: write `docs/notes/phase-04.md` (built, measured, broke), then stop and wait for `START PHASE 5`.

========================================

# Phase 5: Claim and evidence store with extraction evals

**Week:** Week 2  
**Estimated effort:** 18-24h

**Goal:** Turn filing text into cited, structured claims, and measure how accurate extraction is.

## Tasks

- Claim schema: subject, predicate/type (guidance, risk, customer concentration, capex plan, segment trend, etc), value, polarity, source document, character span, `known_at`, extractor version, confidence.
- Extractor: chunk by section, extract claims with a structured-output prompt, validate spans (the quoted text must exist at the offsets).
- Evidence graph tables: nodes (company, claim, fact, document, event) and typed edges (supports, contradicts, supersedes, derived_from).
- Hand-label a gold set: 150+ claims across at least 8 filings, committed in `evals/extraction/`.
- Extraction eval: precision, recall, span-validity rate, schema-failure rate, per extractor version. Gate in `make eval`.

## Deliverables

- Extractor, graph tables, gold set, eval runner, baseline scores in `evals/extraction/baseline.json`

## Exit criteria (all must pass)

- [ ] Span-validity rate is 100% (invalid spans are rejected, not stored).
- [ ] Baseline precision and recall recorded; later changes may not regress them past a stated threshold.

---
When done: write `docs/notes/phase-05.md` (built, measured, broke), then stop and wait for `START PHASE 6`.

========================================

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

========================================

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

========================================

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

========================================

# Phase 9: Inference lab: benchmark harness and baselines

**Week:** Week 3  
**Estimated effort:** 16-22h

**Goal:** A proper measurement setup for the real workloads TITAN generates.

## Tasks

- Define the real workloads: long-document claim extraction (batch), filing diff summarization (long context), agent tool-loop turns (short, latency-sensitive), embeddings.
- Benchmark harness: load generator with concurrency sweeps and workload mixes; TTFT, inter-token latency, tokens/s, p50/p95/p99, requests/s, error rate, GPU memory and utilization, cost per 1,000 extracted claims and cost per investigation.
- Quality harness: run each workload through the extraction and agent evals so every speed result carries an accuracy result.
- Baselines: frontier API vs self-hosted 7-8B vs a larger open model if the GPU fits.
- Long-context study: how accuracy and latency change with context length (8k, 32k, 64k+) on filing tasks.

## Deliverables

- `bench/harness/`, workload definitions, baseline results table, `docs/notes/inference-baseline.md`

## Exit criteria (all must pass)

- [ ] Two runs of the same config agree within 5% on throughput.
- [ ] Every row in the results table has throughput, latency, cost and accuracy.

---
When done: write `docs/notes/phase-09.md` (built, measured, broke), then stop and wait for `START PHASE 10`.

========================================

# Phase 10: Inference lab: optimization, one lever at a time

**Week:** Week 3  
**Estimated effort:** 22-30h

**Goal:** The core inference-engineering phase. Change one thing per run, always re-measure speed and accuracy.

## Tasks

- Continuous batching tuning (`max-num-seqs`, `max-num-batched-tokens`).
- Prefix caching: many agents query the same filing and share long prefixes. Measure hit rate and speedup, and design prompts so the shared prefix comes first.
- Chunked prefill and its effect on tail latency when long and short requests mix.
- Quantization: FP8, AWQ, GPTQ, with extraction and agent evals after each.
- KV cache analysis: memory per token, max concurrent long contexts, effect of context length on batch size.
- Constrained decoding (xgrammar or outlines) for guaranteed schema output: overhead versus invalid-output reduction.
- Speculative decoding (draft model or n-gram) and when it helps or hurts on this workload.
- Compare vLLM with SGLang on the same workloads.

## Deliverables

- One JSON result per run, generated comparison table: baseline, +batching, +prefix cache, +chunked prefill, +quant, +constrained, +spec decode, plus engine comparison

## Exit criteria (all must pass)

- [ ] Every optimization has a measured before/after on speed AND accuracy, with a written explanation of why it helped or did not.
- [ ] Failed optimizations stay in the report.

---
When done: write `docs/notes/phase-10.md` (built, measured, broke), then stop and wait for `START PHASE 11`.

========================================

# Phase 11: Routing, observability and break-even

**Week:** Week 3  
**Estimated effort:** 12-16h

**Goal:** Run the system like a production service and decide honestly when self-hosting pays.

## Tasks

- Router: small self-hosted model first, escalate to a larger model or API on low confidence (logprobs, schema failure, verifier disagreement). Sweep thresholds and plot accuracy against cost.
- Task specialization: use a cheaper model for extraction and classification, a stronger one for thesis and challenger reasoning. Measure the quality delta per agent role.
- Observability: vLLM and app metrics into Prometheus, Grafana dashboards (GPU utilization, queue depth, KV cache usage, TTFT, cost per investigation).
- Fallbacks: API fallback when the GPU endpoint is down, timeouts, load shedding.
- Break-even model: at what monthly investigation volume does self-hosting beat the API, including GPU idle time. State plainly where the API wins.

## Deliverables

- Router, dashboards as committed JSON, `docs/capacity-planning.md`

## Exit criteria (all must pass)

- [ ] Routed system has lower cost per investigation than API-only at equal or better eval accuracy, or the notes explain why it does not.
- [ ] Dashboards show live metrics during a load test.

---
When done: write `docs/notes/phase-11.md` (built, measured, broke), then stop and wait for `START PHASE 12`.

========================================

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

========================================

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

========================================

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

========================================

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

========================================

# Phase 16: Real users and customer deployment track (FDE evidence)

**Week:** Runs in parallel from Week 2, formal build after Phase 15  
**Estimated effort:** 25-35h (spread across the project)

**Goal:** Produce real evidence of forward-deployed behaviors: discovery with a real person, scoping, shipping into their workflow, handling a failure, and changing the product on their feedback.

This phase is mostly human work. Cursor's job is to build the deployment features and keep the documents tidy. It must NOT invent users, quotes, feedback or outcomes. Every entry in the feedback log and pilot notes is written by the human from a real session.

## Parallel schedule (the human does this, Cursor supports it)

- **After Phase 3:** send outreach (`docs/fde/outreach-template.md`) to about 10 people.
- **After Phase 8:** run the first 1-2 sessions on whatever exists. Mostly watch and listen.
- **After Phase 14:** run a second round with the finished terminal. Compare to round one.
- **After Phase 15:** write up the pilot, the feedback log and the postmortem.

## Tasks for Cursor (when told START PHASE 16)

- **Per-user configuration:** each user has their own watchlist, thresholds and report format (analyst preset, portfolio-manager preset). Configuration lives in the database, not in code.
- **Permissions:** simple roles (admin, user, read-only) and per-user data isolation for watchlists, notes and investigations.
- **Audit log:** every investigation, report view, config change and export is logged with user, time and inputs. Add a viewer in the admin area.
- **Onboarding flow:** a user can paste or upload a watchlist (CSV of tickers) and get the first investigations queued, with clear progress and clear error messages for bad input.
- **Admin guide:** `docs/fde/admin-guide.md`, written for a non-engineer: how to start and stop the system, how to read the dashboard, what to do when an investigation fails, who to contact.
- **Failure handling the user can see:** when the GPU endpoint is down or a filing cannot be parsed, the UI explains what happened in plain language and what the system did instead (fallback model, skipped section).
- **Feedback capture:** an in-app "this was wrong / this was useful" control on every claim and report section, stored with context, exportable to the feedback log.
- **Data export:** export a report with its evidence list as PDF or Markdown, so users can take work out of the tool.

## Tasks for the human

- Run discovery calls with `docs/fde/discovery-questions.md` and write a one-page scope using `docs/fde/scope-template.md`.
- Keep `docs/fde/feedback-log.md` updated after every session: they said, I changed, result.
- Run a deliberate failure drill in front of a user, or log a real failure, and write `docs/fde/postmortem-template.md` up as a real postmortem.
- Record two demos using `docs/fde/demo-scripts.md`: five minutes for a non-technical person, and a technical deep dive.
- Write the final `docs/fde/pilot-summary.md` honestly: what you did, what you learned, and what would differ at a large customer (security review, SSO, procurement, data residency).

## Deliverables

- Per-user config, roles, audit log, onboarding flow, feedback capture, export
- `docs/fde/` filled in with real content from real sessions
- Two demo recordings

## Exit criteria (all must pass)

- [ ] At least 2 real people used the system on a company they know, and each session is logged.
- [ ] The feedback log has at least 5 entries of the form: said, changed, result, and at least 2 resulted in shipped product changes.
- [ ] One failure (real or drilled) is written up as a postmortem with cause, detection, fix and prevention.
- [ ] A user other than you completes onboarding and one investigation unaided.
- [ ] `pilot-summary.md` states plainly what this does not prove (no enterprise deployment, no long-term operation).
- [ ] No invented users, quotes or outcomes anywhere in `docs/fde/`.

---
When done: write `docs/notes/phase-16.md` (built, measured, broke). This is the final phase.

========================================