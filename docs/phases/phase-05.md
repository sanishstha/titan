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
