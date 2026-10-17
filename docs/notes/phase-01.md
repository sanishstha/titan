# Phase 1: what was built, why, and how it connects

Phase 1 is the point-in-time store and the only application read path for documents and financial facts. It is the database, the Python types that match it, the migration, and `PointInTimeRepository`. It is not ingestion, and it does not call a model.

Both exit criteria pass. Leakage tests, including restatements, pass. An AST walk keeps SQLAlchemy reads of `facts` and `documents` inside `backend/src/titan/pit/repository.py`.

The design this code implements is [data-model.md](../data-model.md).

## What problem this slice solves

TITAN has to answer "what was publicly known at time T?" A filing accepted on Tuesday must be invisible to a query dated Monday. A later 10-K/A that restates a number must not erase the original. A query dated between the two filings returns the original. A query dated after the amendment returns the restated value.

That rule only works if the database itself can hold both versions and can say when each one became public, and if every application read asks for a cutoff. The schema holds both versions. The repository is the read path.

Three project rules shaped every table:

1. **Point-in-time correctness.** A row is visible when `known_at <= as_of`. `known_at` is when the fact became public (for EDGAR, the acceptance time), not the fiscal period end.
2. **Numbers come from code, never from the model.** Financial values are `Decimal` mapped to PostgreSQL `numeric`. Floats are rejected at the schema boundary so binary rounding cannot enter the database.
3. **Every later claim will cite a source.** Facts already point at the filing that reported them. Claims are only a stub, because the real claim columns arrive in Phase 5.

## How the pieces connect

```mermaid
flowchart TB
  Env["DATABASE_URL or repo .env"] --> Settings["DatabaseSettings"]
  Settings --> Engine["create_engine postgresql+psycopg"]
  Engine --> Factory["sessionmaker"]
  Factory --> Session["Session"]

  Models["SQLAlchemy models"] --> Meta["Base.metadata"]
  Meta --> Alembic["Alembic env.py"]
  Settings --> Alembic
  Alembic --> Pg["Postgres tables"]

  Schemas["Pydantic create and read models"] --> Hash["hash_dimensions"]
  Hash --> Models
  Session --> Models
  Models --> Pg
```

There are two parallel representations of the same six entities, and they do not inherit from each other.

| Layer | Path | Job |
|---|---|---|
| Settings | `backend/src/titan/common/settings.py` | Read `DATABASE_URL`. Rewrite a plain `postgresql://` URL onto the psycopg v3 driver. |
| Sessions | `backend/src/titan/common/session.py` | Build a synchronous engine and a `sessionmaker`. Callers commit or roll back. |
| Dimension identity | `backend/src/titan/common/dimensions.py` | Turn a dimension dict into canonical JSON and a SHA-256. |
| ORM | `backend/src/titan/models/` | The tables SQLAlchemy knows about. Importing `titan.models` registers all six. |
| Schemas | `backend/src/titan/schemas/` | What crosses an API or agent boundary. Unknown fields are rejected. Read models are frozen. |
| Migration | `backend/alembic/` | The SQL that actually created the tables. `env.py` points Alembic at `Base.metadata` and at the same URL the app uses. |

`DatabaseSettings` looks for a `.env` at the repo root and ignores unrelated variables such as `SEC_USER_AGENT`. If `DATABASE_URL` is unset, the default is the local Compose database: `postgresql+psycopg://titan:titan@localhost:5432/titan`. Alembic does not use the `sqlalchemy.url` line in `alembic.ini`. That line is only a reminder of the same default.

The session factory does not connect at import time. `get_engine()` and `get_session_factory()` connect on first use. `open_session()` yields a session and closes it. It does not commit. A caller that forgets to commit leaves the transaction uncommitted, which is what the tests rely on: they insert, assert, and roll back.

Pydantic models are not the ORM classes. A `FactCreate` validates a payload. A `Fact` is a row. `FactRead` can be built from a loaded ORM object (`from_attributes=True`), but the row class is never handed to an agent as the validation schema. That split is deliberate: later agents must not treat a live database object as a trusted document.

## The six tables

Surrogate keys are UUIDs. Public identity is a natural key where one exists. Construction assigns the UUID immediately, and the column also has `server_default gen_random_uuid()` so a raw SQL insert still gets an id.

