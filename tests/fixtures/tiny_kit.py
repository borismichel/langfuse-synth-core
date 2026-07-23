"""A minimal, model-free fixture kit for the determinism golden gate (#28).

Not a real kit — the smallest thing that materializes a tiny deterministic Spool so the
gate has something to bite on in-lib (pointing the gate at the real kits on ``main`` is
Step 0 / #30, out of scope here). It exercises the seam faithfully:

* ``seed(target_traces, params) -> bytes`` returns the FULL pre-ingestion payload,
  deterministically and params-inclusively.
* The ``target_traces`` derivation hook runs at seed time, imported from the RUNTIME
  library (:mod:`langfuse_synth_core.derivation`), never from ``[authoring]``.
* No LLM, no network — model-free by construction, so it passes under the egress block.
"""

from __future__ import annotations

import json
import random
from collections.abc import Mapping
from typing import Any

from langfuse_synth_core.derivation import identity_derivation

_NAMES = ("chat", "search", "summarize", "classify")


def _canonical(obj: Any) -> bytes:
    """Byte-stable JSON: sorted keys, no incidental whitespace — a real Spool payload."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def seed(target_traces: int, params: Mapping[str, Any]) -> bytes:
    # The canonical volume knob flows through the runtime derivation hook at seed time.
    internal = identity_derivation(target_traces, params)
    count = int(internal["target_traces"])
    persona = str(params.get("persona", "default"))

    # Seed the RNG with a STRING (random handles str deterministically via sha512 —
    # unlike hashing a tuple, which PYTHONHASHSEED would make non-reproducible).
    rng = random.Random(f"{params.get('seed', 0)}|{count}|{persona}")

    traces = []
    for i in range(count):
        observations = [
            {"index": j, "type": "generation", "tokens": rng.randint(10, 500)}
            for j in range(rng.randint(1, 3))
        ]
        traces.append(
            {
                "id": f"trace-{i:04d}",
                "name": rng.choice(_NAMES),
                "score": round(rng.random(), 6),
                "observations": observations,
            }
        )

    spool = {"schema": "spool/v1", "persona": persona, "traces": traces}
    return _canonical(spool)
