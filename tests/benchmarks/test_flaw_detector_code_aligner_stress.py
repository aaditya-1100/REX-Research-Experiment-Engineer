"""Empirical Adversarial Stress & Edge-Case Benchmark Suite for flaw_detector.py and code_aligner.py.

Authored by Challenger M2_1 (Track A Focus) for REX Batch 10 Campaign.
Stress-tests boundary conditions, adversarial evasion attempts, AST variations,
and empirical failure modes in experiment specifications and generated source code.
"""

from __future__ import annotations

import pytest

from rex.agents.code_aligner import (
    AlignmentIssueType,
    MethodCodeAligner,
)
from rex.agents.flaw_detector import (
    DesignFlawType,
    ExperimentDesignFlawDetector,
    FlawSeverity,
)


@pytest.fixture
def flaw_detector() -> ExperimentDesignFlawDetector:
    return ExperimentDesignFlawDetector()


@pytest.fixture
def code_aligner() -> MethodCodeAligner:
    return MethodCodeAligner()


# ==============================================================================
# SECTION 1: Experiment Design Flaw Detector Stress Tests
# ==============================================================================


class TestFlawDetectorBaselineStress:
    """Stress tests for baseline validation edge cases and evasion attempts."""

    @pytest.mark.parametrize(
        "empty_or_invalid_baseline",
        [
            None,
            {},
            {"name": ""},
            {"name": "   "},
            {"name": "none"},
            {"name": "null"},
            {"name": "undefined"},
            {"name": "NONE"},
            {"name": "NULL"},
        ],
    )
    def test_empty_or_placeholder_baselines_detected(
        self, flaw_detector: ExperimentDesignFlawDetector, empty_or_invalid_baseline
    ):
        """Any variant of empty, whitespace, or sentinel-named baseline dictionary is flagged as CRITICAL."""
        spec = {
            "name": "baseline_stress_test",
            "method": "my_method",
            "baseline": empty_or_invalid_baseline,
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
        assert report.is_valid is False
        assert report.has_critical_flaws is True
        assert any(f.flaw_type == DesignFlawType.MISSING_BASELINE for f in report.flaws)

    @pytest.mark.parametrize("string_baseline", ["", "none", "null", "undefined", "dummy_string"])
    def test_string_baselines_should_be_flagged_as_missing(
        self, flaw_detector: ExperimentDesignFlawDetector, string_baseline
    ):
        """String baselines such as '' or 'none' must be flagged as MISSING_BASELINE."""
        spec = {
            "name": "string_baseline_test",
            "method": "my_method",
            "baseline": string_baseline,
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
        assert any(f.flaw_type == DesignFlawType.MISSING_BASELINE for f in report.flaws), (
            f"String baseline '{string_baseline}' bypassed missing baseline check"
        )

    def test_malformed_repetitions_type_safety(self, flaw_detector: ExperimentDesignFlawDetector):
        """Passing repetitions=None or non-numeric string should not crash detect_flaws with unhandled exception."""
        spec_none = {
            "name": "malformed_rep",
            "method": "sgd",
            "baseline": {"name": "base"},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy"}],
            "seeds": [1, 2, 3],
            "repetitions": None,
        }
        report = flaw_detector.detect_flaws(spec_none)
        assert report is not None

    def test_malformed_collections_type_safety(self, flaw_detector: ExperimentDesignFlawDetector):
        """None values for datasets, metrics, and analysis_methods should be safely handled without crashing."""
        spec = {
            "name": "malformed_collections_test",
            "method": "sgd",
            "baseline": {"name": "standard_baseline"},
            "datasets": None,
            "metrics": None,
            "analysis_methods": None,
            "variables": {"lr": 0.01, "batch_size": 32},
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
        assert report is not None
        assert any(f.flaw_type == DesignFlawType.CONFOUNDED_VARIABLES for f in report.flaws)


class TestFlawDetectorCrossDatasetStress:
    """Stress tests for cross-dataset detection under complex naming and formatting."""

    def test_cross_dataset_case_insensitivity_and_whitespace(
        self, flaw_detector: ExperimentDesignFlawDetector
    ):
        """Casing and whitespace differences should NOT trigger false positive cross-dataset flaws."""
        spec = {
            "name": "casing_test",
            "method": "my_method",
            "baseline": {"name": "base", "dataset": "  CIFAR10  ", "split": " TEST "},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
        cross_dataset_flaws = [
            f for f in report.flaws if f.flaw_type == DesignFlawType.CROSS_DATASET_COMPARISON
        ]
        assert len(cross_dataset_flaws) == 0

    def test_cross_dataset_detected_with_dataset_name_alias(
        self, flaw_detector: ExperimentDesignFlawDetector
    ):
        """Baseline specifying 'dataset_name' instead of 'dataset' must still trigger cross-dataset detection."""
        spec = {
            "name": "alias_test",
            "method": "my_method",
            "baseline": {"name": "base", "dataset_name": "imagenet", "split": "test"},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
        assert any(f.flaw_type == DesignFlawType.CROSS_DATASET_COMPARISON for f in report.flaws)

    def test_cross_dataset_multi_dataset_treatment(
        self, flaw_detector: ExperimentDesignFlawDetector
    ):
        """When treatment has multiple datasets and baseline matches one, no flaw should trigger."""
        spec = {
            "name": "multi_ds_test",
            "method": "my_method",
            "baseline": {"name": "base", "dataset": "cifar10", "split": "test"},
            "datasets": [
                {"name": "cifar10", "split": "test"},
                {"name": "cifar100", "split": "test"},
            ],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
        assert not any(f.flaw_type == DesignFlawType.CROSS_DATASET_COMPARISON for f in report.flaws)

    def test_cross_dataset_split_mismatch_detected(
        self, flaw_detector: ExperimentDesignFlawDetector
    ):
        """Mismatched splits (validation vs test) between baseline and treatment must be flagged."""
        spec = {
            "name": "split_test",
            "method": "my_method",
            "baseline": {"name": "base", "dataset": "cifar10", "split": "val"},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
        assert any(f.flaw_type == DesignFlawType.CROSS_DATASET_COMPARISON for f in report.flaws)


class TestFlawDetectorConfoundedVariablesStress:
    """Stress tests for multi-variable shifts, ablation bypasses, and control structures."""

    def test_multi_variable_without_ablation_flagged(
        self, flaw_detector: ExperimentDesignFlawDetector
    ):
        """Varying 5 hyperparameters simultaneously without ablation must be flagged as CONFOUNDED."""
        spec = {
            "name": "multi_var",
            "method": "complex_method",
            "baseline": {"name": "base_model", "value": 0.8},
            "variables": {
                "lr": 0.001,
                "batch_size": 64,
                "weight_decay": 1e-4,
                "warmup_steps": 500,
                "optimizer": "adamw",
            },
            "controls": {"architecture": "resnet50"},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
            "analysis_methods": ["t_test"],
        }
        report = flaw_detector.detect_flaws(spec)
        assert any(f.flaw_type == DesignFlawType.CONFOUNDED_VARIABLES for f in report.flaws)

    def test_multi_variable_with_explicit_ablation_passes(
        self, flaw_detector: ExperimentDesignFlawDetector
    ):
        """Varying multiple variables with explicit ablation analysis method is permitted."""
        spec = {
            "name": "multi_var_ablated",
            "method": "complex_method",
            "baseline": {"name": "base_model", "value": 0.8},
            "variables": {"lr": 0.001, "batch_size": 64},
            "controls": {},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
            "analysis_methods": ["ablation_study", "t_test"],
        }
        report = flaw_detector.detect_flaws(spec)
        assert not any(f.flaw_type == DesignFlawType.CONFOUNDED_VARIABLES for f in report.flaws)

    def test_negative_ablation_mention_in_description_should_not_bypass(
        self, flaw_detector: ExperimentDesignFlawDetector
    ):
        """Stating in description that no ablation was performed must NOT bypass the multi-variable shift check."""
        spec = {
            "name": "negative_ablation_desc",
            "description": "Due to tight deadlines, no ablation study was conducted across hyperparameter shifts.",
            "method": "complex_method",
            "baseline": {"name": "base_model", "value": 0.8},
            "variables": {"lr": 0.001, "batch_size": 64, "optimizer": "adamw"},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
            "analysis_methods": ["t_test"],
        }
        report = flaw_detector.detect_flaws(spec)
        assert any(f.flaw_type == DesignFlawType.CONFOUNDED_VARIABLES for f in report.flaws), (
            "Description containing negative ablation claim incorrectly bypassed confounded variables check"
        )


class TestFlawDetectorTrainMetricProxyStress:
    """Stress tests for train metric proxy and generalization claim detection."""

    def test_generalization_claim_with_only_train_metrics(
        self, flaw_detector: ExperimentDesignFlawDetector
    ):
        """Experiment claiming 'test performance' or 'generalization' while only monitoring train loss."""
        spec = {
            "name": "generalization_eval",
            "description": "Demonstrating superior generalization on unseen distributions.",
            "method": "regularizer",
            "baseline": {"name": "vanilla", "value": 1.2},
            "datasets": [{"name": "cifar10", "split": "train"}],
            "metrics": [{"name": "train_loss", "direction": "minimize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
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

    def test_train_metric_without_generalization_claim_is_high_severity(
        self, flaw_detector: ExperimentDesignFlawDetector
    ):
        """Experiment evaluating only train_loss without explicit generalization wording is still flagged as HIGH."""
        spec = {
            "name": "simple_training",
            "description": "Standard optimization convergence.",
            "method": "sgd",
            "baseline": {"name": "adam", "value": 0.5},
            "datasets": [{"name": "cifar10", "split": "train"}],
            "metrics": [{"name": "train_loss", "direction": "minimize"}],
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
        assert any(
            f.flaw_type == DesignFlawType.TRAIN_METRIC_PROXY_FOR_GENERALIZATION
            for f in report.flaws
        )
        proxy_flaw = next(
            f
            for f in report.flaws
            if f.flaw_type == DesignFlawType.TRAIN_METRIC_PROXY_FOR_GENERALIZATION
        )
        assert proxy_flaw.severity == FlawSeverity.HIGH

    def test_valid_test_metrics_clear_proxy_flaw(self, flaw_detector: ExperimentDesignFlawDetector):
        """Including test or validation metrics properly clears the proxy flaw."""
        spec = {
            "name": "valid_eval",
            "description": "Generalization test.",
            "method": "sgd",
            "baseline": {"name": "adam", "value": 0.5},
            "datasets": [
                {"name": "cifar10", "split": "train"},
                {"name": "cifar10", "split": "test"},
            ],
            "metrics": [
                {"name": "train_loss", "direction": "minimize"},
                {"name": "val_accuracy", "direction": "maximize"},
            ],
            "seeds": [1, 2, 3],
            "repetitions": 3,
        }
        report = flaw_detector.detect_flaws(spec)
        assert not any(
            f.flaw_type == DesignFlawType.TRAIN_METRIC_PROXY_FOR_GENERALIZATION
            for f in report.flaws
        )


class TestFlawDetectorSeedFragilityStress:
    """Stress tests for single-seed and replication fragility checks."""

    @pytest.mark.parametrize(
        "seeds,repetitions,should_flag",
        [
            ([42], 1, True),
            ([], 1, True),
            (None, 1, True),
            ([42, 100], 1, False),
            ([42], 3, False),
            ([1, 2, 3, 4, 5], 1, False),
        ],
    )
    def test_seed_configurations(
        self,
        flaw_detector: ExperimentDesignFlawDetector,
        seeds,
        repetitions,
        should_flag,
    ):
        """Verify boundary seed counts and repetition combinations."""
        spec = {
            "name": "seed_test",
            "method": "sgd",
            "baseline": {"name": "adam", "value": 0.5},
            "datasets": [{"name": "cifar10", "split": "test"}],
            "metrics": [{"name": "accuracy", "direction": "maximize"}],
            "seeds": seeds,
            "repetitions": repetitions,
        }
        report = flaw_detector.detect_flaws(spec)
        has_seed_flaw = any(
            f.flaw_type == DesignFlawType.SINGLE_SEED_FRAGILITY for f in report.flaws
        )
        assert has_seed_flaw is should_flag


# ==============================================================================
# SECTION 2: Method Code Aligner Stress Tests
# ==============================================================================


class TestCodeAlignerPRNGSeedingStress:
    """Stress tests for PRNG seeding AST detection across aliasing and import structures."""

    def test_standard_torch_numpy_random_seeding_passes(self, code_aligner: MethodCodeAligner):
        """Code that cleanly sets torch, numpy, and random seeds passes without warning."""
        code = """
import random
import numpy as np
import torch

def setup():
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
"""
        report = code_aligner.align(code)
        assert report.prng_seeded is True
        assert not any(
            i.issue_type == AlignmentIssueType.MISSING_PRNG_SEEDING for i in report.issues
        )

    def test_non_stochastic_code_passes(self, code_aligner: MethodCodeAligner):
        """Code with zero stochastic library imports does not trigger PRNG seeding requirement."""
        code = """
import math
import sys

def compute():
    return math.sqrt(16.0)
"""
        report = code_aligner.align(code)
        assert report.prng_seeded is True
        assert len(report.issues) == 0

    def test_torch_cuda_manual_seed_passes(self, code_aligner: MethodCodeAligner):
        """Code setting torch.cuda.manual_seed or torch.cuda.manual_seed_all is recognized."""
        code = """
import torch

def setup(seed=42):
    torch.cuda.manual_seed_all(seed)
"""
        report = code_aligner.align(code)
        assert report.prng_seeded is True

    def test_seed_everything_passes(self, code_aligner: MethodCodeAligner):
        """Code calling seed_everything() is recognized as seeded."""
        code = """
import torch
import numpy as np

def setup(seed=42):
    seed_everything(seed)
"""
        report = code_aligner.align(code)
        assert report.prng_seeded is True

    def test_aliased_torch_import_should_be_recognized_as_seeded(
        self, code_aligner: MethodCodeAligner
    ):
        """Code importing torch as th and calling th.manual_seed(42) should be recognized as seeded."""
        code = """
import torch as th

def setup(seed=42):
    th.manual_seed(seed)
"""
        report = code_aligner.align(code)
        assert report.prng_seeded is True
        assert not any(
            i.issue_type == AlignmentIssueType.MISSING_PRNG_SEEDING for i in report.issues
        )

    def test_direct_import_manual_seed_should_be_recognized(self, code_aligner: MethodCodeAligner):
        """Code importing manual_seed directly: from torch import manual_seed; manual_seed(42)."""
        code = """
from torch import manual_seed

def setup():
    manual_seed(42)
"""
        report = code_aligner.align(code)
        assert report.prng_seeded is True
        assert not any(
            i.issue_type == AlignmentIssueType.MISSING_PRNG_SEEDING for i in report.issues
        )

    def test_aliased_numpy_seed_import_should_be_recognized(self, code_aligner: MethodCodeAligner):
        """Code importing np_seed: from numpy.random import seed as np_seed; np_seed(42)."""
        code = """
from numpy.random import seed as np_seed

def setup():
    np_seed(42)
"""
        report = code_aligner.align(code)
        assert report.prng_seeded is True
        assert not any(
            i.issue_type == AlignmentIssueType.MISSING_PRNG_SEEDING for i in report.issues
        )

    def test_dummy_local_seed_function_should_not_satisfy_torch_seeding(
        self, code_aligner: MethodCodeAligner
    ):
        """Calling a local dummy function seed() in a PyTorch script must NOT satisfy PRNG seeding."""
        code = """
import torch

def seed():
    pass

def train():
    seed()
    return torch.randn(10, 5)
"""
        report = code_aligner.align(code)
        assert not report.prng_seeded, (
            "Dummy local function seed() incorrectly satisfied PyTorch seeding check"
        )


class TestCodeAlignerDataLeakageStress:
    """Stress tests for data loader and transform leakage into evaluation contexts."""

    def test_train_loader_in_eval_function_flagged(self, code_aligner: MethodCodeAligner):
        """Iterating over train_loader inside validate_model is flagged as TRAIN_LEAKAGE."""
        code = """
import torch

def validate_model(model, train_loader, test_loader):
    model.eval()
    for batch, labels in train_loader:
        out = model(batch)
"""
        report = code_aligner.align(code)
        assert report.has_data_leakage is True
        assert any(
            i.issue_type == AlignmentIssueType.TRAIN_LEAKAGE_INTO_EVALUATION for i in report.issues
        )

    def test_train_dataloader_and_train_set_variants(self, code_aligner: MethodCodeAligner):
        """Verify varied naming for train sources: train_dataloader, train_dataset, train_data."""
        code = """
import torch

def evaluate(model, train_dataloader):
    model.eval()
    for x in train_dataloader:
        pass
"""
        report = code_aligner.align(code)
        assert report.has_data_leakage is True

    def test_clean_eval_loop_passes(self, code_aligner: MethodCodeAligner):
        """Iterating over test_loader or val_loader in evaluate() does not flag leakage."""
        code = """
import torch

def evaluate(model, val_loader, test_loader):
    model.eval()
    for x in val_loader:
        pass
    for y in test_loader:
        pass
"""
        report = code_aligner.align(code)
        assert report.has_data_leakage is False

    def test_fit_transform_on_test_data_flagged(self, code_aligner: MethodCodeAligner):
        """Calling fit_transform on test data array positional arg is flagged as data leakage."""
        code = """
from sklearn.preprocessing import StandardScaler

def prepare(train_x, test_x):
    scaler = StandardScaler()
    scaler.fit_transform(test_x)
"""
        report = code_aligner.align(code)
        assert report.has_data_leakage is True
        assert any(
            i.issue_type == AlignmentIssueType.TRAIN_LEAKAGE_INTO_EVALUATION for i in report.issues
        )

    def test_fit_transform_keyword_arg_on_test_data_should_be_flagged(
        self, code_aligner: MethodCodeAligner
    ):
        """Calling scaler.fit_transform(X=test_x) with keyword argument must be flagged as data leakage."""
        code = """
from sklearn.preprocessing import StandardScaler

def prepare(train_x, test_x):
    scaler = StandardScaler()
    scaler.fit_transform(X=test_x)
"""
        report = code_aligner.align(code)
        assert report.has_data_leakage is True, (
            "Keyword argument X=test_x bypassed leakage detection"
        )

    def test_unrelated_function_with_fit_substring_should_not_trigger_leakage(
        self, code_aligner: MethodCodeAligner
    ):
        """Calling profit(test_data) or benefit(test_data) should NOT trigger preprocessing leakage."""
        code = """
def evaluate():
    profit(test_data)
"""
        report = code_aligner.align(code)
        assert not report.has_data_leakage, (
            "Unrelated function profit() falsely flagged as preprocessing leakage"
        )


class TestCodeAlignerPreprocessingParityStress:
    """Stress tests for preprocessing parity across baseline and treatment arms."""

    def test_symmetric_pipelines_pass(self, code_aligner: MethodCodeAligner):
        """Both pipelines with StandardScaler pass cleanly."""
        code = """
from sklearn.preprocessing import StandardScaler

baseline_pipeline = [
    StandardScaler(),
]

method_pipeline = [
    StandardScaler(),
]
"""
        report = code_aligner.align(code)
        assert report.has_unequal_preprocessing is False

    def test_asymmetric_normalization_with_calls_flagged(self, code_aligner: MethodCodeAligner):
        """Baseline with SimpleImputer while method includes StandardScaler is flagged."""
        code = """
baseline_pipeline = [SimpleImputer()]
method_pipeline = [SimpleImputer(), StandardScaler()]
"""
        report = code_aligner.align(code)
        assert report.has_unequal_preprocessing is True
        assert any(i.issue_type == AlignmentIssueType.UNEQUAL_PREPROCESSING for i in report.issues)

    def test_asymmetric_normalization_raw_baseline_should_be_flagged(
        self, code_aligner: MethodCodeAligner
    ):
        """Baseline with raw tuples while method includes StandardScaler must be flagged."""
        code = """
baseline_pipeline = [
    ("imputer", "simple"),
]

method_pipeline = [
    ("imputer", "simple"),
    ("scaler", StandardScaler()),
]
"""
        report = code_aligner.align(code)
        assert report.has_unequal_preprocessing is True, (
            "Raw baseline pipeline bypassed unequal preprocessing check"
        )

    def test_proposed_pipeline_naming_should_be_checked(self, code_aligner: MethodCodeAligner):
        """Naming the treatment 'proposed_pipeline' must still be compared against baseline_pipeline."""
        code = """
baseline_pipeline = [SimpleImputer()]
proposed_pipeline = [SimpleImputer(), StandardScaler()]
"""
        report = code_aligner.align(code)
        assert report.has_unequal_preprocessing is True, (
            "proposed_pipeline was ignored in preprocessing parity check"
        )


class TestCodeAlignerCleanVsRobustStress:
    """Stress tests for clean vs robust metric verification."""

    def test_robust_metric_with_clean_evaluation_detected(self, code_aligner: MethodCodeAligner):
        """When expected_metrics contains robust_accuracy and code only does clean inference."""
        code = """
import torch

def test(model, loader):
    model.eval()
    for x, y in loader:
        out = model(x)
"""
        report = code_aligner.align(code, expected_metrics=["robust_accuracy"])
        assert report.has_metric_mismatch is True
        assert any(
            i.issue_type == AlignmentIssueType.CLEAN_VS_ROBUST_MISMATCH for i in report.issues
        )

    def test_robust_metric_with_fgsm_attack_passes(self, code_aligner: MethodCodeAligner):
        """When code generates adversarial examples via FGSM, robust_accuracy aligns."""
        code = """
import torch

def fgsm_attack(model, x, y, eps=0.03):
    return x + eps * torch.sign(x)

def test(model, loader):
    model.eval()
    for x, y in loader:
        x_adv = fgsm_attack(model, x, y)
        out = model(x_adv)
"""
        report = code_aligner.align(code, expected_metrics=["robust_accuracy"])
        assert report.has_metric_mismatch is False

    def test_adversarial_keyword_variable_assignment_should_not_bypass(
        self, code_aligner: MethodCodeAligner
    ):
        """Merely defining attack = False in clean evaluation must NOT bypass robust accuracy check."""
        code = """
import torch

attack = False

def test(model, loader):
    model.eval()
    for x, y in loader:
        out = model(x)
"""
        report = code_aligner.align(code, expected_metrics=["robust_accuracy"])
        assert report.has_metric_mismatch is True, (
            "Variable assignment 'attack = False' bypassed robust accuracy check"
        )


# ==============================================================================
# SECTION 3: Multi-File Verification & Syntax Error Resilience
# ==============================================================================


class TestMultiFileAndSyntaxResilience:
    """Tests for multi-file verification and syntax error handling."""

    def test_syntax_error_handled_gracefully(self, code_aligner: MethodCodeAligner):
        """A syntax error in generated code returns is_aligned=False with score=0.0 without crashing."""
        malformed_code = "def broken_syntax(:\n    pass"
        report = code_aligner.align(malformed_code)
        assert report.is_aligned is False
        assert report.alignment_score == 0.0
        assert len(report.issues) == 1
        assert "SyntaxError" in report.issues[0].description

    def test_verify_files_composite_report(self, code_aligner: MethodCodeAligner):
        """verify_files aggregates issues across multiple source files."""
        files = {
            "model.py": """
import torch
def build_model():
    return torch.nn.Linear(10, 2)
""",
            "eval.py": """
import torch
def evaluate(model, train_loader):
    for x in train_loader:
        pass
""",
        }
        report = code_aligner.verify_files(files)
        assert report.is_aligned is False
        assert report.has_data_leakage is True
        assert any(
            i.issue_type == AlignmentIssueType.TRAIN_LEAKAGE_INTO_EVALUATION for i in report.issues
        )
