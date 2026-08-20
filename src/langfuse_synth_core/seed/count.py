"""Spool-count primitive (#35) — the measured billable set read off a materialized Spool.

``count_spool`` is the read-side sibling of :meth:`Ingestor.import_spool`: it walks the
same on-disk NDJSON spool (``.synth_spool/events.ndjson``) and returns the exact set
Langfuse meters — ``{traces, observations, scores}`` plus the billable ``total`` — by
tallying OTLP spans and `score-create` envelopes, which is everything a Spool contains.

This is the count the deploy pipeline reads at the boundary (Spec D wires it into the
``generate-spool -> [cap-gate] -> import-spool`` split; that split is out of scope here).
It lives in the library because the library already speaks the Langfuse data model and
owns the NDJSON spool format.

**Measured, not advisory.** The tally is the ground truth that binds — it is the same
bytes ``import-spool`` will upload. The optional kit-declared ``units_per_trace`` advisory
(see :mod:`langfuse_synth_core.derivation`) is only ever an *estimate*; its inaccuracy is
harmless because this count is what the cap gate actually reads.

**Exclusions.** Experiment runs and dataset items are not billed as line items and never
appear in a Spool (they ride separate REST endpoints), so they are excluded by
construction. Any non-billable envelope (an ``sdk-log``, a future non-metered type) is
likewise ignored.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import otlp
from .events import SCORE_EVENT_TYPES


def count_spool(spool_path: str | Path) -> dict[str, int]:
    """Tally the measured billable set in a materialized NDJSON Spool.

    Returns ``{"traces": int, "observations": int, "scores": int, "total": int}`` —
    Langfuse's exact metered set plus the billable total the cap gate measures against.
    Reads one JSON wire object per line (blank lines skipped): an OTLP span is an
    observation, a `score-create` envelope is a score, and anything else is not billable.

    Raises ``FileNotFoundError`` if the spool does not exist — the same failure mode as
    ``import-spool`` against a missing file, so the boundary behaves identically.

    **The trace term is derived, and the total does not include it** (portal #206, #220).
    v4 has no trace entity, so there is nothing to count directly: the trace term is the
    number of **distinct trace ids** across the Spool's spans. Each of those traces is a
    *view* over its minted root span, which is already inside ``observations``, so adding
    the trace term to ``total`` would count the same objects twice. ``total`` is therefore
    owned here rather than summed by the caller, and it stayed invariant across the fleet's
    cutover from the batch path — the minted roots raised ``observations`` by exactly the
    trace count the retired ``trace-create`` term dropped.
    """
    path = Path(spool_path)
    if not path.exists():
        raise FileNotFoundError(f"count_spool: spool file not found: {path}")

    observations = 0
    scores = 0
    trace_ids: set[str] = set()  # the derived trace term — views, never objects
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if otlp.is_span(entry):
                observations += 1
                trace_ids.add(entry["traceId"])
            elif entry.get("type") in SCORE_EVENT_TYPES:
                scores += 1
    return {
        "traces": len(trace_ids),
        "observations": observations,
        "scores": scores,
        # INGESTED objects only: a derived trace is a view whose minted root is already
        # inside ``observations``, so adding it would bill each trace twice (portal #220).
        "total": observations + scores,
    }
