# IMPLEMENTATION_PLAN.md — REX Implementation Roadmap

## 1. Overview & Strategy

REX is implemented with strict engineering discipline. Rather than attempting a massive, untested code dump, we follow an iterative, test-driven approach centered on verifiable vertical slices.

- **V1 Goal**: Complete vertical slice. Input a research question $\to$ create experiment specification $\to$ generate code $\to$ execute in sandbox $\to$ record telemetry & metrics $\to$ register in evidence graph $\to$ deterministically audit via `rex verify`.
- **V2 Goal**: Closed-loop autonomous research. Critic agent $\to$ autonomous decision (`REFINE`, `REPLICATE`, `PIVOT`, `STOP`) $\to$ multi-step experiment refinement with lineage tracking.
- **V3 Goal**: Literature grounding, multi-hypothesis exploration branches, structured research memory, and full evidence-backed scientific report generation.

---

## 2. Phased Roadmap

```mermaid
flowchart LR
    P1["Phase 1:\nCore Contracts & State Machine"] --> P2["Phase 2:\nSandbox & Execution Plane"]
    P2 --> P3["Phase 3:\nEvidence Plane & Persistence"]
    P3 --> P4["Phase 4:\nDeterministic Analysis Engine"]
    P4 --> P5["Phase 5:\nReasoning Plane & Agents"]
    P5 --> P6["Phase 6:\nV1 End-to-End Slice"]
    P6 --> P7["Phase 7:\nIndependent Verifier"]
    P7 --> P8["Phase 8:\nV2 Closed-Loop Research"]
```

### Phase 1: Core Contracts, Data Models & State Machine
- **Objectives**:
  - Implement Pydantic models for all entities: `ResearchSession`, `Hypothesis`, `ExperimentSpec`, `RunRecord`, `MetricResult`, `AnalysisResult`, `Claim`, `VerificationReport`.
  - Implement the deterministic Finite State Machine (`rex.core.state_machine`) with explicit state transitions, transition guards, and failure traps.
  - Implement structured event logging (`rex.core.events`) emitting standard lifecycle events (`RESEARCH_STARTED`, `EXECUTION_STARTED`, etc.).
  - Implement cryptographic hashing utilities (`rex.utils.hashing`) for source trees, configurations, and artifact files (SHA-256).

### Phase 2: Execution Plane & Sandboxing
- **Objectives**:
  - Implement `rex.planes.execution.sandbox`: Subprocess management, process group isolation (Windows `CREATE_NEW_PROCESS_GROUP` / POSIX `setsid`), wall-clock timeouts, peak memory measurement (via `psutil`), and clean termination.
  - Implement `rex.planes.execution.environment`: Capture Python runtime, installed package versions, OS metadata, CPU/GPU hardware details, and current Git commit SHA.
  - Implement `rex.planes.execution.runner`: Orchestrate execution of an experiment run, generate sequential `RUN-XXXX` IDs, stream stdout/stderr, and capture raw `metrics.json`.

### Phase 3: Evidence Plane & Filesystem Persistence
- **Objectives**:
  - Implement `rex.storage.experiment_fs`: Enforce append-only filesystem conventions (`experiments/EXP-XXXX/executions/RUN-XXXX/`). Guarantee historical records are never mutated.
  - Implement `rex.planes.evidence.store`: Local SQLite relational database with strict foreign keys, indexing, and JSON columns for entity storage.
  - Implement `rex.planes.evidence.graph`: Relational edge management (`SUPPORTED_BY`, `PRODUCED_BY`, `EXECUTED_CODE`, `DERIVED_FROM`, etc.).

### Phase 4: Deterministic Analysis Engine
- **Objectives**:
  - Implement `rex.planes.execution.analyzer`:
    - Descriptive stats: sample mean, median, standard deviation, standard error, IQR.
    - Inferential stats: two-sample Welch's $t$-test, paired $t$-test, Mann-Whitney $U$.
    - Confidence intervals: Student's $t$ 95% CI and BCa bootstrap intervals.
    - Effect sizes: Cohen's $d$, Hedge's $g$.
    - Deterministic visualization: Matplotlib plots (learning curves, ablation bar charts, seed distributions) saved to `artifacts/` along with underlying data JSON.

