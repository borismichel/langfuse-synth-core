"""Versioned setup metadata is validated at the public Manifest boundary."""

from pathlib import Path
import pytest
import yaml
from jsonschema import Draft7Validator
from langfuse_synth_core.authoring.validate import load_schema, validate_doc, validate_path


def manifest():
    doc = yaml.safe_load(
        (
            Path(__file__).parent / "fixtures/manifests/ev-subsidy-regression.usecase.yaml"
        ).read_text()
    )
    doc["config_schema"] = {
        "type": "object",
        "properties": {
            "generation.target_traces": {
                "type": "integer",
                "minimum": 100,
                "maximum": 6000,
                "default": 800,
            },
            "generation.seed": {"type": "integer", "minimum": 0, "default": 42},
            "incident.enabled": {"type": "boolean", "default": True},
        },
    }
    doc["setup"] = {
        "version": 1,
        "fields": {"incident.enabled": {"group": "scenario"}},
        "presets": {
            "quick_rehearsal": {
                "label": "Quick rehearsal",
                "values": {
                    "generation.target_traces": 300,
                    "incident.enabled": False,
                    "generation.seed": 0,
                },
            }
        },
        "host_defaults": {"self_hosted": {"generation.target_traces": 4000}},
    }
    return doc


def test_declared_setup_accepts_typed_false_zero_and_host_defaults():
    assert validate_doc(manifest(), Draft7Validator(load_schema())) == []


@pytest.mark.parametrize("binding", ["preset", "host_default"])
def test_setup_values_resolve_local_configuration_references(binding):
    doc = manifest()
    doc["config_schema"]["definitions"] = {"positive": {"type": "integer", "minimum": 1}}
    doc["config_schema"]["properties"]["demo.count"] = {"$ref": "#/definitions/positive"}
    values = (
        doc["setup"]["presets"]["quick_rehearsal"]["values"]
        if binding == "preset"
        else doc["setup"]["host_defaults"]["self_hosted"]
    )
    values["demo.count"] = 14
    validator = Draft7Validator(load_schema())
    assert validate_doc(doc, validator) == []
    values["demo.count"] = 0
    assert any("demo.count" in error for error in validate_doc(doc, validator))


@pytest.mark.parametrize("binding", ["preset", "host_default", "schedule"])
@pytest.mark.parametrize(
    "constraint",
    [
        {"minimum": "bad"},
        {"type": "bogus"},
        {"$ref": "#/definitions/missing"},
        {"$ref": "https://schemas.invalid/config.json"},
        {"$ref": "#/properties/demo.days"},
    ],
)
def test_invalid_setup_configuration_schema_returns_errors(tmp_path, binding, constraint):
    doc = manifest()
    doc["config_schema"]["properties"]["demo.days"] = {
        "type": "integer",
        "enum": [14, 30],
        "default": 14,
        **constraint,
    }
    if binding == "preset":
        doc["setup"]["presets"]["quick_rehearsal"]["values"]["demo.days"] = 14
    elif binding == "host_default":
        doc["setup"]["host_defaults"]["self_hosted"]["demo.days"] = 14
    else:
        doc["setup"]["schedule"] = {
            "timezone": "UTC",
            "end": "inclusive",
            "history": {"field": "demo.days"},
        }
    path = tmp_path / "usecase.yaml"
    path.write_text(yaml.safe_dump(doc))
    assert validate_path(path)


@pytest.mark.parametrize("binding", ["preset", "host_default"])
@pytest.mark.parametrize("allowed", [False, True])
def test_setup_values_respect_boolean_configuration_schemas(binding, allowed):
    doc = manifest()
    doc["config_schema"]["properties"]["demo.value"] = allowed
    values = (
        doc["setup"]["presets"]["quick_rehearsal"]["values"]
        if binding == "preset"
        else doc["setup"]["host_defaults"]["self_hosted"]
    )
    values["demo.value"] = 14
    errors = validate_doc(doc, Draft7Validator(load_schema()))
    if allowed:
        assert errors == []
    else:
        assert any("demo.value" in error for error in errors)


