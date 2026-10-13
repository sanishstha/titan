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

- [ ] Leakage tests pass, including restated-value cases.
- [ ] No code outside the repository layer reads `facts` or `documents` directly (enforced by a test or lint rule).

---
When done: write `docs/notes/phase-01.md` (built, measured, broke), then stop and wait for `START PHASE 2`.