### Phase 5: Reasoning Plane & Agent Architecture
- **Objectives**:
  - Implement `rex.planes.reasoning.llm_provider`: Uniform provider interface supporting Gemini, Groq, OpenAI, and a deterministic `MockLLMProvider` for offline test suites.
  - Implement `ProblemInvestigator`: Deconstruct research question into testable variables and evaluation metrics.
  - Implement `HypothesisEngine`: Formulate structured hypotheses with explicit falsification conditions.
  - Implement `ExperimentDesigner`: Output validated `ExperimentSpec` contracts.
  - Implement `CodingAgent`: Generate standalone `source/run.py` script adhering strictly to `ExperimentSpec`.

### Phase 6: V1 Vertical Slice Integration & CLI
- **Objectives**:
  - Assemble end-to-end pipeline: Question $\to$ Spec $\to$ Code $\to$ Execution $\to$ Results $\to$ Provenance Graph.
  - Implement Click/Typer CLI commands: `rex research`, `rex status`, `rex inspect`.
  - Validate with a real computational ML task (e.g., comparing learning rate schedulers or optimization algorithms on a synthetic/standard benchmark).

### Phase 7: Independent Verifier (`rex verify`)
- **Objectives**:
  - Implement zero-LLM deterministic verification:
    - Trace every `Claim` to its supporting `Result`.
    - Trace every `Result` to its `Run` on disk.
    - Recompute SHA-256 hashes of code, configuration, and artifacts and match against stored records.
    - Recalculate statistical values from raw metrics and assert equivalence.
    - Flag any disconnected, fabricated, or tampered claims.
  - Generate `verification_report.json` and human-readable `verification_report.md`.
  - Add mutation/tamper tests: deliberately modify a metric or claim and verify `rex verify` catches it.

### Phase 8: V2 Closed-Loop Research
- **Objectives**:
  - Implement `ResearchCritic`: Audit experimental design and results across baseline fairness, sample size, seed dependence, and data leakage.
  - Implement autonomous `DECIDE` state transitions: `REFINE`, `REPLICATE`, `PIVOT`, `STOP`.
  - Implement iterative multi-experiment campaigns with full parent-child lineage tracking.

---

## 3. Concrete V1 Implementation Plan

The V1 milestone establishes the core truth-producing pipeline. Here are the precise steps for V1:

| Step | Component | Action | Verification |
| :---: | :--- | :--- | :--- |
| **1.1** | `rex.core.models` | Define Pydantic models for `ExperimentSpec`, `RunRecord`, `MetricResult`, `EvidenceEntity`. | Unit tests passing serialization/deserialization. |
| **1.2** | `rex.utils.hashing` | Implement SHA-256 directory and file hashing. | Unit test verifying identical hash for identical trees. |
| **1.3** | `rex.storage.experiment_fs` | Build append-only directory manager (`experiments/EXP-XXXX/`). | File system tests verifying non-overwriting behavior. |
| **1.4** | `rex.planes.execution.sandbox` | Build subprocess runner with timeout, memory tracking, and process cleanup. | Test running toy scripts (success, exit 1, timeout, memory cap). |
| **1.5** | `rex.planes.execution.environment`| Build environment inspector (Python, pip freeze, Git commit, CPU). | Test environment snapshot JSON output. |
| **1.6** | `rex.planes.evidence.store` | Build SQLite store and edge recording. | SQL tests inserting and querying runs and evidence edges. |
| **1.7** | `rex.planes.execution.analyzer`| Implement mean, std, 95% CI, Welch's $t$-test, Cohen's $d$, and plot generation. | Stat unit tests against known SciPy reference values. |
| **1.8** | `rex.planes.reasoning` | Build MockLLMProvider and real LLM providers (Gemini/OpenAI) + Designer + Coder. | Unit tests checking prompt generation and schema parsing. |
| **1.9** | `rex.core.controller` | Connect FSM from `INITIALIZE` $\to$ `COMPLETE`. | Integration test running end-to-end pipeline with mock and real execution. |
| **1.10**| `rex.planes.evidence.verifier`| Build `rex verify` logic and report output. | Tamper test: corrupt `metrics.json` and ensure audit fails. |
| **1.11**| `rex.cli` | Wire up CLI commands (`research`, `status`, `inspect`, `verify`). | CLI test verifying command output and exit codes. |

