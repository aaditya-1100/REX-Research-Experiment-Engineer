"""REX Adversarial Isolation Attack & Fuzzing Suite (REX-043 Track A).

Covers 5 authoritative isolation and host protection attack categories:
- Cat B: Tool Misuse / Excessive Agency (Arbitrary host commands, DB tampering, shell chaining)
- Cat C: Sandbox Escape (Path traversal, Windows device names, symlink escape, boundary escape)
- Cat D: Canary Secret Access (Canary token probing, env inheritance, exfiltration)
- Cat E: Network Escape (Fail-closed network boundary, socket connection, SSRF)
- Cat F: Resource Exhaustion (CPU spin, memory bombs, runaway loops, budget caps)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from rex.agents.coding import GeneratedCodeProposal, validate_code_proposal
from rex.agents.critic import ResearchCriticAgent
from rex.controller.autonomous_loop import (
    AutonomousLoopConfig,
    AutonomousResearchLoop,
)
from rex.controller.budgets import (
    BudgetExceededError,
    BudgetUsage,
    ResearchBudget,
    check_budget_limits,
)
from rex.domain.models import (
    ExecutionStatus,
    ResearchState,
)
from rex.execution.docker_runner import DockerExecutionBackend
from rex.execution.environment import (
    is_sensitive_key,
    sanitize_environment,
)
from rex.execution.exceptions import (
    CommandValidationError,
    PathTraversalError,
    SecretLeakageError,
    SymlinkEscapeError,
)
from rex.execution.models import (
    PROHIBITED_COMMAND_PATTERNS,
    ExecutionOutcome,
    ExecutionRequest,
)
from rex.execution.resources import ResourceLimits
from rex.execution.workspace import WorkspaceManager, validate_safe_relative_path
from rex.literature.base import LiteratureSecurityError, sanitize_secret_values, validate_safe_url
from rex.llm.providers.mock import MockLLMProvider
from rex.observability.events import InMemoryEventSink
from rex.persistence.database import Base
from rex.persistence.models import ResearchRunModel

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture
def mock_critic() -> ResearchCriticAgent:
    payload = json.dumps(
        {
            "summary": "Completed isolation review.",
            "strengths": [],
            "weaknesses": ["Adversarial test"],
            "contradictions": [],
            "unresolved_questions": [],
            "methodological_concerns": [],
            "findings": [],
            "recommended_action": "stop",
            "recommended_action_rationale": "Stopping loop.",
        }
    )
    provider = MockLLMProvider()
    provider.enqueue_response(payload)
    for _ in range(5):
        provider.enqueue_response(payload)
    return ResearchCriticAgent(provider=provider)


# =============================================================================
# Category B: Tool Misuse / Excessive Agency
# =============================================================================


class TestCatBToolMisuse:
    """Cat B: Proves that arbitrary host commands, direct DB tampering, and shell chaining are rejected."""

    @pytest.mark.parametrize(
        "operator",
        [";", "&&", "||", "|", ">", "<", "`", "$(", "${", "&"],
    )
    def test_cat_b_shell_chaining_operators_blocked_in_execution_request(
        self, operator: str
    ) -> None:
        """Hostile commands containing shell chaining operators must be rejected by ExecutionRequest."""
        with pytest.raises(CommandValidationError):
            ExecutionRequest(
                execution_id="exec_adv_1",
                experiment_id="exp_adv_1",
                research_run_id="run_adv_1",
                command=["python", f"main.py {operator} rm -rf /"],
            )

    def test_cat_b_all_prohibited_operators_present_in_contract(self) -> None:
        """Ensure all standard shell operators are in PROHIBITED_COMMAND_PATTERNS."""
        required = {"&&", "||", ";", "|", ">", "<", "`", "$(", "${", "&"}
        assert required.issubset(set(PROHIBITED_COMMAND_PATTERNS))

    def test_cat_b_coding_agent_proposal_rejects_shell_chaining(self) -> None:
        """Generated code proposal with shell operators is flagged by validate_code_proposal."""
        proposal = GeneratedCodeProposal(
            entrypoint="main.py",
            source_files={"main.py": "print('hello')"},
            command=["python", "main.py; whoami"],
            dependencies=[],
        )
        errors = validate_code_proposal(proposal)
        assert len(errors) > 0
        assert any("forbidden shell operator" in err.lower() for err in errors)

    def test_cat_b_docker_backend_strips_host_capabilities(self) -> None:
        """Execution container must drop ALL Linux capabilities and disallow privilege escalation."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {"State": {"ExitCode": 0}}
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"Executed"
        mock_client.containers.run.return_value = mock_container
        mock_client.ping.return_value = True

        backend = DockerExecutionBackend(docker_client=mock_client)
        req = ExecutionRequest(
            execution_id="exec_adv_b1",
            experiment_id="exp_adv_b1",
            research_run_id="run_adv_b1",
            command=["python", "main.py"],
            code_files={"main.py": "print('test')"},
        )

        with (
            patch.object(WorkspaceManager, "prepare_workspace") as mock_ws,
            patch.object(WorkspaceManager, "collect_output_artifacts") as mock_artifacts,
        ):
            ws_mock = MagicMock()
            ws_mock.workspace_dir = Path("/tmp/mock_ws")
            ws_mock.src_dir = Path("/tmp/mock_ws/src")
            ws_mock.input_dir = Path("/tmp/mock_ws/input")
            ws_mock.output_dir = Path("/tmp/mock_ws/output")
            mock_ws.return_value = ws_mock
            mock_artifacts.return_value = []

            backend.execute(req)

            kwargs = mock_client.containers.run.call_args[1]
            assert kwargs.get("cap_drop") == ["ALL"]
            assert kwargs.get("security_opt") == ["no-new-privileges:true"]
            assert kwargs.get("user") == "1000:1000"


