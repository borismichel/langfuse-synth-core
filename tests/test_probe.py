"""Backdate probe id strategy — data-model middle field (Ring 2, #33).

The probe trace is throwaway (tagged ``synth-probe``), so its id must be UNIQUE per run,
nonce-salted — NOT deterministic — so it never collides across deployments or lands on a
tombstoned id on a re-used project. The kits' own probe tests share this guard; here it is
locked at the lib level too.
"""

from __future__ import annotations

from langfuse_synth_core.probe import probe_ids
from langfuse_synth_core.rng import Rng


def test_probe_ids_are_unique_per_run_with_the_same_seed():
    rng = Rng(42)
    t1, o1 = probe_ids(rng)
    t2, o2 = probe_ids(rng)
    assert t1 != t2 and o1 != o2


def test_probe_ids_keep_w3c_trace_context_widths():
    tid, obs = probe_ids(Rng(42))
    assert len(tid) == 32 and int(tid, 16) >= 0   # 16-byte trace id, valid hex
    assert len(obs) == 16 and int(obs, 16) >= 0   # 8-byte observation id, valid hex


def test_probe_ids_are_not_the_fixed_deterministic_id():
    # The pre-fix collision id (seed 42, deterministic) — must never be reproduced.
    old_collision = "ebc16bd0f806178ea49c5e8d0d546015"
    for _ in range(50):
        tid, _obs = probe_ids(Rng(42))
        assert tid != old_collision