```mermaid
erDiagram
  companies ||--o{ documents : files
  companies ||--o{ facts : reports
  companies ||--o{ claims : reserved
  documents ||--o{ facts : sources
  documents ||--o{ claims : optional
  jobs ||--o{ traces : records
```

### companies

CIK is the company. It is `char(10)`, unique, and must be ten digits (`0000320193`). Ticker and legal name are labels. A ticker can change or be reused, so `ticker` is indexed and not unique, and it may be null. `name` is required.

### documents

One row is one SEC submission, including an amendment. The accession number `##########-##-######` is unique. The original 10-K and a later 10-K/A are two documents.

`known_at` is the acceptance time and is the visibility cutoff. `filed_at` is filing metadata. When the SEC only supplies a date, that date is stored as `00:00:00+00`. It is not used to hide or show the row. `period_of_report` is the period the filing covers. It can be null, because some 8-Ks do not have one. Form type is non-empty text plus a check, not a Postgres enum, so a new form does not need a type migration.

`(id, company_id)` is unique even though `id` is already the primary key. That extra unique constraint exists so facts and claims can use a composite foreign key. A fact cannot point at another company's filing: `document_id` and `company_id` must match one document row together.

Indexes: `(company_id, known_at)` for "what filings were public by T", and `(company_id, form_type, period_of_report)` for "the 10-K for this period".

### facts

One row is one reported number from one filing.

| Column | Why it is here |
|---|---|
| `company_id`, `document_id` | Who reported it, and which submission. |
| `taxonomy`, `concept` | The XBRL name, for example `us-gaap` / `Revenues`. |
| `value` | `numeric(38, 10)`, Python `Decimal`. Precision covers large share counts. Scale covers EPS and rates. |
| `unit` | The XBRL unit as published: `USD`, `shares`, `USD/shares`, `pure`. No currency conversion in this phase. |
| `decimals` | Optional XBRL precision hint. It can be negative, so it is not checked as `>= 0`. |
| `period_type`, `period_start`, `period_end` | Calendar dates, not timestamps. A duration (revenue) has both dates and `period_start <= period_end`. An instant (assets) has `period_start` null and `period_end` set to the balance-sheet date. |
| `fiscal_year`, `fiscal_period` | How the filer tagged the fact (`FY`, `Q1`…). Not part of identity, because the same calendar span can be both Q4 and FY. |
| `dimensions` | JSON object. `{}` is the company total. A segment is a different object, so it cannot collapse onto the total. |
| `dimensions_hash` | SHA-256 of that object as compact JSON with sorted keys. This is the identity column. JSONB does not preserve key order, so the hash is computed in Python before insert. |
| `known_at` | Copied from the source document at insert and then left alone. Reads filter this column. The repository also requires the source document's `known_at` to be at or before `as_of`. |

The idempotent key is:

`document_id, taxonomy, concept, unit, period_start, period_end, dimensions_hash`

PostgreSQL normally treats two nulls as distinct in a unique constraint. An instant fact has a null `period_start`, so a reload could insert the same balance-sheet fact twice. The constraint is `UNIQUE NULLS NOT DISTINCT`, which closes that hole. Re-running a load of the same document is a conflict, not an overwrite. The same document cannot change its own number.

`ix_facts_as_of` is `(company_id, taxonomy, concept, unit, period_end, period_start, dimensions_hash, known_at DESC)`. `latest_visible_fact` uses that order, then joins the source document and breaks a shared `known_at` by accession number. The full predicate is in the repository section below.

`dimensions_hash` is checked twice before a row is stored. `FactCreate` fills it from the canonical JSON and rejects a caller-supplied hash that does not match. The ORM `before_insert` listener checks again, so a `Fact` built without the Pydantic model still cannot store a mismatched hash. The database checks that the hash is 64 hex characters and that `dimensions` is a JSON object. It does not recompute the hash, because PostgreSQL's JSON text is not the same byte string as the canonical JSON.

### claims

Phase 5 will store predicate, span, polarity, extractor version, and confidence. This phase only reserves `id`, `company_id`, nullable `document_id`, `known_at`, and `created_at`. The nullable document uses the same composite foreign key. When `document_id` is null, PostgreSQL does not enforce the composite key (`MATCH SIMPLE`). When it is set, the document must belong to the same company. Index: `(company_id, known_at)`.

### jobs