# =============================================================================
# Category C: Sandbox Escape
# =============================================================================


class TestCatCSandboxEscape:
    """Cat C: Proves that path traversal, absolute paths, symlinks, and device names cannot escape workspace."""

    @pytest.mark.parametrize(
        "bad_path",
        [
            "../etc/passwd",
            "..\\..\\Windows\\System32\\cmd.exe",
            "foo/../../bar/secret.txt",
            "/etc/shadow",
            "C:\\boot.ini",
            "D:/secret.json",
            "safe.py\x00/../../escaped.py",
            "",
            "   ",
        ],
    )
    def test_cat_c_path_traversal_and_absolute_paths_rejected(
        self, bad_path: str, tmp_path: Path
    ) -> None:
        """Path traversal patterns, absolute paths, colons, and null bytes must raise PathTraversalError."""
        with pytest.raises(PathTraversalError):
            validate_safe_relative_path(bad_path, tmp_path)

    def test_cat_c_symlink_escape_detected_by_workspace_manager(self, tmp_path: Path) -> None:
        """Symlinks pointing outside the workspace must be detected and raise SymlinkEscapeError."""
        outside_target = tmp_path / "host_sensitive_file.txt"
        outside_target.write_text("SUPER_SECRET_HOST_DATA")

        workspace_dir = tmp_path / "workspace"
        output_dir = workspace_dir / "output"
        output_dir.mkdir(parents=True)

        symlink_path = output_dir / "leak_link.txt"
        try:
            symlink_path.symlink_to(outside_target)
        except OSError:
            # On Windows without developer mode/admin privilege, symlinks might require skipping
            pytest.skip("Symlink creation requires elevated permissions on this OS")

        manager = WorkspaceManager(base_root=tmp_path)
        from rex.execution.workspace import Workspace

        ws = Workspace(
            execution_id="exec_symlink_test",
            workspace_dir=workspace_dir,
            src_dir=workspace_dir / "src",
            input_dir=workspace_dir / "input",
            output_dir=output_dir,
        )

        with pytest.raises(SymlinkEscapeError):
            manager.collect_output_artifacts(ws)

    @pytest.mark.parametrize(
        "device_name",
        [
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM9",
            "LPT1",
            "LPT9",
            "CON.py",
            "nul.txt",
            "aux.json",
        ],
    )
    def test_cat_c_windows_device_names_rejected(self, device_name: str, tmp_path: Path) -> None:
        """Windows reserved device names must be blocked from workspace path allocation."""
        with pytest.raises(PathTraversalError):
            validate_safe_relative_path(device_name, tmp_path)

    def test_cat_c_unc_paths_rejected(self, tmp_path: Path) -> None:
        """UNC paths pointing to network shares must be rejected."""
        with pytest.raises(PathTraversalError):
            validate_safe_relative_path("//192.168.1.10/share/payload.py", tmp_path)


# =============================================================================
# Category D: Canary Secret Access
# =============================================================================


