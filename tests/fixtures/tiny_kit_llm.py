"""A fixture kit with a PLANTED LLM call in its generation code (#28 negative test).

This is the adversary the gate must catch: an agent "enriching" a story by calling an
LLM at seed runtime. The call lives in kit-owned generation code — not in the library's
model-free-by-construction write machinery — so it proves the deny-LLM egress block
guards the *agent's* code. Under the block, ``seed`` must FAIL (the connection to the
provider is denied before it is made), never silently succeed.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from typing import Any

from langfuse_synth_core.derivation import identity_derivation


def _enrich_via_llm(prompt: str) -> str:
    # A genuine outbound LLM call planted in generation. Under the egress block this
    # raises before any bytes leave the process; off the block it would (try to) reach
    # the provider — exactly the model-at-seed-time hazard the gate exists to forbid.
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"prompt": prompt}).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
        return response.read().decode("utf-8")


def seed(target_traces: int, params: Mapping[str, Any]) -> bytes:
    internal = identity_derivation(target_traces, params)
    count = int(internal["target_traces"])

    traces = []
    for i in range(count):
        enriched = _enrich_via_llm(f"describe trace {i}")  # <-- the forbidden call
        traces.append({"id": f"trace-{i:04d}", "story": enriched})

    return json.dumps({"schema": "spool/v1", "traces": traces}).encode("utf-8")
