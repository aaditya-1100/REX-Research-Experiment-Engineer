# REX — Feature Ticket List

**Product:** REX — Research Experiment Engineer  
**Ticket source:** PRD + Technical Architecture + Security + Frontend specifications  
**Priority levels:** MUST-HAVE, SHOULD-HAVE, NICE-TO-HAVE  
**Execution order:** Follow dependencies; do not parallelize foundational work prematurely.  
**Last updated:** 2026-09-28

---

# Epic 0 — Repository and project foundation

## REX-001 — Create project skeleton

**Priority:** MUST-HAVE

**Description**

Create the initial Python project, frontend project, test structure, documentation files, configuration system, and development tooling.

**Acceptance criteria**

- Python package installs successfully.
- Backend starts locally.
- Frontend starts locally.
- Tests can run.
- Linting/type-checking commands exist.
- `.env.example` exists.
- No secrets are committed.

**Dependencies**

None.

---

## REX-002 — Implement configuration system

**Priority:** MUST-HAVE

**Description**

Create typed application settings for database, artifact paths, LLM provider, literature providers, Docker execution, resource limits, and logging.

**Acceptance criteria**

- Configuration is represented by typed models.
- Environment variables can override defaults.
- Missing required secrets produce clear errors.
- Secrets are never logged.
- Tests cover configuration loading.

**Dependencies**

REX-001.

---

## REX-003 — Implement structured logging and events

**Priority:** MUST-HAVE

**Description**

Create a structured event system for research lifecycle events and errors.

**Acceptance criteria**

- Events have timestamps and IDs.
- Events can reference research runs and entities.
- Events are persisted.
- Logs do not expose secrets.
- Tests cover event creation.

**Dependencies**

REX-001, REX-002.

---

# Epic 1 — Persistence and domain model

## REX-004 — Implement database layer

**Priority:** MUST-HAVE

**Description**

Implement SQLAlchemy models, SQLite configuration, migrations, and repository abstractions.

**Acceptance criteria**

- Database initializes automatically.
- Migrations can be applied from a clean environment.
- Domain entities have stable IDs.
- Repository operations are tested.
- Historical records are not silently overwritten.

**Dependencies**

REX-002.

---

## REX-005 — Implement research run model

**Priority:** MUST-HAVE

**Description**

Implement creation, retrieval, status transitions, and persistence of research runs.

**Acceptance criteria**

- Each run has a unique ID.
- Research question is persisted.
- Status transitions are validated.
- Creation/update timestamps are stored.
- Invalid state transitions are rejected.

**Dependencies**

REX-004.

---

## REX-006 — Implement hypothesis model

**Priority:** MUST-HAVE

**Description**

Implement structured hypotheses and their relationship to research runs.

**Acceptance criteria**

- Hypotheses have unique IDs.
- Required fields are validated.
- Hypotheses belong to a research run.
- Hypothesis status is tracked.
- Tests cover serialization and persistence.

**Dependencies**

REX-005.

---

## REX-007 — Implement experiment specification model

**Priority:** MUST-HAVE

**Description**

Implement immutable experiment specifications containing variables, controls, datasets, metrics, seeds, repetitions, analysis methods, and success criteria.

**Acceptance criteria**

- Experiment IDs are unique.
- Specification validates required fields.
- Specification becomes immutable once execution begins.
- Parent/child experiment relationships are supported.
- Tests cover immutability.

**Dependencies**

REX-006.

---

## REX-008 — Implement execution/result/artifact models

**Priority:** MUST-HAVE

**Description**

Implement persistence for executions, results, analyses, artifacts, and their relationships.

**Acceptance criteria**

- Results cannot exist without executions.
- Executions cannot exist without experiments.
- Artifacts have integrity metadata.
- Failed executions remain persisted.
- Relationships are queryable.

**Dependencies**

REX-007.

---

# Epic 2 — Research controller

## REX-009 — Implement research state machine

