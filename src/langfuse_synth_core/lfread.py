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
"""
from __future__ import annotations

import os
from datetime import datetime

import requests

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


def scores_path(base: str, *, throttle: float = 0.0) -> str:
    """``/api/public/v2/scores`` on current servers; a Langfuse v2 SERVER (self-hosted
    2.9x — naming is unrelated to the API's own /v2/ prefix, which is v3-era) only serves
    the legacy ``/api/public/scores``. Probe once per run."""
    try:
        get_json(base, "/api/public/v2/scores", {"limit": 1, "page": 1}, throttle=throttle)
        return "/api/public/v2/scores"
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return "/api/public/scores"
        raise


def get_all_scores(base: str, name: str, limit_pages: int = 30, *,
                   throttle: float = 0.0) -> list[dict]:
    """All scores with the given ``name``, following pagination up to ``limit_pages``."""
    out: list[dict] = []
    page = 1
    path = scores_path(base, throttle=throttle)
    while page <= limit_pages:
        data = get_json(base, path, {"name": name, "limit": 100, "page": page},
                        throttle=throttle)
        rows = data.get("data", [])
        out.extend(rows)
        meta = data.get("meta", {})
        if not rows or page >= meta.get("totalPages", page):
            break
        page += 1
    return out


def parse_ts(s: str) -> datetime:
    """Parse a Langfuse ISO timestamp (``...Z``) into an aware :class:`datetime`."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
