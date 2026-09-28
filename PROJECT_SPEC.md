# PROJECT_SPEC.md — REX (Research Experiment Engineer)

## 1. Project Mission & Objective

**REX (Research Experiment Engineer)** is an autonomous computational research system designed to investigate Machine Learning and Artificial Intelligence (ML/AI) research questions. 

Unlike conversational AI research demos or automated paper generators, REX is a computer-science systems project built on the following principle:

> **Build an autonomous computational research agent that investigates ML/AI questions by designing, executing, analyzing, and iteratively refining experiments, while maintaining a machine-readable provenance graph that allows every reported result and conclusion to be traced back to the exact experiment, code, configuration, data, execution, and analysis that produced it.**

REX enforces strict correctness, reproducibility, modularity, observability, deterministic infrastructure, and actual executable experiments over visual polish or paper prose generation.

---

## 2. Core Architectural Principle: The Three Planes

REX strictly decouples its system into three logically separated planes:

```
┌─────────────────────────────────────────────────────────────┐
│                       REASONING PLANE                       │
│  (LLMs & Agents propose: questions, hypotheses, designs,    │
│            code, analyses, critiques, decisions)            │
└──────────────────────────────┬──────────────────────────────┘
                               │ Proposes Actions / Specifications
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                       EXECUTION PLANE                       │
│  (Deterministic software produces facts: Docker sandboxed   │
│    execution, metrics, statistical analysis, artifacts)     │
└──────────────────────────────┬──────────────────────────────┘
                               │ Produces Measurements & Artifacts
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                       EVIDENCE PLANE                        │
│  (Deterministic audit & persistence: immutable graph of     │
│   runs, commits, configs, seeds, outputs, claims, lineage)  │
└──────────────────────────────┬──────────────────────────────┘
                               │ Audited by
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    INDEPENDENT VERIFIER                     │
│  (Deterministic zero-LLM auditor: rex verify <research_id>) │
└─────────────────────────────────────────────────────────────┘
```

### The Inviolable Axiom:
> **The LLM must never be the authoritative source of an experimental result.**
>
> The LLM may propose expectations or interpret findings, but numerical results must originate exclusively from executable computation, recorded artifacts, and deterministic statistical routines.

---

## 3. Product Scope & Milestones

### V1 Scope: Vertical Slice & Auditable Foundation
- Target: Computational ML/AI research tasks.
- Input: A clear computational ML/AI research question (e.g., hyperparameter sensitivity, optimizer comparisons, retrieval chunking trade-offs, regularization impact).
- Core Lifecycle:
  1. Understand question.
  2. Formulate testable hypothesis and immutable experiment specification.
  3. Generate experiment code strictly bound to the specification contract.
  4. Execute experiment in an isolated, sandboxed Docker container (with fail-safe fallback).
  5. Deterministically capture execution metrics, logs, outputs, and hardware/environment state.
  6. Record complete machine-readable provenance in SQLAlchemy/SQLite evidence store.
  7. Deterministically verify the run and claims using `rex verify`.
  8. Prove tamper resistance: intentional corruption of metrics or claims causes `rex verify` to fail with a non-zero exit code.

### V2 Scope: Autonomous Iterative Loop
- Multi-step research state machine with autonomous closed-loop transitions.
- Hypothesis generation $\to$ experiment design $\to$ execution $\to$ deterministic analysis $\to$ critical methodology critique $\to$ autonomous decision:
  - **REFINE**: modify an experiment while strictly preserving parent lineage.
  - **REPLICATE**: rerun an important result with controlled seeds/conditions.
  - **PIVOT**: pursue an alternative hypothesis or research branch.
  - **STOP**: terminate when the compute budget is reached or further experimentation is unjustified.

