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
