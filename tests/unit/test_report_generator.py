"""Unit tests for Evidence-Grounded Research Report Generator (REX-036).

Tests:
1. AC-1: Report includes question, hypotheses, methods, experiments, results, limitations, conclusions.
2. AC-2: Numerical statements link to claim IDs and evidence graph records.
3. AC-3: Unsupported claims are excluded or labelled as unsupported.
4. AC-4: Failed experiments and execution anomalies are represented honestly.
5. Deterministic content hashing and JSON/Markdown parity.
6. Report persistence as disk artifacts and database records.
7. Event emission of EventType.REPORT_GENERATED.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from rex.domain.models import (
    ClaimType,
    ExecutionStatus,
    ExpectedDirection,
)
from rex.evidence.claims import ClaimService
from rex.evidence.graph import (
    EvidenceGraphService,
    EvidenceNodeType,
    EvidenceRelationType,
)
from rex.observability.events import EventType, InMemoryEventSink
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
    ArtifactModel,
    CritiqueModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)
from rex.reporting.report_generator import ReportGenerator


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def populated_research_run(db_session: Session) -> str:
    """Create a fully populated research run with hypotheses, experiments, results, analyses, and claims."""
    run = ResearchRunModel(
        title="Optimizer Evaluation Run: AdamW vs SGD",
        research_question="Does AdamW outperform SGD with momentum on transformer architectures?",
        status="complete",
    )
    db_session.add(run)
    db_session.flush()

    # 1. Hypotheses
    hyp = HypothesisModel(
        research_run_id=run.id,
        statement="Decoupled weight decay in AdamW achieves lower validation perplexity than SGD.",
        rationale="Weight decay regularization is decoupled from gradient update moments.",
        expected_direction=ExpectedDirection.DECREASE.value,
        falsification_condition="Validation perplexity of AdamW is greater than or equal to SGD.",
        status="validated",
    )
    db_session.add(hyp)
    db_session.flush()

    # 2. Experiments
    exp1 = ExperimentModel(
        research_run_id=run.id,
        hypothesis_id=hyp.id,
        objective="Train transformer with AdamW (weight decay 0.01)",
        specification_json={
            "name": "exp_adamw_transformer",
            "method": "transformer_training",
            "baseline": {"name": "sgd_momentum", "value": 24.5},
        },
        status="completed",
    )
    exp2 = ExperimentModel(
        research_run_id=run.id,
        hypothesis_id=hyp.id,
        objective="Train transformer with SGD baseline",
        specification_json={
            "name": "exp_sgd_baseline",
            "method": "transformer_training",
            "baseline": {"name": "none", "value": 0.0},
        },
        status="completed",
    )
    db_session.add_all([exp1, exp2])
    db_session.flush()

    # 3. Executions
    exec1 = ExecutionModel(
        experiment_id=exp1.id,
        status=ExecutionStatus.COMPLETED.value,
        exit_code=0,
        seed=42,
    )
    exec2 = ExecutionModel(
        experiment_id=exp2.id,
        status=ExecutionStatus.COMPLETED.value,
        exit_code=0,
        seed=42,
    )
    db_session.add_all([exec1, exec2])
    db_session.flush()

    # 4. Results
    res1 = ResultModel(
        execution_id=exec1.id,
        metric_name="val_perplexity",
        metric_value=19.45,
        metric_unit="score",
    )
    res2 = ResultModel(
        execution_id=exec2.id,
        metric_name="val_perplexity",
        metric_value=24.50,
        metric_unit="score",
    )
    db_session.add_all([res1, res2])
    db_session.flush()

    # 5. Analyses
    analysis = AnalysisModel(
        research_run_id=run.id,
        analysis_type="compare_groups",
        method="group_comparison",
        input_result_ids=[res1.id],
        output_json={
            "metric_name": "val_perplexity",
            "mean": 19.45,
            "std": 0.52,
            "ci_lower": 18.93,
            "ci_upper": 19.97,
            "sample_size": 5,
            "p_value": 0.0012,
            "effect_size": 1.85,
        },
    )
    db_session.add(analysis)
    db_session.flush()

    # 6. Claims & Evidence Links
    claim_svc = ClaimService(session=db_session)
    c1 = claim_svc.create_claim(
        research_run_id=run.id,
        statement="AdamW achieved significantly lower validation perplexity (19.45) compared to baseline (24.50).",
        claim_type=ClaimType.OBSERVATION,
    )

    # Link c1 to analysis and result
    graph = EvidenceGraphService(db_session)
    graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=c1.id,
        target_type=EvidenceNodeType.ANALYSIS,
        target_id=analysis.id,
        relationship_type=EvidenceRelationType.SUPPORTED_BY,
        research_run_id=run.id,
    )
    graph.create_link(
        source_type=EvidenceNodeType.CLAIM,
        source_id=c1.id,
        target_type=EvidenceNodeType.RESULT,
        target_id=res1.id,
        relationship_type=EvidenceRelationType.SUPPORTED_BY,
        research_run_id=run.id,
    )
    graph.create_link(
        source_type=EvidenceNodeType.ANALYSIS,
        source_id=analysis.id,
        target_type=EvidenceNodeType.RESULT,
        target_id=res1.id,
        relationship_type=EvidenceRelationType.DERIVED_FROM,
        research_run_id=run.id,
    )

    # 7. Add an unsupported claim (no evidence links) to test AC-3
    claim_svc.create_claim(
        research_run_id=run.id,
        statement="AdamW also halves total GPU training energy consumption.",
        claim_type=ClaimType.OBSERVATION,
    )

    # 8. Critique
    critique = CritiqueModel(
        research_run_id=run.id,
        iteration=1,
        summary="AdamW shows clear improvement on validation perplexity; test on more seeds.",
        weaknesses_json=["Only single random seed (42) evaluated"],
        methodological_concerns_json=["Perplexity evaluated on single validation split"],
        findings_json=[
            {
                "category": "seeds",
                "severity": "medium",
                "description": "Evaluate across at least 3 random seeds.",
            }
        ],
        recommended_action="replicate",
        recommended_action_rationale="Replicate across 3 seeds to establish statistical variance.",
    )
    db_session.add(critique)
    db_session.commit()

    return run.id


@pytest.mark.unit
def test_report_generator_structure_and_completeness(
    db_session: Session, populated_research_run: str, tmp_path: Path
) -> None:
    """AC-1: Report includes question, hypotheses, methods, experiments, results, limitations, conclusions."""
    sink = InMemoryEventSink()
    generator = ReportGenerator(event_sink=sink)

    report = generator.generate_report(
        research_run_id=populated_research_run,
        session=db_session,
        save_artifact=True,
        artifact_root=tmp_path,
    )

    # Core metadata
    assert report.research_run_id == populated_research_run
    assert "AdamW" in report.title
    assert "transformer" in report.research_question.lower()
    assert report.run_status == "complete"

    # Hypotheses
    assert len(report.hypotheses) == 1
    assert "weight decay" in report.hypotheses[0].statement.lower()

    # Methods & Experiments
    assert len(report.experiments) == 2
    assert report.total_experiments == 2
    assert report.total_executions == 2

    # Results & Metrics
    assert len(report.metrics) == 2
    assert any(m.metric_name == "val_perplexity" for m in report.metrics)

    # Statistical Analyses
    assert len(report.analyses) == 1
    assert report.analyses[0].mean == 19.45
    assert report.analyses[0].p_value == 0.0012

    # Limitations & Critiques
    assert len(report.critiques) == 1
    assert any("seed" in lim.lower() for lim in report.limitations)

    # Conclusions
    assert len(report.conclusions) > 0
    assert any("19.45" in c for c in report.conclusions)


@pytest.mark.unit
def test_report_generator_numerical_statements_link_to_claim_ids(
    db_session: Session, populated_research_run: str, tmp_path: Path
) -> None:
    """AC-2: Important numerical statements link to claim IDs and evidence lineage."""
    generator = ReportGenerator()
    report = generator.generate_report(
        research_run_id=populated_research_run,
        session=db_session,
        save_artifact=False,
    )

    # Supported claims must link to exact result and analysis IDs
    assert len(report.supported_claims) == 1
    sup_claim = report.supported_claims[0]
    assert len(sup_claim.supporting_analysis_ids) >= 1
    assert len(sup_claim.supporting_result_ids) >= 1
    assert sup_claim.is_supported is True

    # Markdown output must render claim IDs and link to evidence
    md = report.to_markdown()
    assert f"`{sup_claim.claim_id}`" in md
    assert "Analysis:`an_" in md or "Result:`res_" in md


@pytest.mark.unit
def test_report_generator_unsupported_claims_quarantined_and_labelled(
    db_session: Session, populated_research_run: str
) -> None:
    """AC-3: Unsupported claims are excluded or labelled as unsupported."""
    generator = ReportGenerator()
    report = generator.generate_report(
        research_run_id=populated_research_run,
        session=db_session,
        save_artifact=False,
    )

    # We inserted 1 supported claim and 1 unsupported claim
    assert len(report.claims) == 2
    assert len(report.supported_claims) == 1
    assert len(report.unsupported_claims) == 1

    unsupported = report.unsupported_claims[0]
    assert "energy consumption" in unsupported.statement
    assert unsupported.is_supported is False
    assert unsupported.epistemic_status == "unsupported"
    assert len(unsupported.supporting_result_ids) == 0
    assert len(unsupported.supporting_analysis_ids) == 0

    # Report is marked as not fully grounded due to presence of unsupported claims
    assert report.is_fully_grounded is False

    # Markdown report must explicitly surface the quarantine notice
    md = report.to_markdown()
    assert "Unsupported Claims Notice" in md
    assert f"[UNSUPPORTED CLAIM: `{unsupported.claim_id}`]" in md


@pytest.mark.unit
def test_report_generator_honest_failed_experiments_representation(
    db_session: Session, tmp_path: Path
) -> None:
    """AC-4: Failed experiments are represented honestly with execution diagnostics."""
    run = ResearchRunModel(
        title="Failure Handling Investigation",
        research_question="Evaluate model under OOM conditions",
        status="stop",
    )
    db_session.add(run)
    db_session.flush()

    exp_success = ExperimentModel(
        research_run_id=run.id,
        objective="Small batch baseline",
        status="completed",
        specification_json={"name": "exp_small_batch"},
    )
    exp_failed = ExperimentModel(
        research_run_id=run.id,
        objective="Giant batch (causes OOM)",
        status="failed",
        specification_json={"name": "exp_giant_batch"},
    )
    db_session.add_all([exp_success, exp_failed])
    db_session.flush()

    # Success execution
    exec_ok = ExecutionModel(
        experiment_id=exp_success.id,
        status="completed",
        exit_code=0,
    )
    # Failed execution
    exec_err = ExecutionModel(
        experiment_id=exp_failed.id,
        status="failed",
        exit_code=137,  # OOM kill
    )
    db_session.add_all([exec_ok, exec_err])
    db_session.commit()

    generator = ReportGenerator()
    report = generator.generate_report(
        research_run_id=run.id,
        session=db_session,
        save_artifact=False,
    )

    assert report.total_experiments == 2
    assert len(report.failed_experiments) == 1
    failed_summary = report.failed_experiments[0]
    assert failed_summary.experiment_id == exp_failed.id
    assert failed_summary.is_failed is True
    assert failed_summary.failed_executions_count == 1
    assert any("137" in r for r in failed_summary.failure_reasons)

    # Markdown must contain honest failed experiment documentation
    md = report.to_markdown()
    assert "Failed Experiments & Operational Anomalies" in md
    assert "exit code 137" in md
    assert f"`{exp_failed.id}`" in md


@pytest.mark.unit
def test_report_generator_deterministic_content_hashing(
    db_session: Session, populated_research_run: str
) -> None:
    """Report content hash is deterministic and invariant to generation timestamp."""
    generator = ReportGenerator()
    report1 = generator.generate_report(
        research_run_id=populated_research_run,
        session=db_session,
        save_artifact=False,
    )
    report2 = generator.generate_report(
        research_run_id=populated_research_run,
        session=db_session,
        save_artifact=False,
    )

    # Content hash must be identical across multiple generations of the same evidence
    assert report1.content_hash() == report2.content_hash()
    assert len(report1.content_hash()) == 64  # SHA-256 hex


@pytest.mark.unit
def test_report_generator_saves_artifacts(
    db_session: Session, populated_research_run: str, tmp_path: Path
) -> None:
    """Report generator writes JSON and Markdown files and registers ArtifactModel records."""
    generator = ReportGenerator()
    report = generator.generate_report(
        research_run_id=populated_research_run,
        session=db_session,
        save_artifact=True,
        artifact_root=tmp_path,
    )

    # Verify files created on disk
    report_dir = tmp_path / populated_research_run / "reports"
    assert report_dir.exists()
    json_file = report_dir / f"{report.report_id}.json"
    md_file = report_dir / f"{report.report_id}.md"
    assert json_file.exists()
    assert md_file.exists()

    # Verify database artifact records
    artifacts = (
        db_session.query(ArtifactModel)
        .filter(ArtifactModel.research_run_id == populated_research_run)
        .all()
    )
    types = [a.artifact_type for a in artifacts]
    assert "research_report_json" in types
    assert "research_report_md" in types


@pytest.mark.unit
def test_report_generator_emits_event(db_session: Session, populated_research_run: str) -> None:
    """Report generator emits EventType.REPORT_GENERATED to event sink."""
    sink = InMemoryEventSink()
    generator = ReportGenerator(event_sink=sink)

    report = generator.generate_report(
        research_run_id=populated_research_run,
        session=db_session,
        save_artifact=False,
    )

    events = sink.get_by_type(EventType.REPORT_GENERATED)
    assert len(events) == 1
    evt = events[0]
    assert evt.payload["report_id"] == report.report_id
    assert evt.payload["research_run_id"] == populated_research_run
    assert evt.payload["total_experiments"] == 2
    assert evt.payload["claims_count"] == 2
    assert evt.payload["supported_claims_count"] == 1
    assert evt.payload["unsupported_claims_count"] == 1


@pytest.mark.unit
def test_report_generator_empty_run(db_session: Session) -> None:
    """Report generator handles a research run with zero experiments gracefully."""
    run = ResearchRunModel(
        title="Empty Run",
        research_question="Has no experiments",
        status="initialize",
    )
    db_session.add(run)
    db_session.commit()

    generator = ReportGenerator()
    report = generator.generate_report(
        research_run_id=run.id,
        session=db_session,
        save_artifact=False,
    )

    assert report.total_experiments == 0
    assert report.total_executions == 0
    assert report.total_results == 0
    assert len(report.claims) == 0
    assert report.is_fully_grounded is True

    # Serialization should not error
    md = report.to_markdown()
    assert "# Research Report: Empty Run" in md
    json_str = report.to_json()
    assert "Empty Run" in json_str
