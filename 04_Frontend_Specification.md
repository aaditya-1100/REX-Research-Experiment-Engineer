# REX — Frontend Specification Document

**Product:** REX — Research Experiment Engineer  
**Frontend:** React + TypeScript + Vite + Tailwind CSS  
**Design principle:** Research workstation, not chatbot  
**Document status:** V1 source of truth  
**Last updated:** 2026-09-28

---

## 1. Frontend objective

The REX frontend is an observation, control, and audit interface for an autonomous research system.

It must answer four questions immediately:

1. What is REX currently doing?
2. What experiments has it performed?
3. What evidence supports its conclusions?
4. Can I verify the research?

The frontend must not imitate ChatGPT.

The primary interaction model is a **research workspace** with structured state, experiment lineage, logs, results, and evidence.

---

## 2. Visual direction

REX should feel like a serious research/engineering instrument.

Design characteristics:

- dark-first interface;
- high information density without visual clutter;
- strong typographic hierarchy;
- restrained color use;
- monospace treatment for IDs, hashes, commands, metrics, and logs;
- clear state indicators;
- minimal decorative animation.

Avoid:

- glowing AI clichés;
- oversized hero sections;
- animated 3D orbs;
- excessive gradients;
- conversational chat bubbles as the primary interface.

---

## 3. Color system

Use semantic colors rather than hard-coding colors throughout components.

### Base

```text
Background: #0B0D10
Surface: #11151A
Surface Elevated: #171C22
Border: #28303A
Text Primary: #F3F5F7
Text Secondary: #A7B0BA
Text Muted: #737D88
```

### Semantic

```text
Success: #34D399
Warning: #FBBF24
Error: #F87171
Info: #60A5FA
Accent: #A78BFA
```

Use semantic tokens:

```text
--color-bg
--color-surface
--color-border
--color-text-primary
--color-text-secondary
--color-success
--color-warning
--color-error
--color-info
--color-accent
```

---

## 4. Typography

Use a modern sans-serif for interface text.

Recommended:

```text
Inter
```

Fallback:

```text
system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif
```

Use a monospace font for:

- IDs;
- hashes;
- code;
- commands;
- logs;
- raw metrics.

Recommended:

```text
JetBrains Mono
```

---

## 5. Spacing

Use a 4px base grid.

Common spacing:

```text
4px
8px
12px
16px
24px
32px
48px
64px
```

Do not create arbitrary spacing values unless necessary.

---

## 6. Layout

Desktop-first research workstation.

Global structure:

```text
┌─────────────────────────────────────────────────────────┐
│ Top bar: REX / Research Run / Status / Controls         │
├───────────────┬─────────────────────────┬───────────────┤
│ Research      │ Main workspace          │ Inspector     │
│ navigation    │                         │               │
│               │                         │               │
│ Overview      │ Current state           │ Evidence      │
│ Experiments   │ Timeline / Results      │ Metadata      │
│ Hypotheses    │                         │               │
│ Evidence      │                         │               │
│ Logs          │                         │               │
└───────────────┴─────────────────────────┴───────────────┘
```

Responsive behavior may collapse the inspector on smaller screens.

---

## 7. Primary screens

### 7.1 Research Home

Shows:

- research question;
- current status;
- elapsed time;
- experiment count;
- successful/failed runs;
- current hypothesis;
- current action;
- evidence integrity status.

Primary action:

`New Research Run`

### 7.2 Research Run Overview

Shows:

- question;
- lifecycle state;
- timeline;
- hypotheses;
- experiments;
- latest findings;
- research budget;
- verification status.

### 7.3 Experiment Explorer

Each experiment shows:

- experiment ID;
- hypothesis;
- objective;
- specification;
- status;
- runs;
- metrics;
- artifacts;
- lineage.

### 7.4 Execution Detail

Shows:

- run ID;
- command;
- start/end;
- runtime;
- code commit;
- configuration hash;
- dataset;
- seed;
- resource usage;
- stdout;
- stderr;
- produced artifacts.

### 7.5 Evidence Explorer

This is one of the most important screens.

Display a navigable chain:

```text
Claim
 ↓
Analysis
 ↓
Result
 ↓
Execution
 ↓
Experiment
 ↓
Code / Config / Dataset
```

Each node should be clickable.

### 7.6 Verification

Show:

```text
VERIFIED
```

or:

```text
FAILED
```

with individual checks:

```text
✓ Claim mapped to evidence
✓ Result exists
✓ Artifact hash valid
✓ Code version recorded
✓ Configuration recorded
✓ Dataset recorded
✗ Figure hash mismatch
```

### 7.7 Logs

A searchable event stream.

Events should include:

- timestamp;
- event type;
- entity;
- status;
- message.

### 7.8 Research Report

Render the generated report with links from claims to evidence.

---

