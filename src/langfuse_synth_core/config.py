"""The config *contract* the shared core reads against — structural, not a loader.

``pricing`` and ``lfclient`` are byte-identical across kits and annotate their
parameters as ``Config`` / ``Model``. They never construct or evaluate these types:
under ``from __future__ import annotations`` the hints are inert strings, and every
access is duck-typed (``cfg.model_by_role(role)``, ``model.input_per_1k``,
``cfg.target.base_url``). So the library publishes only the *shape* it depends on, as
``typing.Protocol``s — the seam's data contract, made explicit and documented.

The kit keeps its own concrete config loader (a pydantic ``Config`` parsed from
``config/demo.yaml``); a kit's model satisfies these Protocols structurally, so nothing
in the kit changes. Turning the loader itself into a shared, parameterized component is
the "library-with-parameters" middle-field ring (docs/SEAM.md) — deliberately not this
one. This module exists so the byte-identical core resolves its imports inside the lib
without dragging a scenario-shaped loader across the seam.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Model(Protocol):
    """A priced model row. ``pricing`` reads the per-1k rates off it."""

    input_per_1k: float
    output_per_1k: float


@runtime_checkable
class Target(Protocol):
    """The Langfuse instance a run points at. ``lfclient`` reads ``base_url``."""

    # A read-only attribute from the core's view; a concrete kit may back it with a
    # @property (as EV's pydantic Target does) — structurally the same to a Protocol.
    base_url: str


@runtime_checkable
class Config(Protocol):
    """The run configuration surface the shared core touches."""

    target: Target

    def model_by_role(self, role: str) -> Model: ...
