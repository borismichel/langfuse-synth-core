"""Strict-superset parity with the portal's historical ``tools/validate_manifest.py``.

Ported from the portal's ``tools/tests/test_validate_manifest.py``. It proves the
relocated validator reproduces **every Draft7 schema error and every ``semantic_errors``
rule** (LAN-378 / LAN-400) the portal enforced, run through the real single choke point
(``validate_file`` → Draft7 schema + semantic + new authoring checks).

The one adaptation vs. the portal suite: ``BASE`` is upgraded to satisfy the NEW #27
authoring floor (a ``verify`` step + a ``render:markdown`` artifact) so the clean cases
stay clean under the superset. The schema and semantic behaviour under test is identical
to the portal's — every original assertion is preserved verbatim.
"""

import copy
import importlib.util

import pytest

pytest.importorskip("jsonschema", reason="authoring extra not installed")
pytest.importorskip("yaml", reason="authoring extra not installed")

import yaml  # noqa: E402
from jsonschema import Draft7Validator  # noqa: E402

from langfuse_synth_core.authoring.validate import (  # noqa: E402
    load_schema,
    semantic_errors,
    validate_file,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="authoring extra not installed",
)

VALIDATOR = Draft7Validator(load_schema())

# Minimal manifest that is valid under BOTH the portal schema/semantics AND the new #27
# authoring floor: required schema fields + a `seed`+`verify` pipeline + one markdown
# artifact. Variants below add live_components / llm on top, exactly as the portal suite.
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


def _with_live(secrets: list[str]) -> dict:
    m = copy.deepcopy(BASE)
    m["live_components"] = [
        {
            "name": "playground",
            "command": "synth serve --host 0.0.0.0 --port 8080",
            "port": 8080,
            "requires_secrets": secrets,
        }
    ]
    return m


# --- back-compat: today's manifests keep validating -------------------------


def test_base_manifest_valid(tmp_path):
    assert _validate(tmp_path, BASE) == []


def test_bare_anthropic_no_llm_block_stays_valid(tmp_path):
    """The shape every current kit ships: ANTHROPIC_API_KEY, no `llm` block."""
    m = _with_live(["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "ANTHROPIC_API_KEY"])
    assert "llm" not in m
    assert _validate(tmp_path, m) == []


def test_live_component_without_llm_key_and_no_llm_block_valid(tmp_path):
    m = _with_live(["LANGFUSE_PUBLIC_KEY", "LANGFUSE_BASE_URL"])
    assert _validate(tmp_path, m) == []


# --- LLM_API_KEY sentinel + llm block ---------------------------------------


def test_llm_api_key_with_llm_block_valid(tmp_path):
    m = _with_live(["LANGFUSE_PUBLIC_KEY", "LLM_API_KEY"])
    m["llm"] = {"providers": ["anthropic", "openai"]}
    assert _validate(tmp_path, m) == []


def test_llm_block_with_models_subset_valid(tmp_path):
    m = _with_live(["LLM_API_KEY"])
    m["llm"] = {
        "providers": ["anthropic", "openai"],
        "models": {"anthropic": "claude-sonnet-4-5", "openai": "gpt-5.2"},
    }
    assert _validate(tmp_path, m) == []


def test_llm_block_single_provider_valid(tmp_path):
    m = _with_live(["LLM_API_KEY"])
    m["llm"] = {"providers": ["openai"]}
    assert _validate(tmp_path, m) == []


# --- rule (a): LLM_API_KEY requires an llm block ----------------------------


def test_llm_api_key_without_llm_block_rejected(tmp_path):
    m = _with_live(["LANGFUSE_PUBLIC_KEY", "LLM_API_KEY"])
    errs = _validate(tmp_path, m)
    assert any("LLM_API_KEY" in e and "llm" in e for e in errs), errs


# --- rule (b): may not mix LLM_API_KEY and ANTHROPIC_API_KEY -----------------


def test_mixing_llm_api_key_and_anthropic_rejected(tmp_path):
    m = _with_live(["LLM_API_KEY", "ANTHROPIC_API_KEY"])
    m["llm"] = {"providers": ["anthropic"]}
    errs = _validate(tmp_path, m)
    assert any("mix" in e.lower() for e in errs), errs


def test_mixing_across_two_components_rejected(tmp_path):
    """The mix rule is manifest-level (union across all live components)."""
    m = copy.deepcopy(BASE)
    m["llm"] = {"providers": ["anthropic"]}
    m["live_components"] = [
        {"name": "a", "command": "c --port 1", "port": 1, "requires_secrets": ["LLM_API_KEY"]},
        {
            "name": "b",
            "command": "c --port 2",
            "port": 2,
            "requires_secrets": ["ANTHROPIC_API_KEY"],
        },
    ]
    errs = _validate(tmp_path, m)
    assert any("mix" in e.lower() for e in errs), errs


# --- rule (c): llm.models keys must be a subset of llm.providers ------------


def test_models_key_not_in_providers_rejected(tmp_path):
    m = _with_live(["LLM_API_KEY"])
    m["llm"] = {"providers": ["anthropic"], "models": {"openai": "gpt-5.2"}}
    errs = _validate(tmp_path, m)
    assert any("llm/models" in e for e in errs), errs


# --- schema-level guards ----------------------------------------------------


def test_unknown_provider_rejected(tmp_path):
    m = _with_live(["LLM_API_KEY"])
    m["llm"] = {"providers": ["anthropic", "gemini"]}
    errs = _validate(tmp_path, m)
    assert errs  # gemini not in the schema enum


def test_empty_providers_rejected(tmp_path):
    m = _with_live(["LLM_API_KEY"])
    m["llm"] = {"providers": []}
    errs = _validate(tmp_path, m)
    assert errs  # minItems: 1


def test_llm_unknown_key_rejected(tmp_path):
    m = _with_live(["LLM_API_KEY"])
    m["llm"] = {"providers": ["anthropic"], "temperature": 0.7}
    errs = _validate(tmp_path, m)
    assert errs  # additionalProperties: false


def test_missing_required_top_level_field_rejected(tmp_path):
    """Draft7 `required` reproduction: dropping `slug` must be flagged at (root)."""
    m = copy.deepcopy(BASE)
    del m["slug"]
    errs = _validate(tmp_path, m)
    assert any("slug" in e for e in errs), errs


def test_top_level_additional_property_rejected(tmp_path):
    """Draft7 additionalProperties:false catches a typo'd top-level key."""
    m = copy.deepcopy(BASE)
    m["taglineee"] = "typo"
    errs = _validate(tmp_path, m)
    assert errs


# --- semantic_errors unit-level (independent of schema) ---------------------


def test_semantic_errors_clean_on_backcompat():
    assert semantic_errors(_with_live(["ANTHROPIC_API_KEY"])) == []