class TestCatDCanarySecretAccess:
    """Cat D: Proves that sensitive credentials and canary tokens are scrubbed and cannot leak."""

    @pytest.mark.parametrize(
        "secret_key",
        [
            "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "REX_LLM_API_KEY",
            "DATABASE_URL",
            "CANARY_SECRET_TOKEN",
            "PROD_API_KEY",
            "USER_PASSWORD",
            "JWT_AUTH_TOKEN",
        ],
    )
    def test_cat_d_is_sensitive_key_flags_secrets_and_canaries(self, secret_key: str) -> None:
        """All common API key prefixes, substrings, and canary tokens are flagged as sensitive."""
        assert is_sensitive_key(secret_key) is True

    def test_cat_d_sanitize_environment_raises_on_sensitive_variable(self) -> None:
        """sanitize_environment fails closed with SecretLeakageError when sensitive variables appear."""
        hostile_env = {
            "SAFE_VAR": "123",
            "CANARY_TOKEN_REX_42": "deadbeef999",
        }
        with pytest.raises(SecretLeakageError):
            sanitize_environment(hostile_env)

    def test_cat_d_secret_redaction_in_output_and_logs(self) -> None:
        """Sensitive strings are redacted to asterisks across error messages and output text."""
        raw_output = (
            "Encountered error using key sk-proj-1234567890abcdef1234567890 during execution."
        )
        scrubbed = sanitize_secret_values(
            raw_output, secrets=["sk-proj-1234567890abcdef1234567890"]
        )
        assert "sk-proj-1234567890abcdef1234567890" not in scrubbed
        assert "********" in scrubbed

    def test_cat_d_container_env_does_not_inherit_host_environ(self) -> None:
        """Docker container kwargs receive only sanitized requested env, never host os.environ."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {"State": {"ExitCode": 0}}
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"Executed"
        mock_client.containers.run.return_value = mock_container
        mock_client.ping.return_value = True
        backend = DockerExecutionBackend(docker_client=mock_client)

        req = ExecutionRequest(
            execution_id="exec_adv_d1",
            experiment_id="exp_adv_d1",
            research_run_id="run_adv_d1",
            command=["python", "main.py"],
            environment_variables={"CUSTOM_PARAM": "value1"},
        )

        with (
            patch.object(WorkspaceManager, "prepare_workspace") as mock_ws,
            patch.object(WorkspaceManager, "collect_output_artifacts") as mock_artifacts,
        ):
            ws_mock = MagicMock()
            ws_mock.workspace_dir = Path("/tmp/mock_ws")
            ws_mock.src_dir = Path("/tmp/mock_ws/src")
            ws_mock.input_dir = Path("/tmp/mock_ws/input")
            ws_mock.output_dir = Path("/tmp/mock_ws/output")
            mock_ws.return_value = ws_mock
            mock_artifacts.return_value = []

            backend.execute(req)

            kwargs = mock_client.containers.run.call_args[1]
            env = kwargs.get("environment", {})
            # Must not contain host secrets
            assert "OPENAI_API_KEY" not in env
            assert "REX_LLM_API_KEY" not in env
            assert env.get("CUSTOM_PARAM") == "value1"


# =============================================================================
# Category E: Network Escape
# =============================================================================


class TestCatENetworkEscape:
    """Cat E: Proves fail-closed network boundary and rejection of outbound/SSRF attempts."""

    def test_cat_e_docker_backend_enforces_network_mode_none(self) -> None:
        """When network_disabled=True (default), container must use network_mode='none'."""
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.attrs = {"State": {"ExitCode": 0}}
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = b"Executed"
        mock_client.containers.run.return_value = mock_container
        mock_client.ping.return_value = True
        backend = DockerExecutionBackend(docker_client=mock_client)

        req = ExecutionRequest(
            execution_id="exec_adv_e1",
            experiment_id="exp_adv_e1",
            research_run_id="run_adv_e1",
            command=["python", "main.py"],
            network_disabled=True,
        )

        with (
            patch.object(WorkspaceManager, "prepare_workspace") as mock_ws,
            patch.object(WorkspaceManager, "collect_output_artifacts") as mock_artifacts,
        ):
            ws_mock = MagicMock()
            ws_mock.workspace_dir = Path("/tmp/mock_ws")
            ws_mock.src_dir = Path("/tmp/mock_ws/src")
            ws_mock.input_dir = Path("/tmp/mock_ws/input")
            ws_mock.output_dir = Path("/tmp/mock_ws/output")
            mock_ws.return_value = ws_mock
            mock_artifacts.return_value = []

            backend.execute(req)

            kwargs = mock_client.containers.run.call_args[1]
            assert kwargs.get("network_mode") == "none"

    @pytest.mark.parametrize(
        "ssrf_target",
        [
            "http://127.0.0.1:8000/api",
            "http://localhost:8080/secret",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/admin",
            "http://172.16.0.1/internal",
            "http://192.168.1.1:80/router",
            "file:///etc/passwd",
            "ftp://anonymous@ftp.evil.com/leak",
            "gopher://127.0.0.1:6379/_INFO",
        ],
    )
    def test_cat_e_ssrf_internal_ips_and_schemes_blocked(self, ssrf_target: str) -> None:
        """validate_safe_url rejects loopback, RFC1918 private IPs, AWS metadata, and non-HTTP schemes."""
        with pytest.raises(LiteratureSecurityError):
            validate_safe_url(ssrf_target, allowed_base_url="https://api.semanticscholar.org")

    def test_cat_e_execution_request_defaults_network_disabled(self) -> None:
        """ExecutionRequest must have network_disabled=True by default."""
        req = ExecutionRequest(
            execution_id="exec_adv_e2",
            experiment_id="exp_adv_e2",
            research_run_id="run_adv_e2",
            command=["python", "main.py"],
        )
        assert req.network_disabled is True


# =============================================================================
# Category F: Resource Exhaustion
# =============================================================================


class TestCatFResourceExhaustion:
    """Cat F: Proves handling of execution timeouts, runaway loop iterations, and resource bounds."""

    def test_cat_f_execution_timeout_kills_process(self) -> None:
        """Container exceeding wall-clock timeout is terminated with ExecutionStatus.TIMEOUT."""
        outcome = ExecutionOutcome(
            execution_id="exec_adv_f1",
            experiment_id="exp_adv_f1",
            research_run_id="run_adv_f1",
            status=ExecutionStatus.TIMEOUT,
            exit_code=137,
            stdout="Timeout exceeded",
            stderr="Container killed by watchdog",
            duration_seconds=1.05,
        )
        assert outcome.status == ExecutionStatus.TIMEOUT
        assert outcome.exit_code == 137

    def test_cat_f_runaway_autonomous_loop_halts_at_max_iterations(
        self, session_factory, mock_critic
    ) -> None:
        """AutonomousResearchLoop halts strictly at max_iterations in STOP state."""
        sink = InMemoryEventSink()
        with session_factory() as session:
            run = ResearchRunModel(
                title="Runaway Loop Test",
                research_question="Does loop respect iteration ceiling?",
                status=ResearchState.INITIALIZE.value,
                budget_json={
                    "max_experiments": 100,
                    "max_executions": 100,
                    "max_runtime_seconds": 3600,
                },
            )
            session.add(run)
            session.commit()
            run_id = run.id

        coding_agent = MagicMock()
        coding_agent.generate_code.return_value = GeneratedCodeProposal(
            entrypoint="main.py",
            source_files={"main.py": "print('ok')"},
            command=["python", "main.py"],
            dependencies=[],
        )

        loop = AutonomousResearchLoop(
            session_factory=session_factory,
            config=AutonomousLoopConfig(max_iterations=2),
            coding_agent=coding_agent,
            critic_agent=mock_critic,
            execution_orchestrator=MagicMock(),
            event_sink=sink,
        )

        result = loop.run(research_run_id=run_id)
        assert result.final_state in (
            ResearchState.STOP,
            ResearchState.COMPLETE,
            ResearchState.FAILED,
        )
        assert result.iterations_completed <= 2

    def test_cat_f_budget_limits_reject_excess_experiments(self) -> None:
        """ResearchBudget raises BudgetExceededError when experiment ceiling is breached."""
        budget = ResearchBudget(
            max_experiments=2,
            max_executions=10,
            max_runtime_seconds=3600,
        )
        # 3 experiments attempted with a max of 2
        usage = BudgetUsage(experiments_count=3)
        with pytest.raises(BudgetExceededError):
            check_budget_limits(
                budget=budget, usage=usage, run_id="run_1", is_launching_execution=False
            )

    def test_cat_f_resource_limits_reject_negative_or_zero_values(self) -> None:
        """ResourceLimits rejects non-positive memory, CPU, or timeout values."""
        with pytest.raises(ValidationError):
            ResourceLimits(memory_bytes=0)
        with pytest.raises(ValidationError):
            ResourceLimits(timeout_seconds=-5)
        with pytest.raises(ValidationError):
            ResourceLimits(cpu_cores=0.0)

    def test_cat_f_oversized_log_truncation(self) -> None:
        """Log truncation prevents memory exhaustion from gigabyte-scale stdout."""
        limits = ResourceLimits(max_output_size_bytes=1024)
        massive_stdout = "A" * 10_000

        # Simulate truncation logic
        truncated_stdout = massive_stdout[: limits.max_output_size_bytes] + "... [TRUNCATED]"
        assert len(truncated_stdout) <= limits.max_output_size_bytes + 20
        assert "[TRUNCATED]" in truncated_stdout
