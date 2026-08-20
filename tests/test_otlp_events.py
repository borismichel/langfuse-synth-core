"""What the event builders serialise on the OTLP path (portal #206).

The kit-facing Python API does not move: the builders keep their names and their arguments,
and a kit composes exactly the same call tree. What changes under the flag is the *wire
object* each one returns — an OTLP span rather than an ingestion envelope.

These assert the story a span carries, not the shape of a payload for its own sake: the
producer's own ids and timestamps survive, nesting is parent span context, and the Langfuse
fields land on their documented ``langfuse.*`` attributes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from langfuse_synth_core.seed import writepath
from langfuse_synth_core.seed.events import (
    event_event,
    generation_event,
    span_event,
    trace_event,
)

TS = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
TID = "5b8aa1cfd0e34f7a9c2b6d15e0473f88"
OID = "a1b2c3d4e5f60718"


@pytest.fixture(autouse=True)
def _on_otlp():
    with writepath.use_spool_write_path(writepath.OTLP):
        yield


def attrs(span: dict) -> dict:
    """The span's attributes as a plain mapping, unwrapping OTLP AnyValue."""
    out = {}
    for a in span["attributes"]:
        v = a["value"]
        out[a["key"]] = (
            v.get("stringValue")
            if "stringValue" in v
            else v.get("intValue")
            if "intValue" in v
            else [i["stringValue"] for i in v["arrayValue"]["values"]]
        )
    return out


def test_producer_minted_ids_pass_through_verbatim():
    span = span_event(obs_id=OID, trace_id=TID, name="retrieve", start=TS,
                      end=TS + timedelta(milliseconds=180))
    assert span["traceId"] == TID
    assert span["spanId"] == OID


def test_timestamps_are_the_producers_own_nanoseconds():
    end = TS + timedelta(milliseconds=180)
    span = span_event(obs_id=OID, trace_id=TID, name="retrieve", start=TS, end=end)
    # 2026-06-09T12:00:00Z is epoch second 1781006400 (`date -u -j -f ... +%s`), so the
    # backdated nanos are that, verbatim — no wall-clock read anywhere on this path.
    assert span["startTimeUnixNano"] == "1781006400000000000"
    assert span["endTimeUnixNano"] == "1781006400180000000"


def test_nesting_is_parent_span_context_not_a_body_field():
    parent = "0f1e2d3c4b5a6978"
    span = span_event(obs_id=OID, trace_id=TID, name="retrieve", start=TS, end=TS,
                      parent_id=parent)
    assert span["parentSpanId"] == parent
    assert "parentObservationId" not in json.dumps(span)


def test_span_carries_its_type_level_io_and_environment():
    span = span_event(obs_id=OID, trace_id=TID, name="retrieve", start=TS, end=TS,
                      environment="staging", input={"q": "rate"}, output=["a"],
                      level="WARNING", status_message="slow",
                      metadata={"stage": "recall"})
    a = attrs(span)
    assert a["langfuse.observation.type"] == "span"
    assert a["langfuse.observation.input"] == '{"q":"rate"}'
    assert a["langfuse.observation.output"] == '["a"]'
    assert a["langfuse.observation.level"] == "WARNING"
    assert a["langfuse.observation.status_message"] == "slow"
    assert a["langfuse.environment"] == "staging"
    assert a["langfuse.observation.metadata.stage"] == "recall"


def test_generation_carries_model_usage_cost_and_prompt_link():
    gen = generation_event(
        obs_id=OID, trace_id=TID, name="decision", start=TS,
        end=TS + timedelta(seconds=2), completion_start=TS + timedelta(milliseconds=400),
        model="claude-sonnet-4", usage_details={"input": 120, "output": 40},
        cost_details={"total": 0.0031}, model_parameters={"temperature": 0.3},
        prompt_name="credit-decision", prompt_version=3,
    )
    a = attrs(gen)
    assert a["langfuse.observation.type"] == "generation"
    assert a["langfuse.observation.model.name"] == "claude-sonnet-4"
    assert json.loads(a["langfuse.observation.usage_details"]) == {"input": 120, "output": 40}
    assert json.loads(a["langfuse.observation.cost_details"]) == {"total": 0.0031}
    assert json.loads(a["langfuse.observation.model.parameters"]) == {"temperature": 0.3}
    assert a["langfuse.observation.prompt.name"] == "credit-decision"
    assert a["langfuse.observation.prompt.version"] == "3"
    assert a["langfuse.observation.completion_start_time"] == "2026-06-09T12:00:00.400Z"


