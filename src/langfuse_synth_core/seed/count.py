"""Spool-count primitive (#35) — the measured billable set read off a materialized Spool.

``count_spool`` is the read-side sibling of :meth:`Ingestor.import_spool`: it walks the
same on-disk NDJSON spool (``.synth_spool/events.ndjson``) and returns the exact set
Langfuse meters — ``{traces, observations, scores}`` — by tallying envelope ``type``.

This is the count the deploy pipeline reads at the boundary (Spec D wires it into the
``generate-spool -> [cap-gate] -> import-spool`` split; that split is out of scope here).
It lives in the library because the library already speaks the Langfuse data model and
owns the NDJSON spool format.

**Measured, not advisory.** The tally is the ground truth that binds — it is the same
bytes ``import-spool`` will upload. The optional kit-declared ``units_per_trace`` advisory
(see :mod:`langfuse_synth_core.derivation`) is only ever an *estimate*; its inaccuracy is
harmless because this count is what the cap gate actually reads.

**Exclusions.** Experiment runs and dataset items are not billed as line items and never
appear as ingestion envelopes (they ride separate REST endpoints), so the billable-type
whitelist in :mod:`langfuse_synth_core.seed.events` excludes them by construction. Any
non-billable line (an ``sdk-log``, a future non-metered type) is likewise ignored.
"""

from __future__ import annotations

import json
from pathlib import Path

from .events import OBSERVATION_EVENT_TYPES, SCORE_EVENT_TYPES, TRACE_EVENT_TYPES


def count_spool(spool_path: str | Path) -> dict[str, int]:
    """Tally the measured billable set in a materialized NDJSON Spool.

    Returns ``{"traces": int, "observations": int, "scores": int}`` — Langfuse's exact
    metered set. Reads one JSON envelope per line (blank lines skipped) and classifies by
    ``type`` against the billable whitelist; non-billable envelopes (dataset items,
    experiment/dataset-run items, ``sdk-log``, …) are excluded.

    Raises ``FileNotFoundError`` if the spool does not exist — the same failure mode as
    ``import-spool`` against a missing file, so the boundary behaves identically.
    """
    path = Path(spool_path)
    if not path.exists():
        raise FileNotFoundError(f"count_spool: spool file not found: {path}")

    counts = {"traces": 0, "observations": 0, "scores": 0}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            etype = json.loads(line).get("type")
            if etype in TRACE_EVENT_TYPES:
                counts["traces"] += 1
            elif etype in OBSERVATION_EVENT_TYPES:
                counts["observations"] += 1
            elif etype in SCORE_EVENT_TYPES:
                counts["scores"] += 1
    return counts
