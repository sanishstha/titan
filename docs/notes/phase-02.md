# Phase 2: Day 2 review — IN PROGRESS

Reviewed on 2026-10-17. Phase 2 is not complete. The full gate has not passed. Phase 3 has not been started.

The phase goal is to pull real filings and structured financial facts for a starter universe, with `known_at` set from the filing acceptance time. This tree has the EDGAR client, submissions discovery, primary-document storage for 10-K, 10-Q, and 8-K filings, and a section parser. It does not ingest XBRL company facts, it has no `universe.yaml`, and the 20-fact spot check against the SEC website has not been done.

`docs/notes/phase-01.md` still ends with the 2026-10-14 sentence "Phase 2 has not been started." That described that review. The current status is this file.

## Gate

| Exit criterion | Status on this review |
|---|---|
| Re-running ingestion is a no-op (row counts unchanged). | Open for the phase. Document metadata is idempotent on fixtures: `test_ingest_is_idempotent_and_keeps_amendments` and `test_restart_leaves_row_counts_and_hashes_unchanged` passed. There is no company-facts writer, so fact rows cannot be re-run. This review did not execute a committing live ingest. |
| Spot check of 20 facts against the SEC website: values and `known_at` match. | Not started. `facts` has 0 rows. |
| Section parser round-trips: every section offset slices back to the exact filing text. | Met for the parser that exists. Fixture tests passed. A local check of the 98 cached NVIDIA primary documents found 88 sections and 0 slice failures. Those sections are not stored. |

## Completed in this tree

- EDGAR transport in `backend/src/titan/ingest/sec_transport.py`. `SEC_USER_AGENT` is required, must name the operator and include a contact email, and must not look like a browser. The client stays at or under 5 requests per second, retries 429, 5xx, timeouts, and transient network errors with bounded backoff, does not follow redirects, does not read proxy variables, and does not retry HTTP 403.
- Raw cache in `data/raw/`. Archive URLs under `www.sec.gov/Archives/edgar/data/` are immutable. Submissions, company facts, and other SEC URLs are reused only inside `sec_mutable_fresh_seconds`. A stale copy is not returned when the refresh fails.
- Submissions discovery for 10-K, 10-Q, 8-K, and their `/A` amendments, including older `filings.files` whose range overlaps `--since`. `known_at` is the acceptance instant from `parse_acceptance_datetime`, not `period_of_report` and not the filing date.
- Primary-document upsert. A repeated accession uses `ON CONFLICT DO NOTHING` and is kept only when the stored metadata matches. An amendment is a separate accession. Confirmation reads go through `PointInTimeRepository`.
- CLI: `titan ingest --ticker NVDA --since 2019`, with optional `--limit`.
- Section parser for Business, Risk Factors, MD&A, and Notes. Offsets index `sec-filing-text-v1` canonical text. Section ids do not depend on offsets. The CLI and the store do not call the parser, and there is no sections table.

## Not done

These three items are still required before the phase gate can pass:

1. **XBRL company-facts ingestion.** Nothing writes `facts`. The company-facts URL is classified as a mutable SEC endpoint and appears in transport tests. There is no parser, no fact insert, and no cache entry for a company-facts response. `docs/data-model.md` still says a second insert of the same fact identity raises a unique violation. That is still true.
2. **Starter universe.** `universe.yaml` is not in the tree. The only company row is NVIDIA.
3. **20-fact spot check.** Not run. There are no stored facts to compare with the SEC website.

Also still open, and not a substitute for those three: filing sections are computed in memory only; the committed NVIDIA rows omit 14 target filings from 2019; this review did not re-run a committing ingest.

## Files changed

Uncommitted Phase 2 code and tests, against the Phase 1 tree:

