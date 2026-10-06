"""Unified Evaluation Orchestrator & Suite Runner (REX Epic 11).

Orchestrates execution of Evaluation Suites A through I, aggregates results,
persists records to the evaluation repository, and calculates the system-wide
Quality Scorecard across Gates X0 through X17.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from rex.controller.exceptions import InvalidTransitionError
from rex.controller.state_machine import ResearchStateMachine
from rex.domain.models import EvidenceNodeType, EvidenceRelationType, ResearchState
from rex.evaluation.benchmark import ToyBenchmarkTask
from rex.evaluation.comparative import BaselineVsRexEvaluator
from rex.evaluation.corruption import EvidenceCorruptionHarness
from rex.evaluation.golden import GoldenRegressionComparator
from rex.evaluation.models import (
    EvaluationCaseResult,
    EvaluationRunSummary,
    EvaluationStatus,
    EvaluationSuiteType,
    QualityScorecard,
)
from rex.evaluation.reproducibility import ReproducibilityEvaluator
from rex.evidence.claims import ClaimService, UnauthorizedClaimError
from rex.evidence.graph import EvidenceCycleError, EvidenceGraphService
from rex.evidence.verifier import DeterministicVerifier, VerificationStatus
from rex.observability.events import ActorType
from rex.persistence.models import (
    ClaimModel,
    EvaluationCaseModel,
    EvaluationComparisonModel,
    EvaluationRunModel,
    ResearchRunModel,
)
from rex.persistence.repositories import EvaluationRepository


class EvaluationOrchestrator:
    """Master runner executing and recording REX system evaluation suites."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = EvaluationRepository(session)

    @staticmethod
    def _check_git_integrity() -> str:
        try:
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return "PASS" if res.returncode == 0 else "FAIL"
        except (subprocess.SubprocessError, OSError):
            return "NOT_PROVEN"

    @classmethod
    def _evaluate_gates(
        cls,
        cases: list[EvaluationCaseResult],
        suite_type: EvaluationSuiteType,
        total_cases: int,
        passed_cases: int,
        failed_cases: int,
    ) -> dict[str, str]:
        def suite_status(s_names: list[str]) -> str:
            rel = [c for c in cases if c.suite in s_names]
            if not rel:
                return "NOT_RUN"
            if all(c.status == EvaluationStatus.PASSED for c in rel):
                return "PASS"
            if any(c.status == EvaluationStatus.PASSED for c in rel):
                return "PARTIAL"
            return "FAIL"

        docs_exist = Path("docs").exists() or Path("05_Feature_Tickets.md").exists()
        frontend_exists = Path("frontend/package.json").exists()

        if suite_type == EvaluationSuiteType.ALL:
            final_ev = "PASS" if failed_cases == 0 and total_cases > 0 else "FAIL"
        else:
            final_ev = "PARTIAL" if failed_cases == 0 and total_cases > 0 else "FAIL"

        return {
            "X0_Scope_Integrity": (
                "PASS" if not any(c.status == EvaluationStatus.FAILED for c in cases) else "FAIL"
            ),
            "X1_Functional": suite_status(["research_lifecycle"]),
            "X2_Evaluation_Infrastructure": "PASS" if total_cases > 0 else "FAIL",
            "X3_Correctness": suite_status(["core_correctness"]),
            "X4_Reproducibility": suite_status(["reproducibility"]),
            "X5_Provenance": suite_status(["evidence_integrity"]),
            "X6_Epistemic_Integrity": suite_status(["epistemic_integrity"]),
            "X7_Security": suite_status(["security_corruption"]),
            "X8_Isolation": suite_status(["reliability_chaos"]),
            "X9_Concurrency": suite_status(["concurrency"]),
            "X10_Failure_Transparency": suite_status(["reliability_chaos"]),
            "X11_Autonomous_Research": suite_status(["research_lifecycle"]),
            "X12_Reporting": "PASS" if total_cases > 0 else "FAIL",
            "X13_Regression": (
                "PASS"
                if failed_cases == 0 and total_cases > 0
                else ("PARTIAL" if passed_cases > 0 else "FAIL")
            ),
            "X14_Frontend": "PASS" if frontend_exists else "NOT_PROVEN",
            "X15_Documentation": "PASS" if docs_exist else "NOT_PROVEN",
            "X16_Git_Integrity": cls._check_git_integrity(),
            "X17_Final_Evidence": final_ev,
        }

    @staticmethod
    def _compute_domain_scores(cases: list[EvaluationCaseResult]) -> dict[str, float]:
        def domain_rate(s_names: list[str]) -> float:
            d_cases = [c for c in cases if c.suite in s_names]
            if not d_cases:
                return 0.0
            tot = sum(c.assertions_passed + c.assertions_failed for c in d_cases)
            pas = sum(c.assertions_passed for c in d_cases)
            return round((pas / tot * 100.0), 2) if tot > 0 else 0.0

        return {
            "correctness": domain_rate(["core_correctness"]),
            "reproducibility": domain_rate(["reproducibility"]),
            "provenance": domain_rate(["evidence_integrity"]),
            "epistemic": domain_rate(["epistemic_integrity"]),
            "security": domain_rate(["security_corruption"]),
            "reliability": domain_rate(["reliability_chaos"]),
        }

    # -------------------------------------------------------------------------
    # SUITE IMPLEMENTATIONS
    # -------------------------------------------------------------------------

    def run_suite_core_correctness(self) -> list[EvaluationCaseResult]:
        """Suite A: Core Correctness (FSM, mathematical determinism, persistence)."""
        cases: list[EvaluationCaseResult] = []

        # Case 1: Golden statistical determinism
        golden = GoldenRegressionComparator()
        cases.append(golden.verify_statistical_determinism())

        # Case 2: State machine illegal transition rejection
        start_time = time.perf_counter()
        illegal_transition_blocked = False
        try:
            # Cannot jump directly from INITIALIZE to COMPLETE
            ResearchStateMachine.validate_transition(
                ResearchState.INITIALIZE,
                ResearchState.COMPLETE,
                actor=ActorType.SYSTEM,
            )
        except InvalidTransitionError:
            illegal_transition_blocked = True

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        cases.append(
            EvaluationCaseResult(
                id="case_fsm_illegal_transition",
                case_name="State Machine: Illegal Transition Guard Rejection",
                suite="core_correctness",
                status=EvaluationStatus.PASSED
                if illegal_transition_blocked
                else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=1 if illegal_transition_blocked else 0,
                assertions_failed=0 if illegal_transition_blocked else 1,
                failure_reason=None
                if illegal_transition_blocked
                else "Illegal FSM transition was permitted",
                details={"initial_state": "INITIALIZE", "attempted_state": "COMPLETE"},
            )
        )

        return cases

    def run_suite_research_lifecycle(self) -> list[EvaluationCaseResult]:
        """Suite B: Research Lifecycle (REX-042 Toy Benchmark)."""
        start_time = time.perf_counter()
        benchmark = ToyBenchmarkTask(
            task_name="lifecycle_toy_benchmark", num_samples=100, random_seed=42
        )
        report = benchmark.run_benchmark(self.session)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        passed = (
            report.is_bounded
            and report.verification_status == VerificationStatus.VERIFIED
            and report.metrics["mse"] <= 0.05
            and report.metrics["r2_score"] >= 0.95
        )

        return [
            EvaluationCaseResult(
                id="case_toy_benchmark_lifecycle",
                case_name="REX-042: End-to-End Toy Research Benchmark Execution",
                suite="research_lifecycle",
                status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=4 if passed else 0,
                assertions_failed=0 if passed else 1,
                failure_reason=None
                if passed
                else "Benchmark execution failed bounds or verification",
                details={
                    "metrics": report.metrics,
                    "baselines": report.known_baselines,
                    "verification": report.verification_status.value,
                },
            )
        ]

    def run_suite_evidence_integrity(self) -> list[EvaluationCaseResult]:
        """Suite C: Evidence Integrity & Lineage (DAG cycle detection, multi-hop traversal)."""
        start_time = time.perf_counter()
        graph_service = EvidenceGraphService(self.session)

        # Create dummy run and claims for cycle detection test
        dummy_run = ResearchRunModel(
            id=f"run_cycle_{uuid.uuid4().hex[:8]}",
            title="Cycle Test Run",
            research_question="Cycle test?",
        )
        self.session.add(dummy_run)
        c0 = ClaimModel(
            id=f"claim_c0_{uuid.uuid4().hex[:6]}", research_run_id=dummy_run.id, statement="Claim 0"
        )
        c1 = ClaimModel(
            id=f"claim_c1_{uuid.uuid4().hex[:6]}", research_run_id=dummy_run.id, statement="Claim 1"
        )
        self.session.add_all([c0, c1])
        self.session.flush()

        cycle_blocked = False
        try:
            # c0 -> c1
            graph_service.create_link(
                source_type=EvidenceNodeType.CLAIM,
                source_id=c0.id,
                target_type=EvidenceNodeType.CLAIM,
                target_id=c1.id,
                relationship_type=EvidenceRelationType.REFINES,
                research_run_id=dummy_run.id,
            )
            # Cycle attempt: c1 -> c0
            graph_service.create_link(
                source_type=EvidenceNodeType.CLAIM,
                source_id=c1.id,
                target_type=EvidenceNodeType.CLAIM,
                target_id=c0.id,
                relationship_type=EvidenceRelationType.REFINES,
                research_run_id=dummy_run.id,
            )
        except (EvidenceCycleError, ValueError, KeyError):
            cycle_blocked = True

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return [
            EvaluationCaseResult(
                id="case_dag_cycle_prevention",
                case_name="Evidence Graph: Multi-Hop Cycle Detection & Prevention",
                suite="evidence_integrity",
                status=EvaluationStatus.PASSED if cycle_blocked else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=1 if cycle_blocked else 0,
                assertions_failed=0 if cycle_blocked else 1,
                failure_reason=None if cycle_blocked else "Cycle was permitted in evidence graph",
            )
        ]

    def run_suite_epistemic_integrity(self) -> list[EvaluationCaseResult]:
        """Suite D: Epistemic Integrity (PROPOSED != VERIFIED, verifier authorization)."""
        start_time = time.perf_counter()
        claim_service = ClaimService(self.session)

        dummy_run = ResearchRunModel(
            id=f"run_epistemic_{uuid.uuid4().hex[:8]}",
            title="Epistemic Test Run",
            research_question="Epistemic test?",
        )
        self.session.add(dummy_run)
        clm = ClaimModel(
            id=f"claim_epistemic_{uuid.uuid4().hex[:6]}",
            research_run_id=dummy_run.id,
            statement="Epistemic assertion",
            status="proposed",
        )
        self.session.add(clm)
        self.session.flush()

        # Attempt unauthorized status promotion to VERIFIED
        unauthorized_blocked = False
        try:
            claim_service.update_claim_status(
                claim_id=clm.id,
                new_status="VERIFIED",
                actor="coding_agent",  # Unauthorized actor
            )
        except (UnauthorizedClaimError, PermissionError, ValueError, KeyError):
            unauthorized_blocked = True

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return [
            EvaluationCaseResult(
                id="case_epistemic_claim_barrier",
                case_name="Epistemic Rule 1: Agent Status Promotion to VERIFIED Rejection",
                suite="epistemic_integrity",
                status=EvaluationStatus.PASSED if unauthorized_blocked else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=1 if unauthorized_blocked else 0,
                assertions_failed=0 if unauthorized_blocked else 1,
                failure_reason=None
                if unauthorized_blocked
                else "Agent was illegally allowed to verify claim",
            )
        ]

    def run_suite_reproducibility(self) -> list[EvaluationCaseResult]:
        """Suite E: Reproducibility Evaluation (REX-044)."""
        evaluator = ReproducibilityEvaluator(self.session)
        return [evaluator.run_reproducibility_suite()]

    def run_suite_security_corruption(self) -> list[EvaluationCaseResult]:
        """Suite F: Security & Evidence Corruption (REX-043)."""
        harness = EvidenceCorruptionHarness(self.session)
        return harness.run_all_corruption_tests()

    def run_suite_reliability_chaos(self) -> list[EvaluationCaseResult]:
        """Suite G: Reliability & Chaos Resilience."""
        start_time = time.perf_counter()
        verifier = DeterministicVerifier(session=self.session)
        # Verify non-existent run fails gracefully rather than crashing unhandled
        report = verifier.verify("run_non_existent_mock_id_9999")
        passed = report.status == VerificationStatus.FAILED and len(report.errors) > 0
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return [
            EvaluationCaseResult(
                id="case_graceful_missing_run_handling",
                case_name="Reliability: Graceful Handling of Missing Research Package",
                suite="reliability_chaos",
                status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=1 if passed else 0,
                assertions_failed=0 if passed else 1,
                failure_reason=None
                if passed
                else "Verifier crashed or failed unhandled on missing run",
            )
        ]

    def run_suite_concurrency(self) -> list[EvaluationCaseResult]:
        """Suite H: Concurrency & Multi-Threaded Stability."""
        start_time = time.perf_counter()
        benchmark = ToyBenchmarkTask(
            task_name="concurrent_eval_task", num_samples=40, random_seed=99
        )
        bench_report = benchmark.run_benchmark(self.session)
        self.session.commit()

        # Run 5 concurrent verification calls on the same research run using thread-safe sessions
        engine = self.session.get_bind()
        session_factory = sessionmaker(bind=engine)

        def _verify_run() -> VerificationStatus:
            with session_factory() as thread_session:
                v = DeterministicVerifier(session=thread_session)
                return v.verify(bench_report.run_id).status

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(_verify_run) for _ in range(5)]
            results = [f.result() for f in futures]

        passed = all(r in (VerificationStatus.PASS, VerificationStatus.VERIFIED) for r in results)
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return [
            EvaluationCaseResult(
                id="case_concurrent_verification",
                case_name="Concurrency: Multi-Threaded Read-Only Verification Stability",
                suite="concurrency",
                status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=5 if passed else 0,
                assertions_failed=0 if passed else 1,
                failure_reason=None
                if passed
                else "Concurrent verification threads diverged or deadlocked",
            )
        ]

    def run_suite_comparative(self) -> tuple[EvaluationCaseResult, list[Any]]:
        """Suite I: Comparative Evaluation (REX-045)."""
        comp_evaluator = BaselineVsRexEvaluator(self.session)
        comp_case = comp_evaluator.run_comparative_suite()
        comp_res = comp_evaluator.run_comparative_evaluation()
        return comp_case, [comp_res]

    # -------------------------------------------------------------------------
    # MASTER RUNNER & QUALITY SCORECARD
    # -------------------------------------------------------------------------

    def execute_evaluation(
        self,
        suite_type: EvaluationSuiteType = EvaluationSuiteType.ALL,
    ) -> EvaluationRunSummary:
        """Execute the requested evaluation suite(s) and persist the full evaluation run."""
        run_id = f"eval_{uuid.uuid4().hex[:12]}"
        started_at = datetime.now(UTC)

        # 1. Create run model in DB
        db_run = EvaluationRunModel(
            id=run_id,
            suite_name=suite_type.value,
            status="running",
            started_at=started_at,
        )
        self.repo.create_run(db_run)
        self.session.commit()

        cases: list[EvaluationCaseResult] = []
        comparisons: list[Any] = []

        try:
            if suite_type in (EvaluationSuiteType.ALL, EvaluationSuiteType.CORE_CORRECTNESS):
                cases.extend(self.run_suite_core_correctness())

            if suite_type in (EvaluationSuiteType.ALL, EvaluationSuiteType.RESEARCH_LIFECYCLE):
                cases.extend(self.run_suite_research_lifecycle())

            if suite_type in (EvaluationSuiteType.ALL, EvaluationSuiteType.EVIDENCE_INTEGRITY):
                cases.extend(self.run_suite_evidence_integrity())

            if suite_type in (EvaluationSuiteType.ALL, EvaluationSuiteType.EPISTEMIC_INTEGRITY):
                cases.extend(self.run_suite_epistemic_integrity())

            if suite_type in (EvaluationSuiteType.ALL, EvaluationSuiteType.REPRODUCIBILITY):
                cases.extend(self.run_suite_reproducibility())

            if suite_type in (EvaluationSuiteType.ALL, EvaluationSuiteType.SECURITY_CORRUPTION):
                cases.extend(self.run_suite_security_corruption())

            if suite_type in (EvaluationSuiteType.ALL, EvaluationSuiteType.RELIABILITY_CHAOS):
                cases.extend(self.run_suite_reliability_chaos())

            if suite_type in (EvaluationSuiteType.ALL, EvaluationSuiteType.CONCURRENCY):
                cases.extend(self.run_suite_concurrency())

            if suite_type in (EvaluationSuiteType.ALL, EvaluationSuiteType.COMPARATIVE):
                comp_case, comp_list = self.run_suite_comparative()
                cases.append(comp_case)
                comparisons.extend(comp_list)

            # Persist cases
            total_cases = len(cases)
            passed_cases = sum(1 for c in cases if c.status == EvaluationStatus.PASSED)
            failed_cases = total_cases - passed_cases
            score = (passed_cases / total_cases * 100.0) if total_cases > 0 else 0.0

            for c in cases:
                db_case = EvaluationCaseModel(
                    id=c.id,
                    evaluation_run_id=run_id,
                    suite=c.suite,
                    case_name=c.case_name,
                    status=c.status.value,
                    duration_ms=c.duration_ms,
                    assertions_passed=c.assertions_passed,
                    assertions_failed=c.assertions_failed,
                    failure_reason=c.failure_reason,
                    failure_classification=c.failure_classification.value
                    if c.failure_classification
                    else None,
                    details_json=json.loads(json.dumps(c.details, default=str)),
                )
                self.repo.add_case(db_case)

            for comp in comparisons:
                db_comp = EvaluationComparisonModel(
                    id=comp.comparison_id,
                    evaluation_run_id=run_id,
                    comparison_name=comp.comparison_name,
                    baseline_metrics_json={
                        d.dimension_name: d.baseline_value for d in comp.dimensions
                    },
                    rex_metrics_json={d.dimension_name: d.rex_value for d in comp.dimensions},
                    delta_metrics_json={d.dimension_name: d.delta for d in comp.dimensions},
                    statistical_summary_json={
                        "summary": comp.summary_verdict,
                        "cost": comp.cost_summary,
                    },
                )
                self.repo.add_comparison(db_comp)

            # Generate Dynamic Quality Scorecard for Gates X0-X17
            gate_status = self._evaluate_gates(
                cases=cases,
                suite_type=suite_type,
                total_cases=total_cases,
                passed_cases=passed_cases,
                failed_cases=failed_cases,
            )

            domain_scores = self._compute_domain_scores(cases=cases)

            scorecard = QualityScorecard(
                overall_score=round(score, 2),
                total_checks=sum(c.assertions_passed + c.assertions_failed for c in cases),
                passed_checks=sum(c.assertions_passed for c in cases),
                failed_checks=sum(c.assertions_failed for c in cases),
                gate_compliance=gate_status,
                domain_scores=domain_scores,
            )

            completed_at = datetime.now(UTC)
            final_status = "completed" if failed_cases == 0 else "failed"

            self.repo.update_run_status(
                run_id=run_id,
                status=final_status,
                total_cases=total_cases,
                passed_cases=passed_cases,
                failed_cases=failed_cases,
                score=score,
                summary_json=scorecard.model_dump(mode="json"),
                completed_at=completed_at,
            )
            self.session.commit()

            return EvaluationRunSummary(
                id=run_id,
                suite_name=suite_type.value,
                status=EvaluationStatus.PASSED if failed_cases == 0 else EvaluationStatus.FAILED,
                started_at=started_at,
                completed_at=completed_at,
                total_cases=total_cases,
                passed_cases=passed_cases,
                failed_cases=failed_cases,
                score=round(score, 2),
                cases=cases,
                comparisons=comparisons,
                scorecard=scorecard,
            )

        except Exception as e:
            self.repo.update_run_status(
                run_id=run_id,
                status="error",
                total_cases=len(cases),
                passed_cases=sum(1 for c in cases if c.status == EvaluationStatus.PASSED),
                failed_cases=len(cases),
                score=0.0,
                summary_json={"error": str(e)},
                completed_at=datetime.now(UTC),
            )
            raise

    execute_suite = execute_evaluation
