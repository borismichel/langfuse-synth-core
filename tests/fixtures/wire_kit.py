"""A fixture kit that materializes its Spool through the REAL write path (portal #206).

``tiny_kit`` proves the golden gate's determinism machinery on a hand-rolled payload. This
one proves the same law over the machinery the migration actually changes: the event
builders and the ``Ingestor``'s spool phase. It is the in-lib stand-in for a kit, so the
gate can bite on "the flag off is byte-identical, the flag on is deterministic" without a
real kit checked out.

Model-free and network-free by construction — it only ever writes to disk.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langfuse_synth_core.rng import Rng
from langfuse_synth_core.seed.events import generation_event, score_event, span_event, trace_event
from langfuse_synth_core.seed.ingest import Ingestor

_BASE = datetime(2026, 6, 9, 9, 0, 0, tzinfo=timezone.utc)


def seed(target_traces: int, params: Mapping[str, Any]) -> bytes:
    rng = Rng(int(params.get("seed", 0)))
    with tempfile.TemporaryDirectory(prefix="wire-kit-") as tmp:
        spool = Path(tmp) / "events.ndjson"
        ing = Ingestor(base_url="http://unused", public_key="", secret_key="",
                       dry_run=True, spool_path=spool)
        ing.open_spool()
        for i in range(target_traces):
            r = rng.sub("trace", str(i))
            tid = r.trace_id("wire", str(i))
            agent = r.obs_id("agent", str(i))
            start = _BASE + timedelta(days=i, minutes=i * 7)
            ing.extend([
                trace_event(trace_id=tid, timestamp=start, name="wire_check",
                            user_id=f"u-{i}", session_id=f"s-{i % 3}", tags=["fixture"],
                            metadata={"index": i},
                            input={"ask": i}, output={"ok": True}),
                span_event(obs_id=agent, trace_id=tid, name="agent", start=start,
                           end=start + timedelta(seconds=4)),
                generation_event(obs_id=r.obs_id("gen", str(i)), trace_id=tid, name="answer",
                                 start=start + timedelta(seconds=1),
                                 end=start + timedelta(seconds=3), parent_id=agent,
                                 model="claude-sonnet-4",
                                 usage_details={"input": 100 + i, "output": 20 + i},
                                 cost_details={"total": 0.001 * (i + 1)}),
                score_event(score_id=r.obs_id("score", str(i)), name="quality", value=1,
                            data_type="NUMERIC", timestamp=start, trace_id=tid),
            ])
        ing.close_spool()
        return spool.read_bytes()
