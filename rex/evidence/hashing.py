"""REX Artifact and Content Hashing Subsystem (REX-025).

Provides cryptographic integrity verification for experimental artifacts, datasets,
code files, and serialized metadata. Ensures mechanical tamper detection over exact
disk bytes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HashVerificationResult:
    """Result of verifying an artifact's cryptographic content hash against disk bytes."""

    path: Path
    expected_hash: str
    computed_hash: str | None
    is_valid: bool
    file_exists: bool
    size_bytes: int | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Convert result to a serializable dictionary."""
        return {
            "path": str(self.path),
            "expected_hash": self.expected_hash,
            "computed_hash": self.computed_hash,
            "is_valid": self.is_valid,
            "file_exists": self.file_exists,
            "size_bytes": self.size_bytes,
            "error_message": self.error_message,
        }


def compute_bytes_hash(data: bytes) -> str:
    """Compute standard SHA-256 hex digest for an in-memory byte sequence."""
    return hashlib.sha256(data).hexdigest()


def compute_file_hash(path: Path | str, chunk_size: int = 65536) -> str:
    """Compute standard SHA-256 hex digest for raw bytes read from a file on disk.

    Streams chunks to prevent excessive memory usage on large execution artifacts.

    Raises:
        FileNotFoundError: If the file does not exist on disk.
        IsADirectoryError: If the path points to a directory rather than a file.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Artifact file does not exist: {file_path}")
    if file_path.is_dir():
        raise IsADirectoryError(f"Artifact path is a directory, not a file: {file_path}")

    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def canonical_json_dumps(data: Any) -> str:
    """Serialize structured data to a canonical, deterministic JSON string.

    Uses sorted keys, no whitespace around separators, and explicit UTF-8 encoding.
    """
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_json_hash(data: Any) -> str:
    """Compute the SHA-256 hex digest of canonical JSON-serialized structured data."""
    canonical_str = canonical_json_dumps(data)
    return compute_bytes_hash(canonical_str.encode("utf-8"))


def verify_artifact_hash(
    artifact: Any,
    root_dir: Path | str | None = None,
) -> HashVerificationResult:
    """Verify that an artifact's disk content matches its registered cryptographic hash.

    Inspects exact raw bytes from disk rather than reconstructed representations.
    Never mutates the artifact record or repairs mismatches.

    Args:
        artifact: Domain Artifact or persistence ArtifactModel containing `path`
            and `content_hash`.
        root_dir: Optional root directory to resolve relative paths against.

    Returns:
        HashVerificationResult indicating whether the file exists and whether
        computed bytes hash matches the expected hash.
    """
    raw_path_str = getattr(artifact, "path", None)
    expected_hash = getattr(artifact, "content_hash", None)

    if raw_path_str is None or expected_hash is None:
        return HashVerificationResult(
            path=Path(str(raw_path_str or "")),
            expected_hash=str(expected_hash or ""),
            computed_hash=None,
            is_valid=False,
            file_exists=False,
            error_message="Artifact missing path or content_hash attribute.",
        )

    file_path = Path(raw_path_str)
    if not file_path.is_absolute() and root_dir is not None:
        file_path = Path(root_dir) / file_path

    if not file_path.exists():
        return HashVerificationResult(
            path=file_path,
            expected_hash=expected_hash,
            computed_hash=None,
            is_valid=False,
            file_exists=False,
            size_bytes=None,
            error_message=f"Artifact file not found at: {file_path}",
        )

    if file_path.is_dir():
        return HashVerificationResult(
            path=file_path,
            expected_hash=expected_hash,
            computed_hash=None,
            is_valid=False,
            file_exists=True,
            size_bytes=None,
            error_message=f"Artifact path points to directory, not file: {file_path}",
        )

    try:
        size_bytes = file_path.stat().st_size
        computed_hash = compute_file_hash(file_path)
    except OSError as exc:
        return HashVerificationResult(
            path=file_path,
            expected_hash=expected_hash,
            computed_hash=None,
            is_valid=False,
            file_exists=True,
            size_bytes=None,
            error_message=f"Failed to read artifact bytes: {exc}",
        )

    is_valid = computed_hash.lower() == expected_hash.strip().lower()
    error_msg = (
        None
        if is_valid
        else (
            f"Hash mismatch for {file_path.name}: "
            f"expected {expected_hash}, computed {computed_hash}"
        )
    )

    return HashVerificationResult(
        path=file_path,
        expected_hash=expected_hash,
        computed_hash=computed_hash,
        is_valid=is_valid,
        file_exists=True,
        size_bytes=size_bytes,
        error_message=error_msg,
    )
