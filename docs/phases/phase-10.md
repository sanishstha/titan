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
