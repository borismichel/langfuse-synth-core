"""Determinism golden gate + ``synth-authoring freeze`` + deny-LLM egress (#28).

Mirrors the ticket's acceptance criteria against a small in-lib fixture kit
(``tests/fixtures/tiny_kit.py``), which materializes a tiny deterministic Spool. These
run under the ``[authoring]`` extra (the golden gate ships behind it); the module skips
on a bare runtime install, where the boundary is proved elsewhere.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="golden gate ships in the [authoring] extra; not installed on a runtime-only job",
)

FIXTURES = str(Path(__file__).resolve().parent / "fixtures")


def _spec(golden_path, seed_ref="tiny_kit:seed", target_traces=5, params=None):
    from langfuse_synth_core.authoring.golden import GoldenSpec

    return GoldenSpec(
        seed_ref=seed_ref,
        target_traces=target_traces,
        golden_path=golden_path,
        params=params if params is not None else {"seed": 1, "persona": "ada"},
        search_paths=(FIXTURES,),
    )


# --- AC: byte-identical full-payload Spool, offline, pre-ingestion ------------------
def test_gate_passes_on_byte_identical_spool(tmp_path):
    """freeze blesses; a fresh materialization is byte-identical => the gate is green."""
    from langfuse_synth_core.authoring.golden import assert_golden, freeze

    spec = _spec(tmp_path / "tiny.golden")
    freeze(spec)
    # No exception == byte-identical full payload for seed + target_traces + params.
    assert_golden(spec)


def test_gate_is_full_payload_not_ids_and_summary(tmp_path):
    """The blessed golden is the whole materialized Spool, not an ID list + tally."""
    from langfuse_synth_core.authoring.golden import freeze

    spec = _spec(tmp_path / "tiny.golden")
    freeze(spec)
    blob = spec.golden_path.read_bytes()
    # Full payload: the observations and scores are present, not just trace IDs.
    assert b'"observations"' in blob and b'"score"' in blob and b'"tokens"' in blob


def test_materialization_is_deterministic_across_runs(tmp_path):
    """Two independent subprocess materializations are byte-identical (params-inclusive)."""
    from langfuse_synth_core.authoring.golden import materialize_spool

    spec = _spec(tmp_path / "unused.golden")
    assert materialize_spool(spec) == materialize_spool(spec)


# --- AC: the gate is params-inclusive (seed + target_traces + declared params) ------
@pytest.mark.parametrize(
    "mutate",
    [
        {"target_traces": 6},
        {"params": {"seed": 2, "persona": "ada"}},
        {"params": {"seed": 1, "persona": "grace"}},
    ],
)
def test_gate_fails_when_any_declared_input_changes(tmp_path, mutate):
    """Change target_traces, the seed, or a declared param => the golden no longer matches."""
    from langfuse_synth_core.authoring.golden import GoldenMismatch, assert_golden, freeze

    golden = tmp_path / "tiny.golden"
    freeze(_spec(golden))
    with pytest.raises(GoldenMismatch):
        assert_golden(_spec(golden, **mutate))


def test_gate_fails_on_a_perturbed_golden(tmp_path):
    """A hand-edited golden (a stand-in for a refactor that perturbed the pool) fails."""
    from langfuse_synth_core.authoring.golden import GoldenMismatch, assert_golden, freeze

    spec = _spec(tmp_path / "tiny.golden")
    freeze(spec)
    spec.golden_path.write_bytes(spec.golden_path.read_bytes() + b"tamper")
    with pytest.raises(GoldenMismatch):
        assert_golden(spec)


def test_gate_reports_missing_golden(tmp_path):
    from langfuse_synth_core.authoring.golden import GoldenMissing, assert_golden

    with pytest.raises(GoldenMissing):
        assert_golden(_spec(tmp_path / "never-frozen.golden"))


# --- AC: synth-authoring freeze blesses/updates in one step; a re-bless greens an intended change
def test_freeze_reblesses_an_intentionally_changed_pool(tmp_path):
    """A deliberate pool change is one intentional re-bless, never a hand-edit."""
    from langfuse_synth_core.authoring.golden import (
        GoldenMismatch,
        assert_golden,
        freeze,
    )

    golden = tmp_path / "tiny.golden"
    freeze(_spec(golden, target_traces=5))
    assert_golden(_spec(golden, target_traces=5))

    changed = _spec(golden, target_traces=9)  # intentional pool change
    with pytest.raises(GoldenMismatch):
        assert_golden(changed)

    freeze(changed)  # one intentional re-bless
    assert_golden(changed)  # green again


# --- AC: seed runs under the deny-LLM egress block; model-free fixture passes --------
def test_clean_fixture_passes_under_the_egress_block(tmp_path):
    """The whole gate runs seed under deny-LLM egress; a model-free seed is unaffected."""
    from langfuse_synth_core.authoring.golden import freeze

    # freeze -> materialize_spool -> subprocess under egress_block_env + socket guard.
    path = freeze(_spec(tmp_path / "tiny.golden"))
    assert path.stat().st_size > 0


# --- AC: negative test — a planted LLM call fails seed under the egress block --------
def test_planted_llm_call_fails_seed_under_egress_block(tmp_path):
    """A planted LLM call in kit-owned generation must make seed FAIL under the block."""
    from langfuse_synth_core.authoring.egress import EgressBlockedError
    from langfuse_synth_core.authoring.golden import materialize_spool

    spec = _spec(tmp_path / "llm.golden", seed_ref="tiny_kit_llm:seed")
    with pytest.raises(EgressBlockedError):
        materialize_spool(spec)


def test_freeze_of_a_kit_with_a_planted_llm_call_fails(tmp_path):
    """freeze cannot bless a non-model-free kit — the egress block trips first."""
    from langfuse_synth_core.authoring.egress import EgressBlockedError
    from langfuse_synth_core.authoring.golden import freeze

    spec = _spec(tmp_path / "llm.golden", seed_ref="tiny_kit_llm:seed")
    with pytest.raises(EgressBlockedError):
        freeze(spec)
    assert not spec.golden_path.exists()  # nothing blessed on failure


# --- AC: the derivation hook does NOT live behind [authoring] ------------------------
def test_derivation_hook_ships_in_runtime_not_authoring():
    """The seed-time hook must import from the runtime lib, never from the extra."""
    import langfuse_synth_core.derivation as runtime_derivation

    assert runtime_derivation.__name__ == "langfuse_synth_core.derivation"
    assert "authoring" not in runtime_derivation.__name__
    assert runtime_derivation.identity_derivation(7, {}) == {"target_traces": 7}
