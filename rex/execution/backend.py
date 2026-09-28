"""REX Execution Backend Protocol (REX-009).

Defines the interface-neutral protocol for sandbox execution backends.
Decouples execution request handling and container lifecycle management
from concrete execution implementations.
"""

from typing import Protocol, runtime_checkable

from rex.execution.models import ExecutionOutcome, ExecutionRequest


@runtime_checkable
class ExecutionBackend(Protocol):
    """Protocol defining the contract for sandboxed execution backends."""

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        """Execute untrusted experiment code specified in request within an isolated sandbox.

        Implementations must never execute untrusted code on the host process.
        Must enforce resource limits, network isolation, non-root user,
        and ensure complete cleanup of container resources.

        Args:
            request: Validated execution request.

        Returns:
            ExecutionOutcome containing execution status, exit code, outputs, and artifacts.
        """
        ...

    def cancel(self, execution_id: str) -> bool:
        """Cancel an in-flight execution run by ID.

        Args:
            execution_id: ID of the execution attempt to cancel.

        Returns:
            True if the execution was found and cancelled, False otherwise.
        """
        ...