- `backend/pyproject.toml`, `backend/uv.lock` — `tzdata`, so `America/New_York` resolves for acceptance times.
- `backend/src/titan/common/settings.py`, `backend/src/titan/common/__init__.py` — `SecSettings`.
- `backend/src/titan/cli.py`
- `backend/src/titan/ingest/__init__.py`
- `backend/src/titan/ingest/acceptance.py`
- `backend/src/titan/ingest/canonical_text.py`
- `backend/src/titan/ingest/errors.py`
- `backend/src/titan/ingest/identifiers.py`
- `backend/src/titan/ingest/raw_cache.py`
- `backend/src/titan/ingest/run.py`
- `backend/src/titan/ingest/sec_transport.py`
- `backend/src/titan/ingest/sections.py`
- `backend/src/titan/ingest/store.py`
- `backend/src/titan/ingest/submissions.py`
- `backend/src/titan/ingest/tickers.py`
- `backend/tests/test_cli.py`
- `backend/tests/test_filing_sections.py`
- `backend/tests/test_filing_store.py`
- `backend/tests/test_incremental_ingest.py`
- `backend/tests/test_nvda_smoke.py`
- `backend/tests/test_raw_cache.py`
- `backend/tests/test_sec_submissions.py`
- `backend/tests/test_sec_transport.py`
- `backend/tests/fixtures/sec/` — recorded NVIDIA submissions fixtures.
- `backend/tests/fixtures/sections/` — section-parser fixtures.
- `backend/tests/test_settings.py` — SEC settings cases.
- `backend/tests/test_no_direct_financial_reads.py`, `backend/tests/test_point_in_time.py` — read-path guard and decimal formatting carried with this work.
- `docs/architecture.md`, `docs/data-model.md` — the read-path description. The data model still has no sections table and no fact ingest writer.

`docs/notes/phase-01.md` was edited by the Phase 1 review and still describes that phase. `docs/phase-01-technical-report.md` and `docs/phase-01-technical-report.pdf` are untracked Phase 1 artifacts. `.DS_Store` files are not part of the phase.

## Commands and results

Interpreter and tools: Python 3.15.0, ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, Alembic 1.20.0, SQLAlchemy 2.1.4, Pydantic 2.14.1. Database: container `titan2-db-1`, PostgreSQL 16.15, port 5432, database `titan`. `mypy` uses `strict = true` in `backend/pyproject.toml`. The project requires Python 3.11 or newer. This machine ran 3.15.0.

A first `uv run ruff` and `uv run mypy` inside the sandbox failed with `Failed to spawn` before any check ran. The results below are the reruns.

From `backend/`:

```text
uv run ruff check .
uv run mypy src
```

```text
All checks passed!
Success: no issues found in 43 source files
```

From `backend/`, without `TITAN_SEC_SMOKE`:

```text
uv run pytest -q --tb=line -p no:cacheprovider
```

```text
222 passed, 1 skipped in 6.56s
```

The skip is `tests/test_nvda_smoke.py::test_live_nvda_since_2019_includes_the_older_10k`: `set TITAN_SEC_SMOKE=1 to fetch live NVDA submissions`. Confirmed with:

```text
uv run pytest -q --tb=no -rs -p no:cacheprovider tests/test_nvda_smoke.py
```

```text
SKIPPED [1] tests/test_nvda_smoke.py:44: set TITAN_SEC_SMOKE=1 to fetch live NVDA submissions
1 skipped in 0.19s
```

The 222 include the ingestion tests (`test_cli.py`, `test_filing_sections.py`, `test_filing_store.py`, `test_incremental_ingest.py`, `test_raw_cache.py`, `test_sec_submissions.py`, `test_sec_transport.py`, `test_settings.py`) and the Phase 1 leakage, repository, schema, and read-path tests. No ingestion test failed. Fixture HTTP tests use `httpx.MockTransport` and did not call SEC.

`SEC_USER_AGENT` is set in the repo `.env`, so the bounded smoke test was run. It discovers NVIDIA filings on or after 2019 and downloads one primary document, accession `0001045810-19-000023`. A request log recorded method, URL, and status only.

```text
PYTHONPATH=/tmp/titan_smoke_hook TITAN_SEC_SMOKE=1 uv run pytest -vv --tb=short -p no:cacheprovider tests/test_nvda_smoke.py
```

```text
tests/test_nvda_smoke.py::test_live_nvda_since_2019_includes_the_older_10k PASSED
1 passed in 1.22s
```

