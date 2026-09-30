"""REX Execution Workspace Manager and Filesystem Sandbox (REX-009).

Manages unique per-execution directory trees, strictly guards against path traversal
and symlink escape attacks, writes code files, and securely collects output artifacts.
"""

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from rex.config import get_settings
from rex.domain.models import ArtifactType
from rex.execution.exceptions import PathTraversalError, SymlinkEscapeError, WorkspaceExistsError
from rex.execution.models import ExecutionRequest, OutputArtifactMetadata


def validate_safe_relative_path(path: str | Path, base_dir: Path) -> Path:
    """Validate that a relative path does not escape base_dir via directory traversal.

    Rejects absolute paths, drive roots, and any paths containing parent ('..') traversal.
    Returns the resolved absolute Path strictly located inside base_dir.
    """
    raw_path_str = str(path).strip()
    if not raw_path_str:
        raise PathTraversalError("Path cannot be empty.")

    p = Path(raw_path_str)

    # Reject absolute paths (POSIX /root or Windows C:\)
    if p.is_absolute():
        raise PathTraversalError(
            f"Absolute path '{raw_path_str}' is forbidden. Paths must be relative to workspace."
        )

    # Reject drive roots or colons on Windows
    if p.drive or ":" in raw_path_str:
        raise PathTraversalError(f"Drive-relative or colon path '{raw_path_str}' is forbidden.")

    # Check for traversal components
    for part in p.parts:
        if part == "..":
            raise PathTraversalError(
                f"Path traversal component ('..') detected in '{raw_path_str}'."
            )

    base_resolved = base_dir.resolve()
    target_path = (base_dir / p).resolve()

    try:
        target_path.relative_to(base_resolved)
    except ValueError as err:
        raise PathTraversalError(
            f"Path '{raw_path_str}' attempts to escape sandbox boundary '{base_resolved}'."
        ) from err

    return target_path


