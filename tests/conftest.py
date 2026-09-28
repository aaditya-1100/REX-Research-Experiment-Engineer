"""Pytest configuration and shared fixtures for REX tests."""

import os
import tempfile
from pathlib import Path
import pytest


@pytest.fixture
def temp_research_dir():
    """Provides an isolated temporary directory for research experiments."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