Successful requests, in order. No failed requests.

```text
200 GET https://data.sec.gov/submissions/CIK0001045810.json
200 GET https://data.sec.gov/submissions/CIK0001045810-submissions-001.json
200 GET https://www.sec.gov/Archives/edgar/data/1045810/000104581019000023/nvda-2019x10k.htm
```

Three gets in 1.22 seconds is under the 5 requests/second cap. The test's second read of that 10-K came from its temporary cache, so it did not issue a fourth request. The test asserts the body starts with the primary-document header, the SHA-256 matches the bytes, the byte count is greater than 1,000,000, and the acceptance text `2019-02-22T02:37:18.000Z` becomes `known_at` `2019-02-21 21:37:18+00`. The eastern wall time of that instant is `2019-02-21 16:37:18`, which is the EDGAR index header. The store path rolled back. The smoke test does not parse sections.

From `backend/`:

```text
uv run alembic upgrade head
uv run alembic check
uv run alembic current
uv run alembic heads
uv run alembic history
```

```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
No new upgrade operations detected.
54459e9eabff (head)
<base> -> 54459e9eabff (head), initial financial evidence schema
```

No Phase 2 migration was added. `alembic check` does not see `facts_append_only` or `CREATE EXTENSION vector`.

## Row counts

Queried before the suite, after the 222 passed tests, and after the smoke test. The counts did not change. Ingestion tests that commit delete their fixture company afterward. The smoke test rolls back.

| Table | Rows |
|---|---|
| companies | 1 |
| documents | 98 |
| facts | 0 |
| claims | 0 |
| jobs | 0 |
| traces | 0 |

The company is CIK `0001045810`, ticker `NVDA`, name `NVIDIA CORP`.

| Form | Rows |
|---|---|
| 10-K | 7 |
| 10-Q | 20 |
| 8-K | 70 |
| 8-K/A | 1 |

`period_of_report` is set on all 98 rows. `filed_at` runs from `2020-02-13 00:00:00+00` to `2026-09-03 00:00:00+00`. `known_at` runs from `2020-02-13 21:21:20+00` to `2026-09-03 12:03:56+00`. Accession `0001045810-19-000023` is not stored.

The on-disk submissions cache contains 112 target filings with a filing date on or after 2019-01-01. Ninety-eight of them are document rows. The other 14 are all from 2019 and all in `CIK0001045810-submissions-001.json`:

| Accession | Form | Filing date |
|---|---|---|
| 0001045810-19-000004 | 8-K | 2019-01-28 |
| 0001045810-19-000007 | 8-K | 2019-02-14 |
| 0001045810-19-000023 | 10-K | 2019-02-21 |
| 0001045810-19-000029 | 8-K | 2019-03-11 |
| 0001045810-19-000031 | 8-K | 2019-03-11 |
| 0001045810-19-000060 | 8-K | 2019-04-01 |
| 0001045810-19-000077 | 8-K | 2019-05-16 |
| 0001045810-19-000079 | 10-Q | 2019-05-16 |
| 0001045810-19-000107 | 8-K | 2019-05-29 |
| 0001045810-19-000112 | 8-K | 2019-06-17 |
| 0001045810-19-000140 | 8-K | 2019-08-15 |
| 0001045810-19-000144 | 10-Q | 2019-08-15 |
| 0001045810-19-000168 | 8-K | 2019-11-14 |
| 0001045810-19-000170 | 10-Q | 2019-11-14 |

By form, that gap is one 10-K, three 10-Qs, and ten 8-Ks. This review did not insert them.

## Raw cache

`data/raw/` was checked before and after the smoke test. The smoke test writes its own temporary cache, so `data/raw/` stayed at 101 files. No temporary files were present. Every file starts with `TITANSEC1`, and each body matches the SHA-256 and `byte_count` in its header. All 101 entries are HTTP 200. Body bytes sum to 50,835,916.

