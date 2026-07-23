"""Derivation-hook mechanism — runtime library (#29).

Proves the kit-side hook contract: DETERMINISTIC ``target_traces -> kit internals``. The
hook type + identity default MUST be importable from the runtime lib (not gated behind
``[authoring]``), because it runs at seed time. Covers the identity default and a
synthetic non-identity (Lender-style derive-scale) hook.
"""

from __future__ import annotations

from typing import Any, Mapping

from langfuse_synth_core.derivation import (
    NO_PORTAL_MAPPING,
    TARGET_TRACES_KEY,
    DerivationHook,
    identity_derivation,
)


def test_canonical_knob_key_is_the_dotted_set_key():
    # The portal passes `--set generation.target_traces=N` verbatim; the key is fixed.
    assert TARGET_TRACES_KEY == "generation.target_traces"


def test_no_portal_side_mapping_is_documented_and_flagged():
    # The mechanism requires no portal-side mapping table (kit-side hook does the work).
    assert NO_PORTAL_MAPPING is True


def test_identity_derivation_passes_the_count_straight_through():
    # EV-style direct count: target_traces -> {"target_traces": target_traces}.
    assert identity_derivation(1, {}) == {"target_traces": 1}
    assert identity_derivation(2500, {"unrelated": "value"}) == {"target_traces": 2500}


def test_identity_derivation_is_deterministic():
    declared = {"seed": 42, "extra": [1, 2, 3]}
    first = identity_derivation(1000, declared)
    second = identity_derivation(1000, declared)
    assert first == second == {"target_traces": 1000}


def test_identity_derivation_ignores_declared_params():
    assert identity_derivation(50, {"a": 1}) == identity_derivation(50, {"b": 2})


def test_identity_derivation_satisfies_the_hook_type():
    # A DerivationHook is just a callable of the right shape; the default must qualify.
    hook: DerivationHook = identity_derivation
    assert hook(7, {}) == {"target_traces": 7}


# --- A synthetic non-identity hook: Lender-style derive-scale ------------------------
#
# Illustrative ONLY (applying the real hook to the kits is Ring 2, #33/#34). Derives a
# `volume.scale` multiplier from target_traces against a declared baseline, while the
# fixed golden assets (suite / experiments / queue) are passed through UNSCALED — the
# hook contract's "fixed golden assets unscaled" clause.

_BASELINE_TRACES = 500
_GOLDEN_SUITE_SIZE = 12  # a fixed golden asset — must never scale with the knob


def lender_like_derivation(
    target_traces: int, declared: Mapping[str, Any]
) -> Mapping[str, Any]:
    baseline = declared.get("baseline_traces", _BASELINE_TRACES)
    return {
        "volume.scale": target_traces / baseline,
        "golden_suite.size": _GOLDEN_SUITE_SIZE,  # unscaled, verbatim
        "experiments.count": declared.get("experiments.count", 3),  # unscaled
    }


def test_synthetic_derivation_scales_only_volume():
    out = lender_like_derivation(1000, {})
    assert out["volume.scale"] == 2.0  # 1000 / 500 baseline
    # Golden assets are untouched regardless of the knob.
    assert out["golden_suite.size"] == _GOLDEN_SUITE_SIZE
    assert out["experiments.count"] == 3


def test_synthetic_derivation_golden_assets_never_scale():
    small = lender_like_derivation(100, {})
    large = lender_like_derivation(50_000, {})
    assert small["volume.scale"] != large["volume.scale"]
    # The fixed golden asset is identical across wildly different volumes.
    assert small["golden_suite.size"] == large["golden_suite.size"] == _GOLDEN_SUITE_SIZE


def test_synthetic_derivation_is_deterministic():
    declared = {"baseline_traces": 250, "experiments.count": 5}
    runs = [lender_like_derivation(3000, declared) for _ in range(5)]
    assert all(run == runs[0] for run in runs)
    assert runs[0]["volume.scale"] == 12.0  # 3000 / 250
    assert runs[0]["experiments.count"] == 5


def test_synthetic_derivation_satisfies_the_hook_type():
    hook: DerivationHook = lender_like_derivation
    assert hook(500, {}) == {
        "volume.scale": 1.0,
        "golden_suite.size": _GOLDEN_SUITE_SIZE,
        "experiments.count": 3,
    }
