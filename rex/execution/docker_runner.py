"""REX Docker Execution Backend and Container Sandbox (REX-009).

Implements the secure local Docker execution boundary for untrusted experiment code.
Enforces non-root execution, network isolation, strictly bounded workspace mounts,
resource limits, wall-clock timeout kill, secret scrubbing, and guaranteed container cleanup.
"""

import logging
import threading
import time
from collections.abc import Iterable
from typing import Any

import docker
from docker.errors import DockerException

from rex.config import RexSettings, get_settings
from rex.domain.models import ExecutionStatus
from rex.execution.backend import ExecutionBackend
from rex.execution.environment import (
    capture_safe_environment_metadata,
    sanitize_environment,
)
from rex.execution.exceptions import (
    DockerUnavailableError,
    ImagePolicyError,
    SecurityViolationError,
)
from rex.execution.models import ExecutionOutcome, ExecutionRequest
from rex.execution.workspace import Workspace, WorkspaceManager

logger = logging.getLogger(__name__)

# Default trusted base images approved for experiment execution
DEFAULT_APPROVED_IMAGES: frozenset[str] = frozenset(
    {
        "python:3.11-slim",
        "python:3.10-slim",
        "python:3.12-slim",
        "python:3.11",
        "python:3.10",
        "python:3.12",
    }
)


def is_docker_available(client: docker.DockerClient | None = None) -> bool:
    """Check if Docker daemon is responsive and available.

    Never assumes Docker is present. Returns False on any connection, daemon, or client error.
    """
    try:
        c = client or docker.from_env()
        return bool(c.ping())
    except (DockerException, Exception):  # noqa: BLE001
        return False