@dataclass(frozen=True)
class Workspace:
    """Designated filesystem locations for an isolated execution run."""

    workspace_dir: Path
    src_dir: Path
    output_dir: Path
    research_run_id: str
    execution_id: str
    input_dir: Path | None = None
    logs_dir: Path | None = None
    metadata_dir: Path | None = None
    artifacts_dir: Path | None = None
    experiment_id: str = ""

    def __post_init__(self) -> None:
        if self.input_dir is None:
            object.__setattr__(self, "input_dir", self.workspace_dir / "input")
        if self.logs_dir is None:
            object.__setattr__(self, "logs_dir", self.workspace_dir / "logs")
        if self.metadata_dir is None:
            object.__setattr__(self, "metadata_dir", self.workspace_dir / "metadata")
        if self.artifacts_dir is None:
            object.__setattr__(self, "artifacts_dir", self.workspace_dir / "artifacts")

    def write_src_file(self, relative_path: str | Path, content: str | bytes) -> Path:
        """Write a source code file into the workspace src directory."""
        target = validate_safe_relative_path(relative_path, self.src_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        return target

    def write_input_file(self, relative_path: str | Path, content: str | bytes) -> Path:
        """Write an input data/reference file into the workspace input directory."""
        target = validate_safe_relative_path(relative_path, self.input_dir)  # type: ignore[arg-type]
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        return target

    def write_log_file(self, filename: str, content: str | bytes) -> Path:
        """Write a log file into the workspace logs directory."""
        target = validate_safe_relative_path(filename, self.logs_dir)  # type: ignore[arg-type]
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        return target

    def write_metadata_file(self, filename: str, content: str | bytes) -> Path:
        """Write an execution metadata file into the workspace metadata directory."""
        target = validate_safe_relative_path(filename, self.metadata_dir)  # type: ignore[arg-type]
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        return target

    def read_output_file(self, relative_path: str | Path) -> bytes:
        """Read bytes of an output file from the workspace output directory."""
        target = validate_safe_relative_path(relative_path, self.output_dir)
        return target.read_bytes()

    def list_output_files(self) -> list[Path]:
        """List all regular files currently in the workspace output directory."""
        if not self.output_dir.exists():
            return []
        return [p for p in sorted(self.output_dir.rglob("*")) if p.is_file()]


class WorkspaceManager:
    """Coordinates isolated filesystem workspaces for execution runs."""

    def __init__(self, base_root: Path | None = None) -> None:
        if base_root is not None:
            self.base_root = base_root.resolve()
        else:
            settings = get_settings()
            self.base_root = (settings.persistence.artifact_root / "workspaces").resolve()

    def prepare_workspace(
        self,
        request: ExecutionRequest,
        raise_if_exists: bool = False,
    ) -> Workspace:
        """Create isolated directory structure and populate experiment code files."""
        # Sanitize identifiers
        run_part = validate_safe_relative_path(request.research_run_id, self.base_root)
        exec_dir = validate_safe_relative_path(request.execution_id, run_part)

        if raise_if_exists and exec_dir.exists():
            raise WorkspaceExistsError(
                f"Workspace already exists for execution '{request.execution_id}' at '{exec_dir}'."
            )

        src_dir = exec_dir / "src"
        input_dir = exec_dir / "input"
        output_dir = exec_dir / "output"
        logs_dir = exec_dir / "logs"
        metadata_dir = exec_dir / "metadata"
        artifacts_dir = exec_dir / "artifacts"

        for d in (src_dir, input_dir, output_dir, logs_dir, metadata_dir, artifacts_dir):
            d.mkdir(parents=True, exist_ok=True)

        # Write code files with strict traversal protection
        for rel_file_path, content in request.code_files.items():
            dest_file = validate_safe_relative_path(rel_file_path, src_dir)
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_text(content, encoding="utf-8")

        return Workspace(
            workspace_dir=exec_dir,
            src_dir=src_dir,
            output_dir=output_dir,
            input_dir=input_dir,
            logs_dir=logs_dir,
            metadata_dir=metadata_dir,
            artifacts_dir=artifacts_dir,
            research_run_id=request.research_run_id,
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
        )

    def collect_output_artifacts(
        self,
        workspace: Workspace,
        max_files: int = 1000,
    ) -> list[OutputArtifactMetadata]:
        """Inspect workspace output directory, enforce symlink containment, and extract artifacts."""
        if not workspace.output_dir.exists():
            return []

        collected: list[OutputArtifactMetadata] = []
        workspace_resolved = workspace.workspace_dir.resolve()
        output_resolved = workspace.output_dir.resolve()

        file_count = 0
        for item in sorted(workspace.output_dir.rglob("*")):
            if file_count >= max_files:
                break

            # Symlink escape verification
            if item.is_symlink():
                target = item.resolve()
                try:
                    target.relative_to(workspace_resolved)
                except ValueError as err:
                    raise SymlinkEscapeError(
                        f"Symlink '{item}' resolves to '{target}' which escapes workspace boundary '{workspace_resolved}'."
                    ) from err
                if item.is_dir():
                    continue

            if not item.is_file():
                continue

            file_count += 1
            content_bytes = item.read_bytes()
            content_hash = hashlib.sha256(content_bytes).hexdigest()
            size_bytes = len(content_bytes)

            rel_path = item.relative_to(output_resolved).as_posix()
            artifact_type = self._infer_artifact_type(item.name)

            collected.append(
                OutputArtifactMetadata(
                    path=rel_path,
                    content_hash=content_hash,
                    size_bytes=size_bytes,
                    artifact_type=artifact_type,
                    metadata={"filename": item.name},
                )
            )

        return collected

    def cleanup_workspace(self, workspace: Workspace, retain_evidence: bool = True) -> None:
        """Clean workspace directory.

        If retain_evidence is True, safely preserves outputs, logs, metadata, and artifacts,
        while cleaning up temporary source code and scratch files.
        If retain_evidence is False, removes the entire workspace directory.
        """
        if not workspace.workspace_dir.exists():
            return

        if not retain_evidence:
            shutil.rmtree(workspace.workspace_dir, ignore_errors=True)
            return

        # Selective cleanup: remove src directory, preserve output, logs, metadata, artifacts
        if workspace.src_dir.exists():
            shutil.rmtree(workspace.src_dir, ignore_errors=True)

    @staticmethod
    def _infer_artifact_type(filename: str) -> ArtifactType:
        """Infer domain ArtifactType from file extension and filename conventions."""
        lower_name = filename.lower()
        if lower_name.endswith((".png", ".jpg", ".jpeg", ".svg", ".pdf")):
            return ArtifactType.FIGURE
        if lower_name.endswith((".pt", ".pth", ".onnx", ".bin", ".h5", ".pkl")):
            return ArtifactType.MODEL
        if lower_name.endswith((".csv", ".parquet", ".arrow", ".tsv")):
            return ArtifactType.DATASET
        if lower_name.endswith((".json", ".yaml", ".yml")):
            if "metric" in lower_name:
                return ArtifactType.METRIC
            return ArtifactType.OUTPUT
        if lower_name.endswith((".log", ".txt")):
            return ArtifactType.LOG
        return ArtifactType.OUTPUT