| Kind | Files | Host |
|---|---|---|
| Primary-document archives (`.htm`) | 98 | `www.sec.gov` |
| Company ticker list | 1 | `www.sec.gov` |
| Submissions index `CIK0001045810.json` | 1 | `data.sec.gov` |
| Older file `CIK0001045810-submissions-001.json` | 1 | `data.sec.gov` |

The 98 archive accessions are exactly the 98 document accessions. There is no company-facts object in the cache. Fixture cache tests also passed, including a tampered body, a swapped URL, a truncated publish, and a naive timestamp.

## Point-in-time status

Document `known_at` is the converted acceptance time. For every one of the 98 stored accessions, that column equals `parse_acceptance_datetime` applied to `acceptanceDateTime` in the cached submissions JSON. This is an internal match against the cache, not a comparison with the SEC website, and it is not the 20-fact spot check.

`filed_at` is the filing date at `00:00:00+00`. It is not the visibility cutoff. Ninety-two rows have `known_at` after that midnight. Six have `known_at` on the previous UTC evening, because the acceptance instant falls before midnight UTC on the filing date:

| Accession | Form | filed_at | known_at |
|---|---|---|---|
| 0001045810-20-000048 | 8-K | 2020-04-17 00:00:00+00 | 2020-04-16 22:06:42+00 |
| 0001045810-21-000034 | 8-K | 2021-03-19 00:00:00+00 | 2021-03-18 23:30:13+00 |
| 0001045810-21-000075 | 8-K | 2021-06-07 00:00:00+00 | 2021-06-04 22:50:36+00 |
| 0001045810-21-000163 | 10-Q | 2021-11-22 00:00:00+00 | 2021-11-20 02:16:20+00 |
| 0001045810-23-000175 | 10-Q | 2023-08-28 00:00:00+00 | 2023-08-25 23:36:34+00 |
| 0001045810-25-000179 | 8-K | 2025-07-01 00:00:00+00 | 2025-06-30 22:35:29+00 |

The Phase 1 leakage tests passed again in this run, including restatements and a fact that stays hidden until its source document is public. They still use seeded rows. Ingested `facts` do not exist, so those tests do not cover an EDGAR fact load.

Application reads of `documents` and `facts` remain in `backend/src/titan/pit/repository.py`. `test_application_code_queries_facts_and_documents_only_in_the_repository` passed. The ingest writer inserts document rows and selects `companies`. It confirms stored filings with `PointInTimeRepository.visible_documents` at `as_of` `9999-01-01+00`. That timestamp is a writer confirmation cutoff, not an agent `as_of`.

## Parsing round-trip

Fixture tests in `test_filing_sections.py` passed. Each fixture asserts `section.text == canonical.text[start:end]`, non-overlapping offsets, a heading at the start of the slice, and a stable section id. The cases cover a repeated HTML 10-K, a plain-text 10-K, a 10-Q with entities, an 8-K with no narrative sections, malformed HTML, Windows-1252, a bare `Note 1` that is not a notes section, and a table-of-contents risk line with no body.

This review also parsed the 98 cached primary documents with `parse_filing_sections`, using each row's stored form type and accession. Ninety-eight parses succeeded. A second parse of each file matched the first. Every section slice matched the canonical text. No offsets overlapped, and no section failed.

| Parser result | Count |
|---|---|
| Filings parsed | 98 |
| Slice failures | 0 |
| Sections | 88 |
| Business | 7 |
| Risk Factors | 27 |
| MD&A | 27 |
| Notes | 27 |
| Filings with no section | 71 (70 8-K and 1 8-K/A) |

Seven Business sections is one per stored 10-K. Twenty-seven Risk Factors, MD&A, and Notes sections is the 7 10-Ks plus the 20 10-Qs. The parser emitted no Business section for a 10-Q and no section for an 8-K. That is what this parser produced on these files. It is not a manual reading of each NVIDIA filing, and the sections were not written to the database.

## What broke

Nothing in the test, lint, type, or migration runs failed. The smoke test's three live requests all returned HTTP 200.

The committed NVIDIA set is not a `--since 2019` load: the 2019 10-K that the smoke test downloaded is not a document row. Closing that gap is a later ingest, not a parser defect found today.
