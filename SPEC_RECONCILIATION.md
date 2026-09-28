# SPEC_RECONCILIATION.md — Specification Reconciliation

**Product:** REX — Research Experiment Engineer  
**Date:** 2026-09-28  
**Status:** Complete & Approved  

---

## 1. Overview & Hierarchy of Authority

This document reconciles the five authoritative primary specification documents with the initial implementation documents created during repository setup:

### Authoritative Primary Documents
1. [`01_PRD.md`](01_PRD.md) — Product vision, user personas, MVP scope, core lifecycle, success metrics, and non-goals.
2. [`02_Technical_Architecture.md`](02_Technical_Architecture.md) — System architecture, modular monolith design, technology choices, database schema, and API specs.
3. [`03_Security_Access.md`](03_Security_Access.md) — Threat boundaries, Docker execution sandboxing, capability-based permissions, prompt injection barriers, and audit logging.
4. [`04_Frontend_Specification.md`](04_Frontend_Specification.md) — Research workstation UI, design tokens, lineage visualization, and verification views.
5. [`05_Feature_Tickets.md`](05_Feature_Tickets.md) — Feature backlog, ticket dependencies, acceptance criteria, and implementation sequencing.

### Implementation Documents Reconciled
- [`PROJECT_SPEC.md`](PROJECT_SPEC.md)
- [`ARCHITECTURE.md`](ARCHITECTURE.md)
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)

### Strict Hierarchy Invariant
```text
Primary Specifications (01_PRD through 05_Feature_Tickets)
                          ↓
Implementation Documentation (PROJECT_SPEC, ARCHITECTURE, IMPLEMENTATION_PLAN)
                          ↓
                      Source Code
                          ↓
                         Tests
```
If any discrepancy arises, the primary specification set governs unless an explicit technical justification is documented in this reconciliation record.

---

## 2. Document Agreements

The primary and secondary documents firmly agree on the following foundational tenets:

1. **The Inviolable Axiom**: The LLM proposes research actions; deterministic software executes, measures, and records their consequences. LLMs are never the authoritative source of numerical metrics, execution status, statistical analysis, or verification proofs.
2. **Three-Plane Separation**:
   - *Reasoning Plane*: LLMs/agents proposing problem definitions, hypotheses, experiment designs, code, critiques, and decisions.
   - *Execution Plane*: Deterministic code execution, metric extraction, statistical analysis, figure generation, and environment telemetry.
   - *Evidence Plane*: Immutable persistence of runs, code hashes, configs, datasets, results, analyses, figures, claims, and verification audits.
3. **Research Lifecycle Finite State Machine**:
   - `INITIALIZE → UNDERSTAND → LITERATURE → HYPOTHESES → DESIGN → IMPLEMENT → EXECUTE → VERIFY → ANALYZE → CRITIQUE → DECIDE → COMPLETE` (with `FAILED` as a first-class terminal state).
   - `DECIDE` transitions: `REFINE`, `REPLICATE`, `PIVOT`, `STOP`.
4. **Experiment Immutability**: Once an experiment commences execution, its `ExperimentSpec` is strictly immutable. Iterations or changes create child/versioned experiments, preserving full lineage.
5. **Deterministic Verification (`rex verify`)**: Independent, zero-LLM auditor inspecting stored disk artifacts, validating cryptographic hashes (SHA-256), recomputing statistics, and detecting deliberate tampering or ungrounded claims.
6. **Local-First Modular Monolith**: Designed for single-user local research workstations in V1 without premature microservices, Kubernetes, or cloud complexity.

---

## 3. Discrepancies, Conflicts, and Resolutions

### Conflict 1: Sandbox Security Boundary (Subprocess vs. Docker)
- **Primary Specification (`02_Technical_Architecture.md` §2 & §9, `03_Security_Access.md` §7, ticket `REX-017`)**:
  - The security boundary for generated experiment code is **Docker containerization**.
  - Mandatory container controls: non-root user, CPU limits, memory limits, wall-clock timeouts, restricted filesystem mounts, no host Docker socket access, no host credentials/SSH keys, and network **disabled by default**.
  - **Fail-safe rule**: If Docker is unavailable, the system must fail safely with a clear warning or operate in a restricted test/mock fixture mode; it must **never silently execute untrusted generated code directly on the host**.
- **Initial Implementation Plan**:
  - Emphasized host subprocess management with Windows process groups (`CREATE_NEW_PROCESS_GROUP`) and `psutil` resource monitoring as the primary execution engine.
- **Resolution**:
  - Docker containerization is established as the primary execution engine (`rex.execution.docker_runner`, ticket `REX-017`).
  - Host subprocess execution is strictly relegated to local development/unit test fixtures where predefined, trusted scripts are executed.
  - The execution service must verify Docker availability. If Docker is disabled or unavailable when running untrusted generated code, execution fails closed unless an explicit local-testing bypass flag is set.
  - Subprocess timeouts and `psutil` monitoring remain as secondary host-level telemetry and test harness tooling.

