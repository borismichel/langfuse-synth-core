"""The generation.target_traces knob injector — authoring toolchain (#29).

The SDK one-liner injects a schema-valid ``generation.target_traces`` knob (integer, with
bounds/default/title/description) into a kit's ``config_schema``. This is an author-time
helper behind the ``[authoring]`` extra, so the suite is skipped on a runtime-only install
(the boundary is proved by ``test_authoring_boundary`` + the runtime-only CI job).
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="authoring extra not installed; injector lives behind [authoring]",
)


def test_injector_is_reachable_as_the_sdk_one_liner():
    from langfuse_synth_core.authoring import inject_target_traces

    assert callable(inject_target_traces)


def test_inject_adds_the_canonical_dotted_key():
    from langfuse_synth_core.authoring import inject_target_traces
    from langfuse_synth_core.derivation import TARGET_TRACES_KEY

    schema = inject_target_traces()
    assert TARGET_TRACES_KEY in schema["properties"]
    assert schema["type"] == "object"


def test_injected_knob_has_the_full_required_shape():
    from langfuse_synth_core.authoring import inject_target_traces
    from langfuse_synth_core.derivation import TARGET_TRACES_KEY

    knob = inject_target_traces()["properties"][TARGET_TRACES_KEY]
    assert knob["type"] == "integer"
    for field in ("minimum", "maximum", "default", "title", "description"):
        assert field in knob, f"knob is missing {field}"
    assert knob["minimum"] <= knob["default"] <= knob["maximum"]
    assert isinstance(knob["title"], str) and knob["title"]
    assert isinstance(knob["description"], str) and knob["description"]


def test_injected_config_schema_is_valid_jsonschema():
    import jsonschema

    from langfuse_synth_core.authoring import inject_target_traces

    schema = inject_target_traces()
    # Must be a legal Draft-7 schema, and the knob's own default must validate under it.
    jsonschema.Draft7Validator.check_schema(schema)


def test_injected_default_validates_against_the_knob():
    import jsonschema

    from langfuse_synth_core.authoring import inject_target_traces
    from langfuse_synth_core.derivation import TARGET_TRACES_KEY

    knob = inject_target_traces(minimum=10, maximum=20, default=15)["properties"][
        TARGET_TRACES_KEY
    ]
    validator = jsonschema.Draft7Validator(knob)
    validator.validate(knob["default"])  # 15 is in range
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(9)  # below minimum


def test_inject_preserves_existing_properties_and_does_not_mutate_input():
    from langfuse_synth_core.authoring import inject_target_traces
    from langfuse_synth_core.derivation import TARGET_TRACES_KEY

    original = {
        "type": "object",
        "properties": {"generation.persona_mix": {"type": "string"}},
        "required": ["generation.persona_mix"],
    }
    snapshot = {
        "type": "object",
        "properties": {"generation.persona_mix": {"type": "string"}},
        "required": ["generation.persona_mix"],
    }
    result = inject_target_traces(original)

    # Existing property survives; the knob is added alongside it.
    assert "generation.persona_mix" in result["properties"]
    assert TARGET_TRACES_KEY in result["properties"]
    assert result["required"] == ["generation.persona_mix"]
    # Input is untouched (new schema returned).
    assert original == snapshot
    assert TARGET_TRACES_KEY not in original["properties"]


def test_inject_replaces_an_existing_knob():
    from langfuse_synth_core.authoring import inject_target_traces
    from langfuse_synth_core.derivation import TARGET_TRACES_KEY

    seeded = {"type": "object", "properties": {TARGET_TRACES_KEY: {"type": "string"}}}
    result = inject_target_traces(seeded, minimum=5, maximum=9, default=7)
    knob = result["properties"][TARGET_TRACES_KEY]
    assert knob["type"] == "integer"
    assert knob["default"] == 7


def test_custom_bounds_are_honored():
    from langfuse_synth_core.authoring import inject_target_traces
    from langfuse_synth_core.derivation import TARGET_TRACES_KEY

    knob = inject_target_traces(
        minimum=100, maximum=9000, default=1200, title="Volume", description="how many"
    )["properties"][TARGET_TRACES_KEY]
    assert (knob["minimum"], knob["maximum"], knob["default"]) == (100, 9000, 1200)
    assert knob["title"] == "Volume"
    assert knob["description"] == "how many"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum": 100, "maximum": 10},  # min > max
        {"minimum": 10, "maximum": 100, "default": 5},  # default below min
        {"minimum": 10, "maximum": 100, "default": 500},  # default above max
    ],
)
def test_incoherent_bounds_raise(kwargs):
    from langfuse_synth_core.authoring import inject_target_traces

    with pytest.raises(ValueError):
        inject_target_traces(**kwargs)


def test_bool_is_rejected_as_a_bound():
    # bool is an int subclass; guard against True/False slipping in as bounds.
    from langfuse_synth_core.authoring import inject_target_traces

    with pytest.raises(ValueError):
        inject_target_traces(default=True)
