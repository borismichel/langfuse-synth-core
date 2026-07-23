"""The `synth validate` CLI surface + the importable validator API (#27).

Exercises the console dispatcher (`langfuse_synth_core.authoring.cli:main`, wired to the
`synth` entry point) end to end and asserts the downstream-importable API the portal's
`POST /use-cases/sync` will consume in Spec B.
"""

import importlib.util

import pytest

pytest.importorskip("jsonschema", reason="authoring extra not installed")
pytest.importorskip("yaml", reason="authoring extra not installed")

import yaml  # noqa: E402

from langfuse_synth_core.authoring import cli  # noqa: E402

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="authoring extra not installed",
)

VALID: dict = {
    "schema_version": 1,
    "slug": "demo-uc",
    "name": "Demo UC",
    "tagline": "a demo",
    "target": {"project_hint": "demo", "supports": ["cloud_eu"]},
    "pipeline": [
        {"id": "seed", "run": "synth seed {config}"},
        {"id": "verify", "run": "synth verify {config}"},
    ],
    "artifacts": [{"path": "DEMO_SCRIPT.md", "render": "markdown"}],
}


def _write(tmp_path, manifest: dict, name: str = "usecase.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(manifest))
    return path


def test_cli_valid_manifest_returns_zero(tmp_path, capsys):
    path = _write(tmp_path, VALID)
    rc = cli.main(["validate", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "valid" in out and "demo-uc" in out


def test_cli_invalid_manifest_returns_one(tmp_path, capsys):
    bad = {**VALID, "pipeline": [{"id": "seed", "run": "synth seed {config}"}]}  # no verify
    path = _write(tmp_path, bad)
    rc = cli.main(["validate", str(path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "INVALID" in out and "verify" in out


def test_cli_validates_multiple_and_fails_if_any_invalid(tmp_path):
    good = _write(tmp_path, VALID, name="good.usecase.yaml")
    bad_manifest = {**VALID, "artifacts": [{"path": "x.html", "render": "html"}]}
    bad = _write(tmp_path, bad_manifest, name="bad.usecase.yaml")
    rc = cli.main(["validate", str(good), str(bad)])
    assert rc == 1


def test_cli_no_command_prints_help_and_returns_two(capsys):
    rc = cli.main([])
    assert rc == 2


def test_importable_api_from_authoring_package():
    # The pinned lib API a downstream (portal) consumer imports.
    from langfuse_synth_core import authoring

    for name in (
        "load_schema",
        "semantic_errors",
        "authoring_errors",
        "validate_file",
        "validate_doc",
        "validate_path",
        "ManifestValidationError",
    ):
        assert hasattr(authoring, name), name
    assert authoring.ManifestValidationError.http_status == 422


def test_validate_doc_accepts_prevalidated_mapping():
    from jsonschema import Draft7Validator

    from langfuse_synth_core.authoring import load_schema, validate_doc

    assert validate_doc(VALID, Draft7Validator(load_schema())) == []
