"""Scientific Self-Deception Resistance & Grounding Benchmark (Track A Sec 11-13).

Benchmarks detection of self-deception, p-value significance gating, cherry-picking detection,
metric surrogacy, and automatic fail-closed state transitions into INCONCLUSIVE or REJECTED.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from rex.domain.models import ClaimStatus, HypothesisStatus
from rex.evidence.self_deception import (
    ScientificSelfDeceptionDetector,
    SelfDeceptionType,
)
from rex.evidence.verifier import ResearchVerifier
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
    db_file = tmp_path / "test_self_deception.db"
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
    def __init__(self, configuration_json: dict):
        self.configuration_json = configuration_json


class TestStatisticalSignificanceGating:
    """Benchmark tests validating enforcement of p < 0.05 for superiority claims."""

    def test_insignificant_p_value_blocks_superiority_claim(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Claim asserting 'outperforms' when p = 0.35 >= 0.05 fails and transitions to INCONCLUSIVE."""
        claim_stmt = (
            "The proposed attention mechanism significantly outperforms the convolutional baseline."
        )
        analyses = [
            MockAnalysis(
                output_json={
                    "metric_name": "accuracy",
                    "delta": 0.015,
                    "p_value": 0.35,  # Insignificant
                    "method": "welch_t_test",
                }
            )
        ]

        report = detector.audit_claim(
            claim_statement=claim_stmt,
            claim_metadata={"claim_type": "superiority"},
            analyses=analyses,
        )

        assert report.is_grounded is False
        assert report.is_statistically_significant is False
        assert any(
            f.finding_type == SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE
            for f in report.findings
        )
        finding = next(
            f
            for f in report.findings
            if f.finding_type == SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE
        )
        assert finding.recommended_claim_status == ClaimStatus.INCONCLUSIVE
        assert finding.recommended_hypothesis_status == HypothesisStatus.INCONCLUSIVE

    def test_negative_delta_insignificant_rejects_and_falsifies(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Claim asserting superiority when empirical delta is negative transitions to REJECTED/FALSIFIED."""
        claim_stmt = "The proposed model achieves superior performance over baseline."
        analyses = [
            MockAnalysis(
                output_json={
                    "metric_name": "accuracy",
                    "delta": -0.02,  # Actually worse
                    "p_value": 0.12,
                }
            )
        ]

        report = detector.audit_claim(
            claim_statement=claim_stmt,
            claim_metadata={"claim_type": "superiority"},
            analyses=analyses,
        )

        assert report.is_grounded is False
        finding = next(
            f
            for f in report.findings
            if f.finding_type == SelfDeceptionType.LACKS_STATISTICAL_SIGNIFICANCE
        )
        assert finding.recommended_claim_status == ClaimStatus.REJECTED
        assert finding.recommended_hypothesis_status == HypothesisStatus.FALSIFIED

    def test_statistically_significant_superiority_claim_passes(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Claim asserting superiority with p = 0.004 < 0.05 passes significance gating."""
        claim_stmt = "Proposed optimizer improves accuracy by 2.1% over Adam baseline."
        analyses = [
            MockAnalysis(
                output_json={
                    "metric_name": "accuracy",
                    "delta": 0.021,
                    "p_value": 0.004,  # Statistically significant
                    "seeds": [42, 123, 456],
                }
            )
        ]
        executions = [
            MockExecution({"seed": 42}),
            MockExecution({"seed": 123}),
            MockExecution({"seed": 456}),
        ]

        report = detector.audit_claim(
            claim_statement=claim_stmt,
            claim_metadata={"metric_name": "accuracy", "asserted_value": 0.021},
            analyses=analyses,
            executions=executions,
        )

        assert report.is_grounded is True
        assert report.is_statistically_significant is True
        assert len(report.findings) == 0


class TestCherryPickingDetection:
    """Benchmark tests detecting omitted runs and cherry-picked random seeds."""

    def test_cherry_picked_seeds_detected(self, detector: ScientificSelfDeceptionDetector):
        """When 5 seeds were executed but analysis only aggregated the 2 best seeds, cherry-picking is flagged."""
        claim_stmt = "Proposed method improves accuracy across evaluation runs."
        executions = [
            MockExecution({"seed": 42}),
            MockExecution({"seed": 43}),
            MockExecution({"seed": 44}),
            MockExecution({"seed": 45}),
            MockExecution({"seed": 46}),
        ]
        # Analysis only included seeds 42 and 43, omitting poor performers 44, 45, 46
        analyses = [
            MockAnalysis(
                output_json={
                    "metric_name": "accuracy",
                    "seeds": [42, 43],
                    "mean": 0.92,
                    "p_value": 0.01,
                }
            )
        ]

        report = detector.audit_claim(
            claim_statement=claim_stmt,
            analyses=analyses,
            executions=executions,
        )

        assert report.is_grounded is False
        assert report.is_seed_complete is False
        assert any(f.finding_type == SelfDeceptionType.CHERRY_PICKED_SEEDS for f in report.findings)

        finding = next(
            f for f in report.findings if f.finding_type == SelfDeceptionType.CHERRY_PICKED_SEEDS
        )
        assert finding.recommended_claim_status == ClaimStatus.REJECTED
        assert finding.context["omitted_seeds"] == [44, 45, 46]


class TestMetricSurrogacyAndConfounding:
    """Benchmark tests detecting train loss surrogates and confounded causal assertions."""

    def test_train_loss_surrogate_for_generalization_rejected(
        self, detector: ScientificSelfDeceptionDetector
    ):
        """Claiming generalization when only train loss was measured is flagged."""
        claim_stmt = "The model demonstrates improved generalization performance."
        results = [
            MockResult("train_loss", 0.15, {"split": "train"}),
            MockResult("train_loss", 0.12, {"split": "train"}),
        ]

        report = detector.audit_claim(
            claim_statement=claim_stmt,
            results=results,
        )

        assert report.is_grounded is False
        assert any(
            f.finding_type == SelfDeceptionType.METRIC_SURROGATE_MISMATCH for f in report.findings
        )

    def test_confounded_causal_claim_flagged(self, detector: ScientificSelfDeceptionDetector):
        """Claim asserting causal attribution when multiple variables were changed is flagged."""
        claim_stmt = "Improved convergence is caused by the new activation function."
        exp_spec = {
            "variables": {
                "activation": "gelu",
                "learning_rate": 0.001,
                "batch_size": 128,
            },
            "analysis_methods": ["t_test"],  # No ablations
        }

        report = detector.audit_claim(
            claim_statement=claim_stmt,
            experiment_spec=exp_spec,
        )

        assert report.is_grounded is False
        assert any(
            f.finding_type == SelfDeceptionType.CONFOUNDED_CAUSAL_CLAIM for f in report.findings
        )


class TestVerifierEndToEndSelfDeceptionGating:
    """Integration test verifying ResearchVerifier fails-closed when self-deception is present."""

    def test_verifier_transitions_insignificant_claim_to_inconclusive(
        self, session_factory: sessionmaker
    ):
        """In a full database run verification, an insignificant claim transitions fail-closed to INCONCLUSIVE."""
        run_id = "run_deception_test_1"

        with get_db_session(session_factory) as session:
            # Setup complete empirical lineage
            run = ResearchRunModel(id=run_id, research_question="Test significance gating")
            hyp = HypothesisModel(
                id="hyp_01",
                research_run_id=run_id,
                statement="New schedule outperforms baseline.",
                status=HypothesisStatus.PROPOSED.value,
                falsification_condition="Delta < 0.01",
            )
            exp = ExperimentModel(
                id="exp_01",
                research_run_id=run_id,
                hypothesis_id=hyp.id,
                objective="Test schedule",
                specification_json={"name": "exp"},
            )
            exe = ExecutionModel(
                id="exec_01",
                experiment_id=exp.id,
                status="completed",
                exit_code=0,
                seed=42,
            )
            res = ResultModel(
                id="res_01",
                execution_id=exe.id,
                metric_name="accuracy",
                metric_value=0.88,
                result_json={"seed": 42},
            )
            # Insignificant analysis p = 0.40
            an = AnalysisModel(
                id="an_01",
                research_run_id=run_id,
                analysis_type="welch_t_test",
                input_result_ids=[res.id],
                method="welch_t_test",
                output_json={
                    "accuracy": 0.88,
                    "delta": 0.02,
                    "p_value": 0.40,
                    "seeds": [42],
                },
            )
            clm = ClaimModel(
                id="clm_01",
                research_run_id=run_id,
                statement="The proposed schedule outperforms baseline with accuracy 0.88.",
                status=ClaimStatus.PROPOSED.value,
                metadata_json={"metric_name": "accuracy", "asserted_value": 0.88},
            )

            # Create evidence links
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

        # Run verifier
        with get_db_session(session_factory) as session:
            verifier = ResearchVerifier(session=session)
            report = verifier.verify_run(run_id, actor=ActorType.VERIFIER)

            assert report.is_passed is False
            assert any("SELF_DECEPTION" in err for err in report.errors)

            # Verify fail-closed status persisted in database
            persisted_clm = session.get(ClaimModel, clm.id)
            assert persisted_clm.status == ClaimStatus.INCONCLUSIVE.value

            persisted_hyp = session.get(HypothesisModel, hyp.id)
            assert persisted_hyp.status == HypothesisStatus.INCONCLUSIVE.value
