# REX — Technical Architecture Document

**Product:** REX — Research Experiment Engineer  
**Architecture status:** V1 source of truth  
**Architecture style:** Local-first modular monolith with isolated execution workers  
**Primary language:** Python  
**Frontend:** Thin web application  
**Last updated:** 2026-09-28

---

## 1. Architectural goals

REX needs to combine agentic reasoning with deterministic research infrastructure.

The architecture therefore optimizes for:

- reproducibility;
- provenance;
- explicit state transitions;
- sandboxed execution;
- modularity;
- testability;
- resumability;
- provider independence;
- low local complexity;
- future extensibility.

REX should initially be a **modular monolith**, not a distributed microservice platform.

The correct architecture for V1 is:

```text
                 ┌─────────────────────────────┐
                 │        CLI / Web UI         │
                 └──────────────┬──────────────┘
                                │
                         Application API
                                │
                 ┌──────────────▼──────────────┐
                 │      Research Controller    │
                 │                              │
                 │ state machine / loop /      │
                 │ budgets / decisions         │
                 └───────┬───────────┬─────────┘
                         │           │
             ┌───────────▼───┐   ┌──▼────────────┐
             │ Agent Layer    │   │ Domain Logic  │
             │                │   │              │
             │ planner        │   │ experiments  │
             │ hypotheses     │   │ analysis     │
             │ coding         │   │ verification │
             │ critic         │   │ provenance   │
             └────────┬───────┘   └──────┬───────┘
                      │                  │
                      └────────┬─────────┘
                               ▼
                     ┌──────────────────┐
                     │ Persistence      │
                     │ SQLite V1        │
                     │ artifacts/files  │
                     └────────┬─────────┘
                              │
                ┌─────────────▼─────────────┐
                │ Isolated Execution Layer  │
                │ Docker / controlled worker│
                └───────────────────────────┘
```

---

## 2. Recommended technology stack

### Python 3.12+

Python is the primary language because ML experimentation, scientific computing, statistical analysis, and AI SDKs have strong Python ecosystems.

### FastAPI

Use FastAPI for the local application/API layer.

Reasons:

- typed request/response models;
- asynchronous support;
- easy local deployment;
- OpenAPI generation;
- clean separation between UI and backend.

### Pydantic

Use Pydantic models for all important domain objects.

Do not pass unvalidated dictionaries between core modules when a stable schema is appropriate.

### SQLAlchemy + Alembic

Use SQLAlchemy for persistence and Alembic for migrations.

### SQLite for V1

Use SQLite as the default local database.

Reasons:

- zero setup;
- excellent local-first fit;
- transactional;
- easy backup;
- adequate for a single-user research workstation.

Keep the persistence layer abstract enough that PostgreSQL can be added later.

### File-based artifact store

Store large artifacts outside the relational database.

Use a project workspace such as:

```text
data/runs/<research_id>/<experiment_id>/<run_id>/
```

The database stores metadata and references.

### Docker

Use Docker for experiment isolation where available.

The execution layer must enforce timeouts, memory/CPU limits, filesystem boundaries, and controlled network policy.

If Docker is unavailable, fail safely rather than silently executing arbitrary generated code directly on the host.

### Git

Git is the canonical code-version identifier for generated experiment code.

Every execution should record the relevant commit or content hash.

### Typer

Use Typer for the CLI.

### pytest

Use pytest for unit and integration tests.

### Ruff

Use Ruff for linting and formatting where appropriate.

### mypy

Use mypy for type checking in core modules.

### Frontend

Use:

- React;
- TypeScript;
- Vite;
- Tailwind CSS.

The frontend is a thin observation/control surface, not the product's core intelligence.

---

## 3. LLM provider abstraction

Do not hard-code the application around one model provider.

Create:

```text
llm/
  base.py
  providers/
  schemas.py
```

The core system should request capabilities such as:

- structured generation;
- code generation;
- critique;
- summarization;
- tool planning.

Provider-specific adapters should implement those capabilities.

Keep model configuration in environment/configuration rather than source code.

---

## 4. Literature architecture

Use a provider abstraction for scholarly retrieval.

Initial providers can include:

- OpenAlex;
- Semantic Scholar;
- arXiv.

OpenAlex and Semantic Scholar provide structured scholarly metadata; arXiv provides access to preprints.

The literature subsystem should normalize results into an internal schema.

```text
LiteratureSource
├── source_id
├── provider
├── external_id
├── title
├── authors
├── year
├── abstract
├── url
├── retrieved_at
└── raw_metadata
```

Do not treat a search result snippet as a verified paper fact.

Persist source metadata and retrieval time.

---

## 5. Project structure

Recommended initial structure:

