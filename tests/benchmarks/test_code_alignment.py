"""Code Generation & Method Alignment Benchmark (Track A Sec 9-10).

Benchmarks static AST verification of method-code alignments:
- Deterministic PRNG seeding (random, numpy, torch)
- Train loader leakage into validation/test evaluations
- Data preprocessing parity across experimental arms
- Clean vs robust/adversarial evaluation alignment
"""

from __future__ import annotations

import pytest

from rex.agents.code_aligner import (
    AlignmentIssueType,
    MethodCodeAligner,
)


@pytest.fixture
def aligner() -> MethodCodeAligner:
    return MethodCodeAligner()


class TestPRNGSeedingAlignment:
    """Benchmark tests validating deterministic seeding enforcement."""

    def test_missing_prng_seed_detected_for_stochastic_code(self, aligner: MethodCodeAligner):
        """Code importing stochastic libraries without seeding is flagged."""
        unseeded_code = """
import torch
import numpy as np
import random

def train_model():
    data = torch.randn(100, 10)
    indices = np.random.permutation(100)
    return data[indices]
"""
        report = aligner.align(unseeded_code)
        assert report.is_aligned is False
        assert report.prng_seeded is False
        assert any(i.issue_type == AlignmentIssueType.MISSING_PRNG_SEEDING for i in report.issues)

    def test_explicitly_seeded_code_passes(self, aligner: MethodCodeAligner):
        """Code initializing random, numpy, and torch seeds passes seeding checks."""
        seeded_code = """
import torch
import numpy as np
import random

def set_seed(seed=42):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

def train_model():
    set_seed(42)
    return torch.randn(10, 5)
"""
        report = aligner.align(seeded_code)
        assert report.prng_seeded is True
        assert not any(
            i.issue_type == AlignmentIssueType.MISSING_PRNG_SEEDING for i in report.issues
        )


class TestDataLeakageAlignment:
    """Benchmark tests detecting train-set data leakage into validation/testing."""

    def test_train_loader_leakage_into_eval_detected(self, aligner: MethodCodeAligner):
        """Iterating over train_loader inside an evaluation function is flagged as CRITICAL leakage."""
        leaky_code = """
import torch

def evaluate(model, train_loader, val_loader):
    model.eval()
    total_correct = 0
    # Leaking training data into validation metric calculation
    for images, labels in train_loader:
        outputs = model(images)
        total_correct += (outputs.argmax(1) == labels).sum().item()
    return total_correct / len(train_loader.dataset)
"""
        report = aligner.align(leaky_code)
        assert report.is_aligned is False
        assert report.has_data_leakage is True
        assert any(
            i.issue_type == AlignmentIssueType.TRAIN_LEAKAGE_INTO_EVALUATION for i in report.issues
        )

    def test_fit_transform_on_test_data_detected(self, aligner: MethodCodeAligner):
        """Fitting preprocessing scalers directly on test/evaluation sets is flagged."""
        scaler_leaky_code = """
from sklearn.preprocessing import StandardScaler

def preprocess(X_train, X_test):
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    # Fitting on test data violates isolation
    X_test_scaled = scaler.fit_transform(X_test)
    return X_train_scaled, X_test_scaled
"""
        report = aligner.align(scaler_leaky_code)
        assert report.has_data_leakage is True
        assert any(
            i.issue_type == AlignmentIssueType.TRAIN_LEAKAGE_INTO_EVALUATION for i in report.issues
        )

    def test_clean_evaluation_without_leakage_passes(self, aligner: MethodCodeAligner):
        """Standard evaluation looping over test_loader passes leakage inspection."""
        clean_code = """
import torch

def evaluate(model, test_loader):
    model.eval()
    correct = 0
    with torch.no_grad():
        for images, labels in test_loader:
            preds = model(images)
            correct += (preds.argmax(1) == labels).sum().item()
    return correct / len(test_loader.dataset)
"""
        report = aligner.align(clean_code)
        assert report.has_data_leakage is False


class TestPreprocessingParityAlignment:
    """Benchmark tests detecting unequal data preprocessing across experimental arms."""

    def test_unequal_preprocessing_detected(self, aligner: MethodCodeAligner):
        """Asymmetric normalization between baseline and method pipelines is flagged."""
        asymmetric_code = """
from torchvision import transforms

# Baseline pipeline lacks normalization
baseline_transform = transforms.Compose([
    transforms.Resize(32),
    transforms.ToTensor(),
])

# Proposed method pipeline includes normalization
method_transform = transforms.Compose([
    transforms.Resize(32),
    transforms.ToTensor(),
    transforms.Normalize((0.5,), (0.5,)),
])
"""
        report = aligner.align(asymmetric_code)
        assert report.is_aligned is False
        assert report.has_unequal_preprocessing is True
        assert any(i.issue_type == AlignmentIssueType.UNEQUAL_PREPROCESSING for i in report.issues)

    def test_symmetric_preprocessing_passes(self, aligner: MethodCodeAligner):
        """Symmetric preprocessing pipelines across arms pass parity checks."""
        symmetric_code = """
from torchvision import transforms

baseline_transform = transforms.Compose([
    transforms.Resize(32),
    transforms.ToTensor(),
    transforms.Normalize((0.5,), (0.5,)),
])

method_transform = transforms.Compose([
    transforms.Resize(32),
    transforms.ToTensor(),
    transforms.Normalize((0.5,), (0.5,)),
])
"""
        report = aligner.align(symmetric_code)
        assert report.has_unequal_preprocessing is False


class TestCleanVsRobustMetricAlignment:
    """Benchmark tests evaluating clean vs robust/adversarial accuracy specification alignment."""

    def test_robust_metric_with_clean_evaluation_flagged(self, aligner: MethodCodeAligner):
        """Claiming robust_accuracy while evaluating only clean unperturbed images is flagged."""
        clean_only_code = """
import torch

def evaluate_robustness(model, test_loader):
    model.eval()
    correct = 0
    with torch.no_grad():
        for x, y in test_loader:
            out = model(x)  # Clean unperturbed inputs evaluated
            correct += (out.argmax(1) == y).sum().item()
    return {"robust_accuracy": correct / len(test_loader.dataset)}
"""
        report = aligner.align(clean_only_code, expected_metrics=["robust_accuracy"])
        assert report.is_aligned is False
        assert report.has_metric_mismatch is True
        assert any(
            i.issue_type == AlignmentIssueType.CLEAN_VS_ROBUST_MISMATCH for i in report.issues
        )

    def test_robust_metric_with_adversarial_attack_passes(self, aligner: MethodCodeAligner):
        """Evaluating with explicit adversarial perturbations aligns with robust_accuracy metric."""
        adversarial_eval_code = """
import torch

def pgd_attack(model, images, labels, epsilon=0.03):
    perturbed = images.clone().detach() + epsilon * torch.sign(torch.randn_like(images))
    return perturbed

def evaluate_robustness(model, test_loader):
    model.eval()
    correct = 0
    for x, y in test_loader:
        x_adv = pgd_attack(model, x, y)
        out = model(x_adv)
        correct += (out.argmax(1) == y).sum().item()
    return {"robust_accuracy": correct / len(test_loader.dataset)}
"""
        report = aligner.align(adversarial_eval_code, expected_metrics=["robust_accuracy"])
        assert report.has_metric_mismatch is False
