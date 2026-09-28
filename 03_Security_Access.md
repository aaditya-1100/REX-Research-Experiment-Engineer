# REX — Security & Access Document

**Product:** REX — Research Experiment Engineer  
**Security model:** Local-first, single-user, least privilege  
**Document status:** V1 source of truth  
**Last updated:** 2026-09-28

---

## 1. Security objective

REX is an agentic system that can generate and execute code. That makes its primary security boundary different from an ordinary web application.

The highest-risk capability is not user login. It is **agent-controlled execution**.

REX must therefore enforce:

> An LLM may propose an action, but it must never receive unrestricted authority over the host system.

The security design follows least privilege, explicit capabilities, isolation, auditability, and fail-closed behavior.

OWASP identifies prompt injection, excessive agency, improper output handling, supply-chain risk, unbounded consumption, and misinformation among major risks for LLM applications. These are directly relevant to REX because the agent processes external literature and generated code and can initiate expensive or privileged operations.

---

## 2. V1 authentication model

REX is local-first and single-user in V1.

Therefore:

- no public signup;
- no multi-tenant identity system;
- no password database;
- no OAuth requirement;
- no public API exposure by default.

The local API should bind to localhost.

Example:

```text
127.0.0.1:8000
```

Do not bind to `0.0.0.0` by default.

If remote access is intentionally enabled later, authentication and authorization become mandatory.

---

## 3. Roles

V1 has conceptual roles even though there is normally one human user.

### Owner

Can:

- create research runs;
- start/pause/resume research;
- configure models and providers;
- approve or reject dangerous operations;
- inspect experiments;
- inspect artifacts;
- run verification;
- delete research projects;
- export research packages.

### Research Agent

Can:

- read research state;
- create hypotheses;
- propose experiments;
- generate code;
- request approved tools;
- request execution;
- create analyses;
- propose conclusions.

Cannot:

- directly modify evidence records;
- bypass execution controls;
- alter historical runs;
- access arbitrary host files;
- retrieve secrets;
- change its own permissions.

### Execution Worker

Can:

- access only its assigned workspace;
- execute the assigned experiment;
- write allowed artifacts;
- emit logs/results.

Cannot:

- access the host filesystem outside its workspace;
- access credentials;
- modify the research database directly;
- execute privileged host operations.

### Verifier

Can:

- read research artifacts;
- validate hashes;
- inspect claims;
- recompute verification checks.

Cannot:

- modify research evidence.

---

## 4. Permission model

The system should implement capability-based tool access.

Example:

```text
literature.search
literature.fetch
filesystem.read_workspace
filesystem.write_workspace
experiment.create
experiment.execute
analysis.run
evidence.read
evidence.write
report.generate
research.decide
```

The agent should receive only the capabilities required for its current state.

For example, the literature agent does not need:

```text
experiment.execute
filesystem.write_host
database.admin
```

This directly reduces excessive agency.

---

## 5. Database access

V1 is single-user, so row-level security is primarily an architectural requirement rather than a multi-tenant feature.

Every domain query should still be scoped by `research_run_id`.

Rules:

- an agent may only access the current research run unless explicitly granted cross-run read access;
- execution workers cannot directly modify database rows;
- historical execution records are append-only;
- verifier operations are read-only;
- administrative mutation operations are owner-only.

If PostgreSQL is introduced later, enforce tenant/project isolation using database row-level security.

---

## 6. Prompt injection

External literature is untrusted input.

A paper, webpage, repository, dataset description, or generated artifact may contain instructions intended to manipulate the agent.

Never treat retrieved text as an instruction.

Maintain explicit separation:

```text
SYSTEM INSTRUCTIONS
        ↓
APPLICATION POLICY
        ↓
TOOL OUTPUT / EXTERNAL CONTENT
        ↓
MODEL INTERPRETATION
```

Retrieved content must be labelled as data.

Do not concatenate external text into privileged system instructions.

---

## 7. Generated code security

Generated experiment code is untrusted.

Never execute it directly on the host by default.

Required controls:

- isolated container;
- non-root execution;
- CPU limit;
- memory limit;
- execution timeout;
- filesystem restriction;
- output-size limit;
- process cleanup;
- controlled network policy;
- no host Docker socket;
- no host SSH keys;
- no cloud credentials;
- no arbitrary secret environment variables.

---

## 8. Network policy

Default experiment network policy:

```text
DISABLED
```

If an experiment genuinely requires network access, the experiment specification must explicitly declare the requirement.

The execution policy must determine whether that access is permitted.

Future versions may introduce allowlists such as:

```text
pypi.org
huggingface.co
api.openalex.org
api.semanticscholar.org
export.arxiv.org
```