### Conflict 2: Database Technology & Schema Alignment
- **Primary Specification (`02_Technical_Architecture.md` §2 & §6, `REX-004` through `REX-008`)**:
  - Mandates **SQLAlchemy** (2.0+) and **Alembic** migrations over **SQLite** for V1.
  - Requires explicit schema tables: `research_runs`, `hypotheses`, `experiments`, `executions`, `results`, `analyses`, `artifacts`, `literature_sources`, `claims`, `evidence_links`, and `events`.
  - External storage of heavy artifacts in `data/runs/<research_id>/<experiment_id>/<run_id>/` with database storing metadata and file references.
- **Initial Implementation Plan**:
  - Envisaged raw `sqlite3` without Alembic migrations and custom filesystem conventions (`experiments/EXP-XXXX/`).
- **Resolution**:
  - Adopt **SQLAlchemy 2.0+** and **Alembic** as required by `02_Technical_Architecture.md` and `REX-004`.
  - Implement typed SQLAlchemy declarative models matching the 11 schema tables in `02_Technical_Architecture.md` §6.
  - Implement repository patterns (`rex.persistence.repositories`) to isolate database queries.
  - Use typed configuration settings (`rex.config.settings`) to configure `REX_DATABASE_URL` (`sqlite:///./data/database/rex.db`), `REX_ARTIFACT_ROOT` (`./data/runs`), and `REX_WORKSPACE_ROOT` (`./experiments`).

### Conflict 3: CLI Framework & Backend Dependencies
- **Primary Specification (`02_Technical_Architecture.md` §2, `REX-001`, `REX-037`)**:
  - Recommends **Typer** for the CLI.
  - Recommends **FastAPI** + **Uvicorn** for the local backend application API.
- **Initial Implementation Plan**:
  - Initial `pyproject.toml` specified `click` and omitted `fastapi`, `uvicorn`, `sqlalchemy`, `alembic`, `docker`, and `typer`.
- **Resolution**:
  - Update `pyproject.toml` to include:
    - `typer>=0.12.0` (with Click as underlying foundation)
    - `fastapi>=0.110.0` and `uvicorn>=0.28.0`
    - `sqlalchemy>=2.0.0` and `alembic>=1.13.0`
    - `docker>=7.0.0`
  - Ensure all dependencies install cleanly in `.venv`.

### Conflict 4: Verification Ordering and Dual Verification Concepts
- **Clarification**:
  - **Lifecycle State `VERIFY`**: The synchronous step in the research loop immediately after `EXECUTE` and before `ANALYZE`. It deterministically verifies process exit code (0), absence of timeouts, and that the expected output schema (`metrics.json`) is populated before statistical computation begins.
  - **Independent Audit Command (`rex verify <research_id>`)**: The comprehensive end-to-end evidence verifier (ticket `REX-026`). It traverses the full evidence graph (`Claim → Result/Analysis → Run → Experiment → CodeVersion/Config/Dataset`), validates SHA-256 hashes against disk files, recomputes derived statistics, and outputs `verification_report.json` and `verification_report.md`.
- **Resolution**:
  - Both concepts are preserved and disambiguated across all documentation. `VERIFY` state handles execution-level sanity; `rex verify` handles research-level evidentiary integrity.

### Conflict 5: Repository Directory Structure & Module Naming
- **Primary Specification (`02_Technical_Architecture.md` §5)** lists a modular layout under `src/rex/`:
  `api/`, `cli/`, `config/`, `domain/`, `controller/`, `agents/`, `llm/`, `execution/`, `analysis/`, `evidence/`, `literature/`, `persistence/`, `reporting/`, `observability/`.
- **Master Development Context (§8)** notes:
  "The current architecture is organized approximately around: `rex/core/`, `rex/planes/...`, `rex/storage/`, `rex/utils/`. The exact structure may evolve if there is a concrete engineering reason. Do not restructure directories merely for aesthetics. Preserve conceptual separation between: domain/core, reasoning, execution, evidence, storage, utilities."
