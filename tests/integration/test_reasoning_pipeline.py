"""Integration test for end-to-end Research Intelligence reasoning pipeline (Batch 2).

Tests the full reasoning loop:
Question -> InvestigatorAgent -> ResearchContext
         -> HypothesisAgent -> Hypothesis (persisted)
         -> ExperimentDesignerAgent -> ExperimentSpecification & Experiment (persisted)
         -> CodingAgent -> GeneratedExperiment
         -> create_execution_request_from_generated -> ExecutionRequest
"""

import json

import pytest
from sqlalchemy.orm import sessionmaker

from rex.agents.coding import (
    CodingAgent,
    create_execution_request_from_generated,
)
from rex.agents.experiment_designer import ExperimentDesignerAgent
from rex.agents.hypothesis import HypothesisAgent
from rex.agents.investigator import InvestigatorAgent
from rex.controller.budgets import ResearchBudget
from rex.controller.state_machine import create_research_run
from rex.domain.models import (
    ExpectedDirection,
    Experiment,
    ExperimentSpecification,
    GeneratedExperiment,
    Hypothesis,
    ResearchContext,
)
from rex.execution.models import ExecutionRequest
from rex.llm.providers.mock import MockLLMProvider
from rex.observability.events import (
    ActorType,
    EventType,
    InMemoryEventSink,
)
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    get_db_session,
    init_db,
)
from rex.persistence.repositories import (
    EventRepository,
    ExperimentRepository,
    HypothesisRepository,
)


