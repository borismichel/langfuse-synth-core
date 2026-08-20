"""The real-time ingestion header — one fact, shared by both write paths (portal #211).

Langfuse processes exported spans on two paths. Without
``x-langfuse-ingestion-version: 4`` a v4 target takes the slow one, and the data is not
readable for up to fifteen minutes (measured on Cloud, 2026-08-19 — portal #205). With it,
spans are queryable in seconds.

Both of the depot's writers need to say this, and they are on opposite sides of the
determinism line: the Spool's OTLP exporter (:mod:`langfuse_synth_core.seed.ingest`,
backdated, golden-gated) and the live-emission seam's SDK client
(:mod:`langfuse_synth_core.live.emit`, wall-clock, outside the gate). Neither may import
the other, so the constant they share sits **above both** — the same shape as the
observation-type vocabulary (#217).
"""
from __future__ import annotations

#: Selects the v4 ingestion path, so directly-ingested OTEL data is visible in real time.
INGESTION_VERSION_HEADER = "x-langfuse-ingestion-version"

#: The version that path is selected by. A string because it is an HTTP header value.
INGESTION_VERSION = "4"
