"""The canonical ``generation.target_traces`` knob injector (SDK one-liner, #29).

An author calls ``inject_target_traces(config_schema)`` to declare the single, cross-kit
operator volume knob in a kit's ``usecase.yaml`` ``config_schema``. This is an
**author-time** helper, so it lives behind the ``[authoring]`` extra and leans on
``jsonschema`` to prove the emitted knob (and the resulting ``config_schema``) is
schema-valid — a guarantee the runtime deliberately does not carry.

The runtime counterpart (the ``DerivationHook`` type, the identity default, and the
canonical ``TARGET_TRACES_KEY``) lives in ``langfuse_synth_core.derivation`` and is
importable at seed time without this extra.

Contract (usecase.schema.json ``config_schema``): each property NAME is a dotted
kit-config key passed verbatim by the portal as ``--set <name>=<value>``. So the knob is
injected under the property key ``generation.target_traces`` — no portal-side mapping
table, preserving the zero-code invariant.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

import jsonschema

from langfuse_synth_core.derivation import TARGET_TRACES_KEY

# Canonical defaults for the knob's bounds/default. An author may override any of them,
# but the shape (integer, bounded, defaulted, titled, described) is fixed.
DEFAULT_MINIMUM = 1
DEFAULT_MAXIMUM = 100_000
DEFAULT_VALUE = 1_000
DEFAULT_TITLE = "Target traces"
DEFAULT_DESCRIPTION = (
    "Total number of traces to generate. The single, uniform volume knob for this demo; "
    "the kit deterministically derives its internal generation params from it."
)


def target_traces_knob(
    *,
    minimum: int = DEFAULT_MINIMUM,
    maximum: int = DEFAULT_MAXIMUM,
    default: int = DEFAULT_VALUE,
    title: str = DEFAULT_TITLE,
    description: str = DEFAULT_DESCRIPTION,
) -> dict[str, Any]:
    """Build the schema-valid property definition for the canonical volume knob.

    Returns just the JSON-Schema fragment (the value stored under the
    ``generation.target_traces`` property); use :func:`inject_target_traces` to place it
    into a ``config_schema``. Raises ``ValueError`` if the bounds/default are incoherent.
    """
    if not isinstance(minimum, int) or isinstance(minimum, bool):
        raise ValueError("minimum must be an int")
    if not isinstance(maximum, int) or isinstance(maximum, bool):
        raise ValueError("maximum must be an int")
    if not isinstance(default, int) or isinstance(default, bool):
        raise ValueError("default must be an int")
    if minimum > maximum:
        raise ValueError(f"minimum ({minimum}) must be <= maximum ({maximum})")
    if not (minimum <= default <= maximum):
        raise ValueError(
            f"default ({default}) must be within [minimum={minimum}, maximum={maximum}]"
        )

    knob: dict[str, Any] = {
        "type": "integer",
        "minimum": minimum,
        "maximum": maximum,
        "default": default,
        "title": title,
        "description": description,
    }

    # Prove schema-validity rather than asserting it by construction: the fragment must be
    # a legal Draft-7 schema, and its own `default` must validate against it.
    validator_cls = jsonschema.Draft7Validator
    validator_cls.check_schema(knob)
    validator_cls(knob).validate(default)
    return knob


def inject_target_traces(
    config_schema: Mapping[str, Any] | None = None,
    *,
    minimum: int = DEFAULT_MINIMUM,
    maximum: int = DEFAULT_MAXIMUM,
    default: int = DEFAULT_VALUE,
    title: str = DEFAULT_TITLE,
    description: str = DEFAULT_DESCRIPTION,
) -> dict[str, Any]:
    """Inject the canonical ``generation.target_traces`` knob into a ``config_schema``.

    Returns a NEW ``config_schema`` (the input is never mutated) with ``type: object``,
    a ``properties`` map, and the knob added under the ``generation.target_traces`` key.
    Any existing ``generation.target_traces`` property is replaced. The result is proven
    to be a valid Draft-7 schema before it is returned.

    ``config_schema`` defaults to a fresh ``{"type": "object", "properties": {}}`` so an
    author can declare the knob on a kit that has no other config yet.
    """
    result: dict[str, Any] = copy.deepcopy(dict(config_schema)) if config_schema else {}
    result.setdefault("type", "object")
    props = result.get("properties")
    if not isinstance(props, dict):
        props = {}
    else:
        props = dict(props)
    props[TARGET_TRACES_KEY] = target_traces_knob(
        minimum=minimum,
        maximum=maximum,
        default=default,
        title=title,
        description=description,
    )
    result["properties"] = props

    # The whole config_schema must remain a legal JSON Schema after injection.
    jsonschema.Draft7Validator.check_schema(result)
    return result