**Priority:** MUST-HAVE

**Description**

Implement the explicit research lifecycle.

**Acceptance criteria**

- All required states exist.
- Invalid transitions are rejected.
- State transitions are persisted as events.
- Controller can resume from persisted state.
- Tests cover all valid and invalid transitions.

**Dependencies**

REX-005, REX-003.

---

## REX-010 — Implement research controller

**Priority:** MUST-HAVE

**Description**

Create the controller responsible for progressing a research run through its lifecycle.

**Acceptance criteria**

- Controller can start a research run.
- Controller can pause/resume.
- Controller records decisions.
- Controller respects budgets.
- Controller does not directly execute arbitrary shell commands.

**Dependencies**

REX-009, REX-008.

---

## REX-011 — Implement research budgets

**Priority:** MUST-HAVE

**Description**

Implement limits for experiments, runtime, LLM calls, and other configured resources.

**Acceptance criteria**

- Limits are configurable.
- Budget consumption is tracked.
- Exhaustion stops autonomous execution.
- User receives a clear reason.
- Tests cover budget exhaustion.

**Dependencies**

REX-010.

---

# Epic 3 — LLM and agent layer

## REX-012 — Implement LLM provider interface

**Priority:** MUST-HAVE

**Description**

Create a provider-neutral interface for structured generation, code generation, and critique.

**Acceptance criteria**

- Core code does not depend directly on one provider SDK.
- Provider configuration is externalized.
- Structured responses are validated.
- Provider failures are normalized.
- Tests use a fake provider.

**Dependencies**

REX-002.

---

## REX-013 — Implement problem investigator

**Priority:** MUST-HAVE

**Description**

Create the agent that converts a research question into a structured problem definition.

**Acceptance criteria**

- Output follows a validated schema.
- Variables and evaluation metrics are explicit.
- Unsupported assumptions are labelled.
- Invalid output is rejected.
- Tests cover malformed model responses.

**Dependencies**

REX-012, REX-005.

---

## REX-014 — Implement hypothesis agent

**Priority:** MUST-HAVE

**Description**

Generate explicit falsifiable hypotheses.

**Acceptance criteria**

- Every hypothesis has a falsification condition.
- Every hypothesis has an expected direction where meaningful.
- Hypotheses are persisted.
- No hypothesis is accepted without schema validation.

**Dependencies**

REX-006, REX-013.

---

## REX-015 — Implement experiment designer

**Priority:** MUST-HAVE

**Description**

Convert a hypothesis into a structured experiment specification.

**Acceptance criteria**

- Controls are explicitly represented.
- Baseline is explicit.
- Dataset and metric are explicit.
- Seed/repetition policy is explicit.
- Statistical analysis is specified.
- Experiment specification is persisted before code generation.

**Dependencies**

REX-007, REX-014.

---

## REX-016 — Implement coding agent

**Priority:** MUST-HAVE

**Description**

Generate experiment code from an immutable experiment specification.

**Acceptance criteria**

- Generated code is written only to the assigned workspace.
- Code generation cannot modify the experiment specification.
- Generated files are tracked.
- Code hash is captured.
- Errors are returned to the agent for bounded repair.

**Dependencies**

REX-012, REX-015.

---

# Epic 4 — Sandbox execution

## REX-017 — Implement Docker execution worker

**Priority:** MUST-HAVE

**Description**

Run generated experiment code in an isolated Docker container.

**Acceptance criteria**

- Container runs as non-root.
- CPU and memory limits are enforced.
- Wall-clock timeout is enforced.
- Host filesystem is not exposed.
- Docker socket is unavailable.
- Network is disabled by default.
- stdout/stderr are captured.
- Exit status is persisted.

**Dependencies**

REX-008, REX-016.

---

## REX-018 — Implement experiment workspace manager

**Priority:** MUST-HAVE

**Description**

Create isolated filesystem workspaces for experiments and runs.

