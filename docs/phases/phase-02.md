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
