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
    capture_safe_environment_metadata,
    is_sensitive_key,
    sanitize_environment,
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
)
from rex.execution.models import (
    ExecutionOutcome,
    ExecutionRequest,
    OutputArtifactMetadata,
)
from rex.execution.resources import ResourceLimits
from rex.execution.runner import (
    execute_managed_sandbox_run,
    run_execution_in_sandbox,
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
    "DockerUnavailableError",
    "ExecutionBackend",
    "ExecutionCancelledError",
    "ExecutionError",
    "ExecutionOutcome",
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
    "WorkspaceManager",
    "capture_safe_environment_metadata",
    "execute_managed_sandbox_run",
    "is_docker_available",
    "is_sensitive_key",
    "run_execution_in_sandbox",
    "sanitize_environment",
    "validate_safe_relative_path",
]
