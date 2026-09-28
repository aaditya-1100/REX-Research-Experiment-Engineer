"""Unit tests for REX Execution Workspace Manager and Path Traversal Protection (REX-009)."""

from pathlib import Path

import pytest

from rex.domain.models import ArtifactType
from rex.execution.exceptions import PathTraversalError, SymlinkEscapeError
from rex.execution.models import ExecutionRequest
from rex.execution.workspace import (
    WorkspaceManager,
    validate_safe_relative_path,
)


class TestWorkspaceSecurity:
    """Tests for workspace filesystem containment and traversal prevention."""

    def test_validate_safe_relative_path(self, tmp_path: Path) -> None:
        base = tmp_path / "sandbox"
        base.mkdir()

        # Valid relative paths
        p1 = validate_safe_relative_path("script.py", base)
        assert p1 == base / "script.py"

        p2 = validate_safe_relative_path("sub/dir/module.py", base)
        assert p2 == base / "sub" / "dir" / "module.py"

    @pytest.mark.parametrize(
        "bad_path",
        [
            "../secret.txt",
            "../../etc/passwd",
            "dir/../../escaped.py",
            "/etc/shadow",
            "C:\\Windows\\System32",
            "C:/sensitive.data",
            "subdir/../..",
            "",
            "   ",
        ],
    )
    def test_path_traversal_attempts_rejected(self, tmp_path: Path, bad_path: str) -> None:
        base = tmp_path / "sandbox"
        base.mkdir()

        with pytest.raises(PathTraversalError):
            validate_safe_relative_path(bad_path, base)

    def test_prepare_workspace_populates_code_files(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(base_root=tmp_path)
        req = ExecutionRequest(
            execution_id="exec-001",
            experiment_id="exp-001",
            research_run_id="run-001",
            command=["python", "src/train.py"],
            code_files={
                "train.py": "print('Training started')",
                "models/net.py": "class Net: pass",
            },
        )

        ws = mgr.prepare_workspace(req)
        assert ws.workspace_dir.exists()
        assert ws.src_dir.exists()
        assert ws.output_dir.exists()

        train_file = ws.src_dir / "train.py"
        assert train_file.exists()
        assert train_file.read_text(encoding="utf-8") == "print('Training started')"

        net_file = ws.src_dir / "models" / "net.py"
        assert net_file.exists()
        assert net_file.read_text(encoding="utf-8") == "class Net: pass"

    def test_collect_output_artifacts_computes_hash_and_type(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(base_root=tmp_path)
        req = ExecutionRequest(
            execution_id="exec-002",
            experiment_id="exp-002",
            research_run_id="run-002",
            command=["python", "src/main.py"],
        )
        ws = mgr.prepare_workspace(req)

        # Produce fake output files
        loss_plot = ws.output_dir / "loss.png"
        loss_plot.write_bytes(b"\x89PNG\r\n\x1a\nfake plot bytes")

        metrics_file = ws.output_dir / "metrics.json"
        metrics_file.write_text('{"accuracy": 0.95}', encoding="utf-8")

        model_weights = ws.output_dir / "checkpoint.pt"
        model_weights.write_bytes(b"binary model weights")

        data_file = ws.output_dir / "predictions.csv"
        data_file.write_text("id,val\n1,0.5\n", encoding="utf-8")

        artifacts = mgr.collect_output_artifacts(ws)
        assert len(artifacts) == 4

        type_map = {a.path: a.artifact_type for a in artifacts}
        assert type_map["loss.png"] == ArtifactType.FIGURE
        assert type_map["metrics.json"] == ArtifactType.METRIC
        assert type_map["checkpoint.pt"] == ArtifactType.MODEL
        assert type_map["predictions.csv"] == ArtifactType.DATASET

        # Check content hashes are populated and valid 64-char hex strings
        for a in artifacts:
            assert len(a.content_hash) == 64
            assert a.size_bytes > 0

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        outside_file = tmp_path / "secret_outside.txt"
        outside_file.write_text("classified data", encoding="utf-8")

        mgr = WorkspaceManager(base_root=tmp_path / "ws_root")
        req = ExecutionRequest(
            execution_id="exec-003",
            experiment_id="exp-003",
            research_run_id="run-003",
            command=["python", "main.py"],
        )
        ws = mgr.prepare_workspace(req)

        # Attempt to create symlink in output pointing outside workspace
        symlink_path = ws.output_dir / "leak_symlink.txt"
        try:
            symlink_path.symlink_to(outside_file)
        except OSError:
            pytest.skip("Symlink creation not permitted in this OS environment")

        with pytest.raises(SymlinkEscapeError, match="escapes workspace boundary"):
            mgr.collect_output_artifacts(ws)
