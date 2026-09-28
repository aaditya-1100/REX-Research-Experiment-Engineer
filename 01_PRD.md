# REX — Product Requirements Document (PRD)

**Product:** REX — Research Experiment Engineer  
**Document status:** V1 source of truth  
**Target:** Local-first computational ML/AI research agent  
**Primary user:** A technical researcher/developer who wants an agent to conduct reproducible computational experiments  
**Last updated:** 2026-09-28

---

## 1. Product definition

REX is an autonomous computational research system for ML/AI experiments.

A user gives REX a research question. REX decomposes the problem, proposes hypotheses, designs experiments, writes and executes experiment code in an isolated environment, captures results and provenance, analyzes the evidence, critiques the investigation, and can decide whether another experiment should be run.

The defining product property is **auditability**.

REX must not merely produce a plausible research report. It must preserve a machine-readable chain connecting important claims to the exact evidence that produced them.

The core chain is:

`Research Question → Hypothesis → Experiment → Run → Raw Result → Analysis → Claim`

A user must be able to move backwards through that chain and inspect what actually happened.

### Product thesis

> LLMs should propose research actions; deterministic software should execute, measure, and record the consequences.

REX is therefore not primarily a paper generator. The research report is a downstream representation of the experiment record.

---

## 2. Problem

Autonomous research systems are increasingly able to search literature, generate hypotheses, write experiment code, execute experiments, analyze results, and produce manuscripts. Sakana AI's AI Scientist has demonstrated end-to-end ML research automation, while Google's Scientist-One explicitly focuses on verifiable research through a Chain-of-Evidence framework.

The remaining engineering problem is not simply whether an agent can produce a research-looking output. It is whether another person can determine:

- which experiment produced a result;
- which code was actually executed;
- which configuration and seed were used;
- which dataset/version was used;
- whether a reported number matches the underlying artifact;
- whether a conclusion is supported by the evidence;
- whether the experiment can be reproduced;
- whether an agent silently changed the intended experimental design.

REX addresses this by making provenance and verification first-class product features.

---

## 3. Target users

### Primary user

A technical ML/AI researcher, student researcher, or engineer who is comfortable running Python experiments and wants to delegate repetitive experimental work to an autonomous agent without surrendering control over the evidence.

### Secondary user

A reviewer or collaborator who did not operate the agent but needs to audit the resulting research.

### Non-target user

REX V1 is not designed for nontechnical users seeking general-purpose research assistance, nor for arbitrary scientific domains such as wet-lab biology or physical experimentation.

---

## 4. Jobs to be done

When I have a computational ML/AI research question, I want REX to:

1. Turn the question into explicit hypotheses.
2. Convert hypotheses into controlled experiment specifications.
3. Implement and run those experiments.
4. Record enough metadata to reproduce each run.
5. Analyze results using deterministic tooling.
6. Identify methodological weaknesses and unsupported conclusions.
7. Decide whether additional experiments are justified.
8. Produce a report whose important claims can be traced to evidence.
9. Let me independently verify the resulting research package.

---

## 5. Core user journey

### Stage 1 — Create research run

The user provides a research question through CLI or web UI.

Example:

> Does retrieval strategy X improve long-context LLM agent performance?

REX creates a research run with a unique ID.

### Stage 2 — Understand

REX identifies the objective, variables, constraints, metrics, baseline, and unresolved assumptions.

### Stage 3 — Literature

REX retrieves relevant scholarly sources and records their metadata. Literature claims must be grounded in actual retrieved sources.

### Stage 4 — Hypotheses

REX creates explicit, falsifiable hypotheses.

Each hypothesis contains:

- statement;
- expected direction;
- rationale;
- falsification condition;
- proposed experiment.

### Stage 5 — Experiment design

REX creates an immutable experiment specification containing the variables, controls, dataset, metric, seeds, repetitions, statistical method, and success/falsification criteria.

### Stage 6 — Implementation

The coding agent implements the experiment.

### Stage 7 — Execution

The experiment runs inside a controlled sandbox.

The system captures:

- command;
- code version;
- configuration;
- dataset/version;
- seed;
- environment;
- stdout/stderr;
- runtime;
- resource usage;
- exit status;
- produced artifacts.

### Stage 8 — Analysis

The analysis engine computes metrics and statistical results from stored data.

### Stage 9 — Critique

A research critic checks the experimental methodology and evidence.

### Stage 10 — Decision

REX selects one of:

- refine;
- replicate;
- pivot;
- stop.

### Stage 11 — Report

REX produces a research report and an audit package.

### Stage 12 — Verification

