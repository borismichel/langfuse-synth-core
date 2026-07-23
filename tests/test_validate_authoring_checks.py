"""The new #27 authoring checks layered on top of schema + semantic parity.

Covers: `seed`+`verify` present · reserved-verb semantics · >=1 `render:markdown`
artifact · canonical `generation.target_traces` volume-knob consistency.
"""

import copy
import importlib.util

import pytest

pytest.importorskip("jsonschema", reason="authoring extra not installed")
pytest.importorskip("yaml", reason="authoring extra not installed")

import yaml  # noqa: E402
from jsonschema import Draft7Validator  # noqa: E402

from langfuse_synth_core.authoring.validate import (  # noqa: E402
    authoring_errors,
    load_schema,
    validate_file,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="authoring extra not installed",
)

VALIDATOR = Draft7Validator(load_schema())

# A fully #27-compliant baseline: seed+verify pipeline + one markdown artifact, no volume
# knob (a legitimate fixed-volume kit). Each test perturbs exactly one axis.
BASE: dict = {
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


def _validate(tmp_path, manifest: dict) -> list[str]:
    path = tmp_path / "usecase.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return validate_file(path, VALIDATOR)


def test_compliant_baseline_valid(tmp_path):
    assert _validate(tmp_path, BASE) == []


# --- seed + verify present ---------------------------------------------------


def test_missing_seed_rejected(tmp_path):
    m = copy.deepcopy(BASE)
    m["pipeline"] = [{"id": "verify", "run": "synth verify {config}"}]
    errs = _validate(tmp_path, m)
    assert any("seed" in e for e in errs), errs


def test_missing_verify_rejected(tmp_path):
    m = copy.deepcopy(BASE)
    m["pipeline"] = [{"id": "seed", "run": "synth seed {config}"}]
    errs = _validate(tmp_path, m)
    assert any("verify" in e for e in errs), errs


def test_missing_both_reports_both(tmp_path):
    m = copy.deepcopy(BASE)
    m["pipeline"] = [{"id": "plan", "run": "synth plan {config}"}]
    errs = authoring_errors(m)
    assert any("seed" in e for e in errs) and any("verify" in e for e in errs), errs


# --- reserved-verb semantics -------------------------------------------------


def test_reserved_verb_step_must_run_its_verb(tmp_path):
    m = copy.deepcopy(BASE)
    # A step named `seed` that actually runs `synth teardown` is a contract violation.
    m["pipeline"] = [
        {"id": "seed", "run": "synth teardown {config}"},
        {"id": "verify", "run": "synth verify {config}"},
    ]
    errs = _validate(tmp_path, m)
    assert any("reserved verb" in e and "seed" in e for e in errs), errs


def test_reserved_verb_pathed_synth_binary_accepted(tmp_path):
    m = copy.deepcopy(BASE)
    m["pipeline"] = [
        {"id": "seed", "run": "/usr/local/bin/synth seed --config {config}"},
        {"id": "verify", "run": "synth verify {config}"},
    ]
    assert _validate(tmp_path, m) == []


def test_custom_step_id_is_unconstrained(tmp_path):
    m = copy.deepcopy(BASE)
    # `evaluators` / `memo` are not reserved verbs — any command is fine.
    m["pipeline"] = [
        {"id": "seed", "run": "synth seed {config}"},
        {"id": "evaluators", "run": "python -m whatever --provision"},
        {"id": "verify", "run": "synth verify {config}"},
        {"id": "memo", "run": "synth memo {config}"},
    ]
    assert _validate(tmp_path, m) == []


# --- >=1 render:markdown artifact -------------------------------------------


def test_no_artifacts_rejected(tmp_path):
    m = copy.deepcopy(BASE)
    del m["artifacts"]
    errs = _validate(tmp_path, m)
    assert any("markdown" in e for e in errs), errs


def test_artifacts_without_markdown_rejected(tmp_path):
    m = copy.deepcopy(BASE)
    m["artifacts"] = [{"path": "out.html", "render": "html"}]
    errs = _validate(tmp_path, m)
    assert any("markdown" in e for e in errs), errs


def test_at_least_one_markdown_among_many_valid(tmp_path):
    m = copy.deepcopy(BASE)
    m["artifacts"] = [
        {"path": "out.html", "render": "html"},
        {"path": "DEMO_SCRIPT.md", "render": "markdown"},
        {"path": "data.json", "render": "json"},
    ]
    assert _validate(tmp_path, m) == []


# --- canonical target_traces volume-knob consistency ------------------------


def _with_config_schema(props: dict) -> dict:
    m = copy.deepcopy(BASE)
    m["config_schema"] = {"type": "object", "properties": props, "required": []}
    return m


def test_canonical_target_traces_knob_valid(tmp_path):
    m = _with_config_schema(
        {
            "generation.target_traces": {
                "type": "integer",
                "default": 800,
                "minimum": 100,
                "maximum": 6000,
                "title": "Trace volume",
                "description": "Total backdated traces.",
            }
        }
    )
    assert _validate(tmp_path, m) == []


def test_fixed_volume_kit_no_knob_valid(tmp_path):
    # No volume param at all is a legitimate fixed-volume kit.
    m = _with_config_schema({"generation.window_days": {"type": "integer", "enum": [14, 30]}})
    assert _validate(tmp_path, m) == []


def test_target_traces_non_integer_rejected(tmp_path):
    m = _with_config_schema({"generation.target_traces": {"type": "number"}})
    errs = _validate(tmp_path, m)
    assert any("generation.target_traces" in e and "integer" in e for e in errs), errs


def test_canonical_alongside_bespoke_rejected(tmp_path):
    # The ambiguous half-migrated state: canonical knob AND a bespoke one exposed at once.
    m = _with_config_schema(
        {
            "generation.target_traces": {"type": "integer"},
            "generation.total_traces": {"type": "integer"},
        }
    )
    errs = _validate(tmp_path, m)
    assert any("sole operator volume control" in e for e in errs), errs


def test_legacy_bespoke_only_grandfathered(tmp_path):
    # A manifest exposing ONLY a bespoke knob (the pre-migration gold shape) stays valid.
    m = _with_config_schema({"generation.total_traces": {"type": "integer"}})
    assert _validate(tmp_path, m) == []
    m2 = _with_config_schema({"generation.volume.scale": {"type": "number"}})
    assert _validate(tmp_path, m2) == []
