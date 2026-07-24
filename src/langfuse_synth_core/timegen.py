"""ISO-8601 formatting primitive for the event-emission layer.

This is the shared *formatting* primitive the ingestion event bodies need — distinct
from a kit's timestamp *sampling* (diurnal/weekly weighting, session placement), which
is scenario-shaped and stays in the kit (the "library-with-parameters" middle field,
see docs/SEAM.md). ``events`` calls ``iso`` to stamp every envelope, so it lives here
next to the emitters. ``iso`` is byte-identical across the gold-standard kits — it is
the Langfuse ingestion API's on-the-wire timestamp format, not a scenario choice.
"""
from __future__ import annotations

from datetime import datetime, timezone


def iso(dt: datetime) -> str:
    """ISO-8601 with milliseconds and a trailing Z, as the ingestion API expects."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