**Acceptance criteria**

- Each run receives a unique workspace.
- Historical workspaces are not overwritten.
- Artifacts are captured.
- Cleanup does not delete persisted evidence.
- Path traversal is prevented.

**Dependencies**

REX-008.

---

## REX-019 — Implement environment capture

**Priority:** MUST-HAVE

**Description**

Record Python/runtime/dependency/environment metadata necessary for reproduction.

**Acceptance criteria**

- Dependency versions are captured.
- Runtime version is captured.
- OS/container metadata is captured.
- Sensitive environment variables are excluded.
- Metadata is linked to the execution.

**Dependencies**

REX-017.

---

# Epic 5 — Deterministic analysis

## REX-020 — Implement metric extraction

**Priority:** MUST-HAVE

**Description**

Parse structured experiment outputs into validated results.

**Acceptance criteria**

- Results follow a defined schema.
- Malformed metrics are rejected.
- Metric values are persisted.
- Results are linked to the execution.

**Dependencies**

REX-008, REX-017.

---

## REX-021 — Implement statistical analysis engine

**Priority:** MUST-HAVE

**Description**

Implement deterministic statistical analysis functions.

**Acceptance criteria**

- Analysis functions consume stored results.
- Analysis outputs are structured.
- Method names and parameters are recorded.
- Source result IDs are recorded.
- Numerical outputs are reproducible.

**Dependencies**

REX-020.

---

## REX-022 — Implement experiment visualization artifacts

**Priority:** SHOULD-HAVE

**Description**

Generate figures from stored results.

**Acceptance criteria**

- Figures are generated deterministically from data.
- Source data is recorded.
- Figure files receive integrity hashes.
- Figures can be displayed in the frontend.

**Dependencies**

REX-021.

---

# Epic 6 — Evidence and verification

## REX-023 — Implement evidence graph

**Priority:** MUST-HAVE

**Description**

Implement explicit claim/result/run/experiment/artifact/code/dataset relationships.

**Acceptance criteria**

- All core evidence entities have stable IDs.
- Relationships are persisted.
- Evidence can be traversed in both directions.
- Invalid relationships are rejected.

**Dependencies**

REX-008.

---

## REX-024 — Implement claim model

**Priority:** MUST-HAVE

**Description**

Create structured claims linked to evidence.

**Acceptance criteria**

- Claims have unique IDs.
- Numerical claims require evidence.
- Claims have status.
- Unsupported claims cannot be marked verified.

**Dependencies**

REX-023, REX-021.

---

## REX-025 — Implement artifact hashing

**Priority:** MUST-HAVE

**Description**

Create content hashes for important research artifacts.

**Acceptance criteria**

- Hash algorithm is consistent.
- Hash is stored with artifact metadata.
- Modified files are detected.
- Tests deliberately tamper with files and detect the change.

**Dependencies**

REX-008.

---

## REX-026 — Implement `rex verify`

**Priority:** MUST-HAVE

**Description**

Build the independent research integrity verifier.

**Acceptance criteria**

- Claims are traced to evidence.
- Evidence resolves to actual artifacts.
- Hashes are checked.
- Report numbers can be checked against stored results.
- Citation source IDs are validated.
- Integrity failures return non-zero exit status.
- A human-readable verification report is generated.

**Dependencies**

REX-023, REX-024, REX-025.

---

## REX-027 — Implement reproduction command

**Priority:** SHOULD-HAVE

**Description**

Implement `rex reproduce <experiment_id>` to rerun an experiment under its recorded configuration where feasible.

**Acceptance criteria**

- Original configuration is loaded.
- New run receives a new ID.
- Original run remains unchanged.
- Results can be compared.
- Differences are reported.

**Dependencies**

REX-017, REX-019, REX-026.

---

# Epic 7 — Literature

## REX-028 — Create literature provider interface

**Priority:** MUST-HAVE

**Description**