```text
rex/
├── pyproject.toml
├── README.md
├── PROJECT_SPEC.md
├── ARCHITECTURE.md
├── SECURITY.md
├── FRONTEND_SPEC.md
├── FEATURE_TICKETS.md
├── .env.example
├── .gitignore
│
├── src/
│   └── rex/
│       ├── __init__.py
│       ├── main.py
│       │
│       ├── api/
│       │   ├── app.py
│       │   ├── routes/
│       │   └── schemas.py
│       │
│       ├── cli/
│       │   └── commands.py
│       │
│       ├── config/
│       │   ├── settings.py
│       │   └── defaults.py
│       │
│       ├── domain/
│       │   ├── research.py
│       │   ├── hypothesis.py
│       │   ├── experiment.py
│       │   ├── execution.py
│       │   ├── result.py
│       │   ├── analysis.py
│       │   ├── claim.py
│       │   └── evidence.py
│       │
│       ├── controller/
│       │   ├── state_machine.py
│       │   ├── research_controller.py
│       │   ├── decision_engine.py
│       │   └── budgets.py
│       │
│       ├── agents/
│       │   ├── investigator.py
│       │   ├── literature.py
│       │   ├── hypothesis.py
│       │   ├── experiment_designer.py
│       │   ├── coder.py
│       │   └── critic.py
│       │
│       ├── llm/
│       │   ├── base.py
│       │   ├── router.py
│       │   ├── schemas.py
│       │   └── providers/
│       │
│       ├── execution/
│       │   ├── sandbox.py
│       │   ├── docker_runner.py
│       │   ├── resource_limits.py
│       │   ├── environment.py
│       │   └── artifacts.py
│       │
│       ├── analysis/
│       │   ├── metrics.py
│       │   ├── statistics.py
│       │   ├── bootstrap.py
│       │   ├── plots.py
│       │   └── validation.py
│       │
│       ├── evidence/
│       │   ├── graph.py
│       │   ├── lineage.py
│       │   ├── claims.py
│       │   └── verifier.py
│       │
│       ├── literature/
│       │   ├── base.py
│       │   ├── openalex.py
│       │   ├── semantic_scholar.py
│       │   └── arxiv.py
│       │
│       ├── persistence/
│       │   ├── database.py
│       │   ├── models.py
│       │   ├── repositories/
│       │   └── migrations/
│       │
│       ├── reporting/
│       │   ├── report_generator.py
│       │   └── templates/
│       │
│       └── observability/
│           ├── events.py
│           ├── logging.py
│           └── tracing.py
│
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   ├── api/
│   │   └── types/
│   └── package.json
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── security/
│   └── fixtures/
│
├── experiments/
├── data/
│   ├── database/
│   ├── runs/
│   ├── datasets/
│   └── cache/
│
└── scripts/
```

---

## 6. Database schema

### research_runs

Stores a top-level investigation.

Fields:

- id — UUID/string primary key.
- title — human-readable name.
- research_question — original question.
- status — current state.
- created_at.
- updated_at.
- configuration_json.
- budget_json.

### hypotheses

Fields:

- id.
- research_run_id.
- statement.
- rationale.
- expected_direction.
- falsification_condition.
- status.
- created_at.

Relationship:

`research_run 1:N hypotheses`

### experiments

Fields:

- id.
- research_run_id.
- hypothesis_id.
- objective.
- specification_json.
- status.
- created_at.
- parent_experiment_id — nullable, for refinements/branches.

Relationships:

`research_run 1:N experiments`

`hypothesis 1:N experiments`

`experiment N:1 parent experiment`

### executions

Fields:

- id.
- experiment_id.
- status.
- started_at.
- finished_at.
- command.
- git_commit.
- code_hash.
- dataset_hash.
- configuration_hash.
- seed.
- environment_json.
- resource_usage_json.
- exit_code.
- stdout_artifact_id.
- stderr_artifact_id.

Relationship:

`experiment 1:N executions`

### results

Fields:

- id.
- execution_id.
- metric_name.
- metric_value.
- metric_unit.
- result_json.
- created_at.

Relationship:

`execution 1:N results`

### analyses

Fields:

- id.
- research_run_id.
- analysis_type.
- input_result_ids.
- method.
- output_json.
- created_at.

### artifacts

Fields:

- id.
- research_run_id.
- execution_id nullable.
- artifact_type.
- path.
- content_hash.
- size_bytes.
- metadata_json.
- created_at.

### literature_sources

Fields:

- id.
- research_run_id.
- provider.
- external_id.
- title.
- authors_json.
- year.
- abstract.
- url.
- retrieved_at.
- raw_metadata_json.

### claims

Fields:

- id.
- research_run_id.
- text.
- claim_type.
- confidence.
- status.
- created_at.

### evidence_links

Fields:

- id.
- claim_id.
- source_type.
- source_id.
- relationship_type.
- created_at.

This generalized link table allows claims to be supported by results, analyses, literature sources, or other evidence.

