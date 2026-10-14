import copy
from jsonschema import Draft7Validator
from langfuse_synth_core.authoring.validate import load_schema, validate_doc
from test_validate_authoring_checks import BASE


def test_manifest_schedule_requires_a_declared_supported_history_field():
    doc = copy.deepcopy(BASE)
    doc["config_schema"] = {
        "type": "object",
        "properties": {
            "generation.window_days": {"type": "integer", "enum": [14, 30], "default": 30},
        },
    }
    doc["setup"] = {
        "version": 1,
        "schedule": {
            "timezone": "UTC",
            "end": "inclusive",
            "history": {"field": "generation.window_days"},
        },
    }
    validator = Draft7Validator(load_schema())
    assert validate_doc(doc, validator) == []
    doc["setup"]["schedule"]["history"]["field"] = "private.history"
    assert any("history" in error for error in validate_doc(doc, validator))


def test_fixed_history_requires_valid_timezone_and_positive_days():
    doc = copy.deepcopy(BASE)
    doc["setup"] = {
        "version": 1,
        "schedule": {
            "timezone": "Europe/Berlin",
            "end": "inclusive",
            "history": {"fixed_days": 28},
        },
    }
    validator = Draft7Validator(load_schema())
    assert validate_doc(doc, validator) == []
    doc["setup"]["schedule"]["timezone"] = "Invalid/Timezone"
    assert any("timezone" in error for error in validate_doc(doc, validator))
    doc["setup"]["schedule"]["timezone"] = "UTC"
    doc["setup"]["schedule"]["history"]["fixed_days"] = 0
    assert validate_doc(doc, validator)
