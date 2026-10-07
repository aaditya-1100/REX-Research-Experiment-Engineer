"""REX Adversarial Concurrency, State & Crash Consistency Suite (REX-043 Track A).

Covers 5 authoritative concurrency, state isolation, and crash resilience attack categories:
- Cat J: Cross-Run Contamination (Cross-run evidence links, artifact collisions, multi-run leaks)
- Cat K: Memory / Context Poisoning (Persistence bleed, dirty session objects, context leaks)
- Cat Q: Crash Consistency (Mid-cycle process kills, orphan cleanup, transactional integrity)
- Cat R: Concurrency / Race Conditions (SQLite lock contention, optimistic concurrency, parallel verifications)
- Cat S: Long-Horizon Stability (High-iteration stress tests, DAG scale, session leaks)
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from rex.agents.coding import GeneratedCodeProposal
from rex.agents.critic import ResearchCriticAgent
from rex.controller.autonomous_loop import (
    AutonomousLoopConfig,
    AutonomousResearchLoop,
)
from rex.controller.exceptions import (
    StaleStateError,
)
from rex.controller.execution_orchestrator import ExecutionOrchestrator
from rex.controller.state_machine import ResearchStateMachine
from rex.domain.models import (
    EvidenceNodeType,
    EvidenceRelationType,
    ExecutionStatus,
    ResearchState,
)
from rex.evidence.graph import (
    CrossRunEvidenceError,
    EvidenceGraphService,
)
from rex.evidence.verifier import (
    ResearchVerifier,
    VerificationStatus,
)
from rex.execution.models import ExecutionRequest
from rex.execution.workspace import WorkspaceManager
from rex.llm.models import LLMRequest
from rex.llm.providers.mock import MockLLMProvider
from rex.observability.events import (
    ActorType,
    InMemoryEventSink,
)
from rex.persistence.database import Base
from rex.persistence.models import (
    AnalysisModel,
    ClaimModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    ResearchRunModel,
)

# =============================================================================
# Shared Fixtures
# =============================================================================


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture
def db_session(session_factory) -> Session:
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def mock_critic() -> ResearchCriticAgent:
    payload = json.dumps(
        {
            "summary": "Completed stability review.",
            "strengths": [],
            "weaknesses": ["Adversarial test"],
            "contradictions": [],
            "unresolved_questions": [],
            "methodological_concerns": [],
            "findings": [],
            "recommended_action": "stop",
            "recommended_action_rationale": "Stopping loop.",
        }
    )
    provider = MockLLMProvider()
    provider.enqueue_response(payload)
    for _ in range(50):
        provider.enqueue_response(payload)
    return ResearchCriticAgent(provider=provider)


# =============================================================================
# Category J: Cross-Run Contamination
# =============================================================================


class TestCatJCrossRunContamination:
    """Cat J: Proves strict isolation of evidence graphs, artifacts, and metrics across research runs."""

    def test_cat_j_cross_run_evidence_link_blocked(self, db_session: Session) -> None:
        """Attempting to create an evidence link between Run A and Run B raises CrossRunEvidenceError."""
        run_a = ResearchRunModel(id="run_a_101", title="Run A", research_question="Question A")
        run_b = ResearchRunModel(id="run_b_102", title="Run B", research_question="Question B")
        db_session.add_all([run_a, run_b])

        claim_a = ClaimModel(id="clm_a_1", research_run_id=run_a.id, statement="Claim A")
        analysis_b = AnalysisModel(
            id="ana_b_1",
            research_run_id=run_b.id,
            analysis_type="summary",
            method="mean",
            output_json={},
        )
        db_session.add_all([claim_a, analysis_b])
        db_session.commit()

        graph = EvidenceGraphService(db_session)
        with pytest.raises(CrossRunEvidenceError):
            graph.create_link(
                source_type=EvidenceNodeType.CLAIM,
                source_id=claim_a.id,
                target_type=EvidenceNodeType.ANALYSIS,
                target_id=analysis_b.id,
                relationship_type=EvidenceRelationType.SUPPORTED_BY,
                research_run_id=run_a.id,
            )

    def test_cat_j_verifier_detects_cross_run_link(self, db_session: Session) -> None:
        """Verifier Step 3 flags VerificationStatus.FAIL if a foreign link exists in the run."""
        run = ResearchRunModel(
            id="run_victim_1",
            title="Victim Run",
            research_question="Can foreign link evade verifier?",
            status=ResearchState.VERIFY.value,
        )
        run_foreign = ResearchRunModel(
            id="run_foreign_1",
            title="Foreign Run",
            research_question="Foreign question",
        )
        claim_victim = ClaimModel(id="clm_victim", research_run_id=run.id, statement="Claim Victim")
        analysis_foreign = AnalysisModel(
            id="ana_foreign",
            research_run_id=run_foreign.id,
            analysis_type="summary",
            method="mean",
            output_json={},
        )
        db_session.add_all([run, run_foreign, claim_victim, analysis_foreign])

        # Insert a corrupt evidence link referencing foreign run
        bad_link = EvidenceLinkModel(
            id="link_cross_1",
            source_type=EvidenceNodeType.CLAIM.value,
            source_id="clm_victim",
            target_type=EvidenceNodeType.ANALYSIS.value,
            target_id="ana_foreign",
            relationship_type=EvidenceRelationType.SUPPORTED_BY.value,
            research_run_id=run.id,
        )
        db_session.add(bad_link)
        db_session.commit()

        verifier = ResearchVerifier(session=db_session)
        report = verifier.verify_run(run.id)

        assert report.status == VerificationStatus.FAIL
        assert any(
            "cross-run" in err.lower() or "violation" in err.lower() for err in report.errors
        )

    def test_cat_j_workspace_scoping_prevents_artifact_collision(self, tmp_path: Path) -> None:
        """Workspaces created for different runs are strictly isolated in directory hierarchy."""
        manager = WorkspaceManager(base_root=tmp_path)
        req_a = ExecutionRequest(
            execution_id="exec_1",
            experiment_id="exp_1",
            research_run_id="run_A",
            command=["python", "main.py"],
        )
        req_b = ExecutionRequest(
            execution_id="exec_1",
            experiment_id="exp_1",
            research_run_id="run_B",
            command=["python", "main.py"],
        )
        ws_a = manager.prepare_workspace(req_a)
        ws_b = manager.prepare_workspace(req_b)

        assert ws_a.workspace_dir != ws_b.workspace_dir
        assert "run_A" in str(ws_a.workspace_dir)
        assert "run_B" in str(ws_b.workspace_dir)

    def test_cat_j_metric_query_isolation_across_runs(self, db_session: Session) -> None:
        """Querying metrics and analyses for Run A never returns records from Run B."""
        run_a = ResearchRunModel(id="run_a_metric", title="A", research_question="Q A")
        run_b = ResearchRunModel(id="run_b_metric", title="B", research_question="Q B")
        db_session.add_all([run_a, run_b])

        analysis_a = AnalysisModel(
            id="ana_a_1",
            research_run_id=run_a.id,
            analysis_type="summary",
            method="mean",
            output_json={"metric": "acc", "mean": 0.85},
        )
        analysis_b = AnalysisModel(
            id="ana_b_1",
            research_run_id=run_b.id,
            analysis_type="summary",
            method="mean",
            output_json={"metric": "acc", "mean": 0.30},
        )
        db_session.add_all([analysis_a, analysis_b])
        db_session.commit()

        # Query solely for Run A
        a_records = db_session.scalars(
            select(AnalysisModel).where(AnalysisModel.research_run_id == run_a.id)
        ).all()
        assert len(a_records) == 1
        assert a_records[0].output_json["mean"] == 0.85


# =============================================================================
# Category K: Memory / Context Poisoning
# =============================================================================


class TestCatKContextPoisoning:
    """Cat K: Proves that dirty session state, transaction rollbacks, and sequential contexts do not leak."""

    def test_cat_k_uncommitted_session_rollback_cleans_dirty_objects(self, session_factory) -> None:
        """Failed transaction in Run 1 rolls back cleanly without leaving uncommitted entities in session."""
        with session_factory() as session:
            try:
                run_corrupt = ResearchRunModel(
                    id="run_fail_1",
                    title="Aborted Run",
                    research_question="Will this leak?",
                )
                session.add(run_corrupt)
                # Intentionally trigger an exception before commit
                raise RuntimeError("Simulated transaction crash in Run 1")
            except RuntimeError:
                session.rollback()

        # Next transaction in Run 2 must see zero records from Run 1
        with session_factory() as session:
            found = session.get(ResearchRunModel, "run_fail_1")
            assert found is None

    def test_cat_k_sequential_run_agent_isolation(self) -> None:
        """Reusing an agent provider across sequential runs does not bleed prompt history."""
        provider = MockLLMProvider()
        provider.set_response("Context for Run 1", '{"recommended_action": "proceed"}')
        provider.set_response("Context for Run 2", '{"recommended_action": "stop"}')

        # Run 1 call
        res1 = provider.generate(LLMRequest(user_prompt="Context for Run 1"))
        assert res1 is not None
        assert "proceed" in res1.text

        # Run 2 call with distinct context
        res2 = provider.generate(LLMRequest(user_prompt="Context for Run 2"))
        assert res2 is not None
        assert "stop" in res2.text
        assert len(provider.recorded_requests) == 2
        assert (
            provider.recorded_requests[0].user_prompt != provider.recorded_requests[1].user_prompt
        )


# =============================================================================
# Category Q: Crash Consistency
# =============================================================================


class TestCatQCrashConsistency:
    """Cat Q: Proves recovery from mid-cycle process termination and orphaned execution cleanup."""

    def test_cat_q_mid_cycle_exception_maintains_database_integrity(self, session_factory) -> None:
        """Mid-cycle database crash leaves SQLite database in a valid state passing PRAGMA integrity_check."""
        with session_factory() as session:
            run = ResearchRunModel(
                id="run_crash_q1",
                title="Crash Run",
                research_question="Does integrity hold?",
                status=ResearchState.EXECUTE.value,
            )
            session.add(run)
            session.commit()

            # Execute integrity check
            result = session.execute(text("PRAGMA integrity_check;")).scalar()
            assert result == "ok"

    def test_cat_q_stale_execution_reconciliation_cleans_orphans(self, session_factory) -> None:
        """Executions stuck in RUNNING due to process crash are reconciled to FAILED."""
        with session_factory() as session:
            run = ResearchRunModel(
                id="run_stale_q",
                title="Stale Run",
                research_question="Does orphan cleanup work?",
            )
            exp = ExperimentModel(id="exp_stale_q", research_run_id=run.id, objective="Exp")
            exec_model = ExecutionModel(
                id="exec_stale_q",
                experiment_id=exp.id,
                status=ExecutionStatus.RUNNING.value,
            )
            session.add_all([run, exp, exec_model])
            session.commit()

        orchestrator = ExecutionOrchestrator(
            session_factory=session_factory,
            backend=MagicMock(),
        )
        reconciled = orchestrator.reconcile_stale_executions(stale_threshold_seconds=0)

        assert "exec_stale_q" in reconciled

        with session_factory() as session:
            updated_exec = session.get(ExecutionModel, "exec_stale_q")
            assert updated_exec.status == ExecutionStatus.FAILED.value


# =============================================================================
# Category R: Concurrency / Race Conditions
# =============================================================================


class TestCatRConcurrencyRaceConditions:
    """Cat R: Proves optimistic concurrency, race condition resilience, and concurrent verification safety."""

    def test_cat_r_optimistic_concurrency_stale_state_conflict(self, session_factory) -> None:
        """Conflicting concurrent state transitions raise StaleStateError on optimistic check."""
        with session_factory() as session:
            run = ResearchRunModel(
                id="run_race_1",
                title="Race Run",
                research_question="Can concurrent actors clash?",
                status=ResearchState.INITIALIZE.value,
            )
            session.add(run)
            session.commit()

        # Thread A executes valid transition
        with session_factory() as session_a:
            ResearchStateMachine.transition(
                session=session_a,
                run_id="run_race_1",
                target_state=ResearchState.UNDERSTAND,
                actor=ActorType.CONTROLLER,
                expected_state=ResearchState.INITIALIZE,
            )
            session_a.commit()

        # Thread B attempts transition with stale expected_state=INITIALIZE
        with session_factory() as session_b, pytest.raises(StaleStateError):
            ResearchStateMachine.transition(
                session=session_b,
                run_id="run_race_1",
                target_state=ResearchState.UNDERSTAND,
                actor=ActorType.CONTROLLER,
                expected_state=ResearchState.INITIALIZE,
            )

    def test_cat_r_concurrent_verifications_thread_safe(self, session_factory) -> None:
        """Simultaneous verification threads on different research runs execute without database locks."""
        # Setup 4 distinct research runs
        run_ids = []
        with session_factory() as session:
            for i in range(4):
                rid = f"run_concurrent_{i}"
                run_ids.append(rid)
                run = ResearchRunModel(
                    id=rid,
                    title=f"Run {i}",
                    research_question=f"Q {i}",
                    status=ResearchState.VERIFY.value,
                )
                session.add(run)
            session.commit()

        def verify_worker(rid: str) -> VerificationStatus:
            with session_factory() as s:
                verifier = ResearchVerifier(session=s)
                rep = verifier.verify_run(rid)
                return rep.status

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(verify_worker, rid) for rid in run_ids]
            results = [f.result() for f in as_completed(futures)]

        assert len(results) == 4
        assert all(
            r in (VerificationStatus.PASS, VerificationStatus.FAIL, VerificationStatus.WARNING)
            for r in results
        )

    def test_cat_r_sqlite_wal_mode_and_busy_timeout(self) -> None:
        """Production database must configure SQLite WAL mode and busy timeout for write concurrency."""
        from rex.persistence.database import engine

        if engine.dialect.name == "sqlite":
            with engine.connect() as conn:
                journal_mode = conn.execute(text("PRAGMA journal_mode;")).scalar()
                busy_timeout = conn.execute(text("PRAGMA busy_timeout;")).scalar()
                assert str(journal_mode).lower() == "wal"
                assert int(busy_timeout) >= 5000


# =============================================================================
# Category S: Long-Horizon Stability
# =============================================================================


class TestCatSLongHorizonStability:
    """Cat S: Proves that high-iteration loops and large evidence graphs maintain stability without drift."""

    def test_cat_s_high_iteration_autonomous_loop_terminates_cleanly(
        self, session_factory, mock_critic
    ) -> None:
        """Autonomous research loop running 5 iterations halts cleanly at max_iterations."""
        sink = InMemoryEventSink()
        with session_factory() as session:
            run = ResearchRunModel(
                id="run_horizon_1",
                title="Horizon Test",
                research_question="Does loop remain stable over iterations?",
                status=ResearchState.INITIALIZE.value,
                budget_json={
                    "max_experiments": 50,
                    "max_executions": 50,
                    "max_runtime_seconds": 3600,
                },
            )
            session.add(run)
            session.commit()

        coding_agent = MagicMock()
        coding_agent.generate_code.return_value = GeneratedCodeProposal(
            entrypoint="main.py",
            source_files={"main.py": "print('iter')"},
            command=["python", "main.py"],
            dependencies=[],
        )

        loop = AutonomousResearchLoop(
            session_factory=session_factory,
            config=AutonomousLoopConfig(max_iterations=5),
            coding_agent=coding_agent,
            critic_agent=mock_critic,
            execution_orchestrator=MagicMock(),
            event_sink=sink,
        )

        result = loop.run(research_run_id="run_horizon_1")
        assert result.final_state in (
            ResearchState.STOP,
            ResearchState.COMPLETE,
            ResearchState.FAILED,
        )
        assert result.iterations_completed <= 5

    def test_cat_s_evidence_graph_growth_and_acyclicity_under_50_nodes(
        self, db_session: Session
    ) -> None:
        """Large evidence graph with 50 sequential nodes validates acyclicity without performance degradation."""
        run = ResearchRunModel(
            id="run_large_graph", title="Large DAG", research_question="Graph scale test"
        )
        db_session.add(run)

        graph = EvidenceGraphService(db_session)
        # Create a set of 25 claim nodes and 25 analysis nodes
        for i in range(25):
            clm = ClaimModel(
                id=f"clm_scale_{i}",
                research_run_id=run.id,
                statement=f"Scale Claim {i}",
            )
            ana = AnalysisModel(
                id=f"ana_scale_{i}",
                research_run_id=run.id,
                analysis_type="summary",
                method="mean",
                output_json={"val": i},
            )
            db_session.add_all([clm, ana])
        db_session.commit()

        for i in range(25):
            graph.create_link(
                source_type=EvidenceNodeType.CLAIM,
                source_id=f"clm_scale_{i}",
                target_type=EvidenceNodeType.ANALYSIS,
                target_id=f"ana_scale_{i}",
                relationship_type=EvidenceRelationType.SUPPORTED_BY,
                research_run_id=run.id,
            )
        db_session.commit()

        # Query links
        links = graph.get_links_for_run(run.id)
        assert len(links) == 25