`rex verify <research_run>` independently checks the research package.

---

## 6. Must-have V1 capabilities

### Research lifecycle

REX must implement an explicit research state machine:

`INITIALIZE → UNDERSTAND → LITERATURE → HYPOTHESES → DESIGN → IMPLEMENT → EXECUTE → VERIFY → ANALYZE → CRITIQUE → DECIDE → COMPLETE`

Failure must be a first-class state.

### Experiment specification

Every experiment must have a unique immutable ID and a structured specification.

### Sandboxed execution

Generated code must execute with explicit time/resource/network boundaries.

### Provenance

Every execution must have enough metadata to identify the exact code, configuration, dataset, seed, environment, command, and artifacts involved.

### Deterministic analysis

Important numerical results must come from executable analysis code rather than LLM prose.

### Evidence graph

Claims, results, runs, experiments, artifacts, code versions, datasets, configurations, analyses, and literature sources must have explicit relationships.

### Verification

REX must provide a deterministic audit command capable of finding broken evidence chains.

### Resumability

A failed process should be resumable without destroying historical research state.

### Observability

A user must be able to inspect the research trace and understand what the agent did.

---

## 7. Nice-to-have capabilities

These are deliberately deferred until the core works:

- richer literature synthesis;
- multiple competing research branches;
- adaptive experiment selection;
- advanced experiment scheduling;
- automatic ablation generation;
- automatic replication selection;
- research lineage visualization;
- collaborative projects;
- remote compute;
- distributed execution;
- multiple model providers;
- automatic paper formatting;
- publication submission workflows.

---

## 8. MVP definition

The MVP is a working vertical slice, not a collection of mock screens.

The MVP must support:

1. A user enters one computational ML/AI research question.
2. REX creates a research run.
3. REX creates at least one structured experiment specification.
4. The coding agent produces runnable experiment code.
5. The execution engine runs it in an isolated environment.
6. Raw results and execution metadata are stored.
7. The analysis engine computes derived metrics.
8. At least one claim is generated from those results.
9. The claim is linked to its evidence.
10. `rex verify` confirms the chain.
11. If an evidence link or reported number is deliberately corrupted, verification detects it.

The MVP does not require fully autonomous open-ended literature research.

---

## 9. Success metrics

### Reliability

- 100% of persisted experimental runs have unique immutable IDs.
- 100% of numerical claims in the final MVP report are linked to stored result evidence.
- 100% of execution records contain code version, configuration, seed where applicable, and exit status.
- Verification detects deliberate corruption in test fixtures.

### Reproducibility

For supported experiments, a reproduction run should recover the same deterministic result within a documented tolerance.

### Agent performance

Measure:

- experiment completion rate;
- execution success rate;
- useful experiment rate;
- failure recovery rate;
- unsupported-claim rate;
- method-code alignment rate.

### System performance

Track:

- LLM calls;
- token usage where available;
- wall-clock time;
- compute time;
- experiment cost;
- storage used.

Do not optimize these metrics prematurely; capture them first.

---

## 10. Deliberately NOT building in V1

REX V1 will not build:

- a general-purpose autonomous scientist for every scientific discipline;
- a custom foundation model;
- a full paper-generation platform;
- a publication submission system;
- a social/research collaboration platform;
- a 3D/immersive interface;
- a large multi-agent swarm;
- an unrestricted shell-execution agent;
- unrestricted network access from experiments;
- a graph database solely because the architecture contains a graph;
- remote/distributed compute;
- a production SaaS billing system;
- enterprise SSO;
- mobile applications.

---

## 11. Product principles

### Evidence over narration

A polished explanation is not evidence.

### Immutable history

Past experiments are never silently overwritten.

### Deterministic computation

If a number can be calculated by software, software should calculate it.

### Explicit autonomy boundaries

The agent may act only through declared tools with bounded permissions.

### Inspectability

Every major autonomous action should be observable and recoverable.

### Minimal surface area

The product should remain narrow enough that every subsystem can be tested.

---

## 12. V1 acceptance test

A successful demonstration should look like:

```text
rex research "Does method B improve metric X over baseline A?"
        ↓
Research run created
        ↓
Hypothesis generated
        ↓
Experiment specified
        ↓
Code generated
        ↓
Sandbox execution
        ↓
Results captured
        ↓
Statistical analysis
        ↓
Claim generated
        ↓
Evidence graph created
        ↓
Research report generated
        ↓
rex verify
        ↓
PASS
```

Then intentionally alter a stored result or evidence relationship.

`rex verify` must report a failure.

That test is more important than visual polish.
