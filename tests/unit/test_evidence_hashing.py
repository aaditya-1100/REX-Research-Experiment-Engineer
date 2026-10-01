"""Unit tests for REX-025 Artifact and Content Hashing Subsystem."""

import hashlib
from pathlib import Path

import pytest

from rex.evidence.hashing import (
    canonical_json_dumps,
    canonical_json_hash,
    compute_bytes_hash,
    compute_file_hash,
    verify_artifact_hash,
)
from rex.persistence.models import ArtifactModel


@pytest.mark.unit
def test_compute_bytes_hash() -> None:
    data = b"hello world scientific data"
    expected = hashlib.sha256(data).hexdigest()
    assert compute_bytes_hash(data) == expected


@pytest.mark.unit
def test_compute_file_hash(tmp_path: Path) -> None:
    test_file = tmp_path / "artifact.bin"
    content = b"random artifact byte stream \x00\xff\xfe\x01\x42"
    test_file.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    computed = compute_file_hash(test_file)
    assert computed == expected


@pytest.mark.unit
def test_compute_file_hash_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.bin"
    with pytest.raises(FileNotFoundError):
        compute_file_hash(missing)


@pytest.mark.unit
def test_compute_file_hash_directory(tmp_path: Path) -> None:
    sub_dir = tmp_path / "sub_dir"
    sub_dir.mkdir()
    with pytest.raises(IsADirectoryError):
        compute_file_hash(sub_dir)


@pytest.mark.unit
def test_canonical_json_dumps_and_hash() -> None:
    # Different key insertion orders
    obj_a = {"beta": 2, "alpha": 1, "nested": {"z": 10, "a": 20}}
    obj_b = {"nested": {"a": 20, "z": 10}, "alpha": 1, "beta": 2}

    dump_a = canonical_json_dumps(obj_a)
    dump_b = canonical_json_dumps(obj_b)

    assert dump_a == dump_b
    assert dump_a == '{"alpha":1,"beta":2,"nested":{"a":20,"z":10}}'

    hash_a = canonical_json_hash(obj_a)
    hash_b = canonical_json_hash(obj_b)
    assert hash_a == hash_b
    assert len(hash_a) == 64


@pytest.mark.unit
def test_verify_artifact_hash_valid(tmp_path: Path) -> None:
    file_path = tmp_path / "weights.pt"
    content = b"model weights bytes 12345"
    file_path.write_bytes(content)
    actual_hash = hashlib.sha256(content).hexdigest()

    art = ArtifactModel(
        research_run_id="run-1",
        artifact_type="model",
        path=str(file_path),
        content_hash=actual_hash,
        size_bytes=len(content),
    )

    res = verify_artifact_hash(art)
    assert res.is_valid is True
    assert res.file_exists is True
    assert res.computed_hash == actual_hash
    assert res.expected_hash == actual_hash
    assert res.size_bytes == len(content)
    assert res.error_message is None


@pytest.mark.unit
def test_verify_artifact_hash_tampered(tmp_path: Path) -> None:
    file_path = tmp_path / "results.csv"
    original_content = b"metric,value\naccuracy,0.95"
    file_path.write_bytes(original_content)
    recorded_hash = hashlib.sha256(original_content).hexdigest()

    # Tamper with file on disk
    file_path.write_bytes(b"metric,value\naccuracy,0.99")

    art = ArtifactModel(
        research_run_id="run-1",
        artifact_type="metric",
        path=str(file_path),
        content_hash=recorded_hash,
        size_bytes=len(original_content),
    )

    res = verify_artifact_hash(art)
    assert res.is_valid is False
    assert res.file_exists is True
    assert res.computed_hash != recorded_hash
    assert "Hash mismatch" in (res.error_message or "")


@pytest.mark.unit
def test_verify_artifact_hash_missing(tmp_path: Path) -> None:
    missing_path = tmp_path / "ghost.png"
    art = ArtifactModel(
        research_run_id="run-1",
        artifact_type="plot",
        path=str(missing_path),
        content_hash="abc123def456",
        size_bytes=100,
    )

    res = verify_artifact_hash(art)
    assert res.is_valid is False
    assert res.file_exists is False
    assert res.computed_hash is None
    assert "not found" in (res.error_message or "").lower()


@pytest.mark.unit
def test_verify_artifact_hash_relative_resolution(tmp_path: Path) -> None:
    rel_path = "sub/relative_artifact.txt"
    full_path = tmp_path / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    content = b"relative content test"
    full_path.write_bytes(content)
    content_hash = hashlib.sha256(content).hexdigest()

    art = ArtifactModel(
        research_run_id="run-1",
        artifact_type="log",
        path=rel_path,
        content_hash=content_hash,
    )

    res = verify_artifact_hash(art, root_dir=tmp_path)
    assert res.is_valid is True
    assert res.file_exists is True
    assert res.computed_hash == content_hash
