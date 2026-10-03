"""End-to-end integration tests for Evidence-Grounded Research Report Generator (Batch 7 / REX-036).

Validates:
1. End-to-end report generation from a completed AutonomousResearchLoop run (REX-035 -> REX-036).
2. Report artifact persistence (JSON and Markdown) with deterministic SHA-256 hashes and ArtifactModel records.
3. Compatibility with ResearchVerifier protocol.
4. Adversarial epistemic discipline: injected ungrounded claims are quarantined and excluded from conclusions.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rex.agents.critic import ResearchCriticAgent
from rex.controller.autonomous_loop import (
    AutonomousLoopConfig,
    AutonomousResearchLoop,
)
from rex.controller.state_machine import create_research_run
from rex.domain.models import ClaimType
from rex.evidence.claims import ClaimService
from rex.evidence.verifier import ResearchVerifier
from rex.llm.providers.mock import MockLLMProvider
from rex.observability.events import (
    ActorType,
    EventType,
    InMemoryEventSink,
)
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)
from rex.persistence.models import (
    ArtifactModel,
)
from rex.reporting.models import ResearchReport
from rex.reporting.report_generator import ReportGenerator


@pytest.fixture
def session_factory(tmp_path: Path):
    """Isolated SQLite database for reporting integration tests."""
    db_file = tmp_path / "test_reporting_e2e.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def _make_mock_critic_agent(actions: list[str]) -> ResearchCriticAgent:
    """Helper to create a critic agent that returns sequential recommendations."""
    provider = MockLLMProvider()
    for act in actions:
        payload = json.dumps(
            {
                "summary": f"Critique assessing empirical evidence with recommended action {act}.",
                "strengths": ["Clear baseline", "Deterministic execution"],
                "weaknesses": ["Limited sample size"],
                "contradictions": [],
                "unresolved_questions": [],
                "methodological_concerns": ["Variance across seeds not assessed"],
                "findings": [
                    {
                        "category": "methodology",
                        "severity": "medium",
                        "description": "Evaluate variance across random seeds.",
                        "epistemic_status": "observed",
                        "evidence_refs": [],
                        "recommendation": f"Action recommendation: {act}.",
                    }
                ],
                "recommended_action": act,
                "recommended_action_rationale": f"Autonomous recommendation: {act}",
            }
        )
        provider.enqueue_response(payload)
    return ResearchCriticAgent(provider=provider)


@pytest.mark.integration
def test_reporting_e2e_after_autonomous_research_loop(session_factory, tmp_path: Path) -> None:
    """AC-1 to AC-4: End-to-end report generation from a completed Autonomous Research Loop."""
    event_sink = InMemoryEventSink()

    # 1. Create Research Run
    with get_db_session(session_factory) as session:
        budget = {
            "max_experiments": 5,
            "max_executions": 10,
            "max_runtime_seconds": 3600,
        }
        run = create_research_run(
            session=session,
            title="Transformer Scaling and Optimization Investigation",
            research_question="Does decoupled weight decay improve generalisation across transformer architectures?",
            budget=budget,
            event_sink=event_sink,
        )
        run_id = run.id

    # 2. Configure Critic: Iteration 1 -> refine, Iteration 2 -> stop
    critic = _make_mock_critic_agent(actions=["refine", "stop"])

    loop_config = AutonomousLoopConfig(
        max_iterations=2,
        auto_complete_if_sufficient=False,
        record_evidence_claims=True,
    )

    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=loop_config,
        critic_agent=critic,
        event_sink=event_sink,
    )

    # 3. Execute Autonomous Loop
    result = loop.run(research_run_id=run_id)
    assert result.success is True
    assert result.iterations_completed == 2

    # 4. Generate Research Report
    artifact_dir = tmp_path / "artifacts"
    generator = ReportGenerator(event_sink=event_sink)

    with get_db_session(session_factory) as session:
        report = generator.generate_report(
            research_run_id=run_id,
            session=session,
            actor=ActorType.REPORT_GENERATOR,
            save_artifact=True,
            artifact_root=artifact_dir,
        )

        # 5. Assert Report Completeness (AC-1)
        assert report.research_run_id == run_id
        assert report.title == "Transformer Scaling and Optimization Investigation"
        assert "decoupled weight decay" in report.research_question
        assert report.total_experiments == 2
        assert report.total_executions == 2
        assert report.total_results == 2
        assert len(report.hypotheses) >= 1
        assert len(report.experiments) == 2
        assert len(report.metrics) == 2
        assert len(report.analyses) == 2
        assert len(report.critiques) == 2
        assert len(report.limitations) >= 1
        assert len(report.conclusions) >= 1

        # 6. Assert Evidence Lineage (AC-2)
        assert len(report.claims) >= 1
        assert report.is_fully_grounded is True
        for claim in report.supported_claims:
            assert len(claim.supporting_analysis_ids) > 0 or len(claim.supporting_result_ids) > 0

        # 7. Assert Artifact Persistence and Hashing
        json_file = artifact_dir / run_id / "reports" / f"{report.report_id}.json"
        md_file = artifact_dir / run_id / "reports" / f"{report.report_id}.md"

        assert json_file.exists()
        assert md_file.exists()

        json_disk_content = json_file.read_text(encoding="utf-8")
        md_disk_content = md_file.read_text(encoding="utf-8")

        # Parity with loaded report
        loaded_report = ResearchReport.from_json(json_disk_content)
        assert loaded_report.report_id == report.report_id
        assert loaded_report.research_run_id == report.research_run_id
        assert loaded_report.content_hash() == report.content_hash()

        # Database Artifact records
        artifacts = session.query(ArtifactModel).filter_by(research_run_id=run_id).all()
        report_artifacts = [a for a in artifacts if "research_report" in a.artifact_type]
        assert len(report_artifacts) == 2

        json_art = next(a for a in report_artifacts if a.artifact_type == "research_report_json")
        md_art = next(a for a in report_artifacts if a.artifact_type == "research_report_md")

        assert (
            json_art.content_hash == hashlib.sha256(json_disk_content.encode("utf-8")).hexdigest()
        )
        assert md_art.content_hash == hashlib.sha256(md_disk_content.encode("utf-8")).hexdigest()

    # 8. Assert Event Emission
    report_events = event_sink.get_by_type(EventType.REPORT_GENERATED)
    assert len(report_events) == 1
    assert report_events[0].payload["report_id"] == report.report_id
    assert report_events[0].payload["total_experiments"] == 2


@pytest.mark.integration
def test_reporting_verifier_integration(session_factory, tmp_path: Path) -> None:
    """Confirm that the research run verified by ResearchVerifier produces a complete audit report."""
    event_sink = InMemoryEventSink()

    # 1. Run 1-iteration investigation
    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            title="Single Iteration Benchmark Run",
            research_question="Does baseline Adam converge in 100 steps?",
            budget={"max_experiments": 2, "max_executions": 5, "max_runtime_seconds": 600},
            event_sink=event_sink,
        )
        run_id = run.id

    critic = _make_mock_critic_agent(actions=["stop"])
    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=AutonomousLoopConfig(max_iterations=1, record_evidence_claims=True),
        critic_agent=critic,
        event_sink=event_sink,
    )
    result = loop.run(research_run_id=run_id)
    assert result.success is True

    # 2. Run formal verification
    with get_db_session(session_factory) as session:
        verifier = ResearchVerifier(session=session, event_sink=event_sink)
        verification_report = verifier.verify_run(research_run_id=run_id, actor=ActorType.VERIFIER)
        assert verification_report.is_passed is True

        # 3. Generate Research Report
        generator = ReportGenerator(event_sink=event_sink)
        report = generator.generate_report(
            research_run_id=run_id,
            session=session,
            save_artifact=True,
            artifact_root=tmp_path,
        )

        assert report.research_run_id == run_id
        assert report.is_fully_grounded is True
        assert len(report.experiments) == 1
        assert len(report.supported_claims) >= 1


@pytest.mark.integration
def test_reporting_adversarial_quarantines_unsupported_claim(
    session_factory, tmp_path: Path
) -> None:
    """AC-3: Adversarial test injecting an unsupported claim.

    Ensures:
    1. Unsupported claim is excluded from grounded conclusions.
    2. Unsupported claim is isolated under ungrounded/quarantined claims.
    3. Markdown output explicitly flags [UNSUPPORTED CLAIM: <id>].
    4. report.is_fully_grounded is set to False.
    """
    event_sink = InMemoryEventSink()

    # 1. Run 1-iteration investigation
    with get_db_session(session_factory) as session:
        run = create_research_run(
            session=session,
            title="Adversarial Claim Grounding Run",
            research_question="Validating epistemic discipline against fabricated claims.",
            budget={"max_experiments": 2, "max_executions": 5, "max_runtime_seconds": 600},
            event_sink=event_sink,
        )
        run_id = run.id

    critic = _make_mock_critic_agent(actions=["stop"])
    loop = AutonomousResearchLoop(
        session_factory=session_factory,
        config=AutonomousLoopConfig(max_iterations=1, record_evidence_claims=True),
        critic_agent=critic,
        event_sink=event_sink,
    )
    result = loop.run(research_run_id=run_id)
    assert result.success is True

    # 2. Inject adversarial unsupported claim (no evidence links)
    with get_db_session(session_factory) as session:
        claim_svc = ClaimService(session=session)
        fake_claim = claim_svc.create_claim(
            research_run_id=run_id,
            statement="Fabricated claim: Algorithm achieves 99.9% accuracy with zero training steps.",
            claim_type=ClaimType.OBSERVATION,
            confidence_score=0.99,
        )
        session.commit()
        fake_claim_id = fake_claim.id

    # 3. Generate Research Report
    generator = ReportGenerator(event_sink=event_sink)
    with get_db_session(session_factory) as session:
        report = generator.generate_report(
            research_run_id=run_id,
            session=session,
            save_artifact=True,
            artifact_root=tmp_path,
        )

        # 4. Verification of Epistemic Discipline (AC-3)
        assert report.is_fully_grounded is False
        assert len(report.unsupported_claims) >= 1

        unsupported_ids = [c.claim_id for c in report.unsupported_claims]
        assert fake_claim_id in unsupported_ids

        # Must NOT appear in grounded conclusions
        conclusion_text = " ".join(report.conclusions)
        assert "99.9% accuracy" not in conclusion_text

        # Must appear in quarantined section with explicit label
        md = report.to_markdown()
        assert "Unsupported Claims Notice & Epistemic Anomalies" in md
        assert f"[UNSUPPORTED CLAIM: `{fake_claim_id}`]" in md
        assert "Fabricated claim" in md
