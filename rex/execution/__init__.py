"""REX Execution Layer and Container Sandbox Package (REX-009).

Exports execution backends, models, workspace managers, exception hierarchies,
and resource limit specifications for sandboxed code execution.
"""

from rex.execution.backend import ExecutionBackend
from rex.execution.docker_runner import (
    DEFAULT_APPROVED_IMAGES,
    DockerExecutionBackend,
    is_docker_available,
)
from rex.execution.environment import (
    EnvironmentMetadata,
    capture_safe_environment_metadata,
    is_sensitive_key,
    sanitize_environment,
    save_environment_metadata_artifact,
)
from rex.execution.exceptions import (
    CommandValidationError,
    DockerUnavailableError,
    ExecutionCancelledError,
    ExecutionError,
    ExecutionTimeoutError,
    ImagePolicyError,
    PathTraversalError,
    ResourceLimitExceededError,
    SecretLeakageError,
    SecurityViolationError,
    SymlinkEscapeError,
    WorkspaceError,
    WorkspaceExistsError,
)
from rex.execution.models import (
    ExecutionOutcome,
    ExecutionRecord,
    ExecutionRequest,
    OutputArtifactMetadata,
)
from rex.execution.resources import ResourceLimits
from rex.execution.runner import (
    execute_managed_sandbox_run,
    run_execution_in_sandbox,
)
from rex.execution.worker import (
    DockerExecutionWorker,
)
from rex.execution.workspace import (
    Workspace,
    WorkspaceManager,
    validate_safe_relative_path,
)

__all__ = [
    "DEFAULT_APPROVED_IMAGES",
    "CommandValidationError",
    "DockerExecutionBackend",
    "DockerExecutionWorker",
    "DockerUnavailableError",
    "EnvironmentMetadata",
    "ExecutionBackend",
    "ExecutionCancelledError",
    "ExecutionError",
    "ExecutionOutcome",
    "ExecutionRecord",
    "ExecutionRequest",
    "ExecutionTimeoutError",
    "ImagePolicyError",
    "OutputArtifactMetadata",
    "PathTraversalError",
    "ResourceLimitExceededError",
    "ResourceLimits",
    "SecretLeakageError",
    "SecurityViolationError",
    "SymlinkEscapeError",
    "Workspace",
    "WorkspaceError",
    "WorkspaceExistsError",
    "WorkspaceManager",
    "capture_safe_environment_metadata",
    "execute_managed_sandbox_run",
    "is_docker_available",
    "is_sensitive_key",
    "run_execution_in_sandbox",
    "sanitize_environment",
    "save_environment_metadata_artifact",
    "validate_safe_relative_path",
]
