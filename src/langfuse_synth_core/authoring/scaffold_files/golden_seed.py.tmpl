"""Golden-gate seed adapter (dev-only; never shipped in the runtime image).

The determinism golden gate in `langfuse-synth-core[authoring]` drives a kit through one
contract — `seed(target_traces, params) -> bytes` (the full materialized Spool). This
adapter drives the REAL runtime seed path (`synth.seed.run_seed` in dry-run: spool through
the library's `Ingestor`, no network, no upload) and returns the resulting Spool bytes,
so the gate proves the actual `synth seed` is deterministic AND model-free — not a parallel
materializer that could drift from it.

It lives in `tests/` because the gate is authoring-time tooling behind the `[authoring]`
extra; the deployed runtime image must never carry it (Spec A §3). The gate imports it via
`search_paths`, in a subprocess under PYTHONHASHSEED=0 and the deny-LLM egress block.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from langfuse_synth_core.timegen import parse_as_of_date

from synth.config import load_config
from synth.seed import run_seed

CONFIG = Path(__file__).resolve().parent.parent / "config" / "demo.yaml"

# The pinned as-of date — the third leg of the determinism law (`seed + target_traces +
# as-of → byte-identical Spool`, portal #229). It lives HERE, in the dev-only gate, and
# nowhere in `src/`: production resolves the date from the operator (or the clock), and
# the oracle pins it so the golden bytes are reproducible on any day.
AS_OF_DATE = "2026-01-01"


def seed(target_traces: int, params: Mapping[str, Any]) -> bytes:
    """Materialize the full pre-upload Spool for `target_traces` through the runtime seed
    path; return its bytes.

    `params` supports `seed` and `as_of_date`, or their `generation.`-prefixed
    config names. The default date stays pinned for the golden. Volume belongs to
    the dedicated `target_traces` argument. Extend the supported inputs explicitly
    when the Recipe gains new declared parameters.
    """
    declared: dict[str, Any] = {}
    for key, value in params.items():
        name = key.removeprefix("generation.")
        if name == "target_traces":
            raise ValueError("set volume with the target_traces argument, not params")
        if name not in {"seed", "as_of_date"}:
            raise ValueError(f"unsupported golden parameter {key!r}; use seed or as_of_date")
        if name in declared:
            raise ValueError(f"golden parameter {name!r} specified more than once")
        if name == "seed" and (type(value) is not int or value < 0):
            raise ValueError("golden seed must be a non-negative integer")
        if name == "as_of_date":
            if not isinstance(value, str) or not value:
                raise ValueError("golden as_of_date must be YYYY-MM-DD")
            try:
                parse_as_of_date(value)
            except ValueError as exc:
                raise ValueError("golden as_of_date must be YYYY-MM-DD") from exc
        declared[name] = value
    declared.setdefault("as_of_date", AS_OF_DATE)
    cfg = load_config(
        str(CONFIG),
        overrides=[f"generation.target_traces={int(target_traces)}"] + [
            f"generation.{key}={json.dumps(value)}" for key, value in declared.items()
        ],
    )
    with tempfile.TemporaryDirectory(prefix="synth-golden-") as tmp:
        spool_path = Path(tmp) / "events.ndjson"
        # Golden runs own temporary state as well as a temporary Spool. A dry run
        # invalidates verification receipts and may write anchors; neither may touch
        # the last live run, including when the caller inherited SYNTH_STATE_DIR.
        previous_state_dir = os.environ.get("SYNTH_STATE_DIR")
        os.environ["SYNTH_STATE_DIR"] = tmp
        try:
            # dry_run: no guardrail or network; do_import=False: never touch Langfuse.
            run_seed(
                cfg, dry_run=True, do_import=False, spool_path=spool_path,
                output_dir=Path(tmp) / "out",
                runbook_source=CONFIG.parent.parent / "DEMO_SCRIPT.md",
                log=lambda _m: None,
            )
            return spool_path.read_bytes()
        finally:
            if previous_state_dir is None:
                os.environ.pop("SYNTH_STATE_DIR", None)
            else:
                os.environ["SYNTH_STATE_DIR"] = previous_state_dir
