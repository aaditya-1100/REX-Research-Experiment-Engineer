"""REX Execution Layer Exceptions (REX-009).

Defines strongly typed exceptions for sandbox execution, resource limit enforcement,
Docker container lifecycle, and security boundary violations.
"""


class ExecutionError(Exception):
    """Base exception for all execution layer and sandbox errors."""


class DockerUnavailableError(ExecutionError):
    """Raised when Docker is unavailable and secure container execution fails closed.

    Untrusted research code must never execute on the host process as an implicit fallback.
    """


class ImagePolicyError(ExecutionError):
    """Raised when an unapproved container image is requested or image validation fails."""


class SecurityViolationError(ExecutionError):
    """Base exception for security boundary and sandboxing violations."""


class PathTraversalError(SecurityViolationError):
    """Raised when a workspace path attempts to traverse outside its designated sandbox boundary."""


class SymlinkEscapeError(SecurityViolationError):
    """Raised when a symlink or file in the output directory attempts to point outside the sandbox."""


class CommandValidationError(SecurityViolationError):
    """Raised when an execution command violates safety or formatting rules (e.g. shell injection)."""


class SecretLeakageError(SecurityViolationError):
    """Raised when sensitive host credentials or secrets are detected in environment variables."""


class ExecutionTimeoutError(ExecutionError):
    """Raised when a container execution exceeds the configured wall-clock timeout."""


class ExecutionCancelledError(ExecutionError):
    """Raised when an active container execution is cancelled."""


class ResourceLimitExceededError(ExecutionError):
    """Raised when a container execution exceeds memory, CPU, or output limits."""