## 8. Component specification

### Buttons

Primary:

- 36–40px height;
- medium weight;
- 8px radius.

Secondary:

- same dimensions;
- neutral surface.

Danger:

- used only for explicit owner actions.

### Inputs

- 40px minimum height;
- clear focus state;
- visible validation;
- no placeholder text as the only label.

### Cards

Use cards for meaningful objects, not every section.

Card properties:

- 12px radius;
- 1px border;
- 16–20px internal padding.

### Badges

Use badges for state:

```text
RUNNING
COMPLETED
FAILED
TIMEOUT
VERIFYING
VERIFIED
WARNING
```

### Tables

Use tables for:

- experiments;
- runs;
- metrics;
- artifacts;
- claims.

Rows must be clickable when drill-down is available.

### Modal

Use modals only for:

- confirmation;
- dangerous actions;
- configuration requiring focused attention.

Do not use modals for routine navigation.

---

## 9. Research timeline

The timeline is the primary explanation of autonomous behavior.

Example:

```text
22:41:03  RESEARCH_STARTED
22:41:11  HYPOTHESIS_CREATED H-003
22:41:29  EXPERIMENT_DESIGNED EXP-004
22:42:02  CODE_GENERATED
22:42:08  EXECUTION_STARTED RUN-009
22:44:17  EXECUTION_COMPLETED RUN-009
22:44:21  ANALYSIS_COMPLETED AN-005
22:44:36  CRITIQUE_CREATED
22:44:42  DECISION_MADE REFINE
```

---

## 10. Evidence graph interaction

Do not initially render a complicated force-directed graph.

Use a deterministic vertical lineage view.

Example:

```text
CLAIM-014
"Method B improved accuracy by 7.4%"
        │
        ├── AN-009
        │     └── bootstrap analysis
        │
        └── RESULT-021
              └── RUN-017
                    └── EXP-009
                          ├── code commit
                          ├── dataset hash
                          └── configuration
```

This is easier to read and easier to implement.

---

## 11. API integration specification

### REX backend

Base URL:

```text
/api
```

Endpoints:

```text
POST /research
GET /research/{id}
POST /research/{id}/start
POST /research/{id}/pause
POST /research/{id}/resume
GET /research/{id}/events
GET /research/{id}/experiments
GET /research/{id}/claims
GET /research/{id}/evidence
POST /research/{id}/verify
GET /research/{id}/report
GET /experiments/{id}
GET /experiments/{id}/runs
GET /runs/{id}
GET /runs/{id}/artifacts
```

Use JSON responses.

### Event streaming

Use Server-Sent Events initially for live research updates:

```text
GET /research/{id}/events/stream
```

WebSockets are unnecessary for V1 unless a demonstrated requirement appears.

---

## 12. External service integrations

### LLM provider

Purpose:

- planning;
- hypothesis generation;
- experiment design;
- code generation;
- critique;
- report synthesis.

Data sent:

- current research state;
- relevant experiment specification;
- permitted tool results;
- relevant literature;
- bounded context.

Never send:

- host secrets;
- unrelated project data;
- raw credential material.

The provider adapter should return structured objects where possible.

### OpenAlex

Purpose:

- scholarly metadata;
- literature discovery.

The adapter should normalize returned records into the internal LiteratureSource schema.

### Semantic Scholar

Purpose:

- paper search;
- citation/author metadata;
- related scholarly information.

Use its Academic Graph API through a dedicated adapter.

### arXiv

Purpose:

- preprint discovery and metadata.

Treat retrieved paper content as untrusted data.

### Docker

Purpose:

- experiment isolation.

Docker is not a remote API integration; it is a local execution dependency.

The backend controls containers through the execution service.

---

## 13. Frontend state model

Research state should be represented as:

```text
ResearchRun
├── status
├── question
├── current_action
├── hypotheses[]
├── experiments[]
├── events[]
├── claims[]
├── verification
└── metrics
```

Avoid duplicating authoritative state in multiple frontend stores.

The backend is authoritative.

---

## 14. Loading and error states

Every asynchronous screen needs:

- loading;
- empty;
- success;
- error;
- stale/offline state where applicable.

Do not display blank screens during loading.

---

## 15. Accessibility

Requirements:

- keyboard navigation;
- visible focus states;
- semantic HTML;
- sufficient text contrast;
- labels for form controls;
- status messages accessible to screen readers;
- no information conveyed by color alone.

---

## 16. Frontend acceptance criteria

A user should be able to:

1. Create a research run.
2. Watch its current state.
3. Inspect an experiment.
4. Inspect an execution.
5. inspect raw logs.
6. inspect metrics.
7. trace a claim to evidence.
8. run verification.
9. understand why verification passed or failed.
10. open the final report.

The interface is complete when it makes the underlying research state understandable—not when it looks impressive.