def test_a_discrete_event_is_a_zero_duration_span():
    ev = event_event(obs_id=OID, trace_id=TID, name="policy_cache_hit", start=TS)
    assert attrs(ev)["langfuse.observation.type"] == "event"
    assert ev["endTimeUnixNano"] == ev["startTimeUnixNano"]


def test_the_trace_shell_becomes_a_root_span_carrying_the_trace_fields():
    root = trace_event(trace_id=TID, timestamp=TS, name="credit_decision",
                       user_id="u-7", session_id="s-3", tags=["eu", "auto"],
                       environment="production", metadata={"kind": "renewal"},
                       input={"app": 1}, output={"decision": "approve"})
    a = attrs(root)
    assert root["traceId"] == TID
    assert "parentSpanId" not in root          # a root span has no parent context
    assert a["langfuse.trace.name"] == "credit_decision"
    assert a["langfuse.user.id"] == "u-7"
    assert a["langfuse.session.id"] == "s-3"
    assert a["langfuse.trace.tags"] == ["eu", "auto"]
    assert a["langfuse.trace.metadata.kind"] == "renewal"
    # Overall input/output sit on the ROOT OBSERVATION, never on deprecated trace IO.
    assert a["langfuse.observation.input"] == '{"app":1}'
    assert a["langfuse.observation.output"] == '{"decision":"approve"}'


def test_deprecated_trace_input_output_is_never_emitted():
    root = trace_event(trace_id=TID, timestamp=TS, name="credit_decision",
                       input={"app": 1}, output={"decision": "approve"})
    assert "langfuse.trace.input" not in attrs(root)
    assert "langfuse.trace.output" not in attrs(root)


def test_the_batch_path_is_untouched_when_the_flag_is_off():
    with writepath.use_spool_write_path(writepath.BATCH):
        env = span_event(obs_id=OID, trace_id=TID, name="retrieve", start=TS, end=TS)
    assert env["type"] == "span-create"
    assert env["body"]["id"] == OID


def test_an_id_otlp_cannot_carry_fails_at_the_builder():
    """Core's BLAKE2b ids are already the widths OTLP accepts, so this never fires for a
    kit using ``Rng``. It fires for a kit that hand-rolls an id — and it fires at the
    builder, with the offending value, instead of as an opaque server-side rejection
    thousands of spans into an import."""
    from langfuse_synth_core.seed.otlp import OtlpError

    with pytest.raises(OtlpError, match="trace id"):
        span_event(obs_id=OID, trace_id="too-short", name="x", start=TS, end=TS)
    with pytest.raises(OtlpError, match="span id"):
        span_event(obs_id="nothex-nothex-xx", trace_id=TID, name="x", start=TS, end=TS)
    with pytest.raises(OtlpError, match="span id"):
        span_event(obs_id=OID, trace_id=TID, name="x", start=TS, end=TS, parent_id="short")


def test_rich_observation_types_ship_by_default():
    """``RICH_OBSERVATION_TYPES`` is on (portal #210): the agent-graph types go out on the
    wire natively, riding the same core release as the kits' OTLP cutover. Measured before
    flipping: the flag moves NO counts on either path — only the wire kind changes — so a
    typed observation needs no ``metadata.observation_type`` fallback any more."""
    from langfuse_synth_core.seed import events as events_mod

    assert events_mod.RICH_OBSERVATION_TYPES is True

    typed = events_mod.observation_event(
        obs_id=OID, trace_id=TID, name="tool_call", obs_type="TOOL", start=TS, end=TS,
    )
    assert attrs(typed)["langfuse.observation.type"] == "tool"
    assert "langfuse.observation.metadata.observation_type" not in attrs(typed)


def test_turning_the_flag_off_still_degrades_identically(monkeypatch):
    """The degrade mode survives as the escape hatch: with the flag off both paths emit an
    untyped span carrying the intended type in ``metadata.observation_type`` — same rule,
    same spelling — so a revert is a one-line flip, not a format fork."""
    from langfuse_synth_core.seed import events as events_mod

    monkeypatch.setattr(events_mod, "RICH_OBSERVATION_TYPES", False)
    degraded = events_mod.observation_event(
        obs_id=OID, trace_id=TID, name="tool_call", obs_type="TOOL", start=TS, end=TS,
    )
    assert attrs(degraded)["langfuse.observation.type"] == "span"
    assert attrs(degraded)["langfuse.observation.metadata.observation_type"] == "tool"
