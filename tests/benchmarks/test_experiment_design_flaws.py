"""Experiment Design Flaw Detection Benchmark (Track A Sec 8).

Benchmarks automatic identification of critical methodological flaws in experiment designs:
- Missing baselines
- Cross-dataset comparisons and split discrepancies
- Confounded multi-variable shifts
- Training metric proxy for test generalization
- Single-seed fragility
"""

from __future__ import annotations

import pytest

from rex.agents.flaw_detector import (
    DesignFlawType,
    ExperimentDesignFlawDetector,
    FlawSeverity,
)
from rex.domain.models import DatasetSpec, ExperimentSpecification, MetricSpec


@pytest.fixture
def detector() -> ExperimentDesignFlawDetector:
    return ExperimentDesignFlawDetector()


class TestExperimentDesignFlawDetection:
    """Benchmark tests evaluating detection of experimental methodology flaws."""

    def test_missing_baseline_detected(self, detector: ExperimentDesignFlawDetector):
        """Experiment with empty or omitted baseline is flagged as CRITICAL."""
        flawed_spec = {
            "name": "no_baseline_experiment",
            "description": "Evaluate performance of new optimizer.",
            "method": "adam_w_cosine",
            "baseline": {},  # Missing baseline reference
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [42, 123, 999],
            "repetitions": 3,
        }

        report = detector.detect_flaws(flawed_spec)
        assert report.is_valid is False
        assert report.has_critical_flaws is True
        assert any(f.flaw_type == DesignFlawType.MISSING_BASELINE for f in report.flaws)

        baseline_flaw = next(
            f for f in report.flaws if f.flaw_type == DesignFlawType.MISSING_BASELINE
        )
        assert baseline_flaw.severity == FlawSeverity.CRITICAL

    def test_cross_dataset_comparison_detected(self, detector: ExperimentDesignFlawDetector):
        """Experiment evaluating baseline on one dataset and proposed method on another is flagged."""
        flawed_spec = {
            "name": "cross_dataset_experiment",
            "description": "Compare proposed vision transformer against resnet baseline.",
            "method": "vit_small",
            "baseline": {
                "name": "resnet50",
                "dataset": "imagenet_1k",
                "split": "val",
            },
            "datasets": [
                {"name": "cifar100", "split": "test"}  # Mismatched dataset
            ],
            "metrics": [{"name": "top1_accuracy", "direction": "maximize"}],
            "seeds": [42, 123, 456],
            "repetitions": 3,
        }

        report = detector.detect_flaws(flawed_spec)
        assert report.is_valid is False
        assert any(f.flaw_type == DesignFlawType.CROSS_DATASET_COMPARISON for f in report.flaws)

        cross_flaw = next(
            f for f in report.flaws if f.flaw_type == DesignFlawType.CROSS_DATASET_COMPARISON
        )
        assert cross_flaw.severity == FlawSeverity.CRITICAL
        assert "imagenet_1k" in cross_flaw.description
        assert "cifar100" in cross_flaw.description

    def test_split_mismatch_detected(self, detector: ExperimentDesignFlawDetector):
        """Comparing baseline on train split with method on test split is flagged."""
        flawed_spec = {
            "name": "split_mismatch_experiment",
            "description": "Baseline vs method accuracy.",
            "method": "proposed_model",
            "baseline": {
                "name": "baseline_model",
                "dataset": "cifar10",
                "split": "train",  # Baseline on train
            },
            "datasets": [
                {"name": "cifar10", "split": "test"}  # Method on test
            ],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [42, 123, 456],
            "repetitions": 3,
        }

        report = detector.detect_flaws(flawed_spec)
        assert any(f.flaw_type == DesignFlawType.CROSS_DATASET_COMPARISON for f in report.flaws)

    def test_confounded_multi_variable_shift_detected(self, detector: ExperimentDesignFlawDetector):
        """Varying multiple independent variables simultaneously without ablation controls is flagged."""
        flawed_spec = {
            "name": "confounded_experiment",
            "description": "Test new training technique.",
            "method": "modified_training",
            "baseline": {"name": "standard_setup", "value": 0.85},
            "variables": {
                "learning_rate": 0.05,
                "batch_size": 256,
                "weight_decay": 1e-4,
                "optimizer": "adamw",
            },
            "controls": {},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [42, 123, 456],
            "repetitions": 3,
            "analysis_methods": ["t_test"],  # Missing ablation studies
        }

        report = detector.detect_flaws(flawed_spec)
        assert any(f.flaw_type == DesignFlawType.CONFOUNDED_VARIABLES for f in report.flaws)
        conf_flaw = next(
            f for f in report.flaws if f.flaw_type == DesignFlawType.CONFOUNDED_VARIABLES
        )
        assert conf_flaw.severity == FlawSeverity.HIGH

    def test_train_metric_generalization_proxy_detected(
        self, detector: ExperimentDesignFlawDetector
    ):
        """Using training loss/accuracy as a proxy to claim generalization is flagged as CRITICAL."""
        flawed_spec = {
            "name": "train_proxy_experiment",
            "description": "Evaluating generalization capability of regularized architecture.",
            "method": "regularized_net",
            "baseline": {"name": "vanilla_net", "value": 0.75},
            "datasets": [{"name": "cifar10", "split": "train"}],  # Only train split
            "metrics": [{"name": "train_loss", "direction": "minimize"}],  # Only train metric
            "seeds": [42, 123, 456],
            "repetitions": 3,
        }

        report = detector.detect_flaws(flawed_spec)
        assert report.is_valid is False
        assert any(
            f.flaw_type == DesignFlawType.TRAIN_METRIC_PROXY_FOR_GENERALIZATION
            for f in report.flaws
        )
        proxy_flaw = next(
            f
            for f in report.flaws
            if f.flaw_type == DesignFlawType.TRAIN_METRIC_PROXY_FOR_GENERALIZATION
        )
        assert proxy_flaw.severity == FlawSeverity.CRITICAL

    def test_single_seed_fragility_detected(self, detector: ExperimentDesignFlawDetector):
        """Single seed execution with no replications is flagged for reproducibility fragility."""
        fragile_spec = {
            "name": "single_seed_experiment",
            "description": "Standard benchmark test.",
            "method": "proposed_algorithm",
            "baseline": {"name": "control_benchmark", "value": 0.80},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [42],  # Only 1 seed
            "repetitions": 1,  # Only 1 repetition
        }

        report = detector.detect_flaws(fragile_spec)
        assert any(f.flaw_type == DesignFlawType.SINGLE_SEED_FRAGILITY for f in report.flaws)
        seed_flaw = next(
            f for f in report.flaws if f.flaw_type == DesignFlawType.SINGLE_SEED_FRAGILITY
        )
        assert seed_flaw.severity == FlawSeverity.HIGH

    def test_rigorous_experiment_specification_passes(self, detector: ExperimentDesignFlawDetector):
        """A methodologically sound experiment specification passes with zero critical flaws."""
        valid_spec = ExperimentSpecification(
            name="rigorous_cosine_lr_evaluation",
            description="Empirical evaluation of cosine annealing learning rate schedule against constant LR baseline.",
            method="cosine_annealing_schedule",
            variables={"lr_schedule": "cosine_annealing"},
            controls={"batch_size": 128, "architecture": "resnet18", "optimizer": "sgd"},
            baseline={"name": "constant_lr_baseline", "value": 0.885},
            datasets=(DatasetSpec(name="cifar10", split="test"),),
            metrics=(MetricSpec(name="top1_accuracy", direction="maximize"),),
            parameters={"initial_lr": 0.1, "epochs": 100},
            seeds=(42, 123, 456, 789, 1024),
            repetitions=3,
            analysis_methods=("welch_t_test", "confidence_interval_95"),
            success_criteria="top1_accuracy exceeds baseline by at least 1.0% with p < 0.05",
            falsification_criteria="top1_accuracy delta < 1.0% or p >= 0.05",
        )

        report = detector.detect_flaws(valid_spec)
        assert report.is_valid is True
        assert report.has_critical_flaws is False
        assert report.flaw_count == 0
        assert report.soundness_score == 1.0
