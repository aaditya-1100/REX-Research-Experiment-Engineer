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
│  (Deterministic software produces facts: sandboxed code     │
│    execution, metrics, statistical analysis, artifacts)     │
└──────────────────────────────┬──────────────────────────────┘
                               │ Produces Measurements & Artifacts
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                       EVIDENCE PLANE                        │
│  (Deterministic audit & persistence: immutable graph of     │
│   runs, commits, configs, seeds, outputs, claims, lineage)  │
└─────────────────────────────────────────────────────────────┘
```

### The Inviolable Axiom:
> **The LLM must never be the authoritative source of an experimental result.**
>
> The LLM may propose expectations or interpret findings, but numerical results must originate exclusively from executable computation, recorded artifacts, and deterministic statistical routines.

---

## 3. Product Scope

### V1 Scope: Vertical Slice
- Target: Computational ML/AI research tasks.
- Input: A clear computational ML/AI research question (e.g., hyperparameter sensitivity, optimizer comparisons, retrieval chunking trade-offs, regularization impact).
- Workflow:
  1. Understand question.
  2. Formulate testable hypothesis and immutable experiment specification.
  3. Generate experiment code strictly bound to the specification contract.
  4. Execute experiment in a sandboxed, isolated environment.
  5. Deterministically capture execution metrics, logs, outputs, and hardware/environment state.
  6. Record complete machine-readable provenance in an evidence store.
  7. Deterministically verify the run and claims using `rex verify`.

### V2 Scope: Autonomous Iterative Loop
- Multi-step research state machine with autonomous closed-loop transitions.
- Hypothesis generation $\to$ experiment design $\to$ execution $\to$ deterministic analysis $\to$ critical methodology critique $\to$ autonomous decision:
  - **REFINE**: modify an experiment while strictly preserving parent lineage.
  - **REPLICATE**: rerun an important result with controlled seeds/conditions.
  - **PIVOT**: pursue an alternative hypothesis or research branch.
  - **STOP**: terminate when the compute budget is reached or further experimentation is unjustified.

### V3 Scope: Scaled Research System
- Scholarly literature retrieval (ArXiv / OpenAlex / Semantic Scholar) with mandatory grounding (no hallucinated citations).
- Multi-hypothesis branching trees with adaptive compute allocation.
- Structured long-term research memory across projects.
- End-to-end evidence-grounded research report generation.
- Full interactive lineage visualization and replayability.

---

## 4. The Research State Machine

REX implements the research lifecycle as an explicit, deterministic state machine driven by the `ResearchController`. The system never runs as an unconstrained recursive LLM dialogue.

### Lifecycle States:
1. `INITIALIZE`: Parse research question, allocate research ID, initialize experiment workspace and evidence database.
2. `UNDERSTAND`: Problem decomposition into independent/dependent variables, constraints, evaluation criteria, and known baselines.
3. `LITERATURE`: Ground question in real scholarly literature (V3; stubbed/cached in V1).
4. `HYPOTHESES`: Formulate structured testable hypotheses with explicit falsification conditions.
5. `DESIGN`: Create an immutable `ExperimentSpec` contract defining variables, baselines, metrics, repetitions, seeds, and statistical tests.
6. `IMPLEMENT`: Coding agent translates `ExperimentSpec` into executable Python code and dependencies without modifying the specification contract.
7. `EXECUTE`: Execution engine runs code in a sandboxed environment with strict timeouts, capturing stdout, stderr, metrics, and resource telemetry.
8. `VERIFY`: Deterministic sanity verification of run outputs, format compliance, and artifact creation before analysis.
9. `ANALYZE`: Deterministic calculation of descriptive statistics, confidence intervals, bootstrap tests, effect sizes, and diagnostic plots.
10. `CRITIQUE`: Research critic evaluates baseline fairness, controls, leakage, sample size, seed variance, and design adherence.
11. `DECIDE`: Controller evaluates findings against budget and stopping criteria to select: `REFINE`, `REPLICATE`, `PIVOT`, or `STOP`.
12. `COMPLETE`: Final research artifacts, claims, and audit logs compiled into a verified research bundle.
13. `FAILED`: Terminal state if execution or verification fails unrecoverably or budget is exhausted.

---

## 5. Core Subsystems & Responsibilities

| Subsystem | Plane | Primary Responsibility |
| :--- | :--- | :--- |
| **Research Controller** | Orchestration | Owns lifecycle state machine, transitions, experiment scheduling, budget limits, checkpointing, and recovery. |
| **Problem Investigator** | Reasoning | Decomposes problem into variables, constraints, baselines, and evaluation criteria. |
| **Literature Agent** | Reasoning | Queries scholarly databases; strictly verifies paper metadata and citations; extracts empirical baselines. |
| **Hypothesis Engine** | Reasoning | Formulates structured hypotheses with expected direction, mechanism, falsification criteria, and required evidence. |
| **Experiment Designer** | Reasoning | Emits immutable `ExperimentSpec` (contract specifying variables, controls, metrics, seeds, analyses). |
| **Coding Agent** | Reasoning | Writes standalone executable experiment scripts adhering to `ExperimentSpec`; fixes execution errors without fabricating results. |
| **Execution Engine** | Execution | Runs experiments in isolated subprocesses/sandboxes with timeouts, CPU/RAM limits, and full telemetry capture. |
| **Analysis Engine** | Execution | Deterministically computes metrics, mean/median, standard error, 95% CIs, Cohen's $d$, ANOVA/t-tests, and generates plots. |
| **Research Critic** | Reasoning | Evaluates methodological validity (leakage, confounding, fairness, seed dependence) and outputs structured severity alerts (`CRITICAL`, `WARNING`, `INFO`). |
| **Evidence Engine** | Evidence | Maintains the relational/graph provenance database linking claims, results, runs, code commits, configs, and datasets. |
| **Research Memory** | Evidence | Structured, append-only repository of hypotheses, experiment outcomes, negative results, decisions, and lessons. |
| **Independent Verifier** | Evidence | Deterministic auditor (`rex verify`) inspecting stored disk artifacts to prove every claim is backed by uncorrupted raw data. |

---

## 6. Directory Layout & Append-Only Invariants

Every experiment is assigned an immutable sequential ID (`EXP-0001`, `EXP-0002`, etc.) and structured on disk as:

```
experiments/
└── EXP-0001/
    ├── experiment.json       # Immutable ExperimentSpec contract
    ├── metadata.json         # Timestamps, parent lineage, hypothesis link
    ├── source/               # Source code executed for the experiment
    │   ├── run.py
    │   └── requirements.txt
    ├── config/               # Parameter configs and hyperparameter grids
    │   └── config.json
    ├── environment/          # Python version, pip freeze, OS, Git commit hash
    │   └── env_dump.json
    ├── executions/           # Individual execution runs (RUN-XXXX)
    │   └── RUN-0001/
    │       ├── run_meta.json # Seed, start/end time, exit code, CPU/RAM usage
    │       ├── stdout.log
    │       ├── stderr.log
    │       └── metrics.json  # Raw metric outputs produced by the script
    ├── results/              # Aggregated raw result matrices
    ├── analysis/             # Deterministic summary stats, p-values, CI tables
    │   └── stats.json
    └── artifacts/            # Output plots, checkpoints, logs
        └── figure_1.png
