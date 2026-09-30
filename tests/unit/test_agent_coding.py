"""Unit tests for CodingAgent (REX-016)."""

import json

import pytest

from rex.agents.coding import (
    CodingAgent,
    create_execution_request_from_generated,
)
from rex.domain.models import (
    ExperimentSpecification,
    GeneratedExperiment,
    MetricDirection,
    MetricSpec,
    ResearchContext,
)
from rex.execution.models import ExecutionRequest
from rex.llm.providers.mock import MockLLMProvider
from rex.persistence.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)


@pytest.fixture
def session_factory(tmp_path):
    """Isolated SQLite database for coding agent tests."""
    db_file = tmp_path / "test_agent_coding.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    engine = create_db_engine(database_url=db_url)
    init_db(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture
def sample_spec():
    return ExperimentSpecification(
        name="test_mnist_mlp",
        description="Train MLP on synthetic digit classification.",
        method="feedforward_network",
        variables={"learning_rate": 0.01},
        metrics=(MetricSpec(name="accuracy", direction=MetricDirection.MAXIMIZE),),
        seeds=(42,),
    )


@pytest.fixture
def sample_context():
    return ResearchContext(
        research_run_id="run-code-1",
        problem_definition="Classify synthetic digits.",
        task_domain="classification",
    )


def test_coding_agent_input_validation(sample_spec, sample_context):
    """Verify input validation on IDs and cross-run context consistency."""
    agent = CodingAgent(provider=MockLLMProvider())

    with pytest.raises(ValueError, match="experiment_id must be a non-empty string"):
        agent.generate_code(
            specification=sample_spec,
            research_context=sample_context,
            experiment_id="  ",
            research_run_id="run-code-1",
        )

    with pytest.raises(ValueError, match="does not match requested run ID"):
        agent.generate_code(
            specification=sample_spec,
            research_context=sample_context,
            experiment_id="exp-1",
            research_run_id="mismatched-run",
        )


def test_coding_agent_generates_valid_experiment(sample_spec, sample_context):
    """Verify CodingAgent produces a valid GeneratedExperiment with AST validation and content hash."""
    provider = MockLLMProvider()
    agent = CodingAgent(provider=provider)

    valid_proposal = {
        "entrypoint": "main.py",
        "source_files": {
            "main.py": (
                "import random\n"
                "import json\n"
                "random.seed(42)\n"
                "acc = 0.95\n"
                "print(f'accuracy: {acc}')\n"
            ),
            "utils.py": "def add(a, b): return a + b\n",
        },
        "command": ["python", "src/main.py"],
        "dependencies": ["numpy"],
        "configuration": {"lr": 0.01},
        "expected_metrics": ["accuracy"],
    }
    provider.enqueue_response(json.dumps(valid_proposal))

    generated = agent.generate_code(
        specification=sample_spec,
        research_context=sample_context,
        experiment_id="exp-101",
        research_run_id="run-code-1",
        seed=42,
    )

    assert isinstance(generated, GeneratedExperiment)
    assert generated.experiment_id == "exp-101"
    assert generated.research_run_id == "run-code-1"
    assert generated.entrypoint == "main.py"
    assert "main.py" in generated.source_files
    assert "utils.py" in generated.source_files
    assert generated.command == ("python", "src/main.py")
    assert generated.dependencies == ("numpy",)
    assert generated.expected_metrics == ("accuracy",)
    assert len(generated.content_hash) == 64  # SHA256 hex string


def test_coding_agent_rejects_missing_entrypoint(sample_spec, sample_context):
    """Verify failure when entrypoint is missing from source_files even after repair."""
    provider = MockLLMProvider()
    agent = CodingAgent(provider=provider)

    bad_proposal = {
        "entrypoint": "missing_entrypoint.py",
        "source_files": {"other.py": "x = 1\n"},
        "command": ["python", "src/missing_entrypoint.py"],
        "dependencies": [],
        "configuration": {},
        "expected_metrics": ["accuracy"],
    }
    # Both initial and repair attempt omit missing_entrypoint.py
    provider.enqueue_response(json.dumps(bad_proposal))
    provider.enqueue_response(json.dumps(bad_proposal))

    with pytest.raises(ValueError, match="not present in source_files"):
        agent.generate_code(
            specification=sample_spec,
            research_context=sample_context,
            experiment_id="exp-1",
            research_run_id="run-code-1",
        )


def test_coding_agent_rejects_directory_traversal(sample_spec, sample_context):
    """Verify failure when source_files contain parent directory traversal."""
    provider = MockLLMProvider()
    agent = CodingAgent(provider=provider)

    traversal_proposal = {
        "entrypoint": "main.py",
        "source_files": {
            "main.py": "print('ok')\n",
            "../escape.py": "print('escaped')\n",
        },
        "command": ["python", "src/main.py"],
        "dependencies": [],
        "configuration": {},
        "expected_metrics": ["accuracy"],
    }
    provider.enqueue_response(json.dumps(traversal_proposal))
    provider.enqueue_response(json.dumps(traversal_proposal))

    with pytest.raises(ValueError, match="Path traversal"):
        agent.generate_code(
            specification=sample_spec,
            research_context=sample_context,
            experiment_id="exp-1",
            research_run_id="run-code-1",
        )


def test_coding_agent_rejects_syntax_errors(sample_spec, sample_context):
    """Verify failure when Python code contains unparseable syntax error."""
    provider = MockLLMProvider()
    agent = CodingAgent(provider=provider)

    syntax_error_proposal = {
        "entrypoint": "main.py",
        "source_files": {
            "main.py": "def bad_syntax(:\n",
        },
        "command": ["python", "src/main.py"],
        "dependencies": [],
        "configuration": {},
        "expected_metrics": ["accuracy"],
    }
    provider.enqueue_response(json.dumps(syntax_error_proposal))
    provider.enqueue_response(json.dumps(syntax_error_proposal))

    with pytest.raises(ValueError, match="Syntax error"):
        agent.generate_code(
            specification=sample_spec,
            research_context=sample_context,
            experiment_id="exp-1",
            research_run_id="run-code-1",
        )


def test_coding_agent_rejects_shell_injection(sample_spec, sample_context):
    """Verify failure when command contains dangerous shell chaining operators."""
    provider = MockLLMProvider()
    agent = CodingAgent(provider=provider)

    injection_proposal = {
        "entrypoint": "main.py",
        "source_files": {"main.py": "print('hello')\n"},
        "command": ["python", "src/main.py; rm -rf /"],
        "dependencies": [],
        "configuration": {},
        "expected_metrics": ["accuracy"],
    }
    provider.enqueue_response(json.dumps(injection_proposal))
    provider.enqueue_response(json.dumps(injection_proposal))

    with pytest.raises(ValueError, match="Forbidden shell operator"):
        agent.generate_code(
            specification=sample_spec,
            research_context=sample_context,
            experiment_id="exp-1",
            research_run_id="run-code-1",
        )


def test_coding_agent_bounded_repair_recovers_syntax_error(sample_spec, sample_context):
    """Verify that a syntax error is fixed via bounded repair."""
    provider = MockLLMProvider()
    agent = CodingAgent(provider=provider)

    bad_syntax = {
        "entrypoint": "main.py",
        "source_files": {"main.py": "def broken():\n    return 10 + \n"},
        "command": ["python", "src/main.py"],
        "dependencies": [],
        "configuration": {},
        "expected_metrics": ["accuracy"],
    }
    fixed_syntax = {
        "entrypoint": "main.py",
        "source_files": {"main.py": "def fixed():\n    return 10 + 2\n"},
        "command": ["python", "src/main.py"],
        "dependencies": [],
        "configuration": {},
        "expected_metrics": ["accuracy"],
    }
    provider.enqueue_response(json.dumps(bad_syntax))
    provider.enqueue_response(json.dumps(fixed_syntax))

    generated = agent.generate_code(
        specification=sample_spec,
        research_context=sample_context,
        experiment_id="exp-repair",
        research_run_id="run-code-1",
    )

    assert generated.entrypoint == "main.py"
    assert "return 10 + 2" in generated.source_files["main.py"]
    assert provider.call_count == 2


def test_bridge_to_execution_request(sample_spec, sample_context):
    """Verify create_execution_request_from_generated produces ExecutionRequest for execution plane."""
    provider = MockLLMProvider()
    agent = CodingAgent(provider=provider)

    valid_proposal = {
        "entrypoint": "main.py",
        "source_files": {"main.py": "print('ok')\n"},
        "command": ["python", "src/main.py"],
        "dependencies": ["torch"],
        "configuration": {"batch_size": 64},
        "expected_metrics": ["accuracy"],
    }
    provider.enqueue_response(json.dumps(valid_proposal))

    generated = agent.generate_code(
        specification=sample_spec,
        research_context=sample_context,
        experiment_id="exp-bridge-1",
        research_run_id="run-code-1",
    )

    exec_req = create_execution_request_from_generated(
        generated=generated,
        execution_id="exec-run-101",
    )

    assert isinstance(exec_req, ExecutionRequest)
    assert exec_req.execution_id == "exec-run-101"
    assert exec_req.experiment_id == "exp-bridge-1"
    assert exec_req.research_run_id == "run-code-1"
    assert exec_req.code_files == {"main.py": "print('ok')\n"}
    assert exec_req.command == ["python", "src/main.py"]
    assert exec_req.network_disabled is True
    assert exec_req.non_root_user is True