### V3 Scope: Scaled Research System
- Scholarly literature retrieval (ArXiv / OpenAlex / Semantic Scholar) with mandatory grounding and strict prompt injection barriers (no hallucinated citations, no untrusted instructions).
- Multi-hypothesis branching trees with adaptive compute allocation.
- Structured long-term research memory across projects.
- End-to-end evidence-grounded research report generation.
- Full interactive research workstation UI (React/Vite).

---

## 4. The Research State Machine

REX implements the research lifecycle as an explicit, deterministic state machine driven by the `ResearchController`. The system never runs as an unconstrained recursive LLM dialogue.

### Lifecycle States:
1. `INITIALIZE`: Parse research question, allocate research ID, initialize experiment workspace, database, and budget tracker.
2. `UNDERSTAND`: Problem decomposition into independent/dependent variables, constraints, evaluation criteria, and known baselines.
3. `LITERATURE`: Ground question in real scholarly literature (V3; stubbed/mocked in V1).
4. `HYPOTHESES`: Formulate structured testable hypotheses with explicit falsification conditions.
5. `DESIGN`: Create an immutable `ExperimentSpec` contract defining variables, baselines, metrics, repetitions, seeds, and statistical tests.
6. `IMPLEMENT`: Coding agent translates `ExperimentSpec` into executable Python code and dependencies without modifying the specification contract.
7. `EXECUTE`: Execution engine runs code in a sandboxed Docker container with strict timeouts, capturing stdout, stderr, metrics, and resource telemetry.
8. `VERIFY`: Deterministic sanity verification of run outputs, format compliance, and artifact creation before analysis.
9. `ANALYZE`: Deterministic calculation of descriptive statistics, confidence intervals, bootstrap tests, effect sizes, and diagnostic plots.
10. `CRITIQUE`: Research critic evaluates baseline fairness, controls, leakage, sample size, seed variance, and design adherence.
11. `DECIDE`: Controller evaluates findings against budget and stopping criteria to select: `REFINE`, `REPLICATE`, `PIVOT`, or `STOP`.
12. `COMPLETE`: Final research artifacts, claims, and audit logs compiled into a verified research bundle.
13. `FAILED`: Terminal state if execution or verification fails unrecoverably or budget is exhausted.

---

## 5. Core Subsystems & Modular Architecture

REX is structured as a modular monolith in Python with clear subsystem boundaries:

```text
rex/
├── domain/          # Core domain models (ResearchRun, Hypothesis, ExperimentSpec, Execution, etc.)
├── controller/      # State machine, research controller, budget engine
├── agents/          # Reasoning plane: investigator, hypothesis, designer, coder, critic
├── llm/             # LLM provider abstractions (Gemini, Groq, OpenAI, MockLLMProvider)
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

---

## 6. Directory Layout & Append-Only Invariants

Every research run organizes workspaces and artifacts under structured directories:

```
data/
├── database/
│   └── rex.db               # SQLite database with SQLAlchemy models
└── runs/
    └── <research_id>/
        └── <experiment_id>/
            └── <run_id>/
                ├── source/         # Snapshotted source code
                │   ├── run.py
                │   └── requirements.txt
                ├── config/         # JSON execution configuration & seed
                │   └── config.json
                ├── environment/    # Runtime, pip freeze, Git commit, OS
                │   └── env_dump.json
                ├── logs/           # Captured outputs
                │   ├── stdout.log
                │   └── stderr.log
                ├── results/        # Raw execution outputs
                │   └── metrics.json
                ├── analysis/       # Deterministic statistics
                │   └── stats.json
                └── artifacts/      # Plots, checkpoints, tables
                    └── figure_1.png
