"""Determinism golden gate + ``synth freeze`` (#28) — the load-bearing oracle.

The defining test of ``langfuse-synth-core``: ``seed + target_traces + declared params
-> byte-identical Spool``, proven **offline, before ingestion**, on the FULL materialized
payload (not IDs + a summary). This is stronger than internal repeatability: a fresh
materialization is compared byte-for-byte against a blessed golden snapshot, so any
refactor or story change that silently perturbs the pool fails loudly at author time.

Two capabilities, one machine:

* :func:`assert_golden` — the gate. Materializes the Spool under the deny-LLM egress
  block and asserts byte-identity against the blessed golden.
* :func:`freeze` — ``synth freeze``. Materializes the Spool under the same block and
  writes it as the blessed golden, so a *deliberate* pool change (including refreshing an
  author-time LLM-generated fixture) is one intentional re-bless — never a hand-edit.

Both run ``seed`` in a subprocess under :mod:`langfuse_synth_core.authoring.egress`, so
the gate simultaneously proves determinism AND model-free-at-seed-runtime.

The ``target_traces`` derivation hook that runs at seed time is NOT here — it ships in
the runtime library (:mod:`langfuse_synth_core.derivation`) and the kit's own ``seed``
calls it. This module lives behind the ``[authoring]`` extra.

Seed contract (kit-owned):
    ``seed(target_traces: int, params: Mapping[str, Any]) -> bytes``
    Returns the fully materialized Spool payload, deterministically. Referenced by a
    ``"module.path:function"`` string so the gate can run it in a fresh subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langfuse_synth_core.authoring._seed_runner import EGRESS_EXIT_CODE
from langfuse_synth_core.authoring.egress import EgressBlockedError, egress_block_env


class GoldenError(AssertionError):
    """Base for golden-gate failures (an ``AssertionError`` so pytest frames it well)."""


class GoldenMissing(GoldenError):
    """The blessed golden does not exist yet — run ``synth freeze`` to bless it."""


class GoldenMismatch(GoldenError):
    """A freshly materialized Spool differs from the blessed golden.

    Either a refactor perturbed the deterministic pool (fix the code) or the pool was
    changed on purpose (re-bless with ``synth freeze``).
    """


@dataclass(frozen=True)
class GoldenSpec:
    """One golden case: a kit's seed pinned to a seed + params, and its golden path.

    ``target_traces`` and ``params`` are *declared* inputs — the gate is params-inclusive
    by construction because they are fed to ``seed`` and therefore shape the Spool bytes.
    """

    seed_ref: str
    target_traces: int
    golden_path: Path
    params: Mapping[str, Any] = field(default_factory=dict)
    # Extra sys.path entries the subprocess needs to import ``seed_ref`` (e.g. an
    # author's kit dir, or the in-lib fixture dir during tests). In a real kit the
    # package is installed, so this is usually empty.
    search_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "golden_path", Path(self.golden_path))


def materialize_spool(spec: GoldenSpec) -> bytes:
    """Run ``seed`` in a subprocess under the deny-LLM egress block; return Spool bytes.

    Raises :class:`~langfuse_synth_core.authoring.egress.EgressBlockedError` if the seed
    attempts any non-loopback network access (e.g. a planted LLM call), and
    ``RuntimeError`` for any other seed/contract failure.
    """
    with tempfile.TemporaryDirectory(prefix="synth-golden-") as tmp:
        out_path = Path(tmp) / "spool.bin"
        config_path = Path(tmp) / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "seed_ref": spec.seed_ref,
                    "target_traces": spec.target_traces,
                    "params": dict(spec.params),
                    "out_path": str(out_path),
                    "search_paths": list(spec.search_paths),
                }
            ),
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, "-m", "langfuse_synth_core.authoring._seed_runner",
             str(config_path)],
            env=egress_block_env(os.environ),
            capture_output=True,
            text=True,
        )

        if result.returncode == EGRESS_EXIT_CODE:
            raise EgressBlockedError(
                "seed attempted LLM/network egress under the deny-LLM egress block — "
                "seed runtime must be model-free (move any one-off LLM call to authoring "
                "time and freeze its output as a static fixture).\n" + result.stderr.strip()
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"seed failed (exit {result.returncode}) while materializing the Spool:\n"
                f"{result.stderr.strip()}"
            )
        return out_path.read_bytes()


def assert_golden(spec: GoldenSpec) -> None:
    """The golden gate: assert the fresh Spool is byte-identical to the blessed golden.

    Raises :class:`GoldenMissing` if nothing has been frozen yet, and
    :class:`GoldenMismatch` on any byte difference (full payload, params-inclusive).
    """
    if not spec.golden_path.exists():
        raise GoldenMissing(
            f"no blessed golden at {spec.golden_path} — run `synth freeze` to bless the "
            "oracle for this seed + target_traces + params."
        )
    fresh = materialize_spool(spec)
    blessed = spec.golden_path.read_bytes()
    if fresh != blessed:
        raise GoldenMismatch(
            f"materialized Spool for {spec.seed_ref} "
            f"(target_traces={spec.target_traces}, params={dict(spec.params)!r}) is NOT "
            f"byte-identical to the blessed golden at {spec.golden_path} "
            f"({len(fresh)} vs {len(blessed)} bytes). Either a refactor perturbed the "
            "deterministic pool, or the pool changed on purpose — re-bless with "
            "`synth freeze`."
        )


def freeze(spec: GoldenSpec) -> Path:
    """``synth freeze``: materialize under the egress block and bless as the golden.

    One intentional step. Returns the golden path written. Creates parent dirs.
    """
    fresh = materialize_spool(spec)
    spec.golden_path.parent.mkdir(parents=True, exist_ok=True)
    spec.golden_path.write_bytes(fresh)
    return spec.golden_path
