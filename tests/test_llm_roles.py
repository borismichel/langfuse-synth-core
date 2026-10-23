"""Public versioned roles: authoring and frozen runtime bindings."""

import pytest

from jsonschema import Draft7Validator

from langfuse_synth_core.authoring.validate import load_schema, validate_doc


def manifest():
    return {
        "schema_version": 1,
        "slug": "role-demo",
        "name": "Role demo",
        "tagline": "Demo",
        "target": {"project_hint": "demo", "supports": ["cloud_eu"]},
        "pipeline": [
            {"id": "seed", "run": "synth seed {config}"},
            {"id": "verify", "run": "synth verify {config}"},
        ],
        "artifacts": [{"path": "DEMO_SCRIPT.md", "render": "markdown"}],
        "config_schema": {
            "type": "object",
            "properties": {"features.voice": {"type": "boolean", "default": False}},
        },
        "llm": {
            "providers": ["anthropic"],
            "roles": {
                "version": 1,
                "bindings": {
                    "judge": {"provider": "selected", "models": {"anthropic": "judge-model"}},
                    "speech": {
                        "provider": "openai",
                        "models": {"openai": "speech-model"},
                        "enabled_by": "features.voice",
                    },
                },
            },
        },
        "live_components": [
            {
                "name": "companion",
                "command": "synth live {config}",
                "port": 8080,
                "requires_secrets": ["LLM_API_KEY"],
                "llm_roles": ["judge", "speech"],
            }
        ],
    }


def test_optional_feature_roles_are_authorable_and_resolve_only_enabled_models():
    doc = manifest()
    assert validate_doc(doc, Draft7Validator(load_schema())) == []
    from langfuse_synth_core.llm_roles import resolve_manifest_roles

    bindings = resolve_manifest_roles(doc, ["judge", "speech"], "anthropic", {})
    assert {k: (v.provider, v.model) for k, v in bindings.items()} == {
        "judge": ("anthropic", "judge-model")
    }
    enabled = resolve_manifest_roles(
        doc, ["judge", "speech"], "anthropic", {"features.voice": True}
    )
    assert enabled["speech"].provider == "openai"
    assert enabled["speech"].model == "speech-model"


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["llm"]["roles"].update(version=2),
        lambda d: d["live_components"][0].update(llm_roles=["missing"]),
        lambda d: d["llm"]["roles"]["bindings"]["judge"].update(
            models={"openai": "wrong-provider"}
        ),
        lambda d: d["llm"]["roles"]["bindings"]["speech"].update(enabled_by="missing"),
        lambda d: d["config_schema"]["properties"]["features.voice"].update(type="string"),
        lambda d: d["config_schema"]["properties"]["features.voice"].pop("default"),
    ],
)
def test_authoring_rejects_unresolvable_role_capabilities(change):
    doc = manifest()
    change(doc)
    assert validate_doc(doc, Draft7Validator(load_schema()))


def test_legacy_manifest_needs_no_role_capability():
    doc = manifest()
    del doc["llm"]["roles"]
    del doc["live_components"][0]["llm_roles"]
    assert validate_doc(doc, Draft7Validator(load_schema())) == []


@pytest.mark.parametrize(
    "value",
    [
        '{"version":2,"bindings":{}}',
        '{"version":true,"bindings":{}}',
        '{"version":1,"bindings":{"speech":{"provider":"openai","model":"speech","api_key":"sensitive-fixture"}}}',
        "not-json",
        "[]",
    ],
)
def test_frozen_role_envelope_refuses_invalid_or_secret_bearing_input(value):
    from langfuse_synth_core.llm_roles import decode_role_bindings

    with pytest.raises(ValueError, match="invalid versioned LLM role bindings") as exc:
        decode_role_bindings(value)
    assert "sensitive-fixture" not in str(exc.value)


def test_role_switch_requires_boolean_override_without_string_truthiness():
    from langfuse_synth_core.llm_roles import resolve_manifest_roles

    with pytest.raises(ValueError, match="boolean"):
        resolve_manifest_roles(manifest(), ["speech"], "anthropic", {"features.voice": "false"})