class DockerExecutionBackend(ExecutionBackend):
    """Executes untrusted experiment code inside an isolated Docker container sandbox."""

    def __init__(
        self,
        docker_client: docker.DockerClient | None = None,
        workspace_manager: WorkspaceManager | None = None,
        allowed_images: Iterable[str] | None = None,
        settings: RexSettings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = docker_client
        self._workspace_manager = workspace_manager or WorkspaceManager()

        # Build approved images whitelist
        configured_image = self._settings.docker.image
        base_approved = set(DEFAULT_APPROVED_IMAGES)
        if configured_image:
            base_approved.add(configured_image)
        if allowed_images is not None:
            base_approved.update(allowed_images)
        self._allowed_images = frozenset(base_approved)

        # Thread-safe tracking for cancellation
        self._lock = threading.Lock()
        self._active_containers: dict[str, Any] = {}
        self._cancelled_executions: set[str] = set()

    def is_available(self) -> bool:
        """Verify whether the Docker execution daemon is reachable."""
        return is_docker_available(self._client)

    def _get_client(self) -> docker.DockerClient:
        """Obtain active Docker client or raise DockerUnavailableError."""
        if self._client is not None:
            if not is_docker_available(self._client):
                raise DockerUnavailableError(
                    "Configured Docker client is not available. Untrusted execution fails closed."
                )
            return self._client

        try:
            client = docker.from_env()
            if not is_docker_available(client):
                raise DockerUnavailableError(
                    "Docker daemon failed ping response. Untrusted execution fails closed."
                )
            return client
        except DockerException as err:
            raise DockerUnavailableError(
                f"Docker daemon is not available: {err}. Untrusted execution fails closed."
            ) from err
        except Exception as err:
            raise DockerUnavailableError(
                f"Failed to initialize Docker client: {err}. Untrusted execution fails closed."
            ) from err

    def _validate_image(self, requested_image: str | None) -> str:
        """Validate requested image against approved image policy."""
        image_to_use = requested_image or self._settings.docker.image
        if not image_to_use:
            image_to_use = "python:3.11-slim"

        if image_to_use not in self._allowed_images:
            raise ImagePolicyError(
                f"Requested image '{image_to_use}' violates image security policy. "
                f"Approved images: {sorted(self._allowed_images)}"
            )
        return image_to_use

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        """Execute experiment code in an isolated container according to request specification.

        Fails closed with DockerUnavailableError if Docker daemon is unreachable.
        Enforces wall-clock timeout, resource limits, secret sanitization, and output limits.
        """
        # 1. Fail closed if Docker is unavailable
        client = self._get_client()

        # 2. Validate image against policy
        image_name = self._validate_image(request.image)

        # 3. Sanitize environment variables (raises SecretLeakageError on credentials)
        sanitized_env = sanitize_environment(request.environment_variables)

        # 4. Prepare isolated filesystem workspace
        workspace: Workspace = self._workspace_manager.prepare_workspace(request)

        # Verify workspace directory is safe for volume mounting (guard against host root mount)
        resolved_ws = workspace.workspace_dir.resolve()
        if str(resolved_ws) in ("/", "C:\\", "C:/") or str(resolved_ws).startswith(
            ("/var/run", "\\\\.\\pipe")
        ):
            raise SecurityViolationError(
                f"Unsafe workspace path '{resolved_ws}' detected. Cannot mount root or socket."
            )

        # Container volume mount: strictly mount workspace_dir to /workspace
        volumes = {
            str(resolved_ws): {
                "bind": "/workspace",
                "mode": "rw",
            }
        }

        # 5. Determine container configuration and security constraints
        network_mode = "none" if request.network_disabled else "bridge"
        user = "1000:1000" if request.non_root_user else "0:0"
        nano_cpus = int(request.limits.cpu_limit * 1_000_000_000)
        mem_limit = f"{request.limits.memory_limit_mb}m"
        timeout_seconds = request.limits.timeout_seconds

        container = None
        start_time = time.monotonic()
        timed_out = False
        was_cancelled = False
        exit_code: int | None = None
        stdout_raw = b""
        stderr_raw = b""

        try:
            logger.info(
                "Launching Docker execution container for execution_id=%s, image=%s",
                request.execution_id,
                image_name,
            )

            # Create and launch container in detached mode
            container = client.containers.run(
                image=image_name,
                command=request.command,
                volumes=volumes,
                working_dir="/workspace",
                network_mode=network_mode,
                user=user,
                nano_cpus=nano_cpus,
                mem_limit=mem_limit,
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                environment=sanitized_env,
                detach=True,
                stdout=True,
                stderr=True,
            )

            with self._lock:
                self._active_containers[request.execution_id] = container
                if request.execution_id in self._cancelled_executions:
                    was_cancelled = True

            if was_cancelled:
                try:
                    container.kill()
                except Exception as kill_err:  # noqa: BLE001
                    logger.debug("Container kill suppressed: %s", kill_err)

            # Monitor container execution loop with timeout and cancel checks
            while not was_cancelled:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout_seconds:
                    timed_out = True
                    try:
                        container.kill()
                    except Exception as kill_err:  # noqa: BLE001
                        logger.debug("Container kill on timeout suppressed: %s", kill_err)
                    break

                with self._lock:
                    if request.execution_id in self._cancelled_executions:
                        was_cancelled = True
                        try:
                            container.kill()
                        except Exception as kill_err:  # noqa: BLE001
                            logger.debug("Container kill on cancel suppressed: %s", kill_err)
                        break

                try:
                    container.reload()
                except Exception as reload_err:  # noqa: BLE001
                    logger.debug("Container reload stopped: %s", reload_err)
                    break

                if container.status in ("exited", "dead", "stopped"):
                    state = container.attrs.get("State", {})
                    exit_code = state.get("ExitCode")
                    break

                # Sleep brief interval for next status poll
                time.sleep(min(0.2, max(0.05, timeout_seconds - elapsed)))

            duration_seconds = time.monotonic() - start_time

            # Retrieve stdout and stderr logs safely
            try:
                stdout_raw = container.logs(stdout=True, stderr=False)
            except Exception as log_err:  # noqa: BLE001
                logger.debug("Stdout read suppressed: %s", log_err)
                stdout_raw = b""

            try:
                stderr_raw = container.logs(stdout=False, stderr=True)
            except Exception as log_err:  # noqa: BLE001
                logger.debug("Stderr read suppressed: %s", log_err)
                stderr_raw = b""

        except Exception as err:
            duration_seconds = time.monotonic() - start_time
            logger.exception("Execution failed with container error")
            return ExecutionOutcome(
                execution_id=request.execution_id,
                experiment_id=request.experiment_id,
                research_run_id=request.research_run_id,
                status=ExecutionStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=f"Container execution error: {err}",
                stdout_truncated=False,
                stderr_truncated=False,
                output_artifacts=[],
                environment_metadata=capture_safe_environment_metadata(
                    sanitized_env, image_reference=image_name
                ),
                resource_usage={"duration_seconds": duration_seconds, "error": str(err)},
                duration_seconds=duration_seconds,
                failure_reason=f"Container execution exception: {err}",
                cleaned_up=True,
            )
        finally:
            # Guarantee container cleanup under all outcomes
            with self._lock:
                self._active_containers.pop(request.execution_id, None)
                self._cancelled_executions.discard(request.execution_id)

            if container is not None:
                try:
                    container.remove(force=True)
                    logger.debug("Cleaned up container for execution_id=%s", request.execution_id)
                except Exception as cleanup_err:  # noqa: BLE001
                    logger.warning("Failed to remove container: %s", cleanup_err)

        # 6. Apply stdout/stderr size truncation bounds
        if not isinstance(stdout_raw, (bytes, bytearray)):
            stdout_raw = b""
        if not isinstance(stderr_raw, (bytes, bytearray)):
            stderr_raw = b""

        max_bytes = request.limits.max_output_size_bytes
        stdout_truncated = len(stdout_raw) > max_bytes
        stderr_truncated = len(stderr_raw) > max_bytes

        bounded_stdout_bytes = stdout_raw[:max_bytes] if stdout_truncated else stdout_raw
        bounded_stderr_bytes = stderr_raw[:max_bytes] if stderr_truncated else stderr_raw

        stdout_text = bounded_stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = bounded_stderr_bytes.decode("utf-8", errors="replace")

        # 7. Collect output artifacts from /workspace/output/
        artifacts = self._workspace_manager.collect_output_artifacts(
            workspace,
            max_files=request.limits.max_output_files,
        )

        # 8. Determine outcome status and failure explanation
        if was_cancelled:
            outcome_status = ExecutionStatus.CANCELLED
            failure_reason = "Execution was cancelled."
        elif timed_out:
            outcome_status = ExecutionStatus.TIMEOUT
            failure_reason = f"Execution exceeded wall-clock timeout of {timeout_seconds} seconds."
        elif exit_code == 0:
            outcome_status = ExecutionStatus.COMPLETED
            failure_reason = None
        else:
            outcome_status = ExecutionStatus.FAILED
            failure_reason = f"Process terminated with non-zero exit code: {exit_code}."

        env_metadata = capture_safe_environment_metadata(
            sanitized_env,
            image_reference=image_name,
        )

        resource_usage = {
            "duration_seconds": duration_seconds,
            "exit_code": exit_code,
            "cpu_limit": request.limits.cpu_limit,
            "memory_limit_mb": request.limits.memory_limit_mb,
            "timeout_seconds": request.limits.timeout_seconds,
            "network_disabled": request.network_disabled,
            "non_root_user": request.non_root_user,
        }

        return ExecutionOutcome(
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
            research_run_id=request.research_run_id,
            status=outcome_status,
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
            output_artifacts=artifacts,
            environment_metadata=env_metadata,
            resource_usage=resource_usage,
            duration_seconds=duration_seconds,
            failure_reason=failure_reason,
            cleaned_up=True,
        )

    def cancel(self, execution_id: str) -> bool:
        """Cancel an in-flight execution container by execution ID.

        Returns True if the execution was active and termination was initiated, False otherwise.
        """
        with self._lock:
            self._cancelled_executions.add(execution_id)
            container = self._active_containers.get(execution_id)

        if container is not None:
            try:
                container.kill()
                return True
            except Exception as err:  # noqa: BLE001
                logger.warning("Error killing container during cancellation: %s", err)
                return True

        return False
