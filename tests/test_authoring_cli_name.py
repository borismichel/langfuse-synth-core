"""The authoring CLI must not squat the kit-runtime ``synth`` name (collision fix).

``synth <reserved-verb>`` (probe/plan/seed/verify/resume/teardown) is the KIT's runtime
console script and the portal integration surface: CONTRACT.md's reserved-verb table
requires a ``seed`` step to run ``synth seed …``, and every manifest's pipeline invokes
``synth <verb> --config {config}``. The library's *authoring* toolchain
(validate/freeze/new) must therefore NOT register a bare ``synth`` entry point — the
moment a kit installs the lib (Ring 1, #31) the two console scripts collide and whichever
installs last shadows the other, breaking the kit's pipeline. The authoring CLI is
namespaced as ``synth-authoring``.
"""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

import pytest

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _scripts() -> dict[str, str]:
    return tomllib.loads(_PYPROJECT.read_text()).get("project", {}).get("scripts", {})


def test_authoring_cli_does_not_shadow_the_kit_runtime_synth():
    """No bare ``synth`` entry point — that name belongs to the kit runtime CLI."""
    assert "synth" not in _scripts(), (
        "the bare `synth` console script belongs to the kit runtime "
        "(probe/plan/seed/verify); the authoring CLI must not register it or it shadows "
        "the kit's pipeline when the lib is installed into a kit"
    )


def test_authoring_cli_is_registered_as_synth_authoring():
    """The authoring toolchain ships under the namespaced ``synth-authoring`` name."""
    assert _scripts().get("synth-authoring") == "langfuse_synth_core.authoring.cli:main"


@pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="authoring CLI ships in the [authoring] extra; not installed on a runtime-only job",
)
def test_authoring_parser_prog_matches_the_console_script():
    """``--help``/usage must name the tool ``synth-authoring``, not ``synth``."""
    from langfuse_synth_core.authoring.cli import build_parser

    assert build_parser().prog == "synth-authoring"
