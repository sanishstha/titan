# TITAN system architecture

Research tool only. Not investment advice. Paper trading only.

Reads of filings and facts go through the point-in-time repository with an `as_of` timestamp. A row is visible only when `known_at` is at or before `as_of`. Numbers in a report come from stored XBRL facts or deterministic calculations, and every claim cites a document span or fact id.

## System

```mermaid
flowchart LR
  User([User]) --> Terminal[Terminal]
  Terminal --> API[FastAPI]
  API --> Workers[Workers]
  Workers --> Domain[Domain]
  Domain --> Store[(Postgres)]
  Sources[Public sources] --> Workers
  Domain --> Models[ModelClient]
```

## Terminal

```mermaid
flowchart TB
  User([Analyst or portfolio manager]) --> Universe[Universe and company]
  User --> Changed[What changed]
  User --> Evidence[Evidence graph]
  User --> Hypothesis[Competing theses]
  User --> Live[Live investigation]
  User --> Infer[Inference panel]

  Universe --> Inv[Investigations API]
  Changed --> Inv
  Evidence --> Inv
  Hypothesis --> Inv
  Live --> Stream[SSE steps]
  Infer --> Metrics[Latency and cost]
```

## Ingestion

```mermaid
flowchart LR
  EDGAR[SEC EDGAR] --> Client[EDGAR client]
  FRED[FRED] --> Client
  Prices[Price data] --> Client
  Client --> Cache[(Raw cache)]
  Client --> Parser[Section parser]
  Parser --> PIT[Point-in-time repository]
  Monitor[Event monitor] --> EDGAR
  Monitor --> Queue[Job queue]
```

The client sends a descriptive User-Agent, stays at or under 5 requests per second, and retries with backoff. Re-running ingestion for the same filing is a no-op.

## Point-in-time store

```mermaid
flowchart TB
  PIT[Point-in-time repository] --> Companies[(companies)]
  PIT --> Documents[(documents and sections)]
  PIT --> Facts[(facts)]
  PIT --> Claims[(claims)]
  PIT --> EvGraph[(evidence graph)]
  PIT --> Theses[(theses)]
  PIT --> Jobs[(jobs and traces)]
```

Every read takes `as_of` and drops rows whose `known_at` is later than that time. A fact is included when its source document is visible at that same cutoff. Application reads of documents and facts go through `backend/src/titan/pit/repository.py`. An AST test rejects a SQLAlchemy read of those tables from any other application module. A restated value is stored as its own row, so an earlier `as_of` still returns the value that was public then. The version list still contains the original after the amendment is public.

The Phase 1 tables, keys, and indexes are specified in [data-model.md](data-model.md). Sections, the evidence graph, theses, and the paper book are later phases.

## Investigation

```mermaid
flowchart TB
  Queue[Job queue] --> Runner[Checkpointed runner]
  Runner --> Research[Research agent]
  Research --> Thesis[Thesis agent]
  Thesis --> Challenger[Challenger]
  Challenger --> Arbiter[Arbiter]

  Research --> FactsTool[get_facts]
  Research --> RatiosTool[get_ratios]
  Research --> SearchTool[search_filing_text]
  Research --> SectionTool[get_filing_section]

  FactsTool --> PIT[Point-in-time repository]
  RatiosTool --> PIT
  SearchTool --> PIT
  SectionTool --> PIT

  Research --> Verifier[Report verifier]
  Verifier --> Report[Cited report]
```

Ratios are computed in code. The model chooses tools and writes prose. The verifier rejects any number that does not trace to a fact or a claim.

## Inference

```mermaid
flowchart LR
  Agents[Agents] --> Router[Model router]
  Router --> Frontier[Frontier API]
  Router --> GPU[vLLM or SGLang]
  Router --> Mock[Mock]
  Router --> Traces[(traces)]
  GPU --> Prom[Prometheus]
  Frontier --> Prom
  Prom --> Graf[Grafana]
  Bench[Benchmark harness] --> Router
```

Every model call goes through `ModelClient` and records model, tokens, latency, cost, and cache hit. The router starts on the smaller model and escalates when confidence is low.

## Measurement

```mermaid
flowchart LR
  Gold[Gold claims and changes] --> ExtractEval[Extraction eval]
  Cases[Fixed company cases] --> AgentEval[Agent eval]
  History[Point-in-time replay] --> Backtest[Backtest]
  ExtractEval --> Gate[make eval]
  AgentEval --> Gate
  Backtest --> Gate
```

## Paper portfolio

Optional. Paper trading only. No brokerage.

```mermaid
flowchart LR
  Thesis[Thesis version] --> Proposal[Allocation proposal]
  Constraints[Risk constraints] --> Proposal
  Proposal --> Approval[Human approval]
  Approval --> Book[Paper book]
  Book --> Ledger[(Decision ledger)]
  Prices[Stored prices] --> Book
```
