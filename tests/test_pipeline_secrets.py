"""The custom setup capability is opt-in and cannot grant seed a provider key."""

import copy

import pytest

pytest.importorskip("jsonschema", reason="authoring extra not installed")
pytest.importorskip("yaml", reason="authoring extra not installed")

import yaml  # noqa: E402
from jsonschema import Draft7Validator  # noqa: E402

from langfuse_synth_core.authoring.conformance import run_conformance  # noqa: E402
from langfuse_synth_core.authoring.validate import (  # noqa: E402
    PIPELINE_PROVIDER_FORBIDDEN_STEPS,
    load_schema,
    pipeline_secret_errors,
    validate_doc,
    validate_path,
)


def manifest():
    return {
        "schema_version": 1,
        "slug": "setup-test",
        "name": "Setup test",
        "tagline": "Setup capability",
        "target": {"project_hint": "demo", "supports": ["cloud_eu"]},
        "llm": {"providers": ["anthropic"]},
        "pipeline": [
            {
                "id": "configure-evaluators",
                "run": "synth configure-evaluators --config {config}",
                "requires_secrets": ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LLM_API_KEY"],
            },
            {"id": "seed", "run": "synth seed --config {config}"},
            {"id": "verify", "run": "synth verify --config {config}"},
        ],
        "artifacts": [{"path": "DEMO_SCRIPT.md", "render": "markdown"}],
    }


def validate(doc):
    return validate_doc(doc, Draft7Validator(load_schema()))


def test_setup_capability_round_trips_through_published_validator(tmp_path):
    path = tmp_path / "usecase.yaml"
    doc = manifest()
    before = copy.deepcopy(doc)
    path.write_text(yaml.safe_dump(doc))
    assert validate_path(path) == []
    assert pipeline_secret_errors(doc) == []
    assert doc == before  # Validation never fills a missing declaration or grants a key.


@pytest.mark.parametrize("secrets", [[], ["LANGFUSE_PUBLIC_KEY"], ["LANGFUSE_SECRET_KEY"]])
def test_explicit_project_only_and_empty_declarations_need_no_provider(secrets):
    doc = manifest()
    doc.pop("llm")
    for step in doc["pipeline"]:
        step["requires_secrets"] = secrets
    assert validate(doc) == []


def test_omitted_declaration_preserves_legacy_manifest():
    doc = manifest()
    doc.pop("llm")
    doc["pipeline"][0].pop("requires_secrets")
    assert validate(doc) == []


@pytest.mark.parametrize("secrets", [
    ["ANTHROPIC_API_KEY"], ["OPENAI_API_KEY"], ["LANGFUSE_BASE_URL"], ["UNKNOWN"],
    ["LLM_API_KEY", "LLM_API_KEY"], "LLM_API_KEY", None, [123], [{}],
])
def test_unknown_literal_duplicate_or_malformed_declarations_fail_closed(secrets):
    doc = manifest()
    doc["pipeline"][0]["requires_secrets"] = secrets
    assert pipeline_secret_errors(doc)
    assert validate(doc)


@pytest.mark.parametrize("step_id", sorted(PIPELINE_PROVIDER_FORBIDDEN_STEPS))
def test_canonical_step_ids_cannot_request_provider_key(step_id):
    doc = manifest()
    doc["pipeline"][0]["id"] = step_id
    assert any("custom setup" in err for err in pipeline_secret_errors(doc))
    assert validate(doc)


@pytest.mark.parametrize("verb", sorted(PIPELINE_PROVIDER_FORBIDDEN_STEPS))
@pytest.mark.parametrize("field", ["run", "resumable"])
def test_canonical_commands_cannot_hide_behind_custom_ids(verb, field):
    doc = manifest()
    doc["pipeline"][0][field] = f"/usr/local/bin/synth {verb} --config {{config}}"
    assert any(f"synth {verb}" in err for err in pipeline_secret_errors(doc))
    assert validate(doc)


def test_setup_provider_requires_llm_declaration():
    doc = manifest()
    doc.pop("llm")
    assert any("llm.providers" in err for err in pipeline_secret_errors(doc))
    assert validate(doc)


def test_pipeline_and_live_sentinels_share_provider_contract():
    doc = manifest()
    doc["live_components"] = [{
        "name": "companion", "command": "synth companion", "port": 8080,
        "requires_secrets": ["ANTHROPIC_API_KEY"],
    }]
    assert any("mix" in err for err in validate(doc))
    doc["live_components"][0]["requires_secrets"] = ["LLM_API_KEY"]
    assert validate(doc) == []


def test_unparseable_secret_enabled_command_is_rejected():
    doc = manifest()
    doc["pipeline"][0]["run"] = 'synth "unfinished'
    assert any("cannot parse" in err for err in pipeline_secret_errors(doc))


@pytest.mark.parametrize("allowed", [True, False])
def test_conformance_uses_the_same_capability_contract(tmp_path, allowed):
    doc = manifest()
    if not allowed:
        doc["pipeline"][1]["requires_secrets"] = ["LLM_API_KEY"]
    (tmp_path / "usecase.yaml").write_text(yaml.safe_dump(doc))
    report = run_conformance(tmp_path, state_module="absent_setup_test_state")
    errors = [finding for finding in report.findings if "LLM_API_KEY" in finding]
    assert bool(errors) is not allowed
    if allowed:
        assert any("manifest passes" in line for line in report.passed)
