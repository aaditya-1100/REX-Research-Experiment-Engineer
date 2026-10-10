"""Adversarial Empirical Stress Tests for Scientific Self-Deception & Fail-Closed Gating.

Author: Challenger M2_2 (Track A Focus)
Tests:
1. Boundary conditions around statistical significance (p=0.0499 vs p=0.0500 vs p=0.0501).
2. Negative deltas claiming superiority / improvement (both with insignificant and significant p-values).
3. Seed omissions, unlinked seeds, phantom seeds, and cherry-picking detection.
4. Metric surrogacy, metric name discrepancies, and confounded causal claims.
5. Verifier fail-closed state transitions for Claims (INCONCLUSIVE / REJECTED) and Hypotheses (INCONCLUSIVE / FALSIFIED).
6. Vulnerability reproducers documenting false verification and cherry-picking blindspots.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from rex.domain.models import ClaimStatus, HypothesisStatus
from rex.evidence.self_deception import (
    ScientificSelfDeceptionDetector,
    SelfDeceptionType,
)
from rex.evidence.verifier import ResearchVerifier, VerificationStatus
from rex.observability.events import ActorType
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)
from rex.persistence.models import (
    AnalysisModel,
    ClaimModel,
    EvidenceLinkModel,
    ExecutionModel,
    ExperimentModel,
    HypothesisModel,
    ResearchRunModel,
    ResultModel,
)


@pytest.fixture
def detector() -> ScientificSelfDeceptionDetector:
    return ScientificSelfDeceptionDetector(significance_threshold=0.05)


@pytest.fixture
def session_factory(tmp_path):
    """Isolated SQLite database for verifier integration tests."""
    db_file = tmp_path / "test_challenger_stress.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


class MockAnalysis:
    def __init__(self, output_json: dict):
        self.output_json = output_json


class MockResult:
    def __init__(self, metric_name: str, metric_value: float, result_json: dict | None = None):
        self.metric_name = metric_name
        self.metric_value = metric_value
        self.result_json = result_json or {}


class MockExecution:
    def __init__(self, configuration_json: dict | None = None, seed: int | None = None):
        self.configuration_json = configuration_json or {}
        self.seed = seed


class TestStatisticalSignificanceBoundaryConditions:
    """Stress testing the boundary around alpha = 0.05."""

    def test_p_boundary_just_below_threshold_passes(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """p = 0.0499 is strictly below 0.05, should pass significance gating."""
        report = detector.audit_claim(
            claim_statement="The proposed method outperforms baseline accuracy.",
            claim_metadata={"claim_type": "superiority"},
            analyses=[
                MockAnalysis(
                    {
                        "metric_name": "accuracy",
                        "delta": 0.03,
                        "p_value": 0.0499,
                    }
                )
            ],
        )
        assert report.is_grounded is True
        assert report.is_statistically_significant is True
        assert len(report.findings) == 0

    def test_p_boundary_exact_threshold_fails_closed(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """p = 0.0500 exactly meets threshold, should fail significance gating (p >= 0.05)."""
        report = detector.audit_claim(
            claim_statement="The proposed method outperforms baseline accuracy.",
            claim_metadata={"claim_type": "superiority"},
            analyses=[
                MockAnalysis(
                    {
                        "metric_name": "accuracy",
                        "delta": 0.03,
                        "p_value": 0.0500,
                    }
                )
            ],
        )
        assert report.is_grounded is False
        assert report.is_statistically_significant is False
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.finding_type == SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE
        assert finding.recommended_claim_status == ClaimStatus.INCONCLUSIVE
        assert finding.recommended_hypothesis_status == HypothesisStatus.INCONCLUSIVE

    def test_p_boundary_just_above_threshold_fails_closed(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """p = 0.0501 is strictly above 0.05, must fail closed into INCONCLUSIVE."""
        report = detector.audit_claim(
            claim_statement="The proposed method improves accuracy over baseline.",
            claim_metadata={"claim_type": "superiority"},
            analyses=[
                MockAnalysis(
                    {
                        "metric_name": "accuracy",
                        "delta": 0.03,
                        "p_value": 0.0501,
                    }
                )
            ],
        )
        assert report.is_grounded is False
        assert report.is_statistically_significant is False
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.finding_type == SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE
        assert finding.recommended_claim_status == ClaimStatus.INCONCLUSIVE
        assert finding.recommended_hypothesis_status == HypothesisStatus.INCONCLUSIVE

    def test_custom_significance_threshold_boundary(self):
        """Custom threshold (alpha = 0.01): p=0.0099 passes, p=0.0101 fails closed."""
        strict_detector = ScientificSelfDeceptionDetector(significance_threshold=0.01)

        rep_pass = strict_detector.audit_claim(
            claim_statement="The model is superior.",
            claim_metadata={"claim_type": "superiority"},
            analyses=[MockAnalysis({"delta": 0.05, "p_value": 0.0099})],
        )
        assert rep_pass.is_grounded is True
        assert rep_pass.is_statistically_significant is True

        rep_fail = strict_detector.audit_claim(
            claim_statement="The model is superior.",
            claim_metadata={"claim_type": "superiority"},
            analyses=[MockAnalysis({"delta": 0.05, "p_value": 0.0101})],
        )
        assert rep_fail.is_grounded is False
        assert rep_fail.is_statistically_significant is False
        assert rep_fail.findings[0].recommended_claim_status == ClaimStatus.INCONCLUSIVE

    def test_p_value_key_aliases(self, detector: ScientificSelfDeceptionDetector):
        """Verifier extracts p-value from various standard key names."""
        for key in ("p", "pvalue", "p_val", "significance_p"):
            rep = detector.audit_claim(
                claim_statement="Method outperforms baseline.",
                claim_metadata={"claim_type": "superiority"},
                analyses=[MockAnalysis({"delta": 0.02, key: 0.15})],
            )
            assert rep.is_grounded is False
            assert rep.p_value == 0.15
            assert rep.is_statistically_significant is False

    def test_claim_states_statistically_significant_without_test(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Claim stating 'statistically significant' with no analysis test fails closed."""
        rep = detector.audit_claim(
            claim_statement="The results are statistically significant.",
            analyses=[MockAnalysis({"mean": 0.85})],
        )
        assert rep.is_grounded is False
        assert len(rep.findings) == 1
        assert rep.findings[0].finding_type == SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE
        assert rep.findings[0].recommended_claim_status == ClaimStatus.INCONCLUSIVE
        assert rep.findings[0].recommended_hypothesis_status == HypothesisStatus.INCONCLUSIVE