Create a normalized interface for scholarly search and retrieval.

**Acceptance criteria**

- Provider-neutral schema exists.
- Retrieved sources have external IDs.
- Retrieval timestamps are stored.
- Raw metadata can be inspected.

**Dependencies**

REX-004.

---

## REX-029 — Integrate OpenAlex

**Priority:** SHOULD-HAVE

**Description**

Implement OpenAlex search/retrieval adapter.

**Acceptance criteria**

- Search results normalize into LiteratureSource.
- Provider errors are handled.
- API keys are not logged.
- Rate limits are handled conservatively.

**Dependencies**

REX-028.

---

## REX-030 — Integrate Semantic Scholar

**Priority:** SHOULD-HAVE

**Description**

Implement Semantic Scholar Academic Graph adapter.

**Acceptance criteria**

- Paper metadata is normalized.
- External IDs are stored.
- Rate limits are respected.
- Provider failures are recoverable.

**Dependencies**

REX-028.

---

## REX-031 — Integrate arXiv

**Priority:** SHOULD-HAVE

**Description**

Implement arXiv metadata retrieval.

**Acceptance criteria**

- Preprints can be searched.
- Source metadata is persisted.
- Retrieved content is treated as untrusted data.

**Dependencies**

REX-028.

---

## REX-032 — Implement literature prompt-injection boundary

**Priority:** MUST-HAVE

**Description**

Ensure retrieved literature is passed to agents as untrusted data rather than executable instructions.

**Acceptance criteria**

- External content is explicitly labelled.
- Tool permissions cannot be changed by retrieved text.
- Prompt-injection fixtures are tested.
- Literature text cannot directly trigger execution.

**Dependencies**

REX-012, REX-028.

---

# Epic 8 — Research critic and autonomous loop

## REX-033 — Implement research critic

**Priority:** MUST-HAVE

**Description**

Create a critic that evaluates experiment validity and conclusion strength.

**Acceptance criteria**

- Critic checks baseline, controls, leakage, sample size, seeds, metrics, confounders, and conclusion scope.
- Findings have severity.
- Findings reference available evidence.
- Critic cannot modify evidence.

**Dependencies**

REX-021, REX-023.

---

## REX-034 — Implement decision engine

**Priority:** MUST-HAVE

**Description**

Decide whether to refine, replicate, pivot, or stop.

**Acceptance criteria**

- Decision is structured.
- Decision references current evidence.
- Budget is checked before new experiments.
- Stop conditions are enforced.
- Decisions are persisted.

**Dependencies**

REX-033, REX-011.

---

## REX-035 — Implement autonomous research loop

**Priority:** MUST-HAVE

**Description**

Connect hypothesis, design, implementation, execution, analysis, critique, and decision into a bounded loop.

**Acceptance criteria**

- Multiple experiments can be executed sequentially.
- Failed experiments do not corrupt research state.
- Agent can refine or replicate an experiment.
- Loop cannot continue indefinitely.
- Every action is recorded.

**Dependencies**

REX-010, REX-015, REX-017, REX-021, REX-033, REX-034.

---

# Epic 9 — Reporting

## REX-036 — Implement report generator

**Priority:** SHOULD-HAVE

**Description**

Generate a research report from persisted evidence rather than free-form agent memory.

**Acceptance criteria**

- Report includes question, hypotheses, methods, experiments, results, limitations, and conclusions.
- Important numerical statements link to claim IDs.
- Unsupported claims are excluded or labelled.
- Failed experiments are represented honestly.

**Dependencies**

REX-024, REX-035.

---

# Epic 10 — Frontend

## REX-037 — Create frontend shell

**Priority:** SHOULD-HAVE

**Description**

Build the REX research-workstation shell.

**Acceptance criteria**

- React/TypeScript/Vite application starts.
- Design tokens are implemented.
- Navigation exists.
- Backend API integration layer exists.

**Dependencies**

REX-001.

---

