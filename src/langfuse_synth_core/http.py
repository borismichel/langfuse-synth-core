"""Resilient HTTP for the hand-rolled REST helpers (Cloud rate-limits with 429s).

The batch ingestion path has always retried; everything *else* (the ``verify`` query-back,
the prompt-label PATCH, the project guardrail, score-config creation) did a single shot
and ``raise_for_status``'d. Against Langfuse Cloud those single shots get 429ed — the v2
query API in particular throttles the rapid paginated reads a ``verify`` fires, and the
per-trace reserved-pool check hits one ``/traces/{id}`` per trace back-to-back. This is
the one retry helper, **Retry-After-aware**, shared by the paginated read client
(:mod:`.lfread`) and the ingest REST helpers — the project guardrail and score-config
create in :mod:`.seed.ingest`. (The batch-ingestion POST keeps its own inline retry loop,
moved verbatim with the ``Ingestor``; it predates this helper and is not routed through it.)

Ring 2 (#33): scenario-agnostic HTTP resilience — moved verbatim out of the EV kit into
the shared core (the library "speaks the Langfuse data model, bidirectionally"; retrying
its REST reads/writes is part of that machine, not the scenario). The per-target
``throttle_s`` spacing is a *value* the kit supplies from its Cloud-vs-self-hosted profile.
"""
from __future__ import annotations

import time

import requests

# Worth retrying: rate-limit + transient gateway/5xx. 4xx other than 429 is a real error.
_RETRY_STATUS = (429, 500, 502, 503, 504)
_MAX_WAIT_S = 60.0


def request_retry(method: str, url: str, *, auth, attempts: int = 8,
                  timeout: int = 30, throttle_s: float = 0.0, **kwargs) -> requests.Response:
    """``requests.request`` with exponential backoff on 429/5xx and connection errors.

    Honours a 429 ``Retry-After`` header (seconds), caps each wait at 60s. ``throttle_s``
    sleeps that long *after* the call returns terminally — used on Cloud to space
    one-at-a-time reads/writes so we don't trip the limiter in the first place. Returns
    the final :class:`requests.Response` (the caller still decides what to do with the
    status); raises only if every attempt hits a transport error.
    """
    backoff = 2.0
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.request(method, url, auth=auth, timeout=timeout, **kwargs)
        except requests.RequestException:
            if attempt == attempts:
                raise
            time.sleep(min(backoff, _MAX_WAIT_S))
            backoff = min(backoff * 2, _MAX_WAIT_S)
            continue

        if resp.status_code in _RETRY_STATUS and attempt < attempts:
            wait = backoff
            if resp.status_code == 429:
                try:
                    wait = max(wait, float(resp.headers.get("Retry-After", 0)))
                except (TypeError, ValueError):
                    pass
            time.sleep(min(wait, _MAX_WAIT_S))
            backoff = min(backoff * 2, _MAX_WAIT_S)
            continue

        if throttle_s:
            time.sleep(throttle_s)
        return resp

    return resp  # pragma: no cover — last attempt always returns or raises above