class TestNegativeDeltasClaimingSuperiority:
    """Stress testing negative deltas when claims assert superiority or improvement."""

    def test_negative_delta_insignificant_p_rejects_and_falsifies(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """p = 0.0501 with negative delta (-0.03) must fail closed into REJECTED / FALSIFIED."""
        report = detector.audit_claim(
            claim_statement="The new architecture outperforms previous models.",
            claim_metadata={"claim_type": "superiority"},
            analyses=[
                MockAnalysis(
                    {
                        "metric_name": "accuracy",
                        "delta": -0.03,
                        "p_value": 0.0501,
                    }
                )
            ],
        )
        assert report.is_grounded is False
        assert len(report.findings) == 1
        finding = report.findings[0]
        assert finding.finding_type == SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE
        assert finding.recommended_claim_status == ClaimStatus.REJECTED
        assert finding.recommended_hypothesis_status == HypothesisStatus.FALSIFIED

    def test_zero_delta_claiming_superiority_fails_closed(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Delta = 0.0 with p = 0.50 claiming superiority fails closed to INCONCLUSIVE."""
        report = detector.audit_claim(
            claim_statement="The proposed method improves accuracy.",
            claim_metadata={"claim_type": "superiority"},
            analyses=[
                MockAnalysis(
                    {
                        "metric_name": "accuracy",
                        "delta": 0.0,
                        "p_value": 0.50,
                    }
                )
            ],
        )
        assert report.is_grounded is False
        assert report.findings[0].recommended_claim_status == ClaimStatus.INCONCLUSIVE
        assert report.findings[0].recommended_hypothesis_status == HypothesisStatus.INCONCLUSIVE


class TestSeedOmissionAndCherryPicking:
    """Stress testing seed omission and cherry-picking detection."""

    def test_cherry_picked_seeds_rejects_claim_inconclusive_hypothesis(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Executed seeds 1..5, analysis only kept seeds 1 and 2."""
        report = detector.audit_claim(
            claim_statement="Accuracy is consistent.",
            executions=[MockExecution(seed=s) for s in range(1, 6)],
            analyses=[MockAnalysis({"seeds": [1, 2]})],
        )
        assert report.is_grounded is False
        assert report.is_seed_complete is False
        finding = next(
            f for f in report.findings if f.finding_type == SelfDeceptionType.CHERRY_PICKED_SEEDS
        )
        assert finding.recommended_claim_status == ClaimStatus.REJECTED
        assert finding.recommended_hypothesis_status == HypothesisStatus.INCONCLUSIVE
        assert set(finding.context["omitted_seeds"]) == {3, 4, 5}

    def test_seeds_in_configuration_json_and_result_json(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Seeds declared inside configuration_json['seeds'] and result_json['seed']."""
        executions = [
            MockExecution(configuration_json={"seeds": [101, 102, 103]}),
        ]
        results = [
            MockResult("accuracy", 0.9, result_json={"seed": 104}),
        ]
        analyses = [
            MockAnalysis({"seeds": [101, 102]}),
        ]
        report = detector.audit_claim(
            claim_statement="Model accuracy improved.",
            executions=executions,
            results=results,
            analyses=analyses,
        )
        assert report.is_grounded is False
        assert report.is_seed_complete is False
        finding = report.findings[0]
        assert set(finding.context["omitted_seeds"]) == {103, 104}

    def test_seed_list_alias_supported(self, detector: ScientificSelfDeceptionDetector):
        """Analysis using 'seed_list' instead of 'seeds' is recognized."""
        executions = [MockExecution(seed=1), MockExecution(seed=2)]
        analyses = [MockAnalysis({"seed_list": [1, 2]})]
        report = detector.audit_claim(
            claim_statement="Model test.",
            executions=executions,
            analyses=analyses,
        )
        assert report.is_grounded is True
        assert report.is_seed_complete is True


class TestMetricSurrogacyAndConfounding:
    """Stress testing train-as-proxy and multi-variable confounding."""

    def test_train_loss_surrogate_rejects_generalization_claim(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Claim asserting 'generalization' evaluated on 'train_loss' must fail closed."""
        results = [
            MockResult("train_loss", 0.22, {"split": "train"}),
            MockResult("train_loss", 0.18, {"split": "train"}),
        ]
        report = detector.audit_claim(
            claim_statement="The model demonstrates enhanced generalization on the benchmark.",
            results=results,
        )
        assert report.is_grounded is False
        finding = next(
            f
            for f in report.findings
            if f.finding_type == SelfDeceptionType.METRIC_SURROGATE_MISMATCH
        )
        assert finding.recommended_claim_status == ClaimStatus.REJECTED
        assert finding.recommended_hypothesis_status == HypothesisStatus.INCONCLUSIVE

    def test_test_split_present_does_not_flag_surrogate(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """When validation/test split metrics are present, surrogate flag is not raised."""
        results = [
            MockResult("train_loss", 0.22, {"split": "train"}),
            MockResult("val_loss", 0.25, {"split": "val"}),
        ]
        report = detector.audit_claim(
            claim_statement="The model demonstrates enhanced generalization on the benchmark.",
            results=results,
        )
        assert not any(
            f.finding_type == SelfDeceptionType.METRIC_SURROGATE_MISMATCH for f in report.findings
        )

    def test_causal_claim_with_multiple_variables_and_no_ablation_inconclusive(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Claim asserts 'causes' with 3 simultaneously shifted variables and no ablation."""
        exp_spec = {
            "variables": {"lr": 0.01, "layers": 12, "batch_size": 64},
            "analysis_methods": ["welch_t_test"],
        }
        report = detector.audit_claim(
            claim_statement="Lower error is caused by learning rate tuning.",
            experiment_spec=exp_spec,
        )
        assert report.is_grounded is False
        finding = next(
            f
            for f in report.findings
            if f.finding_type == SelfDeceptionType.CONFOUNDED_CAUSAL_CLAIM
        )
        assert finding.recommended_claim_status == ClaimStatus.INCONCLUSIVE
        assert finding.recommended_hypothesis_status == HypothesisStatus.INCONCLUSIVE

    def test_causal_claim_with_ablation_passes(self, detector: ScientificSelfDeceptionDetector):
        """Claim asserts causality with 3 variables, but experiment spec includes ablation."""
        exp_spec = {
            "variables": {"lr": 0.01, "layers": 12, "batch_size": 64},
            "analysis_methods": ["ablation_study", "welch_t_test"],
        }
        report = detector.audit_claim(
            claim_statement="Lower error is caused by learning rate tuning.",
            experiment_spec=exp_spec,
        )
        assert report.is_grounded is True
        assert len(report.findings) == 0


class TestVerifierFailClosedStateTransitions:
    """End-to-end integration tests with SQLite ResearchVerifier confirming strict fail-closed state transitions."""

    def _setup_pipeline(
        self,
        session,
        run_id: str,
        claim_stmt: str,
        claim_status: ClaimStatus = ClaimStatus.PROPOSED,
        claim_meta: dict | None = None,
        analysis_json: dict | None = None,
        result_metric: str = "accuracy",
        result_val: float = 0.85,
        result_json: dict | None = None,
        exec_seed: int = 42,
        exp_spec: dict | None = None,
    ):
        """Helper to construct complete 5-node empirical lineage in SQLite."""
        run = ResearchRunModel(id=run_id, research_question="Empirical stress test question")
        hyp = HypothesisModel(
            id=f"hyp_{run_id}",
            research_run_id=run_id,
            statement="Treatment outperforms baseline.",
            status=HypothesisStatus.PROPOSED.value,
            falsification_condition="Delta <= 0",
        )
        exp = ExperimentModel(
            id=f"exp_{run_id}",
            research_run_id=run_id,
            hypothesis_id=hyp.id,
            objective="Evaluate treatment",
            specification_json=exp_spec or {"name": "exp", "variables": {"lr": 0.01}},
        )
        exe = ExecutionModel(
            id=f"exec_{run_id}",
            experiment_id=exp.id,
            status="completed",
            exit_code=0,
            seed=exec_seed,
        )
        res = ResultModel(
            id=f"res_{run_id}",
            execution_id=exe.id,
            metric_name=result_metric,
            metric_value=result_val,
            result_json=result_json or {"seed": exec_seed},
        )
        an = AnalysisModel(
            id=f"an_{run_id}",
            research_run_id=run_id,
            analysis_type="welch_t_test",
            input_result_ids=[res.id],
            method="welch_t_test",
            output_json=analysis_json
            or {
                "accuracy": result_val,
                "delta": 0.05,
                "p_value": 0.01,
                "seeds": [exec_seed],
            },
        )
        clm = ClaimModel(
            id=f"clm_{run_id}",
            research_run_id=run_id,
            statement=claim_stmt,
            status=claim_status.value,
            metadata_json=claim_meta
            or {"metric_name": result_metric, "asserted_value": result_val},
        )

        lnk1 = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="analysis",
            target_id=an.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )
        lnk2 = EvidenceLinkModel(
            source_type="analysis",
            source_id=an.id,
            target_type="result",
            target_id=res.id,
            relationship_type="derived_from",
            research_run_id=run_id,
        )
        lnk3 = EvidenceLinkModel(
            source_type="result",
            source_id=res.id,
            target_type="execution",
            target_id=exe.id,
            relationship_type="produced_by",
            research_run_id=run_id,
        )
        lnk4 = EvidenceLinkModel(
            source_type="execution",
            source_id=exe.id,
            target_type="experiment",
            target_id=exp.id,
            relationship_type="instance_of",
            research_run_id=run_id,
        )
        lnk_res = EvidenceLinkModel(
            source_type="claim",
            source_id=clm.id,
            target_type="result",
            target_id=res.id,
            relationship_type="supported_by",
            research_run_id=run_id,
        )

        session.add_all([run, hyp, exp, exe, res, an, clm, lnk1, lnk2, lnk3, lnk4, lnk_res])
        session.commit()
        return run, hyp, exp, exe, res, an, clm

    def test_verifier_boundary_p_0501_fails_closed_inconclusive(
        self, session_factory: sessionmaker
    ):
        """At p = 0.0501, claim and hypothesis strictly transition to INCONCLUSIVE."""
        run_id = "run_boundary_0501"
        with get_db_session(session_factory) as session:
            self._setup_pipeline(
                session,
                run_id=run_id,
                claim_stmt="The treatment outperforms the baseline with accuracy 0.85.",
                analysis_json={
                    "accuracy": 0.85,
                    "delta": 0.03,
                    "p_value": 0.0501,
                    "seeds": [42],
                },
            )

        with get_db_session(session_factory) as session:
            verifier = ResearchVerifier(session=session)
            report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

            assert report.status == VerificationStatus.FAIL
            assert not report.is_passed
            assert any("fails significance gating" in err for err in report.errors)

            clm = session.get(ClaimModel, f"clm_{run_id}")
            assert clm.status == ClaimStatus.INCONCLUSIVE.value

            hyp = session.get(HypothesisModel, f"hyp_{run_id}")
            assert hyp.status == HypothesisStatus.INCONCLUSIVE.value

    def test_verifier_boundary_p_0499_passes(self, session_factory: sessionmaker):
        """At p = 0.0499, verification succeeds and claim transitions to VERIFIED."""
        run_id = "run_boundary_0499"
        with get_db_session(session_factory) as session:
            self._setup_pipeline(
                session,
                run_id=run_id,
                claim_stmt="The treatment outperforms baseline with accuracy 0.85.",
                analysis_json={
                    "accuracy": 0.85,
                    "delta": 0.03,
                    "p_value": 0.0499,
                    "seeds": [42],
                },
            )

        with get_db_session(session_factory) as session:
            verifier = ResearchVerifier(session=session)
            report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

            assert report.status == VerificationStatus.PASS
            assert report.is_passed
            assert len(report.errors) == 0

            clm = session.get(ClaimModel, f"clm_{run_id}")
            assert clm.status == ClaimStatus.VERIFIED.value

    def test_verifier_negative_delta_insignificant_rejects_and_falsifies(
        self, session_factory: sessionmaker
    ):
        """Degraded performance (delta = -0.04, p = 0.15) claiming superiority fails closed to REJECTED / FALSIFIED."""
        run_id = "run_neg_delta"
        with get_db_session(session_factory) as session:
            self._setup_pipeline(
                session,
                run_id=run_id,
                claim_stmt="Treatment outperforms baseline with accuracy 0.85.",
                analysis_json={
                    "accuracy": 0.85,
                    "delta": -0.04,
                    "p_value": 0.15,
                    "seeds": [42],
                },
            )

        with get_db_session(session_factory) as session:
            verifier = ResearchVerifier(session=session)
            report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

            assert report.status == VerificationStatus.FAIL
            clm = session.get(ClaimModel, f"clm_{run_id}")
            assert clm.status == ClaimStatus.REJECTED.value

            hyp = session.get(HypothesisModel, f"hyp_{run_id}")
            assert hyp.status == HypothesisStatus.FALSIFIED.value

    def test_verifier_preexisting_verified_claim_fails_closed_to_inconclusive_or_tampered(
        self, session_factory: sessionmaker
    ):
        """A claim previously marked VERIFIED that fails self-deception must NOT remain VERIFIED."""
        run_id = "run_preexisting_verified"
        with get_db_session(session_factory) as session:
            self._setup_pipeline(
                session,
                run_id=run_id,
                claim_stmt="Treatment outperforms baseline with accuracy 0.85.",
                claim_status=ClaimStatus.VERIFIED,  # Pre-existing status
                analysis_json={
                    "accuracy": 0.85,
                    "delta": 0.02,
                    "p_value": 0.20,  # Insignificant
                    "seeds": [42],
                },
            )

        with get_db_session(session_factory) as session:
            verifier = ResearchVerifier(session=session)
            report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

            assert report.status == VerificationStatus.FAIL
            clm = session.get(ClaimModel, f"clm_{run_id}")
            # Must have transitioned out of VERIFIED
            assert clm.status != ClaimStatus.VERIFIED.value
            assert clm.status in (
                ClaimStatus.INCONCLUSIVE.value,
                ClaimStatus.REJECTED.value,
                ClaimStatus.TAMPERED.value,
            )


class TestVulnerabilityReproducers:
    """Explicit adversarial reproducers documenting confirmed vulnerabilities in self_deception.py and verifier.py."""

    def test_reproducer_vuln1_negative_delta_with_significant_p_passes_detector(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Treatment is statistically significantly WORSE (delta = -0.05, p = 0.001).

        Expected: is_grounded should be False, finding LACKS_STATISTICAL_SIGNIFICANCE or DIRECTION_MISMATCH.
        Actual (Bug): is_grounded is True because delta < 0 check is nested inside `p >= alpha`.
        """
        report = detector.audit_claim(
            claim_statement="The proposed method significantly outperforms baseline.",
            claim_metadata={"claim_type": "superiority"},
            analyses=[MockAnalysis({"metric_name": "accuracy", "delta": -0.05, "p_value": 0.001})],
        )
        # Expected behavior: must NOT be grounded
        assert report.is_grounded is False

    def test_reproducer_vuln2_improves_vocabulary_bypasses_verifier(
        self, session_factory: sessionmaker
    ):
        """Claim using 'improves' with negative delta and p = 0.001 passes verification.

        Expected: VerificationStatus.FAIL and claim.status == REJECTED.
        Actual (Bug): VerificationStatus.PASS and claim.status == VERIFIED.
        """
        run_id = "run_vuln2_improves"
        with get_db_session(session_factory) as session:
            run = ResearchRunModel(id=run_id, research_question="Does treatment improve?")
            hyp = HypothesisModel(
                id=f"hyp_{run_id}",
                research_run_id=run_id,
                statement="Treatment improves performance.",
                status=HypothesisStatus.PROPOSED.value,
                falsification_condition="Delta <= 0",
            )
            exp = ExperimentModel(
                id=f"exp_{run_id}",
                research_run_id=run_id,
                hypothesis_id=hyp.id,
                objective="Evaluate treatment",
                specification_json={"name": "exp"},
            )
            exe = ExecutionModel(
                id=f"exec_{run_id}", experiment_id=exp.id, status="completed", exit_code=0, seed=42
            )
            res = ResultModel(
                id=f"res_{run_id}",
                execution_id=exe.id,
                metric_name="accuracy",
                metric_value=0.80,
                result_json={"seed": 42},
            )
            an = AnalysisModel(
                id=f"an_{run_id}",
                research_run_id=run_id,
                analysis_type="welch_t_test",
                input_result_ids=[res.id],
                method="welch_t_test",
                output_json={"accuracy": 0.80, "delta": -0.08, "p_value": 0.001, "seeds": [42]},
            )
            clm = ClaimModel(
                id=f"clm_{run_id}",
                research_run_id=run_id,
                statement="Treatment improves accuracy to 0.80.",
                status=ClaimStatus.PROPOSED.value,
                metadata_json={"metric_name": "accuracy", "asserted_value": 0.80},
            )

            lnk1 = EvidenceLinkModel(
                source_type="claim",
                source_id=clm.id,
                target_type="analysis",
                target_id=an.id,
                relationship_type="supported_by",
                research_run_id=run_id,
            )
            lnk2 = EvidenceLinkModel(
                source_type="analysis",
                source_id=an.id,
                target_type="result",
                target_id=res.id,
                relationship_type="derived_from",
                research_run_id=run_id,
            )
            lnk3 = EvidenceLinkModel(
                source_type="result",
                source_id=res.id,
                target_type="execution",
                target_id=exe.id,
                relationship_type="produced_by",
                research_run_id=run_id,
            )
            lnk4 = EvidenceLinkModel(
                source_type="execution",
                source_id=exe.id,
                target_type="experiment",
                target_id=exp.id,
                relationship_type="instance_of",
                research_run_id=run_id,
            )
            lnk_res = EvidenceLinkModel(
                source_type="claim",
                source_id=clm.id,
                target_type="result",
                target_id=res.id,
                relationship_type="supported_by",
                research_run_id=run_id,
            )

            session.add_all([run, hyp, exp, exe, res, an, clm, lnk1, lnk2, lnk3, lnk4, lnk_res])
            session.commit()

        with get_db_session(session_factory) as session:
            verifier = ResearchVerifier(session=session)
            report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

            # Expected: must fail
            assert report.status == VerificationStatus.FAIL

    def test_reproducer_vuln3_cherry_picked_seeds_bypass_verifier(
        self, session_factory: sessionmaker
    ):
        """Experiment runs seeds 10, 20, 30. Analysis cherry-picks seed 10, omitting 20 and 30.

        Expected: VerificationStatus.FAIL and claim.status == REJECTED.
        Actual (Bug): VerificationStatus.PASS and claim.status == VERIFIED because lineage.executions only has seed 10.
        """
        run_id = "run_vuln3_cherry"
        with get_db_session(session_factory) as session:
            run = ResearchRunModel(id=run_id, research_question="Does treatment improve?")
            hyp = HypothesisModel(
                id=f"hyp_{run_id}",
                research_run_id=run_id,
                statement="Treatment improves performance.",
                status=HypothesisStatus.PROPOSED.value,
                falsification_condition="Delta <= 0",
            )
            exp = ExperimentModel(
                id=f"exp_{run_id}",
                research_run_id=run_id,
                hypothesis_id=hyp.id,
                objective="Evaluate treatment",
                specification_json={"name": "exp"},
            )
            exe1 = ExecutionModel(
                id="exec_1", experiment_id=exp.id, status="completed", exit_code=0, seed=10
            )
            exe2 = ExecutionModel(
                id="exec_2", experiment_id=exp.id, status="completed", exit_code=0, seed=20
            )
            exe3 = ExecutionModel(
                id="exec_3", experiment_id=exp.id, status="completed", exit_code=0, seed=30
            )

            res1 = ResultModel(
                id="res_1",
                execution_id=exe1.id,
                metric_name="accuracy",
                metric_value=0.95,
                result_json={"seed": 10},
            )
            res2 = ResultModel(
                id="res_2",
                execution_id=exe2.id,
                metric_name="accuracy",
                metric_value=0.50,
                result_json={"seed": 20},
            )
            res3 = ResultModel(
                id="res_3",
                execution_id=exe3.id,
                metric_name="accuracy",
                metric_value=0.45,
                result_json={"seed": 30},
            )

            an = AnalysisModel(
                id=f"an_{run_id}",
                research_run_id=run_id,
                analysis_type="welch_t_test",
                input_result_ids=[res1.id],  # Cherry-picks res1
                method="welch_t_test",
                output_json={"accuracy": 0.95, "delta": 0.15, "p_value": 0.01, "seeds": [10]},
            )
            clm = ClaimModel(
                id=f"clm_{run_id}",
                research_run_id=run_id,
                statement="Treatment achieved accuracy 0.95.",
                status=ClaimStatus.PROPOSED.value,
                metadata_json={"metric_name": "accuracy", "asserted_value": 0.95},
            )

            lnk1 = EvidenceLinkModel(
                source_type="claim",
                source_id=clm.id,
                target_type="analysis",
                target_id=an.id,
                relationship_type="supported_by",
                research_run_id=run_id,
            )
            lnk2 = EvidenceLinkModel(
                source_type="analysis",
                source_id=an.id,
                target_type="result",
                target_id=res1.id,
                relationship_type="derived_from",
                research_run_id=run_id,
            )
            lnk3 = EvidenceLinkModel(
                source_type="result",
                source_id=res1.id,
                target_type="execution",
                target_id=exe1.id,
                relationship_type="produced_by",
                research_run_id=run_id,
            )
            lnk4_1 = EvidenceLinkModel(
                source_type="execution",
                source_id=exe1.id,
                target_type="experiment",
                target_id=exp.id,
                relationship_type="instance_of",
                research_run_id=run_id,
            )
            lnk4_2 = EvidenceLinkModel(
                source_type="execution",
                source_id=exe2.id,
                target_type="experiment",
                target_id=exp.id,
                relationship_type="instance_of",
                research_run_id=run_id,
            )
            lnk4_3 = EvidenceLinkModel(
                source_type="execution",
                source_id=exe3.id,
                target_type="experiment",
                target_id=exp.id,
                relationship_type="instance_of",
                research_run_id=run_id,
            )
            lnk_res = EvidenceLinkModel(
                source_type="claim",
                source_id=clm.id,
                target_type="result",
                target_id=res1.id,
                relationship_type="supported_by",
                research_run_id=run_id,
            )

            session.add_all(
                [
                    run,
                    hyp,
                    exp,
                    exe1,
                    exe2,
                    exe3,
                    res1,
                    res2,
                    res3,
                    an,
                    clm,
                    lnk1,
                    lnk2,
                    lnk3,
                    lnk4_1,
                    lnk4_2,
                    lnk4_3,
                    lnk_res,
                ]
            )
            session.commit()

        with get_db_session(session_factory) as session:
            verifier = ResearchVerifier(session=session)
            report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

            # Expected: must fail
            assert report.status == VerificationStatus.FAIL

    def test_reproducer_vuln4_hypothesis_not_falsified_on_direction_mismatch(
        self, session_factory: sessionmaker
    ):
        """Claim with 'improved' and negative delta is rejected, but hypothesis stays PROPOSED.

        Expected: hyp.status in (FALSIFIED, INCONCLUSIVE).
        Actual (Bug): hyp.status == PROPOSED.
        """
        run_id = "run_vuln4_hyp"
        with get_db_session(session_factory) as session:
            run = ResearchRunModel(id=run_id, research_question="Does treatment improve?")
            hyp = HypothesisModel(
                id=f"hyp_{run_id}",
                research_run_id=run_id,
                statement="Treatment improved performance.",
                status=HypothesisStatus.PROPOSED.value,
                falsification_condition="Delta <= 0",
            )
            exp = ExperimentModel(
                id=f"exp_{run_id}",
                research_run_id=run_id,
                hypothesis_id=hyp.id,
                objective="Evaluate treatment",
                specification_json={"name": "exp"},
            )
            exe = ExecutionModel(
                id=f"exec_{run_id}", experiment_id=exp.id, status="completed", exit_code=0, seed=42
            )
            res = ResultModel(
                id=f"res_{run_id}",
                execution_id=exe.id,
                metric_name="accuracy",
                metric_value=0.80,
                result_json={"seed": 42},
            )
            an = AnalysisModel(
                id=f"an_{run_id}",
                research_run_id=run_id,
                analysis_type="welch_t_test",
                input_result_ids=[res.id],
                method="welch_t_test",
                output_json={"accuracy": 0.80, "delta": -0.08, "p_value": 0.001, "seeds": [42]},
            )
            clm = ClaimModel(
                id=f"clm_{run_id}",
                research_run_id=run_id,
                statement="Treatment improved accuracy to 0.80.",
                status=ClaimStatus.PROPOSED.value,
                metadata_json={"metric_name": "accuracy", "asserted_value": 0.80},
            )

            lnk1 = EvidenceLinkModel(
                source_type="claim",
                source_id=clm.id,
                target_type="analysis",
                target_id=an.id,
                relationship_type="supported_by",
                research_run_id=run_id,
            )
            lnk2 = EvidenceLinkModel(
                source_type="analysis",
                source_id=an.id,
                target_type="result",
                target_id=res.id,
                relationship_type="derived_from",
                research_run_id=run_id,
            )
            lnk3 = EvidenceLinkModel(
                source_type="result",
                source_id=res.id,
                target_type="execution",
                target_id=exe.id,
                relationship_type="produced_by",
                research_run_id=run_id,
            )
            lnk4 = EvidenceLinkModel(
                source_type="execution",
                source_id=exe.id,
                target_type="experiment",
                target_id=exp.id,
                relationship_type="instance_of",
                research_run_id=run_id,
            )
            lnk_res = EvidenceLinkModel(
                source_type="claim",
                source_id=clm.id,
                target_type="result",
                target_id=res.id,
                relationship_type="supported_by",
                research_run_id=run_id,
            )

            session.add_all([run, hyp, exp, exe, res, an, clm, lnk1, lnk2, lnk3, lnk4, lnk_res])
            session.commit()

        with get_db_session(session_factory) as session:
            verifier = ResearchVerifier(session=session)
            report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

            assert report.status == VerificationStatus.FAIL
            hyp = session.get(HypothesisModel, f"hyp_{run_id}")
            # Expected: hypothesis must fail closed
            assert hyp.status in (
                HypothesisStatus.FALSIFIED.value,
                HypothesisStatus.INCONCLUSIVE.value,
            )
