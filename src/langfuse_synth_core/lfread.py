"""The Langfuse **read client** — authenticated, paginated GETs (Ring 2 verify split, #33).

The seam (docs/SEAM.md) splits ``verify`` in two:

* **read-helpers** (auth + paginated GET of scores / traces across the Langfuse public REST
  API) → *here*, in the shared core. This is the read direction of "the machine that speaks
  the Langfuse data model, bidirectionally": fetching scores by name across pages, GETing a
  trace by id, probing which scores endpoint a server serves. All scenario-agnostic, all
  parametrized by ``base_url`` (+ a Cloud ``throttle`` value the kit supplies from its
  target profile).
* the **``run_verify`` body** (which assertions to make about what landed) → the kit. That
  is the scenario talking, and it stays there.

Uses raw REST (HTTP Basic) against known public endpoints, so it is robust to SDK
method-name churn, and rides :func:`langfuse_synth_core.http.request_retry` so Cloud's 429s
on the rapid paginated reads back off rather than flake.

**The v4 migration moved the substance of this module** into
:mod:`langfuse_synth_core.read` — the read seam, which owns the v3→v4 endpoint remap and
hands back normalised rows (portal #208). What is left here is the two primitives that did
not change (auth, a single authenticated GET, timestamp parsing) plus
:func:`get_all_scores`, kept as a **compatibility front** so a kit that has not been
rewired yet keeps reading the row shape it was written against — on either generation.
New kit code calls the seam; :func:`get_all_scores` is retired with the last caller (#211).

The legacy ``scores_path`` probe is **gone**. It chose between `/api/public/v2/scores` and
`/api/public/scores`; platform v4 `404`s both, so the question it answered no longer has a
right answer. The seam resolves the API *generation* instead.
"""
from __future__ import annotations

import os
from datetime import datetime

from . import read
from .http import request_retry


def auth_from_env() -> tuple[str, str]:
    """HTTP Basic credentials for the public API, from the standard env vars."""
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


def get_json(base: str, path: str, params: dict | None = None, *, throttle: float = 0.0) -> dict:
    """GET ``{base}{path}`` and return parsed JSON, raising on non-2xx.

    Retry-After-aware: Cloud 429s the rapid paginated reads the verify sweep fires."""
    resp = request_retry("GET", f"{base.rstrip('/')}{path}", params=params or {},
                         auth=auth_from_env(), timeout=30, throttle_s=throttle)
    resp.raise_for_status()
    return resp.json()


def get_all_scores(base: str, name: str, limit_pages: int = 30, *,
                   throttle: float = 0.0) -> list[dict]:
    """All scores with the given ``name``, as **legacy-shaped dicts**, on either generation.

    Reads through :class:`langfuse_synth_core.read.LangfuseReader` and renders each
    normalised row back into the `value` / `stringValue` / `traceId` / `sessionId` shape the
    kits' current ``verify`` bodies index into. On a target that has cut over, those columns
    no longer exist on the wire — v3 answers one typed `value` and a discriminated
    `subject` — so this is a real translation, not a pass-through, and it is what lets a kit
    read a v4 project before it is rewired onto the seam.
    """
    reader = read.LangfuseReader(base, auth=auth_from_env(), throttle=throttle)
    return [_legacy_score_row(s) for s in reader.scores(name=name, limit_pages=limit_pages)]


def _legacy_score_row(score: "read.Score") -> dict:
    """Render a normalised score as the row shape the deprecated scores APIs returned.

    Faithfully, including the parts that were never useful: a categorical score came back
    as ``value: 0`` **beside** its ``stringValue``, so that is what a caller gets here. A
    kit doing ``float(row["value"])`` on one was already reading a placeholder; it must not
    start raising on the day its target cuts over. Reading a categorical score correctly is
    what the seam's :attr:`~langfuse_synth_core.read.Score.string_value` is for.
    """
    row = dict(score.raw)
    row.update({
        "id": score.id,
        "name": score.name,
        "dataType": score.data_type,
        "value": score.numeric_value if score.numeric_value is not None else (
            0 if score.string_value is not None else None),
        "stringValue": score.string_value,
        "comment": score.comment,
        "timestamp": score.timestamp.isoformat() if score.timestamp else None,
        "traceId": score.trace_id,
        "observationId": score.observation_id,
        "sessionId": score.session_id,
        "datasetRunId": score.experiment_id,
    })
    row.pop("subject", None)
    return row


def parse_ts(s: str) -> datetime:
    """Parse a Langfuse ISO timestamp (``...Z``) into an aware :class:`datetime`.

    One implementation, in the read seam — the two must never drift, because a `verify` that
    parsed timestamps differently from the seam that fetched them would compare a demo's
    story against itself and lose."""
    return read.parse_ts(s)
