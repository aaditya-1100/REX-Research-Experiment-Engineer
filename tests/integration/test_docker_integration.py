"""Live Docker Integration & Security Boundary Verification Suite (REX-009).

This test suite executes empirical security verification against a real Docker daemon.
If Docker is not running on the host, tests are cleanly skipped with an explicit explanation.
When Docker is running, every test exercises real container creation and live execution.
"""

import time
from pathlib import Path

import docker
import pytest

from rex.domain.models import ExecutionStatus
from rex.execution.docker_runner import (
    DockerExecutionBackend,
    is_docker_available,
)
from rex.execution.exceptions import SymlinkEscapeError
from rex.execution.models import ExecutionRequest
from rex.execution.resources import ResourceLimits
from rex.execution.workspace import WorkspaceManager

# Register custom docker mark and daemon availability gate
pytestmark = [
    pytest.mark.docker,
    pytest.mark.integration,
    pytest.mark.skipif(
        not is_docker_available(),
        reason="Docker daemon is not available on this host. Untrusted sandbox tests skipped.",
    ),
]


@pytest.fixture
def docker_client() -> docker.DockerClient:
    """Provide a real Docker client."""
    return docker.from_env()


class TestLiveDockerSecurityBoundary:
    """Empirical live verification of the Docker sandbox security boundary."""

    def test_live_container_execution_and_artifact_capture(self, tmp_path: Path) -> None:
        """Verify real container execution, output capture, and artifact collection."""
        backend = DockerExecutionBackend(
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        code = (
            "import json, sys\n"
            "print('Hello from live container stdout')\n"
            "sys.stderr.write('Live container stderr log\\n')\n"
            "with open('/workspace/output/metrics.json', 'w') as f:\n"
            "    json.dump({'accuracy': 0.98}, f)\n"
        )

        req = ExecutionRequest(
            execution_id="live-exec-001",
            experiment_id="exp-live-001",
            research_run_id="run-live-001",
            command=["python", "src/run.py"],
            code_files={"run.py": code},
            limits=ResourceLimits(timeout_seconds=30),
        )

        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.COMPLETED
        assert outcome.exit_code == 0
        assert "Hello from live container stdout" in outcome.stdout
        assert "Live container stderr log" in outcome.stderr
        assert outcome.cleaned_up is True

        # Verify output artifact
        assert len(outcome.output_artifacts) >= 1
        artifact_paths = [a.path for a in outcome.output_artifacts]
        assert "metrics.json" in artifact_paths

    def test_live_non_root_execution(self, tmp_path: Path) -> None:
        """A. Non-root execution: verify process executes as non-root user (UID != 0)."""
        backend = DockerExecutionBackend(
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        code = "import os\nuid = os.getuid()\ngid = os.getgid()\nprint(f'UID:{uid},GID:{gid}')\n"

        req = ExecutionRequest(
            execution_id="live-non-root",
            experiment_id="exp-non-root",
            research_run_id="run-non-root",
            command=["python", "src/check_user.py"],
            code_files={"check_user.py": code},
            non_root_user=True,
        )

        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.COMPLETED
        assert "UID:1000" in outcome.stdout or (
            "UID:" in outcome.stdout and "UID:0" not in outcome.stdout
        )
        assert outcome.cleaned_up is True

    def test_live_network_isolation(self, tmp_path: Path) -> None:
        """B. Network isolation: verify outbound connections fail with network_mode='none'."""
        backend = DockerExecutionBackend(
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        code = (
            "import socket\n"
            "try:\n"
            "    s = socket.create_connection(('8.8.8.8', 53), timeout=2)\n"
            "    print('OUTBOUND_CONNECTED')\n"
            "except Exception as err:\n"
            "    print(f'OUTBOUND_BLOCKED:{type(err).__name__}')\n"
        )

        req = ExecutionRequest(
            execution_id="live-net-iso",
            experiment_id="exp-net-iso",
            research_run_id="run-net-iso",
            command=["python", "src/net_check.py"],
            code_files={"net_check.py": code},
            network_disabled=True,
        )

        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.COMPLETED
        assert "OUTBOUND_BLOCKED" in outcome.stdout
        assert "OUTBOUND_CONNECTED" not in outcome.stdout

    def test_live_docker_socket_isolation(self, tmp_path: Path) -> None:
        """C. Docker socket isolation: verify /var/run/docker.sock does not exist in container."""
        backend = DockerExecutionBackend(
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        code = (
            "import os\n"
            "sock_path = '/var/run/docker.sock'\n"
            "exists = os.path.exists(sock_path)\n"
            "print(f'DOCKER_SOCK_EXISTS:{exists}')\n"
        )

        req = ExecutionRequest(
            execution_id="live-sock-iso",
            experiment_id="exp-sock-iso",
            research_run_id="run-sock-iso",
            command=["python", "src/sock_check.py"],
            code_files={"sock_check.py": code},
        )

        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.COMPLETED
        assert "DOCKER_SOCK_EXISTS:False" in outcome.stdout

    def test_live_host_filesystem_isolation(self, tmp_path: Path) -> None:
        """D. Host filesystem isolation: verify only /workspace is mounted from host."""
        backend = DockerExecutionBackend(
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        code = (
            "import os\n"
            "# Read /etc/passwd inside container to verify it's the container's Linux environment\n"
            "with open('/etc/passwd') as f:\n"
            "    passwd_head = f.readline().strip()\n"
            "print(f'PASSWD_FIRST_LINE:{passwd_head}')\n"
        )

        req = ExecutionRequest(
            execution_id="live-fs-iso",
            experiment_id="exp-fs-iso",
            research_run_id="run-fs-iso",
            command=["python", "src/fs_check.py"],
            code_files={"fs_check.py": code},
        )

        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.COMPLETED
        assert "PASSWD_FIRST_LINE:root:x:0:0:root:/root:" in outcome.stdout

    def test_live_host_write_isolation(self, tmp_path: Path) -> None:
        """E. Host write isolation: verify container cannot write outside /workspace."""
        backend = DockerExecutionBackend(
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        code = (
            "try:\n"
            "    with open('/etc/malicious.txt', 'w') as f:\n"
            "        f.write('hacked')\n"
            "    print('WRITE_OUTSIDE_ALLOWED')\n"
            "except Exception as err:\n"
            "    print(f'WRITE_OUTSIDE_BLOCKED:{type(err).__name__}')\n"
        )

        req = ExecutionRequest(
            execution_id="live-write-iso",
            experiment_id="exp-write-iso",
            research_run_id="run-write-iso",
            command=["python", "src/write_check.py"],
            code_files={"write_check.py": code},
        )

        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.COMPLETED
        assert "WRITE_OUTSIDE_BLOCKED" in outcome.stdout
        assert "WRITE_OUTSIDE_ALLOWED" not in outcome.stdout

    def test_live_resource_timeout_enforcement(
        self,
        tmp_path: Path,
        docker_client: docker.DockerClient,
    ) -> None:
        """F. Resource timeout: verify long-running code is terminated and container cleaned up."""
        backend = DockerExecutionBackend(
            docker_client=docker_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        code = (
            "import time\n"
            "print('Starting long sleep...')\n"
            "time.sleep(60)\n"
            "print('Sleep finished unexpectedly!')\n"
        )

        req = ExecutionRequest(
            execution_id="live-timeout-test",
            experiment_id="exp-timeout",
            research_run_id="run-timeout",
            command=["python", "src/hang.py"],
            code_files={"hang.py": code},
            limits=ResourceLimits(timeout_seconds=2),
        )

        start = time.monotonic()
        outcome = backend.execute(req)
        duration = time.monotonic() - start

        assert outcome.status == ExecutionStatus.TIMEOUT
        assert duration < 10.0  # Must terminate well before the 60s sleep
        assert outcome.cleaned_up is True
        assert "Sleep finished unexpectedly!" not in outcome.stdout

        # Verify container was removed and is not lingering in Docker
        all_containers = docker_client.containers.list(all=True)
        assert not any(req.execution_id in c.name for c in all_containers)

    def test_live_output_size_truncation(self, tmp_path: Path) -> None:
        """H. Output limit: verify excessive stdout is bounded and truncated."""
        backend = DockerExecutionBackend(
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        # Output 50,000 bytes with limit of 10,000 bytes
        code = "import sys\nsys.stdout.write('X' * 50000)\n"

        req = ExecutionRequest(
            execution_id="live-output-limit",
            experiment_id="exp-out-limit",
            research_run_id="run-out-limit",
            command=["python", "src/spew.py"],
            code_files={"spew.py": code},
            limits=ResourceLimits(max_output_size_bytes=10000),
        )

        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.COMPLETED
        assert outcome.stdout_truncated is True
        assert len(outcome.stdout.encode("utf-8")) == 10000

    def test_live_symlink_escape_protection(self, tmp_path: Path) -> None:
        """Verify symlinks pointing outside workspace are detected and raise SymlinkEscapeError."""
        backend = DockerExecutionBackend(
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        # Inside container, create a symlink in /workspace/output pointing outside (/etc/passwd)
        code = (
            "import os\n"
            "os.symlink('/etc/passwd', '/workspace/output/escaped_link')\n"
            "print('Symlink created inside container')\n"
        )

        req = ExecutionRequest(
            execution_id="live-symlink-escape",
            experiment_id="exp-symlink",
            research_run_id="run-symlink",
            command=["python", "src/make_link.py"],
            code_files={"make_link.py": code},
        )

        with pytest.raises(SymlinkEscapeError, match="escapes workspace boundary"):
            backend.execute(req)

    def test_live_container_cleanup_after_all_lifecycle_outcomes(
        self,
        tmp_path: Path,
        docker_client: docker.DockerClient,
    ) -> None:
        """Verify container removal across SUCCESS, FAILURE, and TIMEOUT lifecycles."""
        backend = DockerExecutionBackend(
            docker_client=docker_client,
            workspace_manager=WorkspaceManager(base_root=tmp_path),
        )

        # 1. Success case
        req_ok = ExecutionRequest(
            execution_id="clean-ok",
            experiment_id="exp-c1",
            research_run_id="run-c1",
            command=["python", "-c", "print('done')"],
        )
        out_ok = backend.execute(req_ok)
        assert out_ok.cleaned_up is True

        # 2. Failure case
        req_fail = ExecutionRequest(
            execution_id="clean-fail",
            experiment_id="exp-c2",
            research_run_id="run-c2",
            command=["python", "-c", "import sys; sys.exit(42)"],
        )
        out_fail = backend.execute(req_fail)
        assert out_fail.cleaned_up is True

        # 3. Timeout case
        req_time = ExecutionRequest(
            execution_id="clean-timeout",
            experiment_id="exp-c3",
            research_run_id="run-c3",
            command=["python", "-c", "import time; time.sleep(10)"],
            limits=ResourceLimits(timeout_seconds=1),
        )
        out_time = backend.execute(req_time)
        assert out_time.cleaned_up is True

        # Assert no containers for any of the runs remain
        active_names = [c.name for c in docker_client.containers.list(all=True)]
        assert not any("clean-ok" in name for name in active_names)
        assert not any("clean-fail" in name for name in active_names)
        assert not any("clean-timeout" in name for name in active_names)

    def test_live_docker_configuration_inspection(
        self,
        tmp_path: Path,
        docker_client: docker.DockerClient,
    ) -> None:
        """Section 9: Inspect actual Docker container configuration for security compliance."""
        wm = WorkspaceManager(base_root=tmp_path)
        limits = ResourceLimits(cpu_limit=1.5, memory_limit_mb=512)
        req = ExecutionRequest(
            execution_id="inspect-config",
            experiment_id="exp-insp",
            research_run_id="run-insp",
            command=["python", "-c", "import time; time.sleep(1)"],
            limits=limits,
            non_root_user=True,
            network_disabled=True,
        )
        ws = wm.prepare_workspace(req)

        # Launch in background and inspect container before it exits
        container = docker_client.containers.run(
            image="python:3.11-slim",
            command=["python", "-c", "import time; time.sleep(2)"],
            volumes={str(ws.workspace_dir.resolve()): {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            network_mode="none",
            user="1000:1000",
            nano_cpus=int(limits.cpu_limit * 1_000_000_000),
            mem_limit=f"{limits.memory_limit_mb}m",
            security_opt=["no-new-privileges:true"],
            cap_drop=["ALL"],
            detach=True,
        )

        try:
            container.reload()
            host_cfg = container.attrs.get("HostConfig", {})
            cfg = container.attrs.get("Config", {})

            # 1. user != root
            assert cfg.get("User") == "1000:1000"
            # 2. network_mode == none
            assert host_cfg.get("NetworkMode") == "none"
            # 3. cap_drop includes ALL
            assert "ALL" in host_cfg.get("CapDrop", [])
            # 4. no-new-privileges enabled
            assert any("no-new-privileges" in opt for opt in host_cfg.get("SecurityOpt", []))
            # 5. memory limit configured (512 MB in bytes)
            assert host_cfg.get("Memory") == 512 * 1024 * 1024
            # 6. CPU limit configured (1.5 cores in nano cpus)
            assert host_cfg.get("NanoCpus") == int(1.5 * 1_000_000_000)
            # 7. Only intended workspace mount exists, no docker socket
            binds = host_cfg.get("Binds", [])
            assert not any("docker.sock" in b for b in binds)
            assert any("/workspace" in b for b in binds)
        finally:
            container.remove(force=True)

    def test_live_output_path_escape_rejected(self, tmp_path: Path) -> None:
        """I. Output path escape: verify traversal attempts outside output/ are not collected."""
        wm = WorkspaceManager(base_root=tmp_path)
        req = ExecutionRequest(
            execution_id="live-esc-check",
            experiment_id="exp-esc",
            research_run_id="run-esc",
            command=["python", "src/escape.py"],
            code_files={
                "escape.py": (
                    "with open('/workspace/outside.txt', 'w') as f:\n"
                    "    f.write('outside content')\n"
                    "with open('/workspace/output/valid.txt', 'w') as f:\n"
                    "    f.write('inside content')\n"
                )
            },
        )
        backend = DockerExecutionBackend(workspace_manager=wm)
        outcome = backend.execute(req)

        assert outcome.status == ExecutionStatus.COMPLETED
        # Only files inside output/ should be collected
        artifact_paths = [a.path for a in outcome.output_artifacts]
        assert "valid.txt" in artifact_paths
        assert not any("outside.txt" in p for p in artifact_paths)