Do not allow arbitrary internet access by default.

---

## 9. Secret handling

Secrets may exist for:

- LLM providers;
- literature APIs;
- optional external services.

Secrets must:

- live in environment variables or an OS-level secret store;
- never be written into experiment artifacts;
- never be injected into generated code unless explicitly required;
- never be included in prompts;
- never appear in logs.

The execution environment should receive a minimal environment rather than inheriting the host environment wholesale.

---

## 10. Supply-chain security

Generated experiments may request Python dependencies.

V1 should:

- record dependency versions;
- prefer lock files;
- record installation logs;
- reject suspicious package installation commands;
- avoid executing arbitrary install scripts outside the sandbox.

Dependency installation belongs inside the isolated environment.

Future versions should support dependency allowlists and vulnerability scanning.

---

## 11. Resource abuse

Autonomous research can consume large amounts of compute or API budget.

Implement:

- maximum experiment runtime;
- maximum number of experiments per research run;
- maximum LLM calls;
- maximum token/cost budget where provider information is available;
- CPU limit;
- memory limit;
- maximum artifact size;
- maximum concurrent executions.

When a budget is exhausted, the controller must stop or request explicit user approval.

---

## 12. Error handling

All major failures must become structured states rather than raw crashes.

### LLM failure

Record:

- provider;
- model;
- request ID if available;
- error category;
- retry count.

Use bounded retries.

### Literature API failure

Retry transient failures with exponential backoff.

If unavailable, preserve the research state and allow resume.

### Code-generation failure

Return the error to the coding agent with the relevant compiler/runtime output.

Do not fabricate success.

### Execution timeout

Mark the run:

```text
TIMEOUT
```

Preserve partial logs and artifacts.

### Out-of-memory

Mark:

```text
RESOURCE_EXCEEDED
```

Preserve the experiment as failed evidence.

### Dependency installation failure

Do not retry indefinitely.

Record the package/version and installation output.

### Malformed result

The result parser must reject malformed or schema-incompatible output.

### Statistical analysis failure

Do not generate a conclusion from incomplete analysis.

### Verification failure

The verification command must return a non-zero exit code.

Never silently downgrade an integrity failure to a warning.

---

## 13. Important edge cases

REX must handle:

### Duplicate experiments

Detect whether the same specification/configuration already exists.

Allow intentional replication, but label it as such.

### Partial execution

A process may crash after producing some artifacts.

Preserve those artifacts and mark the run incomplete.

### Agent restart

The controller must reconstruct state from persisted records.

### Machine restart

A research run should resume from the last durable state.

### Corrupted artifact

Hash mismatch must invalidate the artifact.

### Missing dataset

Mark the run unreproducible rather than pretending it succeeded.

### Changed code

A new code hash must create a new execution identity.

### Changed experiment specification

After execution starts, changes require a new experiment version or child experiment.

### Non-deterministic output

Record seeds where applicable and label the experiment as nondeterministic when exact reproduction is impossible.

### LLM hallucinated metric

Reject any metric that does not exist in the execution result.

### Unsupported claim

Claims without sufficient evidence must be marked unsupported.

### Prompt injection in a paper

Treat the text as untrusted content and do not execute instructions found in it.

### Infinite research loop

The controller must have explicit loop and budget limits.

---

## 14. Audit logging

Record:

- user actions;
- agent decisions;
- tool calls;
- experiment launches;
- execution results;
- state transitions;
- verification events;
- permission failures.

Audit records must not expose secrets.

---

## 15. Data integrity

Use content hashes for important artifacts.

At minimum:

- source code;
- configuration;
- dataset manifests;
- result files;
- figures;
- reports.

The verifier should detect hash mismatches.

---

## 16. Deletion policy

Historical research should not be physically deleted as part of ordinary agent behavior.

The agent has no deletion capability for evidence.

Owner-level project deletion may remove a research package, but should require an explicit user action.

The system should distinguish:

- logical archival;
- destructive deletion.

---

## 17. Security acceptance tests

V1 security tests must demonstrate:

1. Generated code cannot read arbitrary host files.
2. Generated code cannot access host credentials.
3. Generated code cannot access the Docker socket.
4. Network is disabled by default.
5. CPU/memory/time limits are enforced.
6. Prompt injection in retrieved text does not grant tools.
7. The agent cannot modify historical evidence.
8. Verification detects artifact tampering.
9. Missing evidence cannot be represented as verified.
10. Exhausted research budgets stop autonomous execution.

---

## 18. Security philosophy

REX should fail conservatively.

If the system cannot establish:

- what code ran;
- what data was used;
- what result was produced;
- what evidence supports a claim;

then it should report uncertainty or failure rather than infer a successful outcome.
