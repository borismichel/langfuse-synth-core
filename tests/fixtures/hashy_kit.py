"""A fixture kit whose Spool ordering is hash-seed-sensitive (the foil to tiny_kit).

Unlike ``tiny_kit`` — which is hand-hardened to be hash-seed-independent — this kit is
deliberately *careless* in the most ordinary way: it serializes a ``set`` of strings
without sorting, so its iteration order (and therefore its bytes) is salted by
``PYTHONHASHSEED``. A real kit author writes code like this all the time.

It exists to prove the golden gate PINS the hash seed: a careless-but-common kit must
still materialize a byte-stable Spool, so the determinism law
(``seed + target_traces + params -> byte-identical Spool``) holds by the gate's
construction, not by the kit author's vigilance. Model-free and network-free, so it
passes cleanly under the deny-LLM egress block.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

# Enough distinct strings that two independent random hash seeds iterate the set in
# different orders with overwhelming probability (20 elements => negligible collision).
_TAGS = tuple(f"tag-{chr(c)}" for c in range(ord("a"), ord("u")))


def seed(target_traces: int, params: Mapping[str, Any]) -> bytes:
    # A set of strings: iteration order is salted by PYTHONHASHSEED unless it is pinned.
    # No sort() anywhere on purpose — that is exactly the carelessness the gate guards.
    tags = {t for t in _TAGS}
    return json.dumps(list(tags)).encode("utf-8")
