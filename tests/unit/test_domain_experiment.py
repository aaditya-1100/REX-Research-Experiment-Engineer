"""Unit tests for Experiment domain model, specification structure, and deep immutability (REX-007)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rex.domain.models import (
    TERMINAL_EXPERIMENT_STATUSES,
    DatasetSpec,
    Experiment,
    ExperimentSpecification,
    ExperimentStatus,
    MetricDirection,
    MetricSpec,
)
from rex.persistence.models import ExperimentModel


def test_valid_experiment_construction_and_defaults():
    """Verify default values and valid instantiation of Experiment domain entity."""
    exp = Experiment(
        research_run_id="run_101",
        objective="Evaluate whether cosine learning rate schedule improves Transformer convergence.",
    )

    assert exp.id.startswith("exp_")
    assert exp.research_run_id == "run_101"
    assert exp.hypothesis_id is None
    assert "cosine learning rate" in exp.objective
    assert exp.status == ExperimentStatus.DESIGNED
    assert exp.created_at.tzinfo == UTC
    assert exp.parent_experiment_id is None
    assert isinstance(exp.specification, ExperimentSpecification)
    assert exp.specification.repetitions == 1
    assert exp.specification.seeds == (42,)
    assert not exp.is_terminal


def test_experiment_specification_structured_fields():
    """Verify detailed structured fields of ExperimentSpecification."""
    dataset = DatasetSpec(name="cifar10", split="test", parameters={"augment": True})
    metric = MetricSpec(name="accuracy", direction=MetricDirection.MAXIMIZE, target_value=0.92)

    spec = ExperimentSpecification(
        name="cosine_lr_benchmark",
        description="Benchmark comparing cosine decay vs step decay on CIFAR-10",
        method="ResNet-18 trained with SGD + momentum",
        variables={"learning_rate_schedule": "cosine_decay"},
        controls={"optimizer": "SGD", "momentum": 0.9, "weight_decay": 5e-4},
        baseline={"schedule": "step_decay", "step_size": 30},
        datasets=(dataset,),
        metrics=(metric,),
        parameters={"epochs": 100, "batch_size": 128},
        seeds=(42, 100, 2026),
        repetitions=3,
        analysis_methods=("welch_t_test", "cohens_d"),
        success_criteria="accuracy > 0.91 with p < 0.05",
        falsification_criteria="accuracy <= baseline accuracy",
    )

    exp = Experiment(
        research_run_id="run_101",
        hypothesis_id="hyp_001",
        objective="Validate cosine decay schedule advantage.",
        specification=spec,
    )

    assert exp.specification.name == "cosine_lr_benchmark"
    assert exp.specification.repetitions == 3
    assert exp.specification.seeds == (42, 100, 2026)
    assert exp.specification.variables["learning_rate_schedule"] == "cosine_decay"
    assert exp.specification.controls["optimizer"] == "SGD"
    assert exp.specification.datasets[0].name == "cifar10"
    assert exp.specification.datasets[0].parameters["augment"] is True
    assert exp.specification.metrics[0].name == "accuracy"
    assert exp.specification.metrics[0].target_value == 0.92


def test_dataset_and_metric_spec_coercion_and_validation():
    """Verify coercion of string and dict representations into typed specs."""
    spec = ExperimentSpecification(
        datasets=["cifar10", {"name": "cifar100", "split": "val"}],
        metrics=["loss", {"name": "top1_acc", "direction": "maximize", "target_value": 0.95}],
    )

    assert len(spec.datasets) == 2
    assert spec.datasets[0].name == "cifar10"
    assert spec.datasets[0].split == "train"
    assert spec.datasets[1].name == "cifar100"
    assert spec.datasets[1].split == "val"

    assert len(spec.metrics) == 2
    assert spec.metrics[0].name == "loss"
    assert spec.metrics[0].direction == MetricDirection.MAXIMIZE
    assert spec.metrics[1].name == "top1_acc"
    assert spec.metrics[1].target_value == 0.95

    # Validation errors on empty names
    with pytest.raises(ValidationError):
        DatasetSpec(name="")
    with pytest.raises(ValidationError):
        MetricSpec(name="   ")


def test_empty_or_whitespace_fields_rejected():
    """Verify validation errors when required fields are empty or whitespace."""
    with pytest.raises(ValidationError):
        Experiment(research_run_id="run_1", objective="")

    with pytest.raises(ValidationError):
        Experiment(research_run_id="run_1", objective="   \t  ")

    with pytest.raises(ValidationError):
        Experiment(research_run_id="", objective="Valid objective")

    with pytest.raises(ValidationError):
        Experiment(id="  ", research_run_id="run_1", objective="Valid objective")


def test_top_level_domain_immutability():
    """Verify Pydantic frozen configuration prevents attribute assignment."""
    exp = Experiment(
        research_run_id="run_1",
        objective="Baseline comparison",
    )

    with pytest.raises(ValidationError):
        exp.objective = "Mutated objective"  # type: ignore[misc]

    with pytest.raises(ValidationError):
        exp.status = ExperimentStatus.RUNNING  # type: ignore[misc]


def test_nested_mapping_deep_immutability():
    """Verify nested configuration dictionaries inside ExperimentSpecification cannot be mutated."""
    params = {"lr": 0.01, "layers": [64, 128], "nested": {"key": "val"}}
    spec = ExperimentSpecification(parameters=params)
    exp = Experiment(
        research_run_id="run_1",
        objective="Test nested immutability",
        specification=spec,
    )

    # Directly mutating frozen mapping proxy must raise TypeError
    with pytest.raises(TypeError):
        exp.specification.parameters["lr"] = 0.99  # type: ignore[index]

    with pytest.raises(TypeError):
        exp.specification.parameters["new_key"] = "forbidden"  # type: ignore[index]


def test_caller_dict_mutation_isolation():
    """Verify defensive copying: mutating caller-owned dictionary has zero effect on domain model."""
    caller_params = {"lr": 0.01, "optimizer": "adam"}
    caller_vars = {"batch_size": 32}

    spec = ExperimentSpecification(parameters=caller_params, variables=caller_vars)
    exp = Experiment(
        research_run_id="run_1",
        objective="Test defensive copying",
        specification=spec,
    )

    # Mutate caller dictionary
    caller_params["lr"] = 999.0
    caller_params["injected"] = "malicious"
    caller_vars["batch_size"] = 10000

    # Domain entity remains completely unaffected
    assert exp.specification.parameters["lr"] == 0.01
    assert "injected" not in exp.specification.parameters
    assert exp.specification.variables["batch_size"] == 32


def test_caller_list_mutation_isolation():
    """Verify defensive copying: mutating caller-owned list has zero effect on domain model."""
    caller_seeds = [42, 43, 44]
    caller_datasets = ["cifar10"]

    spec = ExperimentSpecification(seeds=caller_seeds, datasets=caller_datasets)
    exp = Experiment(
        research_run_id="run_1",
        objective="Test list isolation",
        specification=spec,
    )

    caller_seeds.append(9999)
    caller_datasets.append("imagenet")

    assert exp.specification.seeds == (42, 43, 44)
    assert len(exp.specification.datasets) == 1
    assert exp.specification.datasets[0].name == "cifar10"


def test_with_status_evolution():
    """Verify with_status returns a new immutable Experiment preserving all scientific content."""
    spec = ExperimentSpecification(
        name="test_spec",
        parameters={"lr": 0.001},
        seeds=(123,),
    )
    exp = Experiment(
        id="exp_original",
        research_run_id="run_1",
        hypothesis_id="hyp_1",
        objective="Scientific objective",
        specification=spec,
        status=ExperimentStatus.DESIGNED,
    )

    updated = exp.with_status(ExperimentStatus.RUNNING)

    assert updated is not exp
    assert updated.id == exp.id
    assert updated.research_run_id == exp.research_run_id
    assert updated.hypothesis_id == exp.hypothesis_id
    assert updated.objective == exp.objective
    assert updated.specification.name == "test_spec"
    assert updated.specification.parameters["lr"] == 0.001
    assert updated.specification.seeds == (123,)
    assert updated.status == ExperimentStatus.RUNNING
    assert exp.status == ExperimentStatus.DESIGNED


def test_create_refinement_relationship():
    """Verify create_refinement produces child experiment referencing parent with DESIGNED status."""
    spec = ExperimentSpecification(parameters={"lr": 0.01})
    exp = Experiment(
        id="exp_parent",
        research_run_id="run_1",
        hypothesis_id="hyp_1",
        objective="Parent experiment",
        specification=spec,
        status=ExperimentStatus.COMPLETED,
    )

    child = exp.create_refinement(
        objective="Refined learning rate study",
        specification={"parameters": {"lr": 0.001}},
    )

    assert child.id.startswith("exp_")
    assert child.id != exp.id
    assert child.parent_experiment_id == "exp_parent"
    assert child.research_run_id == "run_1"
    assert child.hypothesis_id == "hyp_1"
    assert child.objective == "Refined learning rate study"
    assert child.status == ExperimentStatus.DESIGNED
    assert child.specification.parameters["lr"] == 0.001


def test_persistence_roundtrip_fidelity():
    """Verify exact round-trip equality between domain Experiment and SQLAlchemy ExperimentModel."""
    dataset = DatasetSpec(name="mnist", split="train", parameters={"normalized": True})
    metric = MetricSpec(name="cross_entropy", direction=MetricDirection.MINIMIZE, target_value=0.05)
    spec = ExperimentSpecification(
        name="roundtrip_test",
        description="Comprehensive persistence roundtrip test",
        method="MLP with ReLU activations",
        variables={"hidden_dim": 256},
        controls={"seed": 42},
        baseline={"hidden_dim": 64},
        datasets=(dataset,),
        metrics=(metric,),
        parameters={"batch_size": 64, "optimizer": "adam"},
        seeds=(42, 43),
        repetitions=2,
        analysis_methods=("t_test",),
        success_criteria="val_loss < 0.05",
        falsification_criteria="val_loss >= 0.20",
    )

    original_exp = Experiment(
        id="exp_roundtrip_001",
        research_run_id="run_persist_1",
        hypothesis_id="hyp_persist_1",
        objective="Validate roundtrip fidelity",
        specification=spec,
        status=ExperimentStatus.PENDING,
        parent_experiment_id="exp_parent_000",
    )

    # Domain -> Persistence
    model = original_exp.to_persistence()
    assert isinstance(model, ExperimentModel)
    assert model.id == "exp_roundtrip_001"
    assert model.research_run_id == "run_persist_1"
    assert model.hypothesis_id == "hyp_persist_1"
    assert model.objective == "Validate roundtrip fidelity"
    assert model.status == "pending"
    assert model.parent_experiment_id == "exp_parent_000"
    assert isinstance(model.specification_json, dict)
    assert model.specification_json["name"] == "roundtrip_test"
    assert model.specification_json["parameters"]["batch_size"] == 64

    # Persistence -> Domain
    restored_exp = Experiment.from_persistence(model)
    assert restored_exp.id == original_exp.id
    assert restored_exp.research_run_id == original_exp.research_run_id
    assert restored_exp.hypothesis_id == original_exp.hypothesis_id
    assert restored_exp.objective == original_exp.objective
    assert restored_exp.status == original_exp.status
    assert restored_exp.parent_experiment_id == original_exp.parent_experiment_id
    assert restored_exp.created_at == original_exp.created_at
    assert restored_exp.specification.name == original_exp.specification.name
    assert restored_exp.specification.parameters == original_exp.specification.parameters
    assert restored_exp.specification.seeds == original_exp.specification.seeds
    assert restored_exp.specification.repetitions == original_exp.specification.repetitions
    assert len(restored_exp.specification.datasets) == 1
    assert restored_exp.specification.datasets[0].name == "mnist"
    assert len(restored_exp.specification.metrics) == 1
    assert restored_exp.specification.metrics[0].name == "cross_entropy"
    assert restored_exp.specification.metrics[0].direction == MetricDirection.MINIMIZE


def test_from_persistence_attaches_utc_timezone():
    """Verify that a naive timestamp in ExperimentModel is safely converted to UTC."""
    naive_dt = datetime(2026, 9, 28, 12, 0, 0)  # noqa: DTZ001
    model = ExperimentModel(
        id="exp_naive",
        research_run_id="run_1",
        objective="Naive timestamp test",
        specification_json={},
        status="designed",
        created_at=naive_dt,
    )

    domain_exp = Experiment.from_persistence(model)
    assert domain_exp.created_at.tzinfo == UTC
    assert domain_exp.created_at.hour == 12


def test_terminal_statuses():
    """Verify is_terminal property behavior on terminal vs active experiment statuses."""
    for terminal in TERMINAL_EXPERIMENT_STATUSES:
        exp = Experiment(
            research_run_id="run_1",
            objective="Terminal test",
            status=terminal,
        )
        assert exp.is_terminal

    for active in (
        ExperimentStatus.DESIGNED,
        ExperimentStatus.PENDING,
        ExperimentStatus.RUNNING,
        ExperimentStatus.ANALYZING,
    ):
        exp = Experiment(
            research_run_id="run_1",
            objective="Active test",
            status=active,
        )
        assert not exp.is_terminal
