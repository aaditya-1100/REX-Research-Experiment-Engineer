"""Unit tests for Hypothesis domain model, immutability, and validation (REX-006)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rex.domain.models import ExpectedDirection, Hypothesis, HypothesisStatus
from rex.persistence.models import HypothesisModel


def test_valid_hypothesis_construction_and_defaults():
    """Verify default values and valid instantiation of Hypothesis domain entity."""
    hyp = Hypothesis(
        research_run_id="run_abc123",
        statement="FlashAttention reduces memory footprint by O(N) compared to standard attention.",
        falsification_condition="Peak GPU memory allocation is >= standard attention across batch sizes.",
        rationale="Fused kernel eliminates materialization of intermediate attention matrices.",
    )

    assert hyp.id.startswith("hyp_")
    assert hyp.research_run_id == "run_abc123"
    assert "FlashAttention reduces memory" in hyp.statement
    assert hyp.rationale.startswith("Fused kernel")
    assert hyp.expected_direction == ExpectedDirection.INCREASE
    assert hyp.status == HypothesisStatus.PROPOSED
    assert hyp.created_at.tzinfo == UTC


def test_hypothesis_expected_direction_and_status_enums():
    """Verify custom status and expected direction values serialize and validate properly."""
    hyp = Hypothesis(
        research_run_id="run_123",
        statement="Dropout decreases validation cross-entropy loss under high regularization.",
        falsification_condition="Validation loss increases with p < 0.05.",
        expected_direction=ExpectedDirection.DECREASE,
        status=HypothesisStatus.ACTIVE,
    )

    assert hyp.expected_direction == "decrease"
    assert hyp.status == "active"
    assert ExpectedDirection("decrease") == ExpectedDirection.DECREASE
    assert HypothesisStatus("active") == HypothesisStatus.ACTIVE


def test_statement_validation_rejects_empty():
    """Verify that empty or whitespace-only statements are rejected."""
    with pytest.raises(ValidationError):
        Hypothesis(
            research_run_id="run_1",
            statement="",
            falsification_condition="condition",
        )

    with pytest.raises(ValidationError):
        Hypothesis(
            research_run_id="run_1",
            statement="   \t\n  ",
            falsification_condition="condition",
        )


def test_falsification_condition_validation_rejects_empty():
    """Verify that empty or whitespace-only falsification conditions are rejected."""
    with pytest.raises(ValidationError):
        Hypothesis(
            research_run_id="run_1",
            statement="Valid statement",
            falsification_condition="",
        )

    with pytest.raises(ValidationError):
        Hypothesis(
            research_run_id="run_1",
            statement="Valid statement",
            falsification_condition="   ",
        )


def test_identifier_validation_rejects_empty():
    """Verify that empty research_run_id or hypothesis id is rejected."""
    with pytest.raises(ValidationError):
        Hypothesis(
            research_run_id="",
            statement="Statement",
            falsification_condition="Condition",
        )

    with pytest.raises(ValidationError):
        Hypothesis(
            id="   ",
            research_run_id="run_1",
            statement="Statement",
            falsification_condition="Condition",
        )


def test_invalid_status_and_direction_rejected():
    """Verify that unsupported status or direction values are rejected."""
    with pytest.raises(ValidationError):
        Hypothesis(
            research_run_id="run_1",
            statement="Statement",
            falsification_condition="Condition",
            status="unsupported_status",  # type: ignore
        )

    with pytest.raises(ValidationError):
        Hypothesis(
            research_run_id="run_1",
            statement="Statement",
            falsification_condition="Condition",
            expected_direction="diagonal",  # type: ignore
        )


def test_hypothesis_immutability():
    """Verify that Hypothesis objects are frozen and immutable."""
    hyp = Hypothesis(
        research_run_id="run_1",
        statement="Initial statement",
        falsification_condition="Initial condition",
    )

    with pytest.raises(ValidationError):
        hyp.statement = "Modified statement"  # type: ignore

    with pytest.raises(ValidationError):
        hyp.status = HypothesisStatus.VALIDATED  # type: ignore


def test_hypothesis_with_status_evolution():
    """Verify with_status returns a new immutable instance without mutating scientific content."""
    original = Hypothesis(
        id="hyp_fixed_123",
        research_run_id="run_fixed_456",
        statement="LoRA fine-tuning preserves generalisation performance comparable to full fine-tuning.",
        falsification_condition="GLUE benchmark score degrades by > 2.0 points.",
        rationale="Low-rank decomposition captures task-specific intrinsic dimension.",
        expected_direction=ExpectedDirection.NO_CHANGE,
        status=HypothesisStatus.PROPOSED,
    )

    updated = original.with_status(HypothesisStatus.VALIDATED)

    # Original remains untouched
    assert original.status == HypothesisStatus.PROPOSED
    # Updated has new status
    assert updated.status == HypothesisStatus.VALIDATED
    # Scientific content is completely preserved
    assert updated.id == original.id
    assert updated.research_run_id == original.research_run_id
    assert updated.statement == original.statement
    assert updated.falsification_condition == original.falsification_condition
    assert updated.rationale == original.rationale
    assert updated.expected_direction == original.expected_direction
    assert updated.created_at == original.created_at


def test_hypothesis_persistence_roundtrip():
    """Verify bidirectional conversion between Hypothesis and HypothesisModel."""
    now = datetime.now(UTC)
    domain_hyp = Hypothesis(
        id="hyp_test_roundtrip",
        research_run_id="run_test_roundtrip",
        statement="Quantization aware training maintains perplexity within 0.1 of fp16.",
        rationale="Fake quantization during backward pass adapts weights to int8 boundaries.",
        expected_direction=ExpectedDirection.NO_CHANGE,
        falsification_condition="Perplexity degradation exceeds 0.1 on WikiText-103.",
        status=HypothesisStatus.TESTING,
        created_at=now,
    )

    # Convert to SQLAlchemy model
    model = domain_hyp.to_persistence()
    assert isinstance(model, HypothesisModel)
    assert model.id == "hyp_test_roundtrip"
    assert model.research_run_id == "run_test_roundtrip"
    assert model.statement == domain_hyp.statement
    assert model.rationale == domain_hyp.rationale
    assert model.expected_direction == "no_change"
    assert model.falsification_condition == domain_hyp.falsification_condition
    assert model.status == "testing"

    # Reconstruct back to domain entity
    reconstructed = Hypothesis.from_persistence(model)
    assert reconstructed.id == domain_hyp.id
    assert reconstructed.research_run_id == domain_hyp.research_run_id
    assert reconstructed.statement == domain_hyp.statement
    assert reconstructed.rationale == domain_hyp.rationale
    assert reconstructed.expected_direction == ExpectedDirection.NO_CHANGE
    assert reconstructed.falsification_condition == domain_hyp.falsification_condition
    assert reconstructed.status == HypothesisStatus.TESTING
    assert reconstructed.created_at == now
