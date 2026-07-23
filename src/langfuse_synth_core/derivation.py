"""Canonical `target_traces` derivation hook — home reserved for #29.

The operator turns ONE uniform volume knob (`generation.target_traces`, an integer).
A kit-side, deterministic hook maps that knob to the kit's internal params at seed
runtime — EV: direct count; Lender: derived `scale` with golden suite/experiments/
queue left unscaled. Because the mapping is kit-side and deterministic, the portal
stays zero-code (`--set generation.target_traces=N` passed verbatim) and the
byte-identical determinism law holds.

This hook ships in the RUNTIME library (not the [authoring] extra) because it runs at
seed time. #29 lands the full mechanism: the one-liner that injects the schema knob,
the tested identity default, and the application to each kit (done in Ring 2, #33/#34).
This module fixes the public type + name so downstream imports are stable; the identity
default below keeps the scaffold's runtime import green.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

# (target_traces, declared_params) -> kit-internal params. Kit-side, deterministic.
DerivationHook = Callable[[int, Mapping[str, Any]], Mapping[str, Any]]


def identity_derivation(target_traces: int, declared: Mapping[str, Any]) -> Mapping[str, Any]:
    """Trivial default: pass the count straight through (EV-style direct count).

    Placeholder home-reservation for #29, which replaces this with the full
    knob-injector + mechanism and its tests.
    """
    return {"target_traces": target_traces}