```

### Invariants:
1. **Append-Only History**: Historical runs are never overwritten or deleted.
2. **Deterministic Hashes**: Code hash, config hash, and dataset hash are computed via SHA-256 and stored with every run.
3. **Execution Idempotence**: A run record contains complete metadata necessary to execute the exact command again under identical seed/parameter settings.

---

## 7. Evidence Graph Entities and Relationships

REX persists the research record as an explicit entity-relationship model in SQLite + JSON.

### Entities:
- `Hypothesis`
- `Experiment`
- `CodeVersion`
- `Configuration`
- `Dataset`
- `Run`
- `Result`
- `Analysis`
- `Figure`
- `Claim`
- `LiteratureSource`

### Relationships:
- `CLAIM` $\xrightarrow{\text{SUPPORTED_BY}}$ `RESULT`
- `RESULT` $\xrightarrow{\text{PRODUCED_BY}}$ `RUN`
- `RUN` $\xrightarrow{\text{INSTANCE_OF}}$ `EXPERIMENT`
- `RUN` $\xrightarrow{\text{EXECUTED_CODE}}$ `CODE_VERSION`
- `RUN` $\xrightarrow{\text{USED_DATASET}}$ `DATASET`
- `RUN` $\xrightarrow{\text{USED_CONFIGURATION}}$ `CONFIGURATION`
- `ANALYSIS` $\xrightarrow{\text{DERIVED_FROM}}$ `RESULT`
- `FIGURE` $\xrightarrow{\text{GENERATED_FROM}}$ `RESULT` / `ANALYSIS`
- `REPORT` $\xrightarrow{\text{CONTAINS}}$ `CLAIM`

---

## 8. Independent Verification Criteria (`rex verify`)

The verifier is a zero-LLM, 100% deterministic auditing tool that verifies:
1. **Claim Grounding**: Every numerical assertion in a claim maps directly to a verified `Result` entry.
2. **Result Provenance**: Every `Result` references a valid `Run` ID.
3. **Run Integrity**: Every `Run` has an intact exit code (0), stdout, stderr, run metadata, and valid start/end timestamps.
4. **Code Traceability**: The source code directory for the experiment matches the recorded `CodeVersion` SHA-256 hash.
5. **Configuration Match**: The execution arguments match the `ExperimentSpec` contract.
6. **Seed Traceability**: Random seeds are explicitly recorded.
7. **Numerical Consistency**: Stored metric files (`metrics.json`) match the values recorded in the evidence database byte-for-byte.
8. **Analysis Derivation**: Statistical values (means, CIs, p-values) match recalculated statistics over raw run outputs.
9. **Artifact Integrity**: Generated figures and tables exist on disk with valid hashes.
10. **Append-Only Preservation**: No historical runs have missing IDs or tampered sequence numbers.

Output artifacts:
- `verification_report.json`
- `verification_report.md`

---

## 9. Security and Execution Sandboxing

Generated experiment code is treated as untrusted:
- **Process Isolation**: Experiments are executed in isolated subprocesses with decoupled process groups.
- **Resource Constraints**: Strict wall-clock timeouts (default 300s), memory limits, and process cleanup on termination (killing child processes).
- **Filesystem Confinement**: Experiment scripts only write to their designated run execution directory.
- **Dependency Guard**: Permitted package allowlists; no arbitrary system package installations.

---

## 10. Anti-Drift Guardrails

The following are strictly out of scope and prohibited:
- Chatbot interfaces or general-purpose conversation loops.
- Paper writing engines that generate prose without empirical code execution.
- LLMs performing internal arithmetic or statistical evaluations without execution code.
- Hallucinated citations or unverified web search summaries.
- Mocking or faking experimental results during non-testing operations.
