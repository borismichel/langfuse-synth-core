"""Authenticated raw-REST primitives for the endpoints the read seam does not model.

The seam (docs/SEAM.md) splits ``verify`` in two: the read-helpers live in the shared core,
the ``run_verify`` body (which assertions to make about what landed) stays in the kit. The
v4 migration then moved the substance of the read side into
:mod:`langfuse_synth_core.read` — the **read seam**, which owns the v3→v4 endpoint remap
and hands back normalised rows (portal #208). Every trace / observation / session / score /
experiment read goes through :class:`~langfuse_synth_core.read.LangfuseReader`.

What is left here is the three primitives that survive that move because they never had a
generation to remap: HTTP Basic credentials from the env, one authenticated GET, and
timestamp parsing. They are how a kit reads the endpoints the migration left alone —
``/api/public/projects``, ``/datasets``, ``/dataset-items``, ``/score-configs``,
``/v2/prompts``, ``/annotation-queues``, ``/health``, ``/models`` — without reaching for an
HTTP client of its own and losing the Retry-After-aware backoff with it.

**Retired in v3.0.0** (portal #211): ``get_all_scores``, the compatibility front that
rendered the seam's rows back into the deprecated `value` / `stringValue` / `traceId` dict
shape so a not-yet-rewired kit kept working on either generation. All three kits now read
scores through ``reader.scores(...)``, so the shim has no callers and the legacy row shape
is gone from the codebase — including its worst habit, a categorical score reporting
``value: 0`` beside its label. Read a score's label with
:attr:`~langfuse_synth_core.read.Score.string_value`.

``scores_path`` went in the same direction one release earlier: it probed
``/api/public/v2/scores`` with a fallback to ``/api/public/scores``, and platform v4 `404`s
both, so the question it answered no longer has a right answer. The seam resolves the API
*generation* instead.
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


def parse_ts(s: str) -> datetime:
    """Parse a Langfuse ISO timestamp (``...Z``) into an aware :class:`datetime`.

    One implementation, in the read seam — the two must never drift, because a `verify` that
    parsed timestamps differently from the seam that fetched them would compare a demo's
    story against itself and lose."""
    return read.parse_ts(s)
