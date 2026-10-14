"""Cross-field checks for the optional inclusive schedule declaration."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft7Validator


def setup_schedule_errors(doc: dict) -> list[str]:
    setup = doc.get("setup")
    if not isinstance(setup, dict) or not isinstance(setup.get("schedule"), dict):
        return []
    schedule = setup["schedule"]
    errors = []
    zone = schedule.get("timezone")
    try:
        if not isinstance(zone, str):
            raise ValueError("missing timezone")
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append("setup.schedule.timezone must be a valid IANA timezone")
    history = schedule.get("history")
    if not isinstance(history, dict) or "field" not in history:
        return errors
    key = history["field"]
    schema = doc.get("config_schema")
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    field = properties.get(key) if isinstance(key, str) and isinstance(properties, dict) else None
    if not isinstance(field, dict) or field.get("type") != "integer":
        errors.append(
            "setup.schedule.history.field must reference a declared integer configuration field"
        )
        return errors
    choices = field.get("enum")
    if (
        not isinstance(choices, list)
        or not choices
        or any(type(value) is not int or value < 1 for value in choices)
    ):
        errors.append("setup.schedule.history.field must declare a positive integer enum")
    elif any(not Draft7Validator(field).is_valid(value) for value in choices):
        errors.append("setup.schedule.history choices must satisfy their configuration schema")
    default = field.get("default")
    if type(default) is not int or not isinstance(choices, list) or default not in choices:
        errors.append("setup.schedule.history.field must declare a supported default")
    return errors