### events

Fields:

- id.
- research_run_id.
- event_type.
- timestamp.
- actor_type.
- actor_id.
- payload_json.

This is the event/audit trail.

---

## 7. Domain invariants

The system must enforce:

1. An execution cannot exist without an experiment.
2. A result cannot exist without an execution.
3. A claim cannot be marked verified unless it has at least one evidence link.
4. A numerical experimental claim must ultimately resolve to stored result/analysis evidence.
5. Historical executions cannot be updated in-place except for explicitly mutable operational metadata.
6. Experiment specifications are immutable after execution begins.
7. Artifacts are content-addressed or otherwise integrity-checked.
8. A failed run remains part of research history.

---

## 8. Execution architecture

The execution engine should expose a narrow interface:

```python
run_experiment(specification) -> ExecutionRecord
```

Internally:

```text
Experiment Specification
        ↓
Validation
        ↓
Workspace Creation
        ↓
Environment Setup
        ↓
Sandbox Launch
        ↓
Execution
        ↓
Artifact Capture
        ↓
Metric Extraction
        ↓
Persistence
```

No LLM should directly call arbitrary shell commands.

The coding agent requests an execution through the execution service.

---

## 9. Sandbox requirements

The sandbox must support:

- CPU limit;
- memory limit;
- wall-clock timeout;
- filesystem isolation;
- controlled environment variables;
- optional network disablement;
- process cleanup;
- output size limits.

For V1, Docker is the preferred implementation.

Do not silently fall back to unrestricted host execution.

---

## 10. Evidence verification algorithm

`rex verify` should:

1. Load the research run.
2. Enumerate all report claims.
3. Resolve each claim to evidence links.
4. Resolve evidence to results/analyses/runs.
5. Check referenced artifacts exist.
6. Check artifact hashes.
7. Recompute selected derived metrics where feasible.
8. Compare report numbers with stored results.
9. Check code/configuration/dataset metadata.
10. Validate citation source IDs.
11. Emit structured failures/warnings.
12. Return non-zero exit status if integrity checks fail.

---

## 11. API design

Initial endpoints:

```text
POST   /api/research
GET    /api/research/{id}
POST   /api/research/{id}/start
POST   /api/research/{id}/pause
POST   /api/research/{id}/resume
GET    /api/research/{id}/events
GET    /api/research/{id}/experiments
GET    /api/experiments/{id}
GET    /api/experiments/{id}/runs
GET    /api/runs/{id}
GET    /api/runs/{id}/artifacts
GET    /api/research/{id}/claims
GET    /api/research/{id}/evidence
POST   /api/research/{id}/verify
GET    /api/research/{id}/report
```

Use typed request/response schemas.

---

## 12. Configuration

Example environment variables:

```text
REX_ENV=development
REX_DATABASE_URL=sqlite:///./data/database/rex.db
REX_ARTIFACT_ROOT=./data/runs
REX_WORKSPACE_ROOT=./experiments
REX_LLM_PROVIDER=
REX_LLM_API_KEY=
REX_LLM_MODEL=
REX_OPENALEX_API_KEY=
REX_SEMANTIC_SCHOLAR_API_KEY=
REX_DOCKER_ENABLED=true
REX_MAX_EXPERIMENT_RUNTIME_SECONDS=1800
REX_MAX_CPU=
REX_MAX_MEMORY_MB=
REX_NETWORK_POLICY=disabled
REX_LOG_LEVEL=INFO
```

Only required provider keys should be mandatory.

Never commit secrets.

---

## 13. Observability

Use structured JSON logs for backend events.

Every event should contain:

- timestamp;
- research_run_id;
- event_type;
- actor;
- correlation ID;
- relevant entity ID;
- status;
- error information when applicable.

---

## 14. Deployment model

### V1

Local machine:

```text
Browser
  ↓
React/Vite
  ↓
FastAPI
  ↓
Python REX core
  ↓
SQLite + filesystem
  ↓
Docker experiment worker
```

### Future

The same application boundaries should permit:

- PostgreSQL;
- remote object storage;
- remote execution workers;
- distributed experiment scheduling.

Do not build these in V1.

---

## 15. Architecture decisions to preserve

### ADR: Modular monolith

Use a modular monolith because the initial workload is single-user and the major engineering challenge is correctness, not horizontal scaling.

### ADR: SQLite first

Use SQLite because REX is initially a local research workstation.

### ADR: Filesystem artifacts

Large outputs belong in files; relational metadata belongs in the database.

### ADR: Docker sandbox

Generated code is untrusted and must not run with unrestricted host permissions.

### ADR: Explicit evidence graph

Evidence relationships must be represented explicitly rather than inferred from filenames or LLM text.

### ADR: Provider abstraction

Do not couple core research logic to a single LLM vendor or literature provider.
