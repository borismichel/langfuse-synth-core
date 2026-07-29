"""The config *contract* the shared core reads against + the shared config *loader*.

Two things live here, both data-model-facing:

1. **The contract** (``typing.Protocol``s). ``pricing`` and ``lfclient`` are byte-identical
   across kits and annotate their parameters as ``Config`` / ``Model``. They never construct
   or evaluate these types: under ``from __future__ import annotations`` the hints are inert
   strings, and every access is duck-typed (``cfg.model_by_role(role)``, ``model.input_per_1k``,
   ``cfg.target.base_url``). So the library publishes only the *shape* it depends on — the
   seam's data contract, made explicit and documented.

2. **The loader** (``load_config`` / ``apply_overrides`` / ``set_dotted``). Ring 2 (#33)
   moved the config-loading *mechanism* into the lib as "library-with-parameters": reading
   YAML and applying ``--set dotted.key=value`` overrides is scenario-agnostic plumbing; the
   only per-kit *value* is the concrete model it validates into. So the loader is parametrized
   by a ``model_factory`` (e.g. a pydantic ``Config.model_validate``) — the lib never imports
   pydantic or knows the kit's config *shape*. The kit keeps its own model classes and passes
   the factory in. (The pydantic model itself is scenario substance and stays in the kit; only
   the load-and-override mechanism crossed the seam.)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class Model(Protocol):
    """A priced model row. ``pricing`` reads the per-1k rates off it."""

    input_per_1k: float
    output_per_1k: float


@runtime_checkable
class Target(Protocol):
    """The Langfuse instance a run points at. ``lfclient`` reads ``base_url``.

    ``base_url`` MUST honor the ``LANGFUSE_BASE_URL`` environment variable, letting it override
    whatever the kit's config file says. That is not decoration: it is how the portal retargets
    ONE shipped config at whatever Langfuse a deployment points to (the worker injects the var
    into the container). A kit that returns only its committed value satisfies the *shape* of
    this Protocol and is still undeployable — it dials its own loopback wherever it runs.

    A Protocol cannot express that, so it is stated here and gated instead: the scaffold emits
    ``tests/test_retargeting.py``, one call into
    :func:`langfuse_synth_core.authoring.retarget.assert_retargetable`. Portal #187 is the
    deployment this was learned on; see ``CONTRACT.md`` §"Retargeting".
    """

    # A read-only attribute from the core's view; a concrete kit may back it with a
    # @property (as EV's pydantic Target does) — structurally the same to a Protocol, and the
    # natural place to let the env win.
    base_url: str


@runtime_checkable
class Config(Protocol):
    """The run configuration surface the shared core touches."""

    target: Target

    def model_by_role(self, role: str) -> Model: ...


# ---------------------------------------------------------------------------
# The shared config loader (Ring 2 middle field, #33)
# ---------------------------------------------------------------------------
def set_dotted(raw: dict, dotted_key: str, value: Any) -> None:
    """Set ``value`` at the dotted path ``a.b.c`` in the raw config dict, creating
    intermediate mappings as needed (overwriting a non-mapping intermediate)."""
    keys = dotted_key.split(".")
    node = raw
    for k in keys[:-1]:
        child = node.get(k)
        if not isinstance(child, dict):
            child = {}
            node[k] = child
        node = child
    node[keys[-1]] = value


def apply_overrides(raw: dict, overrides: list[str] | None) -> dict:
    """Apply ``--set dotted.key=value`` overrides to the RAW yaml dict before validation.

    Each value is coerced with ``yaml.safe_load`` so ``800``→int, ``true``→bool,
    ``1.5``→float, quoted/other→str — matching how the same value would parse in the yaml
    file. Mutates and returns ``raw``. Used to let the portal scale a single shipped config
    via the manifest ``config_schema`` (e.g. ``generation.target_traces``)."""
    import yaml

    for item in overrides or []:
        key, sep, rawval = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"--set expects dotted.key=value, got {item!r}")
        set_dotted(raw, key, yaml.safe_load(rawval))
    return raw


def load_config(
    path: str | Path,
    model_factory: Callable[[dict], Any],
    overrides: list[str] | None = None,
) -> Any:
    """Read a YAML config, apply ``--set`` overrides, and validate it into a kit model.

    ``model_factory`` is the kit's ``dict -> Config`` builder (e.g. a pydantic
    ``Config.model_validate``); the lib stays ignorant of the kit's config *shape* and of
    pydantic itself. Returns whatever the factory returns.
    """
    import yaml

    raw = yaml.safe_load(Path(path).read_text())
    apply_overrides(raw, overrides)
    return model_factory(raw)