- **Resolution**:
  - We align the module packages so that every ticket in `05_Feature_Tickets.md` has a clear, unambiguous destination while maintaining the Three Planes conceptual model.
  - Structure:
    ```text
    rex/
    ├── domain/          # Core domain models (ResearchRun, Hypothesis, Experiment, etc.)
    ├── controller/      # State machine, research controller, budget engine
    ├── agents/          # Reasoning plane: investigator, hypothesis, designer, coder, critic
    ├── llm/             # LLM provider abstractions (Gemini, Groq, OpenAI, Mock)
    ├── execution/       # Execution plane: Docker runner, sandbox, workspace, environment
    ├── analysis/        # Execution plane: metrics, statistics, bootstrap, plots
    ├── evidence/        # Evidence plane: graph, lineage, verifier, claims
    ├── persistence/     # SQLite database, SQLAlchemy models, Alembic, repositories
    ├── literature/      # Scholarly providers: OpenAlex, Semantic Scholar, arXiv
    ├── reporting/       # Research report generator
    ├── observability/   # Structured events, JSON logging, tracing
    ├── config/          # Typed application settings (Pydantic BaseSettings)
    ├── api/             # FastAPI local REST & SSE endpoints
    └── cli/             # Typer CLI commands (rex research, rex verify, etc.)
    ```
  - This 100% matches `02_Technical_Architecture.md` §5 and maps 1:1 to every ticket (`REX-001` through `REX-045`), providing modular boundaries without redundant nested planes folders.

---

## 4. Security Corrections Established

1. **Docker Sandbox as Inviolable Security Boundary**:
   - Untrusted generated code must execute inside Docker containers with non-root permissions, CPU/RAM quotas, wall-clock timeouts, disabled network, and no access to host credentials or the Docker daemon socket (`03_Security_Access.md` §7).
2. **Untrusted Literature & External Content Barrier**:
   - Text retrieved from external literature providers (OpenAlex, Semantic Scholar, arXiv) is untrusted data.
   - External text is strictly quarantined in prompt data sections with clear delimiter tags and cannot alter agent capabilities, system instructions, or initiate execution (`03_Security_Access.md` §6, `REX-032`).
3. **Capability-Based Agent Permissions**:
   - Agents possess strictly scoped capabilities based on current lifecycle state (e.g., Literature Agent cannot call `experiment.execute` or access the host filesystem; `03_Security_Access.md` §4).
4. **Research Budget Enforcement**:
   - Explicit caps on experiment runs, LLM requests, token usage, runtime duration, and storage usage prevent runaway autonomous execution (`03_Security_Access.md` §11, `REX-011`).

---

## 5. Implementation-Order Corrections

Following `05_Feature_Tickets.md` and the master development instructions:

1. **Do not jump directly to LLM agents or the frontend UI.**
2. **Build the truth-producing foundation first**:
   - Ticket `REX-001`: Project skeleton & dependencies.
   - Ticket `REX-002`: Typed configuration system (`rex.config.settings`).
   - Ticket `REX-003`: Structured logging and event tracking (`rex.observability.events`).
   - Ticket `REX-004`: Database layer (`rex.persistence.database`, SQLAlchemy, SQLite).
   - Ticket `REX-005` to `REX-008`: Domain entities & persistence (Run, Hypothesis, Experiment, Execution, Result, Artifact).
   - Ticket `REX-009` & `REX-010`: Research state machine and controller.
   - Ticket `REX-011`: Research budget tracking.
   - Ticket `REX-012`: LLM provider abstraction & `MockLLMProvider`.
   - Ticket `REX-017` & `REX-018`: Execution sandbox & workspace isolation.
   - Ticket `REX-020` & `REX-021`: Metric extraction & deterministic statistical analysis.
   - Ticket `REX-023` to `REX-026`: Evidence graph, claims, hashing, and `rex verify`.
   - Ticket `REX-042` & `REX-043`: Toy research benchmark & deliberate evidence corruption tests.
3. Only after the execution and verification core is tested and proven does the system proceed to the autonomous research loop (`REFINE`/`REPLICATE`/`PIVOT`/`STOP`), scholarly literature integration, reporting, and frontend.

---

## 6. Unresolved Questions & Status

| Topic | Analysis | Status / Resolution |
| :--- | :--- | :--- |
| **Docker availability in test environments** | Some developer environments or CI runners lack a running Docker daemon. | Handled: `REX_DOCKER_ENABLED` setting. When Docker is active, generated code runs in containers. When running unit tests with mock scripts, an explicit mock runner can be used. When Docker is unavailable during real autonomous research, REX fails closed with a clear message rather than running untrusted code on the host. |
| **Literature API credentials** | OpenAlex, Semantic Scholar, and arXiv provide open-access or rate-limited tiers without mandatory paid keys. | Handled: Fallback mock/fixture literature providers exist for offline testing; external keys are optional and loaded via environment variables without committing secrets. |
| **Frontend implementation timing** | Frontend is specified in `04_Frontend_Specification.md`. | Reaffirmed: Frontend (React/Vite) is built in Epic 10 after backend APIs and the core research engine are completely functional and tested. |

---

## 7. Approval & Alignment Confirmation

All requirements across `01_PRD.md`, `02_Technical_Architecture.md`, `03_Security_Access.md`, `04_Frontend_Specification.md`, and `05_Feature_Tickets.md` are now fully reflected and reconciled. Implementation will proceed strictly in ticket-driven dependency order starting with Epic 0 (`REX-001` & `REX-002`).
