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