## REX-038 — Build research overview

**Priority:** SHOULD-HAVE

**Description**

Create research run overview with state, timeline, hypotheses, experiments, and verification status.

**Acceptance criteria**

- Current state is visible.
- Timeline updates.
- Experiments can be opened.
- Verification status is visible.

**Dependencies**

REX-003, REX-005, REX-009, REX-037.

---

## REX-039 — Build experiment explorer

**Priority:** SHOULD-HAVE

**Description**

Create experiment/run detail screens.

**Acceptance criteria**

- Experiment specification is visible.
- Execution metadata is visible.
- Logs are searchable.
- Artifacts are accessible.
- Metrics are displayed.

**Dependencies**

REX-020, REX-037.

---

## REX-040 — Build evidence explorer

**Priority:** SHOULD-HAVE

**Description**

Create a navigable claim-to-evidence lineage view.

**Acceptance criteria**

- Claims are selectable.
- Evidence chain is displayed.
- Each node can be opened.
- Broken evidence relationships are visually obvious.

**Dependencies**

REX-023, REX-024, REX-037.

---

## REX-041 — Build verification interface

**Priority:** SHOULD-HAVE

**Description**

Create the verification results interface.

**Acceptance criteria**

- Verification can be triggered.
- Checks are displayed individually.
- Pass/fail state is clear.
- Integrity failures include actionable explanations.

**Dependencies**

REX-026, REX-037.

---

# Epic 11 — Quality and evaluation

## REX-042 — Build end-to-end toy research benchmark

**Priority:** MUST-HAVE

**Description**

Create a deterministic toy ML research task suitable for repeated autonomous runs.

**Acceptance criteria**

- Benchmark has known baselines.
- Experiments execute locally.
- Expected results are bounded.
- Research package can be verified.
- Benchmark can run in CI where feasible.

**Dependencies**

REX-021, REX-026.

---

## REX-043 — Build evidence corruption tests

**Priority:** MUST-HAVE

**Description**

Create tests that deliberately modify results, artifacts, claims, and relationships.

**Acceptance criteria**

- Result tampering is detected.
- Artifact tampering is detected.
- Broken evidence links are detected.
- Unsupported claims are detected.
- Exit code indicates failure.

**Dependencies**

REX-026.

---

## REX-044 — Build reproducibility evaluation

**Priority:** SHOULD-HAVE

**Description**

Compare original experiment runs against reproduction runs.

**Acceptance criteria**

- Reproduction receives a new run ID.
- Metrics are compared.
- Tolerance is configurable.
- Reproducibility status is recorded.

**Dependencies**

REX-027, REX-042.

---

## REX-045 — Build baseline-vs-REX evaluation

**Priority:** SHOULD-HAVE

**Description**

Create a controlled evaluation comparing an agent without evidence infrastructure against REX with evidence infrastructure.

**Acceptance criteria**

Measure:

- claim-evidence accuracy;
- method-code alignment;
- reproducibility;
- unsupported claims;
- successful experiment completion;
- research cost.

The evaluation must define the comparison before running it.

**Dependencies**

REX-035, REX-044.

---

# Recommended implementation order

Do not implement tickets strictly by numeric order if dependencies suggest otherwise, but preserve this broad sequence:

```text
Foundation
  ↓
Persistence
  ↓
State machine
  ↓
LLM abstraction
  ↓
Experiment specification
  ↓
Sandbox
  ↓
Results + analysis
  ↓
Evidence graph
  ↓
Verifier
  ↓
Toy benchmark
  ↓
Research loop
  ↓
Literature
  ↓
Critic/decision refinement
  ↓
Frontend
  ↓
Evaluation
```

The first real milestone is:

**REX can execute one experiment and independently verify its evidence.**

The second is:

**REX can autonomously run several experiments and choose what to do next.**

The third is:

**We can experimentally evaluate whether the evidence infrastructure improves autonomous research reliability.**