---

## 4. Required Dependencies

All dependencies are standard, production-ready, and pinned for reproducibility.

### Core Runtime Dependencies
- `pydantic >= 2.6.0`: Strict schema definition, immutable data models, JSON serialization.
- `click >= 8.1.7`: Deterministic CLI parsing, subcommands, and parameter validation.
- `numpy >= 1.26.0`: Numerical array manipulation and metric processing.
- `scipy >= 1.12.0`: Deterministic statistical testing (Welch's $t$, Mann-Whitney, bootstrap, CIs).
- `matplotlib >= 3.8.0`: Headless plotting (`Agg` backend) for deterministic figure generation.
- `psutil >= 5.9.8`: Process resource tracking, child process tree termination, memory monitoring.
- `rich >= 13.7.1`: Clean terminal tables, status spinners, and structured logs.
- `httpx >= 0.27.0`: Robust async/sync HTTP client for LLM API integrations (Gemini, Groq, OpenAI).

### Standard Library Components Utilized
- `sqlite3`: Zero-daemon, transactional, local relational database for evidence graph.
- `subprocess`: Process isolation and execution management.
- `hashlib`: Cryptographic SHA-256 computation.
- `pathlib`: Cross-platform path manipulations.
- `typing`: Type annotations.

### Development & Test Dependencies
- `pytest >= 8.0.0`: Test runner.
- `pytest-cov >= 4.1.0`: Code coverage reporting.
- `pytest-mock >= 3.12.0`: Mocking utilities for unit tests.
- `ruff >= 0.3.0`: High-speed linting and formatting.

---

## 5. Comprehensive Test Strategy

The REX test suite guarantees deterministic behavior, security, and tamper resistance:

### 5.1 Unit Tests (`tests/unit/`)
- `test_state_machine.py`: Validate all legal transitions and ensure illegal transitions throw `InvalidStateTransitionError`.
- `test_models.py`: Validate Pydantic schema validation, defaults, immutability, and JSON export.
- `test_hashing.py`: Verify deterministic SHA-256 for code files, ignoring transient files (`.pyc`, `__pycache__`).
- `test_sandbox.py`:
  - Normal execution captures stdout, stderr, exit code 0.
  - Script raising exception captures stack trace and exit code 1.
  - Infinite loop script terminates strictly at timeout (e.g. 2s) with `TimeoutExpired`.
  - Process cleanup: child processes spawned by script are forcefully killed upon termination.
- `test_analyzer.py`: Test statistical routines (mean, std, 95% CI, $p$-values, effect sizes) against hardcoded mathematical reference values.
- `test_evidence_store.py`: Test database CRUD, foreign key enforcement, append-only constraints, and relational edge queries.

### 5.2 Integration Tests (`tests/integration/`)
- `test_execution_flow.py`: Full execution of an experiment specification from source generation to `metrics.json` extraction and database registration.
- `test_cli.py`: Execute `rex inspect`, `rex status`, `rex verify` via Click's `CliRunner`.

### 5.3 Deterministic Verification & Tamper Tests (`tests/unit/test_verifier.py`)
- **Positive Verification**: Run a legitimate experiment and confirm `rex verify` passes with 0 errors.
- **Tamper Test 1 (Corrupted Metric)**: Modify a metric value in `metrics.json` after execution $\to$ verify `rex verify` detects numerical mismatch.
- **Tamper Test 2 (Code Tampering)**: Modify a line in `source/run.py` without updating code version $\to$ verify `rex verify` detects SHA-256 mismatch.
- **Tamper Test 3 (Ungrounded Claim)**: Insert a claim asserting an accuracy value not found in any `Result` $\to$ verify `rex verify` flags the claim as ungrounded.
- **Tamper Test 4 (Missing Log)**: Delete `stdout.log` $\to$ verify `rex verify` detects missing execution artifact.

### 5.4 End-to-End Tests (`tests/e2e/`)
- `test_v1_slice.py`: Run an actual ML question (e.g., comparing Adam vs SGD on a synthetic logistic regression task), let REX execute 3 repetitions with different seeds, compute statistics, generate plots, store evidence, and verify.
