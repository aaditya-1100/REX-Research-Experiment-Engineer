"""Adversarial Isolation & Sandbox Hardening Stress Suite.

Authored by challenger_campaign_1 (Empirical Challenger).
Stress-tests:
1. Deep path traversal, Windows reserved device names, UNC paths, and directory breakout.
2. Canary token scrubbing, regex patterns, environment key filtering, and API log leakage.
3. SafeCodeVisitor AST filters, forbidden imports/calls, and dynamic evaluation bypass vectors.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from rex.agents.coding import GeneratedCodeProposal, SafeCodeVisitor, validate_code_proposal
from rex.api.app import create_app
from rex.config import RexSettings
from rex.execution.canaries import (
    detect_canary_leakage,
    generate_canary_token,
    scrub_logs_and_credentials,
)
from rex.execution.environment import is_sensitive_key, sanitize_environment
from rex.execution.exceptions import PathTraversalError, SecretLeakageError
from rex.execution.models import ExecutionRequest
from rex.execution.workspace import WorkspaceManager, validate_safe_relative_path
from rex.persistence.database import Base
from rex.persistence.models import ArtifactModel, ExecutionModel, ResearchRunModel

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture
def db_session(session_factory) -> Session:
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def api_client(session_factory) -> TestClient:
    settings = RexSettings()
    engine = session_factory.kw["bind"]
    app = create_app(settings=settings, db_engine=engine, db_session_factory=session_factory)
    return TestClient(app)


# =============================================================================
# 1. Path Traversal & Sandbox Boundary Stress Tests
# =============================================================================


class TestPathTraversalAndSandboxStress:
    """Stress-tests validate_safe_relative_path, WorkspaceManager, and Artifact API."""

    @pytest.mark.parametrize(
        "device_name",
        [
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "COM2",
            "COM3",
            "COM4",
            "COM5",
            "COM6",
            "COM7",
            "COM8",
            "COM9",
            "LPT1",
            "LPT2",
            "LPT3",
            "LPT4",
            "LPT5",
            "LPT6",
            "LPT7",
            "LPT8",
            "LPT9",
            # Case variations
            "con",
            "prn",
            "aux",
            "nul",
            "com1",
            "lpt9",
            "CoN",
            "NuL",
            # With file extensions
            "CON.txt",
            "prn.log",
            "AUX.json",
            "NUL.py",
            "com1.bin",
            "LPT5.tar",
            # Inside subdirectories
            "sub/CON",
            "deep/nested/dir/PRN.txt",
            "models/weights/NUL.pt",
            # Path components
            "CON/foo.txt",
            "aux/subfile.json",
        ],
    )
    def test_windows_reserved_device_names_exhaustive(
        self, device_name: str, tmp_path: Path
    ) -> None:
        """All variations of Windows device names in paths must be rejected."""
        with pytest.raises(PathTraversalError):
            validate_safe_relative_path(device_name, tmp_path)

    @pytest.mark.parametrize(
        "unc_path",
        [
            "//server/share/file.py",
            "\\\\server\\share\\file.py",
            "//127.0.0.1/c$/windows/system32/cmd.exe",
            "\\\\localhost\\share\\data.csv",
            "//?/C:/escaped/path.py",
            "\\\\?\\C:\\escaped\\path.py",
            "//./pipe/docker_engine",
        ],
    )
    def test_unc_paths_exhaustive(self, unc_path: str, tmp_path: Path) -> None:
        """All forms of UNC paths must be rejected."""
        with pytest.raises(PathTraversalError):
            validate_safe_relative_path(unc_path, tmp_path)

    @pytest.mark.parametrize(
        "traversal_path",
        [
            "../secret.txt",
            "..\\secret.txt",
            "foo/../../secret.txt",
            "foo/bar/../../../secret.txt",
            "./../secret.txt",
            "foo/bar/..",
            "a/b/c/../../../../etc/shadow",
            "/",
            "\\",
            "C:/Windows",
            "C:\\Windows\\System32",
            "D:data.txt",
            "E:\\",
            "payload.py\x00/../../etc/passwd",
            "\x00",
            "",
            "   ",
            "\t\n",
        ],
    )
    def test_traversal_and_absolute_paths_exhaustive(
        self, traversal_path: str, tmp_path: Path
    ) -> None:
        """Every directory breakout, absolute path, drive root, and null byte must raise PathTraversalError."""
        with pytest.raises(PathTraversalError):
            validate_safe_relative_path(traversal_path, tmp_path)

    def test_workspace_manager_rejects_traversal_in_run_and_exec_ids(self, tmp_path: Path) -> None:
        """WorkspaceManager must reject attempts to break out via research_run_id or execution_id."""
        manager = WorkspaceManager(base_root=tmp_path)

        # Hostile research_run_id
        req_bad_run = ExecutionRequest(
            execution_id="exec_valid",
            experiment_id="exp_1",
            research_run_id="../escaped_run",
            command=["python", "main.py"],
        )
        with pytest.raises(PathTraversalError):
            manager.prepare_workspace(req_bad_run)

        # Hostile execution_id
        req_bad_exec = ExecutionRequest(
            execution_id="../../escaped_exec",
            experiment_id="exp_1",
            research_run_id="valid_run",
            command=["python", "main.py"],
        )
        with pytest.raises(PathTraversalError):
            manager.prepare_workspace(req_bad_exec)

        # Hostile code file path
        req_bad_file = ExecutionRequest(
            execution_id="exec_valid",
            experiment_id="exp_1",
            research_run_id="run_valid",
            command=["python", "main.py"],
            code_files={"../../evil.py": "print('evil')"},
        )
        with pytest.raises(PathTraversalError):
            manager.prepare_workspace(req_bad_file)

    def test_artifact_content_api_blocks_breakouts(
        self, api_client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """GET /api/artifacts/{id}/content must return 403 on traversal attempts."""
        # Create a file outside the artifact root
        secret_file = tmp_path / "super_secret.txt"
        secret_file.write_text("HOST_SECRETS_NEVER_LEAK")

        run = ResearchRunModel(id="run_test", title="Test Run", research_question="Test Q")
        db_session.add(run)

        # Artifact pointing outside via absolute path
        art_abs = ArtifactModel(
            id="art_outside_abs",
            research_run_id="run_test",
            execution_id="exec_1",
            path=str(secret_file.resolve()),
            artifact_type="log",
            size_bytes=secret_file.stat().st_size,
            content_hash="abc",
        )
        db_session.add(art_abs)

        # Artifact pointing outside via traversal
        art_rel = ArtifactModel(
            id="art_outside_rel",
            research_run_id="run_test",
            execution_id="exec_1",
            path="../../super_secret.txt",
            artifact_type="log",
            size_bytes=secret_file.stat().st_size,
            content_hash="abc",
        )
        db_session.add(art_rel)
        db_session.commit()

        # Both must return 403 Forbidden
        resp_abs = api_client.get(f"/api/artifacts/{art_abs.id}/content")
        assert resp_abs.status_code == 403

        resp_rel = api_client.get(f"/api/artifacts/{art_rel.id}/content")
        assert resp_rel.status_code == 403


# =============================================================================
# 2. Canary Secret Scrubbing & Sensitive Env Stress Tests
# =============================================================================


class TestCanarySecretScrubbingStress:
    """Stress-tests canary generation, registration, pattern detection, and log scrubbing."""

    def test_canary_generation_uniqueness_and_registration(self) -> None:
        """Generated canary tokens must be cryptographically unique and registered."""
        token1 = generate_canary_token()
        token2 = generate_canary_token()
        assert token1 != token2
        assert token1.startswith("rex_canary_")
        assert len(token1) >= 40

        # Must be detected in text
        sample_log = f"Process spawned with token {token1} and secret data."
        detected = detect_canary_leakage(sample_log)
        assert token1 in detected

    def test_canary_scrubbing_replaces_all_registered_and_pattern_canaries(self) -> None:
        """scrub_logs_and_credentials must replace active tokens and patterns with asterisks."""
        token = generate_canary_token(prefix="rex_canary_test_")
        raw_log = (
            f"Execution traceback:\n"
            f"  Authorization: Bearer my_super_secret_bearer_token_12345\n"
            f"  Canary token: {token}\n"
            f"  Pattern token: CANARY_SECRET_abcdef123456\n"
            f"  API_KEY: sk-proj-1234567890abcdef1234567890\n"
            f"  SECRET=top_secret_vault_credential_987\n"
        )

        scrubbed = scrub_logs_and_credentials(raw_log)

        assert token not in scrubbed
        assert "my_super_secret_bearer_token_12345" not in scrubbed
        assert "CANARY_SECRET_abcdef123456" not in scrubbed
        assert "sk-proj-1234567890abcdef1234567890" not in scrubbed
        assert "top_secret_vault_credential_987" not in scrubbed
        assert "********" in scrubbed

    @pytest.mark.parametrize(
        "sensitive_key",
        [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "GITHUB_TOKEN",
            "SLACK_BOT_TOKEN",
            "STRIPE_SECRET_KEY",
            "DATABASE_URL",
            "REX_DATABASE_URL",
            "REX_LLM_API_KEY",
            "REX_LITERATURE_API_KEY",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "SSH_PRIVATE_KEY",
            "USER_PASSWORD",
            "DB_PASS",
            "AUTH_SECRET",
            "canary_token_rex",
        ],
    )
    def test_environment_sensitive_keys_flagged(self, sensitive_key: str) -> None:
        """is_sensitive_key must flag all standard cloud, LLM, and secret credential keys."""
        assert is_sensitive_key(sensitive_key) is True

    def test_sanitize_environment_rejects_leakage(self) -> None:
        """sanitize_environment must reject dictionary containing any sensitive key."""
        clean_env = {"PYTHONPATH": "src", "BATCH_SIZE": "32"}
        assert sanitize_environment(clean_env) == clean_env

        dirty_env = {"PYTHONPATH": "src", "OPENAI_API_KEY": "sk-leak"}
        with pytest.raises(SecretLeakageError):
            sanitize_environment(dirty_env)

    def test_api_execution_logs_scrubs_canary_and_api_keys(
        self, api_client: TestClient, db_session: Session, tmp_path: Path
    ) -> None:
        """GET /api/executions/{id}/logs must scrub all credentials before returning."""
        canary = generate_canary_token()
        log_file = tmp_path / "stdout.log"
        log_file.write_text(
            f"Execution started.\nToken: {canary}\nAPI_KEY: my_leaked_openai_key_abcdef123\nDone."
        )

        run = ResearchRunModel(id="run_log_test", title="Log Run", research_question="Log Q")
        db_session.add(run)

        from rex.persistence.models import ExperimentModel

        exp = ExperimentModel(id="exp_log_test", research_run_id=run.id, objective="Test exp")
        db_session.add(exp)

        art = ArtifactModel(
            id="art_stdout",
            research_run_id="run_log_test",
            execution_id="exec_log_test",
            path=str(log_file.resolve()),
            artifact_type="log",
            size_bytes=log_file.stat().st_size,
            content_hash="hash1",
        )
        db_session.add(art)

        execution = ExecutionModel(
            id="exec_log_test",
            experiment_id="exp_log_test",
            status="completed",
            exit_code=0,
            stdout_artifact_id="art_stdout",
        )
        db_session.add(execution)
        db_session.commit()

        resp = api_client.get("/api/executions/exec_log_test/logs")
        assert resp.status_code == 200
        data = resp.json()

        assert canary not in data["stdout"]
        assert "my_leaked_openai_key_abcdef123" not in data["stdout"]
        assert "********" in data["stdout"]


# =============================================================================
# 3. AST Code Generation Filters (SafeCodeVisitor) Stress Tests
# =============================================================================


class TestSafeCodeVisitorStress:
    """Stress-tests AST inspection of code proposals for forbidden modules and builtins."""

    @pytest.mark.parametrize(
        "module_name",
        [
            "os",
            "sys",
            "subprocess",
            "socket",
            "pty",
            "ctypes",
            "shutil",
            "urllib",
            "requests",
            "multiprocessing",
        ],
    )
    def test_forbidden_module_direct_imports_rejected(self, module_name: str) -> None:
        """Direct imports of dangerous modules must be blocked."""
        code = f"import {module_name}\nprint('attack')"
        tree = ast.parse(code)
        visitor = SafeCodeVisitor(filename="test.py")
        visitor.visit(tree)
        assert len(visitor.errors) > 0
        assert any(module_name in err for err in visitor.errors)

    @pytest.mark.parametrize(
        "module_name",
        [
            "os",
            "sys",
            "subprocess",
            "socket",
            "pty",
            "ctypes",
            "shutil",
            "urllib",
            "requests",
            "multiprocessing",
        ],
    )
    def test_forbidden_module_from_imports_rejected(self, module_name: str) -> None:
        """From-imports of dangerous modules must be blocked."""
        code = f"from {module_name} import something\nprint('attack')"
        tree = ast.parse(code)
        visitor = SafeCodeVisitor(filename="test.py")
        visitor.visit(tree)
        assert len(visitor.errors) > 0
        assert any(module_name in err for err in visitor.errors)

    @pytest.mark.parametrize(
        "submodule",
        [
            "os.path",
            "urllib.request",
            "urllib.parse",
            "multiprocessing.pool",
        ],
    )
    def test_forbidden_submodule_imports_rejected(self, submodule: str) -> None:
        """Submodule imports (e.g. urllib.request, os.path) must be blocked."""
        code = f"import {submodule}\nprint('attack')"
        tree = ast.parse(code)
        visitor = SafeCodeVisitor(filename="test.py")
        visitor.visit(tree)
        assert len(visitor.errors) > 0

    @pytest.mark.parametrize(
        "builtin_call",
        [
            "eval('2+2')",
            "exec('x = 1')",
            "__import__('math')",
            "open('test.txt', 'w')",
            "compile('1+1', '', 'eval')",
            "breakpoint()",
        ],
    )
    def test_forbidden_builtin_calls_rejected(self, builtin_call: str) -> None:
        """Calls to forbidden builtins must be blocked."""
        code = f"result = {builtin_call}"
        tree = ast.parse(code)
        visitor = SafeCodeVisitor(filename="test.py")
        visitor.visit(tree)
        assert len(visitor.errors) > 0

    def test_safe_scientific_code_passes(self) -> None:
        """Legitimate scientific and numerical Python code must pass with zero errors."""
        scientific_code = """
import math
import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import accuracy_score

def train():
    data = np.random.randn(100, 10)
    tensor = torch.tensor(data)
    norm = math.sqrt(10.0)
    score = accuracy_score([1, 0], [1, 0])
    return score
"""
        tree = ast.parse(scientific_code)
        visitor = SafeCodeVisitor(filename="main.py")
        visitor.visit(tree)
        assert len(visitor.errors) == 0

        proposal = GeneratedCodeProposal(
            entrypoint="main.py",
            source_files={"main.py": scientific_code},
            command=["python", "src/main.py"],
            dependencies=["numpy", "torch", "scikit-learn"],
        )
        errors = validate_code_proposal(proposal)
        assert len(errors) == 0
