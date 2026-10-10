"""Hypothesis Quality, Falsifiability & Rival Generation Benchmark (Track A Sec 6-7).

Benchmarks hypothesis proposal evaluation, falsification conditions, IV/DV partitioning,
tautology/circularity detection, and competing alternative hypothesis formulations.
"""

from __future__ import annotations

import pytest

from rex.agents.hypothesis import HypothesisAgent, SingleHypothesisProposal
from rex.agents.hypothesis_validator import HypothesisValidator
from rex.domain.models import ExpectedDirection, Hypothesis, ResearchContext


@pytest.fixture
def validator() -> HypothesisValidator:
    return HypothesisValidator()


@pytest.fixture
def sample_research_context() -> ResearchContext:
    return ResearchContext(
        research_run_id="run_bench_hyp_01",
        problem_definition="Investigate if Cosine Annealing learning rate schedule improves test accuracy over constant LR on CIFAR-10.",
        task_domain="deep_learning",
        relevant_terminology=("learning_rate", "cosine_annealing", "generalization"),
        methodological_approaches=("stochastic_gradient_descent", "lr_scheduling"),
        likely_baselines=("constant_lr", "step_decay"),
        measurable_outcomes=("top1_test_accuracy", "validation_loss"),
        important_assumptions=("fixed_architecture", "identical_random_seeds"),
        unresolved_questions=("optimal_minimum_lr", "warmup_duration"),
        experiment_considerations=("seed_replication", "early_stopping"),
    )


class TestHypothesisFalsifiabilityAndQuality:
    """Benchmark tests validating falsifiability and quantitative refutation criteria."""

    def test_rigorous_falsifiable_hypothesis_passes(self, validator: HypothesisValidator):
        """A well-formed hypothesis with quantitative falsification and clear IV/DV passes validation."""
        result = validator.validate(
            statement=(
                "Applying cosine annealing learning rate schedule increases top-1 test accuracy "
                "by at least 1.5 percentage points compared to constant learning rate baseline on CIFAR-10."
            ),
            falsification_condition="Top-1 test accuracy fails to exceed constant LR baseline by >= 1.5% with p < 0.05.",
            rationale="Gradual decay avoids sharp local minima and enables deeper convergence in parameter space.",
            expected_direction=ExpectedDirection.INCREASE,
            independent_variables=["learning_rate_schedule"],
            dependent_variables=["top1_test_accuracy"],
            baseline_reference="constant_learning_rate",
            competing_hypothesis="Performance gains are due to increased training duration rather than annealing dynamics.",
        )

        assert result.is_valid is True
        assert result.quality_score >= 0.85
        assert result.falsifiability_score >= 0.8
        assert result.tautology_detected is False
        assert result.has_baseline_reference is True
        assert result.has_iv_dv_partition is True
        assert result.has_rival_hypothesis is True
        assert len(result.errors) == 0

    def test_non_quantitative_falsification_condition_rejected(
        self, validator: HypothesisValidator
    ):
        """Hypotheses with qualitative or vague falsification conditions fail falsifiability checks."""
        result = validator.validate(
            statement="Applying dropout improves model generalization on image classification tasks.",
            falsification_condition="The model does not seem to perform well or feels unstable.",
            rationale="Dropout prevents co-adaptation of feature detectors.",
            independent_variables=["dropout_rate"],
            dependent_variables=["generalization_accuracy"],
            baseline_reference="vanilla_network",
        )

        assert result.is_valid is False
        assert result.falsifiability_score < 0.5
        assert any(
            "NON_QUANTITATIVE_FALSIFICATION" in err or "UNFALSIFIABLE" in err
            for err in result.errors
        )

    def test_empty_falsification_condition_rejected(self, validator: HypothesisValidator):
        """Empty falsification conditions are strictly rejected."""
        result = validator.validate(
            statement="Increasing batch size speeds up training epoch runtime.",
            falsification_condition="",
            rationale="Larger batches utilize GPU tensor cores more efficiently.",
            independent_variables=["batch_size"],
            dependent_variables=["epoch_runtime_seconds"],
            baseline_reference="batch_size_32",
        )

        assert result.is_valid is False
        assert result.falsifiability_score == 0.0
        assert any("FALSIFICATION_CONDITION_MISSING" in err for err in result.errors)


