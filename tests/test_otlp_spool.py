"""The finalised OTLP Spool (portal #206) — what actually goes on the wire.

A builder cannot see its trace's whole story: a kit composes child observations first and
prepends the trace shell afterwards, and the root's end time is only known once the last
child exists. So the Spool is finalised as it is closed. These assert the finalised story —
every observation of a trace is filterable by the trace's own dimensions, the hierarchy is
intact, and the root covers its children — rather than the shape of the payload carrying it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


from langfuse_synth_core.seed.events import generation_event, score_event, span_event, trace_event
from langfuse_synth_core.seed.ingest import Ingestor
from langfuse_synth_core.seed.otlp import trace_root_span_id

TS = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
TID = "5b8aa1cfd0e34f7a9c2b6d15e0473f88"


def _trace_events(tid: str = TID) -> list[dict]:
    """One trace shaped like a real kit's: children first, shell prepended last."""
    top = "aaaa1111bbbb2222"
    events = [
        span_event(obs_id=top, trace_id=tid, name="agent", start=TS,
                   end=TS + timedelta(seconds=3)),
        generation_event(obs_id="cccc3333dddd4444", trace_id=tid, name="decision",
                         start=TS + timedelta(seconds=1), end=TS + timedelta(seconds=5),
                         parent_id=top, model="m", usage_details={}, cost_details={}),
    ]
    events.insert(0, trace_event(trace_id=tid, timestamp=TS, name="credit_decision",
                                 user_id="u-7", session_id="s-3", tags=["eu"],
                                 metadata={"kind": "renewal"},
                                 input={"app": 1}, output={"ok": True}))
    return events


def _spool(tmp_path: Path, events: list[dict]) -> list[dict]:
    ing = Ingestor(base_url="http://x", public_key="p", secret_key="s",
                   spool_path=tmp_path / "events.ndjson")
    ing.open_spool()
    ing.extend(events)
    ing.close_spool()
    return [json.loads(line) for line in
            (tmp_path / "events.ndjson").read_text(encoding="utf-8").splitlines()]


def _attrs(span: dict) -> dict:
    return {a["key"]: a["value"] for a in span["attributes"]}


def test_trace_dimensions_are_queryable_on_every_observation(tmp_path: Path):
    lines = _spool(tmp_path, _trace_events())
    for span in lines:
        a = _attrs(span)
        assert a["langfuse.user.id"] == {"stringValue": "u-7"}
        assert a["langfuse.session.id"] == {"stringValue": "s-3"}
        assert a["langfuse.trace.name"] == {"stringValue": "credit_decision"}
        assert a["langfuse.trace.metadata.kind"] == {"stringValue": "renewal"}


def test_the_hierarchy_survives_as_parent_span_context(tmp_path: Path):
    lines = _spool(tmp_path, _trace_events())
    by_id = {s["spanId"]: s for s in lines}
    root_id = trace_root_span_id(TID)
    assert "parentSpanId" not in by_id[root_id]
    # A kit's top-level observation had no parent; it hangs off the trace's root.
    assert by_id["aaaa1111bbbb2222"]["parentSpanId"] == root_id
    # An explicitly parented child keeps its own parent.
    assert by_id["cccc3333dddd4444"]["parentSpanId"] == "aaaa1111bbbb2222"


def test_the_root_observation_covers_its_children(tmp_path: Path):
    lines = _spool(tmp_path, _trace_events())
    root = next(s for s in lines if s["spanId"] == trace_root_span_id(TID))
    latest_child_end = max(int(s["endTimeUnixNano"]) for s in lines
                           if s["spanId"] != root["spanId"])
    assert int(root["endTimeUnixNano"]) == latest_child_end
    assert int(root["startTimeUnixNano"]) < latest_child_end


def test_one_traces_context_never_leaks_into_another(tmp_path: Path):
    other = "1111222233334444555566667777888a"
    lines = _spool(tmp_path, _trace_events() + _trace_events(other))
    for span in lines:
        a = _attrs(span)
        assert a["langfuse.trace.name"] == {"stringValue": "credit_decision"}
    assert {s["traceId"] for s in lines} == {TID, other}


def test_scores_stay_ingestion_envelopes_on_the_otlp_path(tmp_path: Path):
    lines = _spool(tmp_path, [score_event(score_id="s1", name="quality", value=1,
                                          data_type="NUMERIC", timestamp=TS, trace_id=TID)])
    assert lines == [{"id": lines[0]["id"], "type": "score-create",
                      "timestamp": "2026-06-09T12:00:00.000Z",
                      "body": {"id": "s1", "name": "quality", "value": 1,
                               "dataType": "NUMERIC", "traceId": TID,
                               "environment": "production"}}]


def test_finalisation_is_a_pure_function_of_the_lines(tmp_path: Path):
    a = _spool(tmp_path / "a", _trace_events())
    b = _spool(tmp_path / "b", _trace_events())
    assert a == b


def test_a_spool_carrying_no_span_is_never_rewritten(tmp_path: Path):
    """Finalisation is driven by what was written, not by an assumption. A Spool of nothing
    but score envelopes has no trace shell to apply and no re-parenting to do, so closing it
    must not re-serialise a single line."""
    from langfuse_synth_core.seed.events import score_event

    events = [score_event(score_id="a1b2c3d4e5f60718", name="q", value=1,
                          data_type="NUMERIC", timestamp=TS, trace_id=TID)]
    ing = Ingestor(base_url="http://x", public_key="p", secret_key="s",
                   spool_path=tmp_path / "events.ndjson")
    ing.open_spool()
    ing.extend(events)
    ing.close_spool()
    # The exact bytes `add` wrote, re-derived independently — a rewrite pass would reorder
    # keys or reformat numbers and this would catch it.
    expected = b"".join(
        json.dumps(e, separators=(",", ":")).encode() + b"\n" for e in events)
    assert (tmp_path / "events.ndjson").read_bytes() == expected
