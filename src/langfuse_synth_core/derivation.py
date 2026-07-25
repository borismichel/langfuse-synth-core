"""Canonical ``target_traces`` volume knob + kit-side derivation-hook mechanism (#29).

The operator turns ONE uniform volume knob — ``generation.target_traces``, an integer —
on every kit. A kit-side, **deterministic** hook maps that knob to the kit's internal
params at seed runtime (EV: direct count; Lender: a derived ``volume.scale`` with the
golden suite / experiments / queue left unscaled). Because the mapping is kit-owned and
deterministic:

  * the portal stays **zero-code** — it passes ``--set generation.target_traces=N``
    verbatim, with NO portal-side mapping table (see ``NO_PORTAL_MAPPING``); and
  * the byte-identical determinism law holds: ``seed + target_traces (+ other declared
    params) -> byte-identical pool``, with fixed golden assets unscaled.

This module ships in the **runtime** library (never behind the ``[authoring]`` extra),
because the hook runs at seed time wherever the lib runs. It fixes the public type +
name + canonical knob key so downstream kit imports are stable, and keeps a trivial
identity default so the scaffold's runtime import stays green.

The ``config_schema`` knob **injector** — the SDK one-liner an author calls to declare
the knob — lives in the authoring toolchain (``langfuse_synth_core.authoring``, behind
the extra), because it is an author-time helper that leans on ``jsonschema`` to prove the
emitted knob is schema-valid. Only the hook TYPE, the identity default, and the canonical
key constant live here in the runtime, since only those are needed at seed time.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

# The one canonical, cross-kit operator volume knob. This is the dotted kit-config key
# the portal passes verbatim as `--set generation.target_traces=N` (usecase.schema.json
# config_schema: each property NAME is the `--set` key). A kit must not ship a bespoke
# `total_traces` / `volume.scale` OPERATOR knob — those stay internal to the kit.
TARGET_TRACES_KEY = "generation.target_traces"

# The mechanism deliberately needs NO portal-side mapping table: the portal never
# translates target_traces -> kit internals; the kit-side DerivationHook does, at seed
# time. Exposed as a named flag so a doc/drift test can assert the invariant.
NO_PORTAL_MAPPING = True

# Semantic aliases for the hook contract (illustrative names from Spec A / #19).
TargetTraces = int
DeclaredParams = Mapping[str, Any]
KitInternalParams = Mapping[str, Any]

# (target_traces, declared_params) -> kit-internal params. Kit-side, DETERMINISTIC:
# identical (target_traces, declared) MUST yield identical internals every call. Runs at
# seed runtime, so it lives in the runtime lib — never gated behind [authoring].
DerivationHook = Callable[[TargetTraces, DeclaredParams], KitInternalParams]


def identity_derivation(target_traces: TargetTraces, declared: DeclaredParams) -> KitInternalParams:
    """Trivial default hook: pass the count straight through (EV-style direct count).

    This is the identity mapping ``target_traces -> {"target_traces": target_traces}``.
    ``declared`` is accepted (and ignored) to satisfy the ``DerivationHook`` signature so
    the scaffold and every kit can wire the same call shape before writing a bespoke
    derivation. A real kit replaces this with its own deterministic hook (e.g. Lender's
    derive-scale) in its Ring 2 migration (#33/#34) — out of scope here.
    """
    return {"target_traces": target_traces}


# ---------------------------------------------------------------------------
# Advisory density override (#35): the optional kit-declared units_per_trace.
# ---------------------------------------------------------------------------
# A kit may declare, up front, roughly how many billable units one trace expands into, so
# the deploy wizard can show an ADVISORY estimate (target_traces x units_per_trace) before
# a run. The default ~11 = 10 observations + 1 sampled score per trace.
#
# This is ADVISORY ONLY and never binds: the measured Spool count
# (:func:`langfuse_synth_core.seed.count.count_spool`) is what the cap gate reads, so an
# inaccurate units_per_trace is harmless. Kept next to target_traces because it is the same
# kind of piece — a runtime-safe, kit-declared knob key that ships without the [authoring]
# extra (the estimate is computed at deploy time wherever the lib runs). The author-time,
# jsonschema-validated field builder lives in ``langfuse_synth_core.authoring.knob``.
UNITS_PER_TRACE_KEY = "generation.units_per_trace"
DEFAULT_UNITS_PER_TRACE = 11


def _validate_units_per_trace(value: Any) -> int:
    """Coerce/guard a units_per_trace value: a positive int (bool rejected)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"units_per_trace must be an int, got {value!r}")
    if value < 1:
        raise ValueError(f"units_per_trace must be >= 1, got {value}")
    return value


def resolve_units_per_trace(declared: DeclaredParams | None) -> int:
    """Read the kit-declared ``generation.units_per_trace`` from declared params.

    Falls back to :data:`DEFAULT_UNITS_PER_TRACE` when the key is absent (or ``declared``
    is ``None``). Raises ``ValueError`` if a declared value is not a positive int.
    """
    if not declared or UNITS_PER_TRACE_KEY not in declared:
        return DEFAULT_UNITS_PER_TRACE
    return _validate_units_per_trace(declared[UNITS_PER_TRACE_KEY])


def advisory_estimate(target_traces: TargetTraces, units_per_trace: int | None = None) -> int:
    """The operator's advisory volume estimate: ``target_traces x units_per_trace``.

    ``units_per_trace`` defaults to :data:`DEFAULT_UNITS_PER_TRACE`. Advisory only — never
    a binding count. Raises ``ValueError`` on a non-positive/bool input.
    """
    if not isinstance(target_traces, int) or isinstance(target_traces, bool):
        raise ValueError(f"target_traces must be an int, got {target_traces!r}")
    if target_traces < 0:
        raise ValueError(f"target_traces must be >= 0, got {target_traces}")
    if units_per_trace is None:
        units_per_trace = DEFAULT_UNITS_PER_TRACE
    else:
        units_per_trace = _validate_units_per_trace(units_per_trace)
    return target_traces * units_per_trace
