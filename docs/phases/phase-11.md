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