Jobs are operational. They have no `known_at`, because they are not public facts. A job has `kind`, `status` (`queued`, `running`, `succeeded`, `failed`), a JSON object `payload`, an optional `idempotency_key`, `run_after`, lock columns (`locked_at`, `locked_by`, which must be set together), `attempts`, and `last_error`.

`idempotency_key` is unique only when it is not null, so many jobs can omit it. The partial index `(status, run_after) WHERE status = 'queued'` is the access path a later worker will use with `SKIP LOCKED`. There is no worker in this slice.

### traces

One row is one model call: `model`, `tokens_in`, `tokens_out`, `latency_ms`, `cost_usd` as `numeric(18, 8)`, and `cache_hit`. `job_id` is optional and uses `ON DELETE SET NULL`, so a trace can outlive its job. Prompt and completion text wait for Phase 3. Indexes: `(job_id)` and `(created_at)`.

## How a restatement is stored

Suppose Assets at 2023-12-31 is first reported in the 10-K and later changed by a 10-K/A.

1. Insert the company. CIK `0000320193` is the stable id. The UUID is only for joins.
2. Insert the 10-K document. `known_at` is that filing's acceptance time.
3. Insert the fact. `value` is the original number. `document_id` is the 10-K. `known_at` is the same acceptance time. `period_type` is `instant`, so `period_start` is null.
4. Insert the 10-K/A as a second document, with a later `known_at`.
5. Insert a second fact. Same company, taxonomy, concept, unit, period, and dimension hash. Different `document_id`, different `value`, later `known_at`.

Both rows stay. The unique key does not collide, because `document_id` differs. Updating the first row's `value` is rejected by the ORM before flush, and by the database trigger if something bypasses the ORM with raw SQL:

```text
CREATE TRIGGER facts_append_only
BEFORE UPDATE ON facts
FOR EACH ROW
EXECUTE FUNCTION facts_reject_update()
```

`DELETE` and `TRUNCATE` still work, so tests can clean up. The tests insert both versions, assert both values are present, then roll back.

## Why the checks exist

Text columns that identify something are checked in the database, not only in Pydantic. A future script that inserts with SQL still cannot store a CIK of `320193`, an accession number without dashes, a duration fact with no start date, or a negative token count.

Form type, job status, and fiscal period are text plus `CHECK`, not Postgres enums. Adding a status or a form is a constraint change, not `ALTER TYPE`.

Foreign keys that are not the leading column of another index have their own index. `claims.document_id` is indexed for that reason. `facts.document_id` is already the leading column of the unique identity key. `facts.company_id` is the leading column of `ix_facts_as_of`.

The `vector` extension is created in this migration and no embedding column is added. Compose already runs `pgvector/pgvector:pg16`. Creating the extension now means a later phase can add a vector column without a separate extension migration.

## What the tests lock

| Test | What it proves |
|---|---|
| Schema tests | Naive datetimes are rejected. Offsets are converted to UTC. Floats are rejected for money. Duration and instant period rules hold. The dimension hash is canonical and a mismatched hash is rejected. |
| Repository leakage tests | A naive `as_of` is rejected. A row at exactly `known_at` is visible and a microsecond earlier is not. Future filings and facts stay hidden, including a fact whose own `known_at` is already in the past while its source document is not. Between an original 10-K and a 10-K/A, the latest value is the original. After the amendment, both values are still returned and the latest value is the restatement. Concept, unit, period, taxonomy, and dimension hash each select their own row. A shared `known_at` resolves by accession number. |
| Read-path guard | An AST walk of `backend/src/titan` allows SQLAlchemy reads of `Fact`, `Document`, `facts`, and `documents` only in `pit/repository.py`. Alembic migrations and tests are outside the walk. The patterns and the static blind spots are documented in `backend/tests/test_no_direct_financial_reads.py`. |
| Metadata tests | Six tables are registered. `value` is `Numeric(38, 10)`. `known_at` is timezone-aware and NOT NULL. The fact unique constraint asks for `NULLS NOT DISTINCT`. The as-of index is `known_at DESC`. Job indexes are partial. The ORM refuses a fact update. |
| Database tests | Skipped if Postgres or the `facts` table is absent. Otherwise they check the live column types, insert a restatement, reject `UPDATE` from SQL and from the ORM, and reject a second insert of the same instant fact. Each test rolls back. |