class TestTautologyAndCircularClaimDetection:
    """Benchmark tests asserting automated rejection of trivial tautologies and circular reasoning."""

    @pytest.mark.parametrize(
        "tautological_statement",
        [
            "Trained neural network models achieve better accuracy than untrained randomly initialized models.",
            "Changing model hyperparameters alters the resulting training performance and loss.",
            "Using different learning rates produces different loss values across runs.",
            "Models with lower validation loss have lower loss than high loss models.",
            "Increasing network parameters changes total model capacity and parameter count.",
            "If test loss decreases then the model has lower loss.",
        ],
    )
    def test_detects_and_rejects_tautologies(
        self, validator: HypothesisValidator, tautological_statement: str
    ):
        """Definitional truths and trivial circular statements must be rejected as tautological."""
        result = validator.validate(
            statement=tautological_statement,
            falsification_condition="Observed loss does not change by at least 1.0%.",
            rationale="Direct theoretical consequence of optimization.",
            independent_variables=["parameters"],
            dependent_variables=["loss"],
            baseline_reference="random_baseline",
        )

        assert result.tautology_detected is True
        assert result.is_valid is False
        assert any("TAUTOLOGY_DETECTED" in err for err in result.errors)

    def test_rejects_subjective_unfalsifiable_claims(self, validator: HypothesisValidator):
        """Subjective language and impossible-to-refute claims must be rejected."""
        result = validator.validate(
            statement="The proposed transformer architecture demonstrates genuine understanding and feels aesthetically natural.",
            falsification_condition="The model cannot be falsified because its elegance is self-evident.",
            rationale="The mathematical symmetry is inherently complete.",
        )

        assert result.is_valid is False
        assert any("UNFALSIFIABLE_CLAIM" in err for err in result.errors)

    def test_detects_circular_rationale(self, validator: HypothesisValidator):
        """Detects rationale that simply repeats statement without proposing causal mechanism."""
        statement = "Adding weight decay reduces model overfitting on test data."
        rationale = "Weight decay reduces overfitting on test data because of weight decay."

        assert validator.detect_circularity(statement, rationale) is True


class TestVariablePartitioningAndBaselineControls:
    """Benchmark tests validating independent/dependent variable partitioning and baseline controls."""

    def test_missing_independent_variable_rejected(self, validator: HypothesisValidator):
        """Proposal declaring no independent variable fails validation."""
        result = validator.validate(
            statement="Test accuracy increases by 2.0% over standard baseline.",
            falsification_condition="Accuracy delta < 2.0%.",
            independent_variables=[],
            dependent_variables=["test_accuracy"],
            baseline_reference="standard_baseline",
        )

        assert result.is_valid is False
        assert result.has_iv_dv_partition is False
        assert any("MISSING_INDEPENDENT_VARIABLE" in err for err in result.errors)

    def test_confounded_overlapping_variables_rejected(self, validator: HypothesisValidator):
        """Variables declared simultaneously as both independent and dependent must be flagged."""
        result = validator.validate(
            statement="Adjusting accuracy improves accuracy over baseline.",
            falsification_condition="Accuracy delta < 1.0%.",
            independent_variables=["test_accuracy"],
            dependent_variables=["test_accuracy"],
            baseline_reference="baseline_run",
        )

        assert result.is_valid is False
        assert result.has_iv_dv_partition is False
        assert any("CONFOUNDED_VARIABLES" in err for err in result.errors)

    def test_missing_baseline_generates_warning(self, validator: HypothesisValidator):
        """Proposals omitting any reference to baseline or control are flagged."""
        result = validator.validate(
            statement="Applying spectral normalization stabilizes gradient norm variance across training steps.",
            falsification_condition="Gradient norm variance fails to reduce by >= 25% with p < 0.05.",
            independent_variables=["spectral_norm"],
            dependent_variables=["gradient_norm_variance"],
            baseline_reference="",
        )

        assert result.has_baseline_reference is False
        assert any("MISSING_BASELINE_REFERENCE" in warn for warn in result.warnings)


class TestCompetingHypothesisGeneration:
    """Benchmark tests evaluating generation of rival/competing hypothesis pairs (HA vs HB)."""

    def test_single_hypothesis_proposal_schema_supports_competing_fields(self):
        """Verifies SingleHypothesisProposal schema supports IV/DV, baseline, and rival hypothesis."""
        proposal = SingleHypothesisProposal(
            statement="LayerNorm stabilizes deep transformer training more effectively than BatchNorm.",
            rationale="LayerNorm normalizes across feature dimensions independently of batch statistics.",
            expected_direction=ExpectedDirection.INCREASE,
            falsification_condition="LayerNorm training gradient divergence is >= BatchNorm divergence.",
            independent_variables=["normalization_layer"],
            dependent_variables=["gradient_stability"],
            baseline_reference="batch_normalization",
            competing_hypothesis="Stability differences are driven by batch size rather than normalization mechanics.",
        )

        assert proposal.independent_variables == ["normalization_layer"]
        assert proposal.dependent_variables == ["gradient_stability"]
        assert proposal.baseline_reference == "batch_normalization"
        assert "batch size" in str(proposal.competing_hypothesis)

    def test_generate_competing_hypotheses_pair(
        self, sample_research_context: ResearchContext, validator: HypothesisValidator
    ):
        """HypothesisAgent generates a paired competing hypothesis set (HA vs HB) for a research problem."""
        agent = HypothesisAgent()
        hyp_a, hyp_b = agent.generate_competing_hypotheses(research_context=sample_research_context)

        assert isinstance(hyp_a, Hypothesis)
        assert isinstance(hyp_b, Hypothesis)
        assert hyp_a.id != hyp_b.id
        assert hyp_a.statement
        assert hyp_b.statement
        assert hyp_a.falsification_condition
        assert hyp_b.falsification_condition

        # Both hypotheses must be valid and testable
        res_a = validator.validate_domain_hypothesis(hyp_a)
        res_b = validator.validate_domain_hypothesis(hyp_b)

        assert res_a.falsifiability_score > 0.0
        assert res_b.falsifiability_score > 0.0