@pytest.mark.parametrize("binding", ["field", "preset", "host_default", "schedule"])
@pytest.mark.parametrize("marker", [{"writeOnly": True}, {"secret": True}, {"format": "password"}])
@pytest.mark.parametrize("referenced", [False, True])
def test_setup_rejects_secret_configuration_fields(binding, marker, referenced):
    doc = manifest()
    field = {"type": "integer", "enum": [14, 30], "default": 14, **marker}
    if referenced:
        doc["config_schema"]["definitions"] = {"private": field}
        field = {"$ref": "#/definitions/private"}
    doc["config_schema"]["properties"]["demo.private"] = field
    if binding == "field":
        doc["setup"]["fields"]["demo.private"] = {"group": "advanced"}
    elif binding == "preset":
        doc["setup"]["presets"]["quick_rehearsal"]["values"]["demo.private"] = 14
    elif binding == "host_default":
        doc["setup"]["host_defaults"]["self_hosted"]["demo.private"] = 14
    else:
        doc["setup"]["schedule"] = {
            "timezone": "UTC",
            "end": "inclusive",
            "history": {"field": "demo.private"},
        }
    errors = validate_doc(doc, Draft7Validator(load_schema()))
    assert any("secret" in error for error in errors)


def test_setup_rejects_secret_annotations_in_composed_local_references():
    doc = manifest()
    doc["config_schema"]["definitions"] = {"private": {"type": "integer", "writeOnly": True}}
    doc["config_schema"]["properties"]["demo.private"] = {
        "allOf": [{"$ref": "#/definitions/private"}]
    }
    doc["setup"]["fields"]["demo.private"] = {"group": "advanced"}
    assert any("secret" in error for error in validate_doc(doc, Draft7Validator(load_schema())))


def test_setup_rejects_malformed_reference_targets_outside_schema_keywords():
    doc = manifest()
    doc["config_schema"]["x-constraints"] = {"count": {"type": "integer", "minimum": "bad"}}
    doc["config_schema"]["properties"]["generation.seed"] = {"$ref": "#/x-constraints/count"}
    assert validate_doc(doc, Draft7Validator(load_schema()))


def test_schedule_resolves_local_schema_and_validates_every_choice(tmp_path):
    doc = manifest()
    doc["config_schema"]["definitions"] = {
        "history": {"type": "integer", "enum": [14, 30], "default": 14, "maximum": 30}
    }
    doc["config_schema"]["properties"]["demo.days"] = {"$ref": "#/definitions/history"}
    doc["setup"]["schedule"] = {
        "timezone": "UTC",
        "end": "inclusive",
        "history": {"field": "demo.days"},
    }
    path = tmp_path / "usecase.yaml"
    path.write_text(yaml.safe_dump(doc))
    assert validate_path(path) == []
    doc["config_schema"]["definitions"]["history"]["maximum"] = 20
    path.write_text(yaml.safe_dump(doc))
    assert any("history" in error for error in validate_path(path))


@pytest.mark.parametrize(
    "key,value",
    [
        ("undeclared.key", 300),
        ("generation.target_traces", True),
        ("generation.target_traces", 100.5),
        ("generation.target_traces", 99),
        ("generation.target_traces", float("inf")),
        ("incident.enabled", "false"),
    ],
)
def test_invalid_preset_values_rejected(key, value):
    doc = manifest()
    doc["setup"]["presets"]["quick_rehearsal"]["values"] = {key: value}
    assert validate_doc(doc, Draft7Validator(load_schema()))


def test_unknown_field_and_unsupported_host_rejected():
    doc = manifest()
    doc["setup"]["fields"]["secret.key"] = {"group": "scenario"}
    assert validate_doc(doc, Draft7Validator(load_schema()))
    doc = manifest()
    doc["setup"]["host_defaults"] = {"cloud_jp": {"generation.seed": 0}}
    assert validate_doc(doc, Draft7Validator(load_schema()))


@pytest.mark.parametrize("schedule", [[], "UTC", {"history": 42}])
def test_malformed_schedule_is_a_validation_error_not_an_exception(schedule):
    doc = manifest()
    doc["setup"]["schedule"] = schedule
    assert validate_doc(doc, Draft7Validator(load_schema()))


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "cloud_eu"),
        ("target", ["cloud_eu"]),
        (
            "setup",
            {
                "version": 1,
                "schedule": {"history": {"field": []}},
                "presets": {
                    "quick_rehearsal": {
                        "label": "Quick rehearsal",
                        "values": {"generation.seed": 0},
                    }
                },
            },
        ),
    ],
)
def test_malformed_contract_objects_return_validation_errors(field, value):
    doc = manifest()
    doc[field] = value
    assert validate_doc(doc, Draft7Validator(load_schema()))
