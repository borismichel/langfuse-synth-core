"""The ``units_per_trace`` advisory field builder — authoring toolchain (#35).

The kit-declared advisory density field is a schema-valid integer fragment an author can
place in a manifest to declare "my traces are denser than the default ~11 units". Like
``target_traces_knob`` it is an author-time helper behind the ``[authoring]`` extra, so
the suite skips on a runtime-only install. Unlike ``target_traces`` it is NOT an operator
knob — the operator never tunes density — so it is a plain kit-declared field, not injected
into ``config_schema``.
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="authoring extra not installed; field builder lives behind [authoring]",
)


def test_field_builder_is_reachable_as_the_sdk_helper():
    from langfuse_synth_core.authoring import units_per_trace_field

    assert callable(units_per_trace_field)


def test_field_has_the_advisory_shape_and_default():
    from langfuse_synth_core.authoring import units_per_trace_field
    from langfuse_synth_core.derivation import DEFAULT_UNITS_PER_TRACE

    field = units_per_trace_field()
    assert field["type"] == "integer"
    assert field["default"] == DEFAULT_UNITS_PER_TRACE
    assert field["minimum"] == 1  # at least one unit per trace
    assert isinstance(field["title"], str) and field["title"]
    assert isinstance(field["description"], str) and field["description"]


def test_field_is_valid_jsonschema_and_default_validates():
    import jsonschema

    from langfuse_synth_core.authoring import units_per_trace_field

    field = units_per_trace_field(default=40)
    jsonschema.Draft7Validator.check_schema(field)
    jsonschema.Draft7Validator(field).validate(field["default"])


def test_field_allows_extreme_density_defaults():
    """Extreme-density scenarios (the whole reason the override exists) are honored."""
    from langfuse_synth_core.authoring import units_per_trace_field

    assert units_per_trace_field(default=500)["default"] == 500


def test_field_rejects_non_positive_and_bool_default():
    from langfuse_synth_core.authoring import units_per_trace_field

    with pytest.raises(ValueError):
        units_per_trace_field(default=0)
    with pytest.raises(ValueError):
        units_per_trace_field(default=-3)
    with pytest.raises(ValueError):
        units_per_trace_field(default=True)
