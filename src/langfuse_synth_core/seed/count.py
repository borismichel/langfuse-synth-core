"""Spool-count primitive (#35) — the measured billable set read off a materialized Spool.

``count_spool`` is the read-side sibling of :meth:`Ingestor.import_spool`: it walks the
same on-disk NDJSON spool (``.synth_spool/events.ndjson``) and returns the exact set
Langfuse meters — ``{traces, observations, scores}`` plus the billable ``total`` — by
tallying envelope ``type`` (batch lines) and spans (OTLP lines).

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

from . import otlp
from .events import OBSERVATION_EVENT_TYPES, SCORE_EVENT_TYPES, TRACE_EVENT_TYPES


def count_spool(spool_path: str | Path) -> dict[str, int]:
    """Tally the measured billable set in a materialized NDJSON Spool.

    Returns ``{"traces": int, "observations": int, "scores": int, "total": int}`` —
    Langfuse's exact metered set plus the billable total the cap gate measures against.
    Reads one JSON envelope per line (blank lines skipped) and classifies by ``type``
    against the billable whitelist; non-billable envelopes (dataset items,
    experiment/dataset-run items, ``sdk-log``, …) are excluded.

    Raises ``FileNotFoundError`` if the spool does not exist — the same failure mode as
    ``import-spool`` against a missing file, so the boundary behaves identically.

    **Both write paths, one output shape** (portal #206). A batch Spool is tallied by
    envelope ``type``. An OTLP Spool has no trace envelope to count — v4 has no trace
    entity — so the trace term is derived from **distinct trace ids** across its spans, and
    every span is an observation. The returned shape is identical either way, which is what
    keeps the plan-time estimate, the cap gate and the over-cap halt untouched by the
    migration.

    **``total`` is owned here, not summed by the caller** (portal #220). Only this reader
    knows which write path produced a line, and the trace term's billing meaning differs by
    path: a batch ``trace-create`` is an ingested object, so it counts toward ``total``; an
    OTLP trace is a *view* over its minted root span, which is already inside
    ``observations``, so adding the derived trace term would count the same things twice.
    ``total`` therefore stays invariant across a kit's cutover — the minted roots raise
    ``observations`` by exactly the trace count the total drops.
    """
    path = Path(spool_path)
    if not path.exists():
        raise FileNotFoundError(f"count_spool: spool file not found: {path}")

    envelope_traces = 0  # ingested trace OBJECTS — batch lines only
    observations = 0
    scores = 0
    otlp_trace_ids: set[str] = set()  # the derived trace term — views, never objects
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if otlp.is_span(entry):
                observations += 1
                otlp_trace_ids.add(entry["traceId"])
                continue
            etype = entry.get("type")
            if etype in TRACE_EVENT_TYPES:
                envelope_traces += 1
            elif etype in OBSERVATION_EVENT_TYPES:
                observations += 1
            elif etype in SCORE_EVENT_TYPES:
                scores += 1
    return {
        "traces": envelope_traces + len(otlp_trace_ids),
        "observations": observations,
        "scores": scores,
        # The billable total counts INGESTED objects only: an envelope trace is an
        # object; an OTLP-derived trace is a view whose minted root is already inside
        # ``observations``, so adding it would bill each OTLP trace twice (portal #220).
        "total": envelope_traces + observations + scores,
    }