```

### Invariants:
1. **Append-Only History**: Historical runs are never overwritten or deleted.
2. **Deterministic Hashes**: Code hash, config hash, and dataset hash are computed via SHA-256 and stored with every execution and artifact.
3. **Execution Idempotence**: A run record contains complete metadata necessary to execute the exact command again under identical seed/parameter settings.

---

## 7. Evidence Graph Entities and Relationships

REX persists the research record as an explicit entity-relationship model in SQLite via SQLAlchemy.

### Entities:
- `research_runs`
- `hypotheses`
- `experiments`
- `executions`
- `results`
- `analyses`
- `artifacts`
- `literature_sources`
- `claims`
- `evidence_links`
- `events`

### Relationships:
- `CLAIM` $\xrightarrow{\text{SUPPORTED_BY}}$ `RESULT` / `ANALYSIS` / `LITERATURE_SOURCE`
- `RESULT` $\xrightarrow{\text{PRODUCED_BY}}$ `EXECUTION`
- `EXECUTION` $\xrightarrow{\text{INSTANCE_OF}}$ `EXPERIMENT`
- `EXECUTION` $\xrightarrow{\text{EXECUTED_CODE}}$ `CODE_VERSION`
- `EXECUTION` $\xrightarrow{\text{USED_DATASET}}$ `DATASET`
- `EXECUTION` $\xrightarrow{\text{USED_CONFIGURATION}}$ `CONFIGURATION`
- `ANALYSIS` $\xrightarrow{\text{DERIVED_FROM}}$ `RESULT`
- `FIGURE` $\xrightarrow{\text{GENERATED_FROM}}$ `RESULT` / `ANALYSIS`
- `REPORT` $\xrightarrow{\text{CONTAINS}}$ `CLAIM`

---

## 8. Independent Verification Criteria (`rex verify`)

The verifier is a zero-LLM, 100% deterministic auditing tool that verifies:
1. **Claim Grounding**: Every numerical assertion in a claim maps directly to a verified `Result` or `Analysis` entry.
2. **Result Provenance**: Every `Result` references a valid `Execution` ID.
3. **Run Integrity**: Every `Execution` has an intact exit code (0), stdout, stderr, run metadata, and valid start/end timestamps.
4. **Code Traceability**: The source code directory for the experiment matches the recorded `CodeVersion` SHA-256 hash.
5. **Configuration Match**: The execution arguments match the `ExperimentSpec` contract.
6. **Seed Traceability**: Random seeds are explicitly recorded.
7. **Numerical Consistency**: Stored metric files (`metrics.json`) match the values recorded in the database byte-for-byte.
8. **Analysis Derivation**: Statistical values (means, CIs, p-values) match recalculated statistics over raw run outputs.
9. **Artifact Integrity**: Generated figures and tables exist on disk with valid SHA-256 hashes.
10. **Append-Only Preservation**: No historical runs have missing IDs or tampered sequence numbers.

Output artifacts:
- `verification_report.json`
- `verification_report.md`

---

## 9. Security, Sandboxing, & Guardrails

### 9.1 Docker Container Security Boundary
Generated experiment code is untrusted. The primary execution environment is an isolated Docker container with:
- Non-root user execution;
- Explicit CPU cores limit;
- Explicit memory limit (e.g. 2048 MB);
- Wall-clock timeout (e.g. 300s);
- Read-only root filesystem with write access restricted to assigned workspace;
- Output size limits;
- **Network disabled by default** (`network_mode="none"`);
- No host Docker socket access;
- No host SSH keys or cloud credentials mounted;
- Environment variable sanitization.

**Fail-Safe Policy**: If Docker is unavailable in non-testing mode, REX fails closed with a clear warning rather than silently executing untrusted code on the host.

### 9.2 Prompt Injection & Literature Sanitization
- Retrieved literature content (OpenAlex, Semantic Scholar, arXiv) is untrusted data.
- Never concatenated into privileged instructions; clearly quarantined in data delimiters.
- Cannot grant agent capabilities, alter budgets, or initiate execution commands.

### 9.3 Capability-Based Permissions & Research Budgets
- Agents are restricted to capability sets matching their lifecycle state (least privilege).
- Strict budgets: `max_experiments`, `max_runtime_seconds`, `max_llm_calls`, `max_token_cost`, and `max_artifact_size`.
- Budget exhaustion terminates autonomous loops and demands owner intervention.
