"""Initial sanity test verifying environment and package import."""

import rex


def test_package_import():
    assert rex.__version__ == "0.1.0"
