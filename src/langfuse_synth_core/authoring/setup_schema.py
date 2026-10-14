"""Configuration schema checks shared by optional setup annotations."""

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT7


def field_schema(config: dict, field: dict | bool) -> tuple[dict | bool, list[str]]:
    """Resolve field annotations and reject secrets without retrieving remote schemas."""
    resolver = Registry().resolver_with_root(Resource.from_contents(config, DRAFT7))
    resolver = resolver.in_subresource(Resource.from_contents(field, DRAFT7))
    return _field_schema(field, resolver, set())


def _field_schema(field, resolver, ancestors):
    if id(field) in ancestors:
        return field, []
    ancestors = ancestors | {id(field)}
    seen: set[int] = set()
    errors = []
    while isinstance(field, dict):
        if id(field) in seen:
            errors.append("invalid configuration schema reference: circular field reference")
            break
        seen.add(id(field))
        if field.get("writeOnly") or field.get("format") == "password" or field.get("secret"):
            errors.append("secrets cannot be declared in setup")
        if "$ref" not in field:
            break
        try:
            resolved = resolver.lookup(field["$ref"])
        except Unresolvable as error:
            errors.append(f"invalid configuration schema reference: {error}")
            break
        field, resolver = resolved.contents, resolved.resolver
        try:
            Draft7Validator.check_schema(field)
        except SchemaError as error:
            errors.append(f"invalid configuration schema reference: {error.message}")
            return field, errors
    if isinstance(field, dict):
        # Inspect applied child schemas, not unused definitions or literal default values.
        applied = {
            key: value for key, value in field.items() if key not in ("definitions", "$defs")
        }
        for child in Resource.from_contents(applied, DRAFT7).subresources():
            _, child_errors = _field_schema(
                child.contents, resolver.in_subresource(child), ancestors
            )
            errors.extend(child_errors)
    return field, errors


def value_errors(config: dict, field: dict | bool, value: object) -> list[str]:
    """Validate a field value with local references rooted in its configuration."""
    try:
        validator = Draft7Validator(config, registry=Registry())
        return [error.message for error in validator.descend(value, field)]
    except (Unresolvable, RecursionError) as error:
        return [f"invalid configuration schema reference: {error}"]
