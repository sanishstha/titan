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
