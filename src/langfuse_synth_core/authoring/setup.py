"""Schema-bound setup annotations; kit internals and secrets are never exposed."""
from __future__ import annotations

import math
from jsonschema import Draft7Validator
from .setup_schedule import setup_schedule_errors


def setup_errors(doc: dict) -> list[str]:
    setup = doc.get('setup')
    if not isinstance(setup, dict):
        return []
    props = (doc.get('config_schema') or {}).get('properties') or {}
    errors: list[str] = setup_schedule_errors(doc)
    fields = setup.get('fields') or {}
    if isinstance(fields, dict):
        for key in fields:
            if key not in props:
                errors.append(f'  at setup/fields/{key}: must name a declared config_schema property')
    history = ((setup.get('schedule') or {}).get('history') or {}).get('field')
    presets = setup.get('presets') or {}
    maps: list[tuple[str, object]] = []
    if isinstance(presets, dict):
        for name, preset in presets.items():
            if isinstance(preset, dict):
                values = preset.get('values')
                maps.append((f'presets/{name}/values', values))
                if isinstance(values, dict) and history in values:
                    errors.append(f'  at setup/presets/{name}: data presets cannot override the schedule history field')
    defaults = setup.get('host_defaults') or {}
    if isinstance(defaults, dict):
        supports = (doc.get('target') or {}).get('supports') or []
        for host, values in defaults.items():
            maps.append((f'host_defaults/{host}', values))
            if host not in supports:
                errors.append(f'  at setup/host_defaults/{host}: host is not supported by this kit')
    for path, values in maps:
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            loc = f'  at setup/{path}/{key}'
            if key not in props:
                errors.append(f'{loc}: must name a declared config_schema property')
                continue
            schema = props[key]
            if not isinstance(schema, dict):
                continue
            if schema.get('writeOnly') or schema.get('format') == 'password' or schema.get('secret'):
                errors.append(f'{loc}: secrets cannot be declared in setup')
            if isinstance(value, float) and not math.isfinite(value):
                errors.append(f'{loc}: must be finite')
            for err in Draft7Validator(schema).iter_errors(value):
                errors.append(f'{loc}: {err.message}')
    return errors