This review added `backend/src/titan/services/filing_reader.py` with `return select(Fact)` and ran `test_application_code_queries_facts_and_documents_only_in_the_repository`. It failed:

```text
AssertionError: assert ['services/filing_reader.py:9:11: select'] == []
```

The file and the empty `services` directory were removed. The same test is green in the suite below.

## Point-in-time repository

`PointInTimeRepository` in `backend/src/titan/pit/repository.py` is the application read path. Services and agents are not built yet. When they are, they call this module. The query rules are written up in [data-model.md](../data-model.md#repository-query-semantics).

Every method takes `company_id` and a timezone-aware `as_of`. The cutoff is not stored on the repository. A naive datetime raises `AsOfError`. An aware datetime is converted to UTC. A row is visible when `known_at <= as_of`. `filed_at` and `period_of_report` do not decide that.

`visible_documents` returns the company's filings that were already public, ordered by `known_at`, accession number, then id.

`visible_fact_versions` returns every stored version that was already public. The fact and its source document both have to pass the cutoff, and the document has to belong to the same company. An original and a later restatement are both in the result once the amendment is public. This method does not collapse them, and the repository has no update or delete method.

`latest_visible_fact` returns one version of an explicit logical fact: taxonomy, concept, unit, reporting period, and dimensions hash. The greatest `known_at` wins. A tie breaks on accession number, then fact id. Before the amendment, that row is the original. After it, that row is the restatement. The original remains in `visible_fact_versions`.

Results are frozen `DocumentRead` and `FactRead` snapshots taken inside the open session. Text sort keys use the C collation, so `USD` stays before `shares` if the database locale would otherwise reverse them. A null `period_start` sorts after a date.

The historical query is `DISTINCT ON` the logical key, implemented with SQLAlchemy's PostgreSQL `distinct_on`, joined to `documents`, with `known_at <= as_of` on both tables. `ix_facts_as_of` is that key order with `known_at DESC`.

## Commands and results

Reviewed on 2026-10-14 against Compose `pgvector/pgvector:pg16` (`titan2-db-1`, Postgres 16, port 5432, database `titan`). Interpreter and tools: Python 3.15.0, ruff 0.17.0, mypy 2.4.0, pytest 9.1.1, Alembic 1.20.0, SQLAlchemy 2.1.4, Pydantic 2.14.1. The project requires Python 3.11 or newer. This machine ran 3.15.0.

From the repo root:

```text
make check
```

`make check` is `ruff check .`, then `mypy src`, then `pytest -q`, each from `backend/`.

The run below is after `services/filing_reader.py` was deleted.

```text
cd backend && uv run ruff check .
All checks passed!
cd backend && uv run mypy src
Success: no issues found in 31 source files
cd backend && uv run pytest -q
54 passed in 0.54s
```

No tests were skipped. The leakage cases ran against the migrated database. A separate run of the read-path test after the probe was removed: `1 passed in 0.06s`.

From `backend/`:

```text
uv run pytest -v --tb=no
```

```text
54 passed in 0.54s
```

Leakage and restatement tests in that run, all passed:

- `test_future_fact_is_invisible`
- `test_fact_becomes_visible_at_exact_known_at`
- `test_restatement_switches_only_after_the_amendment`
- `test_fact_waits_for_its_source_document`
- `test_restatement_preserves_the_original_and_as_of_selects_the_known_value`
- `test_fact_stays_hidden_until_its_source_document_is_visible`
- `test_future_fact_on_an_already_public_document_stays_hidden`
- `test_future_rows_and_other_companies_stay_hidden`
- `test_boundary_includes_the_cutoff_and_ignores_filed_at`
- `test_restatement_keeps_both_versions`
- `test_database_rejects_fact_updates`
- `test_same_instant_fact_cannot_be_inserted_twice`

The read-path gate is `test_application_code_queries_facts_and_documents_only_in_the_repository`. It passed after the probe file was removed.

From `backend/`, the database was already at head, so upgrade applied nothing:

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

`alembic check` compares the live database with `Base.metadata`. "No new upgrade operations detected" means the migration and the models describe the same tables. The trigger and the `vector` extension are not in the metadata, so that check does not cover them.

```text
docker compose exec -T db psql -U titan -d titan -c '\dt'
```

```text
 public | alembic_version | table | titan
 public | claims          | table | titan
 public | companies       | table | titan
 public | documents       | table | titan
 public | facts           | table | titan
 public | jobs            | table | titan
 public | traces          | table | titan
```

`alembic_version.version_num` is `54459e9eabff`. `pg_extension` contains `vector`. `facts_append_only` is `BEFORE UPDATE ON public.facts FOR EACH ROW EXECUTE FUNCTION facts_reject_update()`.

Column and constraint inspection also confirmed:

- `facts.value` is `numeric(38,10)` NOT NULL. `facts.known_at` and `documents.known_at` are `timestamp with time zone` NOT NULL. `companies.cik` is `character(10)` NOT NULL.
- `uq_facts_source_identity` is `UNIQUE NULLS NOT DISTINCT (document_id, taxonomy, concept, unit, period_start, period_end, dimensions_hash)`.
- `ix_facts_as_of` is `(company_id, taxonomy, concept, unit, period_end, period_start, dimensions_hash, known_at DESC)`.
- `ix_jobs_queued_run_after` is `WHERE (status = 'queued')`. `uq_jobs_idempotency_key` is unique `WHERE (idempotency_key IS NOT NULL)`.
- `fk_facts_document_company` and `fk_claims_document_company` reference `documents (id, company_id)` with `ON DELETE RESTRICT`.

Integer assertions in `tests/test_point_in_time.py` use `Decimal(100)` and the same form. `Decimal(100)` equals `Decimal("100")` for these integers. Ruff 0.17's `FURB157` asks for that constructor. An earlier tree kept `Decimal("100")` and `ruff check .` reported those hints. This review's `ruff check .` reports none.

## What broke, and the fix

**The first migration was a placeholder.** It created `companies (id integer, ticker)` and had already been stamped as head. Alembic will not re-run a revision that is already applied, so editing that file in place would have left the integer table in the database. It was downgraded to base, deleted, and replaced by `54459e9eabff`.

**Check names were doubled.** The naming convention is `ck_<table>_<suffix>`. Names that already included that prefix became `ck_companies_ck_companies_cik_digits`. The models now pass the suffix only (`cik_digits`), and Postgres stores `ck_companies_cik_digits`.

**`company.id` was null before flush.** `default=uuid.uuid4` runs when SQLAlchemy inserts, not when the Python object is constructed. Tests that copied `company.id` onto a document before flush inserted `company_id = null` and hit the NOT NULL constraint. An `init` listener on `Base` now assigns a UUID as the object is constructed. `gen_random_uuid()` remains the server default for inserts that never go through the ORM.

**Text order followed the database locale.** With the default collation, `shares` sorted before `USD`. The repository now sorts taxonomy, concept, unit, dimensions hash, and accession number with `COLLATE "C"`, so the order is byte order. `latest_visible_fact` uses PostgreSQL `distinct_on` rather than `Select.distinct(...)`, which SQLAlchemy 2.1 deprecates.

## Gate

Both Phase 1 exit criteria pass on this review.

1. Leakage tests pass, including restatements. A future fact is invisible. Between the 10-K and the 10-K/A the latest value is the original. After the amendment both rows are still returned and the latest value is the restatement. A fact stays hidden until its source document is public.
2. Application code outside `backend/src/titan/pit/repository.py` does not read `facts` or `documents`. `test_application_code_queries_facts_and_documents_only_in_the_repository` enforces that. A planted `select(Fact)` failed the test, then was removed.

`make check`, Alembic, and the live table list all succeeded. Phase 1 is complete. Phase 2 has not been started.

## What this slice does not do

- No EDGAR client, no filing parser, no agent, no `ModelClient`. Inserts of filings still happen in tests and, later, in ingestion. The repository only reads. There is no `ON CONFLICT DO NOTHING` writer yet. A duplicate identity raises `IntegrityError`.
- No evidence graph, thesis, price, portfolio, or embedding column. The `vector` extension is installed and unused.
- The read-path guard is static. The blind spots are listed in `backend/tests/test_no_direct_financial_reads.py` and locked by `test_static_detection_misses_indirect_queries`.
- `alembic check` does not see `facts_append_only` or `CREATE EXTENSION vector`. Those were checked with `psql`.
- Database tests skip when Postgres is down or `public.facts` is missing. This run did not skip.
