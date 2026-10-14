"""Schema-bound setup annotations; kit internals and secrets are never exposed."""

from __future__ import annotations

import math
from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError
from .setup_schedule import setup_schedule_errors
from .setup_schema import field_schema, value_errors


def setup_errors(doc: dict) -> list[str]:
    setup = doc.get("setup")
    if not isinstance(setup, dict):
        return []
    config = doc.get("config_schema", {})
    try:
        Draft7Validator.check_schema(config)
    except SchemaError as error:
        path = "/".join(str(part) for part in error.path)
        return [f"  at config_schema/{path}: {error.message}"]
    props = config.get("properties", {}) if isinstance(config, dict) else {}
    if not isinstance(props, dict):
        props = {}
    errors: list[str] = setup_schedule_errors(doc)
    fields = setup.get("fields") or {}
    if isinstance(fields, dict):
        for key in fields:
            if key not in props:
                errors.append(
                    f"  at setup/fields/{key}: must name a declared config_schema property"
                )
            else:
                _, field_errors = field_schema(config, props[key])
                errors.extend(f"  at setup/fields/{key}: {error}" for error in field_errors)
    schedule = setup.get("schedule")
    history_spec = schedule.get("history") if isinstance(schedule, dict) else None
    history = history_spec.get("field") if isinstance(history_spec, dict) else None
    presets = setup.get("presets") or {}
    maps: list[tuple[str, object]] = []
    if isinstance(presets, dict):
        for name, preset in presets.items():
            if isinstance(preset, dict):
                values = preset.get("values")
                maps.append((f"presets/{name}/values", values))
                if isinstance(values, dict) and isinstance(history, str) and history in values:
                    errors.append(
                        f"  at setup/presets/{name}: data presets cannot override "
                        "the schedule history field"
                    )
    defaults = setup.get("host_defaults") or {}
    if isinstance(defaults, dict):
        target = doc.get("target")
        supports = target.get("supports") if isinstance(target, dict) else []
        if not isinstance(supports, list):
            supports = []
        for host, values in defaults.items():
            maps.append((f"host_defaults/{host}", values))
            if host not in supports:
                errors.append(f"  at setup/host_defaults/{host}: host is not supported by this kit")
    for path, values in maps:
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            loc = f"  at setup/{path}/{key}"
            if key not in props:
                errors.append(f"{loc}: must name a declared config_schema property")
                continue
            schema = props[key]
            _, field_errors = field_schema(config, schema)
            errors.extend(f"{loc}: {error}" for error in field_errors)
            if field_errors:
                continue
            if isinstance(value, float) and not math.isfinite(value):
                errors.append(f"{loc}: must be finite")
            for error in value_errors(config, schema, value):
                errors.append(f"{loc}: {error}")
    return errors
