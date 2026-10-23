"""Versioned, secret-free model bindings shared by authoring, Depot and Adapter.

Role resolution never reads credentials or the primary LLM_MODEL override. A role is
an explicit provider/model pair, optionally enabled by validated Configuration.
"""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class RoleBinding:
    provider: str
    model: str


def resolve_manifest_roles(
    manifest: dict,
    names: list[str] | tuple[str, ...],
    selected_provider: str,
    overrides: Mapping[str, object] | None = None,
) -> dict[str, RoleBinding]:
    """Resolve opted-in enabled roles from a validated Manifest and Configuration."""
    if not names:
        return {}
    capability = manifest.get("llm", {}).get("roles", {})
    if capability.get("version") != 1:
        raise ValueError("unsupported llm.roles version")
    definitions = capability.get("bindings", {})
    properties = manifest.get("config_schema", {}).get("properties", {})
    result = {}
    for name in names:
        if name not in definitions:
            raise ValueError(f"undefined LLM role: {name}")
        definition = definitions[name]
        switch = definition.get("enabled_by")
        if switch is not None:
            prop = properties.get(switch, {})
            enabled = (overrides or {}).get(switch, prop.get("default"))
            if prop.get("type") != "boolean" or not isinstance(enabled, bool):
                raise ValueError(f"LLM role {name} requires a boolean Configuration value")
            if not enabled:
                continue
        provider = definition["provider"]
        if provider == "selected":
            provider = selected_provider
        model = definition.get("models", {}).get(provider)
        if (
            provider not in {"anthropic", "openai"}
            or not isinstance(model, str)
            or not model.strip()
        ):
            raise ValueError(f"LLM role {name} has no supported provider/model binding")
        result[name] = RoleBinding(provider, model)
    return result


def encode_role_bindings(bindings: Mapping[str, RoleBinding]) -> str:
    """Canonical non-secret envelope for the worker-to-Adapter environment contract."""
    import json

    return json.dumps(
        {
            "version": 1,
            "bindings": {
                name: {"provider": binding.provider, "model": binding.model}
                for name, binding in bindings.items()
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_role_bindings(value: str | None) -> dict[str, RoleBinding]:
    """Read a frozen worker envelope; malformed capabilities fail without echoing input."""
    import json
    import re

    if value is None:
        return {}
    try:
        envelope = json.loads(value)
        if (
            set(envelope) != {"version", "bindings"}
            or type(envelope["version"]) is not int
            or envelope["version"] != 1
        ):
            raise ValueError
        result = {}
        for name, binding in envelope["bindings"].items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or set(binding) != {
                "provider",
                "model",
            }:
                raise ValueError
            if (
                binding["provider"] not in {"anthropic", "openai"}
                or not isinstance(binding["model"], str)
                or not binding["model"].strip()
                or len(binding["model"]) > 256
            ):
                raise ValueError
            result[name] = RoleBinding(**binding)
        return result
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("invalid versioned LLM role bindings") from None
