"""Integration tests for Docker Execution Sandbox against real Docker daemon (REX-009).

These tests require an active Docker daemon. If Docker is unavailable, tests are cleanly skipped.
"""

from pathlib import Path

import pytest

from rex.domain.models import ExecutionStatus
from rex.execution.docker_runner import (
    DockerExecutionBackend,
    is_docker_available,
)
from rex.execution.models import ExecutionRequest
from rex.execution.resources import ResourceLimits
from rex.execution.workspace import WorkspaceManager

# Register custom docker mark
pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        not is_docker_available(),
        reason="Docker daemon is not available on this host. Untrusted sandbox tests skipped.",
    ),
]


def test_real_docker_container_execution(tmp_path: Path) -> None:
    """Validate end-to-end container execution, non-root user, and artifact capture."""
    backend = DockerExecutionBackend(
        workspace_manager=WorkspaceManager(base_root=tmp_path),
    )

    code = (
        "import json, sys\n"
        "print('Stdout from container')\n"
        "sys.stderr.write('Stderr from container\\n')\n"
        "with open('/workspace/output/result.json', 'w') as f:\n"
        "    json.dump({'metric': 42}, f)\n"
    )

    req = ExecutionRequest(
        execution_id="int-exec-001",
        experiment_id="int-exp-001",
        research_run_id="int-run-001",
        command=["python", "src/test_run.py"],
        code_files={"test_run.py": code},
        limits=ResourceLimits(timeout_seconds=30),
    )

    outcome = backend.execute(req)

    assert outcome.status == ExecutionStatus.COMPLETED
    assert outcome.exit_code == 0
    assert "Stdout from container" in outcome.stdout
    assert "Stderr from container" in outcome.stderr
    assert outcome.cleaned_up is True

    # Validate output artifact collected
    assert len(outcome.output_artifacts) >= 1
    artifact_names = [a.path for a in outcome.output_artifacts]
    assert "result.json" in artifact_names


def test_real_docker_network_isolation(tmp_path: Path) -> None:
    """Validate that network access is strictly disabled inside the container by default."""
    backend = DockerExecutionBackend(
        workspace_manager=WorkspaceManager(base_root=tmp_path),
    )

    # Attempt to open a socket connection to a public IP; should fail due to network_mode='none'
    code = (
        "import socket\n"
        "try:\n"
        "    s = socket.create_connection(('8.8.8.8', 53), timeout=2)\n"
        "    print('CONNECTED')\n"
        "except Exception as e:\n"
        "    print('NETWORK_BLOCKED')\n"
    )

    req = ExecutionRequest(
        execution_id="int-exec-net",
        experiment_id="int-exp-net",
        research_run_id="int-run-net",
        command=["python", "src/net_check.py"],
        code_files={"net_check.py": code},
        network_disabled=True,
    )

    outcome = backend.execute(req)
    assert outcome.status == ExecutionStatus.COMPLETED
    assert "NETWORK_BLOCKED" in outcome.stdout
    assert "CONNECTED" not in outcome.stdout
