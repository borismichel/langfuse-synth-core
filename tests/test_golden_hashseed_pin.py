"""The golden gate pins the hash seed (determinism hardening for #28 / #30).

The determinism law is ``seed + target_traces + params -> byte-identical Spool``. Python
salts ``str``/``bytes`` hashing with ``PYTHONHASHSEED`` per process, so a kit that
serializes a ``set`` (or any hash-ordered structure) without sorting would materialize
*different bytes on every run* — a false ``GoldenMismatch``, and exactly the flakiness
that would bite Step 0 (#30) when the gate is pointed at the real kits. The gate must
therefore pin the hash seed in the seed subprocess, so the byte-identity guarantee holds
by construction rather than by the kit author's vigilance.

Runs under the ``[authoring]`` extra (the gate ships behind it); skipped on a bare
runtime install, where the boundary is proved elsewhere.
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


def _hashy_spec(golden_path):
    from langfuse_synth_core.authoring.golden import GoldenSpec

    return GoldenSpec(
        seed_ref="hashy_kit:seed",
        target_traces=5,
        golden_path=golden_path,
        params={},
        search_paths=(FIXTURES,),
    )


def test_gate_pins_hash_seed_so_set_ordering_cannot_perturb_the_spool(tmp_path):
    """A kit careless about set ordering must still yield a byte-stable Spool.

    ``hashy_kit`` serializes a set of strings without sorting; without a pinned hash
    seed its two independent subprocess materializations would differ. The gate pins
    ``PYTHONHASHSEED``, so they are byte-identical.
    """
    from langfuse_synth_core.authoring.golden import materialize_spool

    spec = _hashy_spec(tmp_path / "unused.golden")
    first = materialize_spool(spec)
    second = materialize_spool(spec)
    assert first == second
