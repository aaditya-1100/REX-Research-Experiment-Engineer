"""Systematic Evidence Corruption & Tamper Evaluation Harness (REX-043).

Deliberately injects data tampering across results, disk artifacts, evidence graph links,
claims, and status flags, asserting that the deterministic verifier detects 100% of corruptions
with non-zero exit codes.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from rex.domain.models import EvidenceNodeType, EvidenceRelationType
from rex.evaluation.benchmark import ToyBenchmarkTask
from rex.evaluation.models import (
    EvaluationCaseResult,
    EvaluationStatus,
)
from rex.evidence.graph import CrossRunEvidenceError, EvidenceGraphService
from rex.evidence.verifier import DeterministicVerifier, VerificationStatus
from rex.persistence.models import (
    ArtifactModel,
    ClaimModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    ResultModel,
)


@dataclass
class CorruptionScenarioOutcome:
    """Outcome of an adversarial corruption scenario."""

    scenario_name: str
    tamper_applied: str
    detection_successful: bool
    verification_status: VerificationStatus
    error_message: str
    exit_code: int


class EvidenceCorruptionHarness:
    """Orchestrates adversarial evidence corruption tests (REX-043)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def setup_baseline_run(self) -> str:
        """Create a fresh, cleanly verified benchmark run to use for corruption."""
        task = ToyBenchmarkTask(
            task_name="corruption_test_baseline", num_samples=50, random_seed=123
        )
        report = task.run_benchmark(self.session)
        assert report.verification_status in (
            VerificationStatus.PASS,
            VerificationStatus.VERIFIED,
        ), "Baseline run must verify cleanly"
        return report.run_id

    def test_artifact_tampering(self, run_id: str | None = None) -> EvaluationCaseResult:
        """Tamper Scenario 1: Modify bytes of an artifact on disk without updating recorded SHA-256."""
        target_run_id = run_id or self.setup_baseline_run()
        start_time = time.perf_counter()

        artifact = (
            self.session.query(ArtifactModel)
            .filter(ArtifactModel.research_run_id == target_run_id)
            .first()
        )
        assert artifact is not None, "Baseline must have at least one artifact"

        file_path = Path(artifact.path)
        original_bytes = file_path.read_bytes()
        try:
            # Deliberately modify file on disk
            with open(file_path, "ab") as f:
                f.write(b"\n# TAMPERED_CORRUPTION_PAYLOAD_TEST_REX_043")

            verifier = DeterministicVerifier(session=self.session)
            report = verifier.verify(target_run_id)

            passed = (report.status == VerificationStatus.FAIL) and any(
                "hash mismatch" in err.lower() or "cryptographic" in err.lower()
                for err in report.errors
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            return EvaluationCaseResult(
                id=f"case_artifact_tamper_{target_run_id[:8]}",
                case_name="REX-043: Artifact Byte Tamper Detection",
                suite="security_corruption",
                status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=1 if passed else 0,
                assertions_failed=0 if passed else 1,
                failure_reason=None
                if passed
                else f"Tampered artifact was not detected as FAIL: {report.status}",
                details={"errors": report.errors, "artifact_id": artifact.id},
            )
        finally:
            file_path.write_bytes(original_bytes)

    def test_metric_result_tampering(self, run_id: str | None = None) -> EvaluationCaseResult:
        """Tamper Scenario 2: Modify result metrics in database to mismatch recorded calculations."""
        target_run_id = run_id or self.setup_baseline_run()
        start_time = time.perf_counter()

        result = (
            self.session.query(ResultModel)
            .join(ExecutionModel, ResultModel.execution_id == ExecutionModel.id)
            .join(ExperimentModel, ExecutionModel.experiment_id == ExperimentModel.id)
            .filter(
                ExperimentModel.research_run_id == target_run_id, ResultModel.metric_name == "mse"
            )
            .first()
        )
        assert result is not None, "Baseline must have at least one mse result"

        orig_val = result.metric_value
        orig_json = copy.deepcopy(result.result_json)
        try:
            # Deliberately falsify result metrics in the DB
            result.metric_value = 999.999
            result.result_json = {"mse": 999.999}
            self.session.flush()

            verifier = DeterministicVerifier(session=self.session)
            report = verifier.verify(target_run_id)

            # Verification should fail because claim or integrity fails
            passed = (report.status == VerificationStatus.FAIL) and any(
                "mismatch" in err.lower() for err in report.errors
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            return EvaluationCaseResult(
                id=f"case_metric_tamper_{target_run_id[:8]}",
                case_name="REX-043: Result Metric Falsification Detection",
                suite="security_corruption",
                status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=1 if passed else 0,
                assertions_failed=0 if passed else 1,
                failure_reason=None if passed else "Tampered metric was not detected as FAIL",
                details={"errors": report.errors, "result_id": result.id},
            )
        finally:
            result.metric_value = orig_val
            result.result_json = orig_json
            self.session.flush()

    def test_broken_evidence_link(self, run_id: str | None = None) -> EvaluationCaseResult:
        """Tamper Scenario 3: Remove an evidence link to break the lineage path."""
        target_run_id = run_id or self.setup_baseline_run()
        start_time = time.perf_counter()

        claim = (
            self.session.query(ClaimModel)
            .filter(ClaimModel.research_run_id == target_run_id)
            .first()
        )
        assert claim is not None, "Baseline must have at least one claim"

        links = (
            self.session.query(EvidenceLinkModel)
            .filter(
                (EvidenceLinkModel.research_run_id == target_run_id)
                & (
                    (EvidenceLinkModel.claim_id == claim.id)
                    | (EvidenceLinkModel.source_id == claim.id)
                    | (EvidenceLinkModel.target_id == claim.id)
                )
            )
            .all()
        )
        assert len(links) > 0, "Claim must have supporting links"

        backup_links = [
            {
                "source_type": link.source_type,
                "source_id": link.source_id,
                "target_type": link.target_type,
                "target_id": link.target_id,
                "relationship_type": link.relationship_type,
                "research_run_id": link.research_run_id,
                "claim_id": link.claim_id,
            }
            for link in links
        ]
        try:
            for link in links:
                self.session.delete(link)
            self.session.flush()

            verifier = DeterministicVerifier(session=self.session)
            report = verifier.verify(target_run_id)

            passed = (report.status == VerificationStatus.FAIL) and any(
                "unsupported" in err.lower() or "missing" in err.lower() or "lineage" in err.lower()
                for err in report.errors
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            return EvaluationCaseResult(
                id=f"case_broken_link_{target_run_id[:8]}",
                case_name="REX-043: Broken Evidence DAG Linkage Detection",
                suite="security_corruption",
                status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=1 if passed else 0,
                assertions_failed=0 if passed else 1,
                failure_reason=None if passed else "Broken evidence link was not detected as FAIL",
                details={"errors": report.errors},
            )
        finally:
            for b_link in backup_links:
                self.session.add(EvidenceLinkModel(**b_link))
            self.session.flush()

    def test_unsupported_claim_injection(self, run_id: str | None = None) -> EvaluationCaseResult:
        """Tamper Scenario 4: Insert a claim asserting a hallucinated metric not in any result."""
        target_run_id = run_id or self.setup_baseline_run()
        start_time = time.perf_counter()

        unsupported_claim = ClaimModel(
            research_run_id=target_run_id,
            statement="The algorithm achieved a hallucinated accuracy of 99.999% and zero test loss.",
            claim_type="empirical",
            status="proposed",
        )
        self.session.add(unsupported_claim)
        self.session.flush()

        try:
            verifier = DeterministicVerifier(session=self.session)
            report = verifier.verify(target_run_id)

            passed = (report.status == VerificationStatus.FAIL) and any(
                "unsupported" in err.lower()
                or "missing evidence" in err.lower()
                or "lineage is broken" in err.lower()
                for err in report.errors
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            return EvaluationCaseResult(
                id=f"case_unsupported_claim_{target_run_id[:8]}",
                case_name="REX-043: Ungrounded Claim Injection Detection",
                suite="security_corruption",
                status=EvaluationStatus.PASSED if passed else EvaluationStatus.FAILED,
                duration_ms=round(duration_ms, 2),
                assertions_passed=1 if passed else 0,
                assertions_failed=0 if passed else 1,
                failure_reason=None if passed else "Unsupported claim was not detected as FAIL",
                details={"errors": report.errors, "claim_id": unsupported_claim.id},
            )
        finally:
            self.session.delete(unsupported_claim)
            self.session.flush()

    def test_cross_run_evidence_rejection(self) -> EvaluationCaseResult:
        """Tamper Scenario 5: Attempt to link evidence across two different research runs."""
        start_time = time.perf_counter()
        run1 = self.setup_baseline_run()
        run2 = self.setup_baseline_run()

        claim1 = self.session.query(ClaimModel).filter(ClaimModel.research_run_id == run1).first()
        claim2 = self.session.query(ClaimModel).filter(ClaimModel.research_run_id == run2).first()
        assert claim1 is not None and claim2 is not None

        graph_service = EvidenceGraphService(self.session)
        rejected = False
        try:
            # Attempt to add cross-run edge: claim1 (from run1) -> claim2 (from run2)
            graph_service.create_link(
                source_type=EvidenceNodeType.CLAIM,
                source_id=claim1.id,
                target_type=EvidenceNodeType.CLAIM,
                target_id=claim2.id,
                relationship_type=EvidenceRelationType.REFINES,
                research_run_id=run1,
            )
        except (CrossRunEvidenceError, ValueError, KeyError):
            rejected = True

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        return EvaluationCaseResult(
            id="case_cross_run_rejection",
            case_name="REX-043: Cross-Run Evidence Linkage Rejection",
            suite="security_corruption",
            status=EvaluationStatus.PASSED if rejected else EvaluationStatus.FAILED,
            duration_ms=round(duration_ms, 2),
            assertions_passed=1 if rejected else 0,
            assertions_failed=0 if rejected else 1,
            failure_reason=None if rejected else "Cross-run link was not rejected",
            details={"run1": run1, "run2": run2},
        )

    def run_all_corruption_tests(self) -> list[EvaluationCaseResult]:
        """Execute the complete REX-043 corruption test suite."""
        run_id = self.setup_baseline_run()
        return [
            self.test_artifact_tampering(run_id),
            self.test_metric_result_tampering(run_id),
            self.test_broken_evidence_link(run_id),
            self.test_unsupported_claim_injection(run_id),
            self.test_cross_run_evidence_rejection(),
        ]
