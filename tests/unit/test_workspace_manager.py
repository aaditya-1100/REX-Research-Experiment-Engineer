"""Unit tests for Experiment Workspace Manager and Subdirectory Containment (REX-018)."""

from pathlib import Path

import pytest

from rex.execution.exceptions import PathTraversalError, WorkspaceExistsError
from rex.execution.models import ExecutionRequest
from rex.execution.workspace import Workspace, WorkspaceManager


class TestWorkspaceManagerSubdirectories:
    """Tests for workspace subdirectories, collision handling, and selective cleanup."""

    def test_prepare_workspace_creates_all_subdirectories(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(base_root=tmp_path)
        req = ExecutionRequest(
            execution_id="exec-ws-001",
            experiment_id="exp-ws-001",
            research_run_id="run-ws-001",
            command=["python", "src/main.py"],
            code_files={"main.py": "print('hello')"},
        )

        ws = mgr.prepare_workspace(req)

        assert ws.workspace_dir.exists()
        assert ws.src_dir.exists()
        assert ws.input_dir.exists()
        assert ws.output_dir.exists()
        assert ws.logs_dir.exists()
        assert ws.metadata_dir.exists()
        assert ws.artifacts_dir.exists()

        assert ws.src_dir == ws.workspace_dir / "src"
        assert ws.input_dir == ws.workspace_dir / "input"
        assert ws.output_dir == ws.workspace_dir / "output"
        assert ws.logs_dir == ws.workspace_dir / "logs"
        assert ws.metadata_dir == ws.workspace_dir / "metadata"
        assert ws.artifacts_dir == ws.workspace_dir / "artifacts"

        assert (ws.src_dir / "main.py").read_text(encoding="utf-8") == "print('hello')"

    def test_workspace_collision_detection(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(base_root=tmp_path)
        req = ExecutionRequest(
            execution_id="exec-collision",
            experiment_id="exp-001",
            research_run_id="run-001",
            command=["python", "src/main.py"],
        )

        mgr.prepare_workspace(req)

        # Preparing again with raise_if_exists=True must fail
        with pytest.raises(WorkspaceExistsError):
            mgr.prepare_workspace(req, raise_if_exists=True)

    def test_workspace_helper_methods(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "custom_ws"
        ws_dir.mkdir(parents=True)
        ws = Workspace(
            workspace_dir=ws_dir,
            src_dir=ws_dir / "src",
            output_dir=ws_dir / "output",
            research_run_id="run-1",
            execution_id="exec-1",
        )

        # Write src file
        src_path = ws.write_src_file("algo.py", "def run(): pass")
        assert src_path.exists()
        assert src_path.read_text(encoding="utf-8") == "def run(): pass"

        # Write input file
        input_path = ws.write_input_file("data.csv", "a,b\n1,2\n")
        assert input_path.exists()
        assert input_path.read_text(encoding="utf-8") == "a,b\n1,2\n"

        # Write log file
        log_path = ws.write_log_file("stdout.log", "program output")
        assert log_path.exists()
        assert log_path.read_text(encoding="utf-8") == "program output"

        # Write metadata file
        meta_path = ws.write_metadata_file("env.json", '{"py": "3.11"}')
        assert meta_path.exists()
        assert meta_path.read_text(encoding="utf-8") == '{"py": "3.11"}'

        # Read output file and list outputs
        (ws.output_dir).mkdir(parents=True, exist_ok=True)
        out_file = ws.output_dir / "result.txt"
        out_file.write_bytes(b"result bytes")

        read_bytes = ws.read_output_file("result.txt")
        assert read_bytes == b"result bytes"

        output_files = ws.list_output_files()
        assert len(output_files) == 1
        assert output_files[0].name == "result.txt"

    def test_workspace_helper_path_traversal_rejection(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "custom_ws"
        ws = Workspace(
            workspace_dir=ws_dir,
            src_dir=ws_dir / "src",
            output_dir=ws_dir / "output",
            research_run_id="run-1",
            execution_id="exec-1",
        )

        with pytest.raises(PathTraversalError):
            ws.write_src_file("../escaped.py", "malicious code")

        with pytest.raises(PathTraversalError):
            ws.write_input_file("../../etc/passwd", "malicious input")

    def test_selective_cleanup_preserves_evidence(self, tmp_path: Path) -> None:
        mgr = WorkspaceManager(base_root=tmp_path)
        req = ExecutionRequest(
            execution_id="exec-cleanup-test",
            experiment_id="exp-001",
            research_run_id="run-001",
            command=["python", "src/main.py"],
            code_files={"main.py": "print('work')"},
        )
        ws = mgr.prepare_workspace(req)

        # Create output, log, and metadata evidence
        (ws.output_dir / "metrics.json").write_text('{"acc": 0.9}', encoding="utf-8")
        (ws.logs_dir / "stdout.log").write_text("log content", encoding="utf-8")
        (ws.metadata_dir / "env.json").write_text('{"python": "3.11"}', encoding="utf-8")
        (ws.artifacts_dir / "model.pt").write_bytes(b"fake model")

        # Selective cleanup (retain_evidence=True)
        mgr.cleanup_workspace(ws, retain_evidence=True)

        # src directory should be removed
        assert not ws.src_dir.exists()

        # Evidence directories MUST be preserved
        assert ws.workspace_dir.exists()
        assert ws.output_dir.exists()
        assert (ws.output_dir / "metrics.json").exists()
        assert ws.logs_dir.exists()
        assert (ws.logs_dir / "stdout.log").exists()
        assert ws.metadata_dir.exists()
        assert (ws.metadata_dir / "env.json").exists()
        assert ws.artifacts_dir.exists()
        assert (ws.artifacts_dir / "model.pt").exists()

        # Full cleanup (retain_evidence=False)
        mgr.cleanup_workspace(ws, retain_evidence=False)
        assert not ws.workspace_dir.exists()
