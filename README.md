# REX — Research Experiment Engineer

> **REX** is an autonomous computational research system for ML/AI that designs, executes, analyzes, and iteratively refines experiments while maintaining an immutable, machine-readable provenance graph.

Every reported result, figure, and scientific claim is deterministically auditable back to the exact code, configuration, dataset, execution run, and statistical analysis that produced it via `rex verify`.

---

## The Three Planes

1. **Reasoning Plane**: LLM agents that decompose research questions, formulate hypotheses, design experiments, write code, and critique results. (Proposes actions; never an authoritative source of results).
2. **Execution Plane**: Deterministic software that runs code in isolated sandboxes, captures runtime telemetry, executes statistical analyses (SciPy/NumPy), and renders figures. (Produces facts).
3. **Evidence Plane**: An immutable, relational/graph persistence layer in SQLite that captures the complete provenance chain from raw bytes to high-level research claims. (Audits and verifies).

---

## Core Lifecycle States

```
INITIALIZE -> UNDERSTAND -> LITERATURE -> HYPOTHESES -> DESIGN -> IMPLEMENT -> EXECUTE -> VERIFY -> ANALYZE -> CRITIQUE -> DECIDE (REFINE / REPLICATE / PIVOT / STOP) -> COMPLETE
```

---

## CLI Interface

```bash
# Start an autonomous research investigation
rex research "Does adaptive gradient clipping improve convergence stability in deep transformer pretraining?"

# Check progress and status
rex status <research_id>

# Inspect an experiment specification, code, and runs
rex inspect <experiment_id>

# Deterministically audit all claims and artifacts (zero LLMs)
rex verify <research_id>

# Reproduce an experiment run with identical seeds and config
rex reproduce <experiment_id> --run-id <run_id>

# Generate the evidence-grounded research report
rex report <research_id>
```

---

## Documentation

- [`PROJECT_SPEC.md`](PROJECT_SPEC.md): Full project specification and guardrails.
- [`ARCHITECTURE.md`](ARCHITECTURE.md): System architecture, component models, and data contracts.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md): Implementation roadmap, V1 milestone, and test strategy.
