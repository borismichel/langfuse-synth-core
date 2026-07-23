"""Authoring-side of the distribution boundary.

Runs in the CI job that installs `.[authoring,dev]`. With the extra present, the
subpackage must import cleanly.
"""

import importlib.util

import pytest


@pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="authoring extra not installed; boundary proved by the runtime-only job",
)
def test_authoring_imports_with_the_extra():
    import langfuse_synth_core.authoring  # noqa: F401
