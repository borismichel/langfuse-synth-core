"""The observation-type vocabulary, and the guard that replaces the batch path's 400 (#217).

Batch ingestion accepted three observation types and **rejected** everything else with a
``400``. The OTLP wire accepts anything: an unrecognised ``langfuse.observation.type`` is
not refused, it is quietly filed as something else — a ``SPAN``, or a ``GENERATION`` when
the span carries a model. Confirmed against a real Langfuse Cloud project on 2026-08-19.

So a typo'd tool step that names a model lands in cost and usage views with nothing
reported anywhere, and the demo tells a different story than its author wrote. These
assert core supplies the safety net the wire removed: the vocabulary is closed, the wire
boundary is case-sensitive about it, and a kit finds out at its own builder call.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from langfuse_synth_core.seed import otlp
from langfuse_synth_core.seed.events import observation_event
from langfuse_synth_core.observation_types import (
    OBSERVATION_TYPES,
    UnknownObservationType,
    checked_observation_type,
)

TS = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
TID = "5b8aa1cfd0e34f7a9c2b6d15e0473f88"
OID = "a1b2c3d4e5f60718"


def attrs(span: dict) -> dict:
    return {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}


def test_the_vocabulary_is_the_ten_types_langfuse_recognises():
    """The closed set, lowercase — the three the batch path carried plus the seven
    agent-graph types the OTLP wire adds."""
    assert set(OBSERVATION_TYPES) == {
        "span", "generation", "event",
        "agent", "tool", "chain", "retriever", "embedding", "evaluator", "guardrail",
    }


def test_a_type_outside_the_vocabulary_is_refused_at_the_wire_boundary():
    with pytest.raises(UnknownObservationType, match="genration"):
        otlp.observation_span(
            obs_id=OID, trace_id=TID, name="x", obs_type="genration", start=TS, end=TS
        )


def test_the_wire_boundary_is_case_sensitive():
    """Uppercase is exactly the failure the wire hides: ``AGENT`` lands as ``SPAN``. The
    boundary that writes the attribute therefore takes the wire's own spelling only."""
    with pytest.raises(UnknownObservationType):
        checked_observation_type("AGENT")
    assert checked_observation_type("agent") == "agent"


def test_the_refusal_names_the_silent_degradation():
    """AC: an author who sees a valid-looking value refused learns *why* — not that a rule
    exists, but that Langfuse would have accepted it and shown something else."""
    with pytest.raises(UnknownObservationType) as exc:
        checked_observation_type("llm")
    message = str(exc.value).lower()
    assert "generation" in message and "model" in message
    assert "span" in message
    assert "guardrail" in message  # the vocabulary itself is in the message


def test_a_mistyped_observation_type_fails_at_the_builder():
    """The kit-facing builder is where an author meets this: the vocabulary is a property
    of the target, and the builder refuses before anything reaches the wire."""
    with pytest.raises(UnknownObservationType, match="toool"):
        observation_event(
            obs_id=OID, trace_id=TID, name="lookup", obs_type="TOOOL",
            start=TS, end=TS,
        )


@pytest.mark.parametrize("spelling", ["TOOL", "tool"])
def test_the_builder_takes_either_spelling_and_writes_the_wire_one(spelling):
    """Kits spell these uppercase and core lowercases for the wire. That normalisation
    stays — what it may not do is normalise a value that is not in the vocabulary at
    all."""
    span = observation_event(
        obs_id=OID, trace_id=TID, name="lookup", obs_type=spelling, start=TS, end=TS,
    )
    assert attrs(span)["langfuse.observation.type"] == "tool"


def test_every_type_the_vocabulary_carries_builds():
    from langfuse_synth_core.seed import events as events_mod

    for obs_type in OBSERVATION_TYPES:
        span = events_mod.observation_event(
            obs_id=OID, trace_id=TID, name="x", obs_type=obs_type, start=TS, end=TS,
        )
        assert attrs(span)["langfuse.observation.type"] == obs_type


def test_the_live_seam_refuses_a_type_the_sdk_would_pass_through(monkeypatch):
    """The other seam: the SDK writes ``as_type`` verbatim and Langfuse takes it, so the
    live surface is where the spec's `AGENT` → `SPAN` row actually bites. Checked strictly
    there, because nothing downstream lowercases it."""
    from langfuse_synth_core.live.emit import LiveTrace

    class _Root:
        def start_as_current_observation(self, **kw):
            raise AssertionError(f"the SDK should never be reached: {kw}")

    trace = LiveTrace(_Root(), emitter=None)
    for refused in ("AGENT", "genration"):
        with pytest.raises(UnknownObservationType):
            with trace.observation("step", as_type=refused):
                pass