@pytest.fixture
def session_factory(tmp_path):
    """Isolated SQLite database for reasoning pipeline test."""
    db_file = tmp_path / "test_reasoning_pipeline.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def test_full_reasoning_pipeline_end_to_end(session_factory: sessionmaker):
    """Verify that all four agents interact seamlessly and produce an ExecutionRequest ready for execution plane."""
    sink = InMemoryEventSink()
    provider = MockLLMProvider()

    # 1. Initialize Research Run with Budget in DB
    with get_db_session(session_factory) as session:
        budget = ResearchBudget(
            max_llm_calls=20,
            max_token_cost=5.0,
        ).model_dump()
        run = create_research_run(
            session=session,
            research_question="Does cosine annealing learning rate schedule improve validation accuracy in CNNs?",
            budget=budget,
            event_sink=sink,
        )
        run_id = run.id

    # 2. Stage 1: Problem Investigation (REX-013)
    investigator = InvestigatorAgent(provider=provider, event_sink=sink)
    investigation_response = {
        "problem_definition": "Evaluate effect of cosine annealing vs constant learning rate on CIFAR-10 classification.",
        "task_domain": "computer_vision",
        "relevant_terminology": ["learning_rate_schedule", "cosine_annealing", "top1_accuracy"],
        "methodological_approaches": ["cosine_annealing", "step_decay", "constant_lr"],
        "likely_baselines": ["constant_learning_rate"],
        "measurable_outcomes": ["top1_accuracy", "cross_entropy_loss"],
        "important_assumptions": ["fixed_epochs_10", "standard_resnet18"],
        "unresolved_questions": ["minimum_learning_rate_impact"],
        "experiment_considerations": ["synthetic_data_for_fast_eval"],
    }
    provider.enqueue_response(json.dumps(investigation_response))

    with get_db_session(session_factory) as session:
        research_context = investigator.investigate(
            research_question="Does cosine annealing learning rate schedule improve validation accuracy in CNNs?",
            research_run_id=run_id,
            session=session,
            actor=ActorType.RESEARCH_AGENT,
        )

    assert isinstance(research_context, ResearchContext)
    assert research_context.research_run_id == run_id

    # 3. Stage 2: Hypothesis Generation (REX-014)
    hypothesis_agent = HypothesisAgent(provider=provider, event_sink=sink)
    hypothesis_response = {
        "hypotheses": [
            {
                "statement": "Cosine annealing schedule achieves higher top-1 accuracy than constant learning rate on CIFAR-10.",
                "rationale": "Gradual reduction in step size allows convergence to flatter, more generalizable minima.",
                "expected_direction": "increase",
                "falsification_condition": "Top-1 accuracy with cosine annealing is <= constant learning rate baseline.",
            }
        ]
    }
    provider.enqueue_response(json.dumps(hypothesis_response))

    with get_db_session(session_factory) as session:
        hypotheses = hypothesis_agent.generate_hypotheses(
            research_context=research_context,
            count=1,
            session=session,
            actor=ActorType.RESEARCH_AGENT,
        )

    assert len(hypotheses) == 1
    target_hypothesis = hypotheses[0]
    assert isinstance(target_hypothesis, Hypothesis)
    assert target_hypothesis.expected_direction == ExpectedDirection.INCREASE

    # 4. Stage 3: Experiment Design (REX-015)
    designer_agent = ExperimentDesignerAgent(provider=provider, event_sink=sink)
    experiment_design_response = {
        "name": "cosine_annealing_vs_constant",
        "description": "Empirical comparison of cosine annealing vs constant learning rate.",
        "method": "cnn_training_comparison",
        "variables": {"schedule": "cosine_annealing"},
        "controls": {"epochs": 10, "batch_size": 32, "initial_lr": 0.01},
        "baseline": {"schedule": "constant", "initial_lr": 0.01},
        "datasets": [{"name": "synthetic_cifar", "split": "test"}],
        "metrics": [
            {"name": "top1_accuracy", "direction": "maximize"},
            {"name": "cross_entropy_loss", "direction": "minimize"},
        ],
        "parameters": {"optimizer": "SGD", "momentum": 0.9},
        "seeds": [42, 100],
        "repetitions": 1,
        "analysis_methods": ["mean_difference"],
        "success_criteria": "top1_accuracy(cosine) > top1_accuracy(constant)",
        "falsification_criteria": "top1_accuracy(cosine) <= top1_accuracy(constant)",
    }
    provider.enqueue_response(json.dumps(experiment_design_response))

    with get_db_session(session_factory) as session:
        spec, experiment_entity = designer_agent.design_experiment(
            research_question="Does cosine annealing learning rate schedule improve validation accuracy in CNNs?",
            research_context=research_context,
            hypothesis=target_hypothesis,
            session=session,
            actor=ActorType.RESEARCH_AGENT,
        )

    assert isinstance(spec, ExperimentSpecification)
    assert isinstance(experiment_entity, Experiment)
    assert experiment_entity.research_run_id == run_id
    assert experiment_entity.hypothesis_id == target_hypothesis.id

    # 5. Stage 4: Code Generation (REX-016)
    coding_agent = CodingAgent(provider=provider, event_sink=sink)
    code_proposal_response = {
        "entrypoint": "main.py",
        "source_files": {
            "main.py": (
                "import json\n"
                "import random\n"
                "random.seed(42)\n"
                "# Simulated experiment script\n"
                "results = {'top1_accuracy': 0.885, 'cross_entropy_loss': 0.35}\n"
                "print(json.dumps(results))\n"
            ),
            "model.py": "def create_model(): return 'mock_model'\n",
        },
        "command": ["python", "src/main.py"],
        "dependencies": ["numpy"],
        "configuration": {"schedule": "cosine_annealing", "initial_lr": 0.01},
        "expected_metrics": ["top1_accuracy", "cross_entropy_loss"],
    }
    provider.enqueue_response(json.dumps(code_proposal_response))

    with get_db_session(session_factory) as session:
        generated_experiment = coding_agent.generate_code(
            specification=spec,
            research_context=research_context,
            experiment_id=experiment_entity.id,
            research_run_id=run_id,
            seed=42,
            session=session,
            actor=ActorType.RESEARCH_AGENT,
        )

    assert isinstance(generated_experiment, GeneratedExperiment)
    assert generated_experiment.experiment_id == experiment_entity.id
    assert generated_experiment.research_run_id == run_id
    assert "main.py" in generated_experiment.source_files
    assert "model.py" in generated_experiment.source_files
    assert len(generated_experiment.content_hash) == 64

    # 6. Bridge to Execution Plane (REX-009/010 interface)
    execution_request = create_execution_request_from_generated(
        generated=generated_experiment,
        execution_id="exec-run-1",
    )

    assert isinstance(execution_request, ExecutionRequest)
    assert execution_request.execution_id == "exec-run-1"
    assert execution_request.experiment_id == experiment_entity.id
    assert execution_request.command == ["python", "src/main.py"]
    assert "main.py" in execution_request.code_files

    # 7. Verify Event Ledger & Provenance Consistency
    with get_db_session(session_factory) as session:
        events = EventRepository(session).list_by_run(run_id)
        event_types = [str(e.event_type) for e in events]

        assert EventType.RESEARCH_CREATED.value in event_types
        assert EventType.HYPOTHESIS_CREATED.value in event_types
        assert EventType.EXPERIMENT_CREATED.value in event_types
        assert EventType.AGENT_ACTION.value in event_types

        # Verify hypothesis was persisted correctly
        db_hyp = HypothesisRepository(session).get_by_id(target_hypothesis.id)
        assert db_hyp is not None

        # Verify experiment was persisted correctly
        db_exp = ExperimentRepository(session).get_by_id(experiment_entity.id)
        assert db_exp is not None
        assert db_exp.hypothesis_id == target_hypothesis.id
