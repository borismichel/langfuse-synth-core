"""Public live-emission contract against the installed SDK and controlled HTTP/export sinks.

These tests do not claim real-project ingestion/readiness; released-image rehearsals must
also read back observations and scores on Cloud or self-hosted Langfuse >=4.59.0.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
from langfuse import Langfuse
from langfuse.model import TextPromptClient
from langfuse.api import TextPrompt
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from langfuse_synth_core.live.emit import LiveEmitter


@pytest.fixture
def live():
    exported = InMemorySpanExporter()
    scores = []

    def receive(request):
        if request.url.path == "/api/public/scores" and request.method == "POST":
            batch = json.loads(request.content)
            scores.extend(batch)
            return httpx.Response(200, json={"message": "Accepted"})
        raise AssertionError(f"Unexpected SDK request: {request.method} {request.url.path}")

    client = Langfuse(
        base_url="http://langfuse.test", public_key=f"pk-test-{uuid.uuid4()}",
        secret_key="sk-test", span_exporter=exported,
        httpx_client=httpx.Client(transport=httpx.MockTransport(receive)),
        flush_at=1000, flush_interval=0.05,
    )
    emitter = LiveEmitter("http://langfuse.test", client=client, environment="sdk-test")
    yield emitter, exported, scores
    client.shutdown()


def test_live_observations_preserve_typed_context_io_and_generation_details(live):
    emitter, exported, _ = live
    prompt = TextPromptClient(TextPrompt(name="decision", version=7, prompt="Decide",
                                         config={}, labels=[], tags=[]))
    with emitter.trace("request", input={"question": "approve?"}, user_id="analyst",
                       session_id="review-session", tags=["certification"],
                       metadata={"rubric": {"threshold": 0.9}, "attempt": 2,
                                 "approved": True, "source": "live"}) as trace:
        with trace.span("retrieval", input="filing") as retrieval:
            retrieval.update(output="complete filing")
        with trace.generation("decision", model="test-model", prompt=prompt,
                              usage={"input": 300, "output": 100},
                              cost={"input": 0.003, "output": 0.002}) as decision:
            decision.update(output={"approved": True})
        trace.update(output={"decision": "approve"})
    spans = {s.name: s for s in exported.get_finished_spans()}
    root = spans["request"]
    assert root.parent is None
    assert json.loads(root.attributes["langfuse.observation.input"]) == {"question": "approve?"}
    assert json.loads(root.attributes["langfuse.observation.output"]) == {"decision": "approve"}
    for span in spans.values():
        assert span.context.trace_id == root.context.trace_id
        assert span.attributes["user.id"] == "analyst"
        assert span.attributes["session.id"] == "review-session"
        assert span.attributes["langfuse.environment"] == "sdk-test"
        assert list(span.attributes["langfuse.trace.tags"]) == ["certification"]
        assert json.loads(span.attributes["langfuse.trace.metadata.source"]) == "live"
        assert json.loads(span.attributes["langfuse.trace.metadata.attempt"]) == 2
        assert json.loads(span.attributes["langfuse.trace.metadata.approved"]) is True
        assert json.loads(span.attributes["langfuse.trace.metadata.rubric"]) == {"threshold": 0.9}
    for name in ("retrieval", "decision"):
        assert spans[name].parent.span_id == root.context.span_id
    generation = spans["decision"].attributes
    assert generation["langfuse.observation.model.name"] == "test-model"
    assert json.loads(generation["langfuse.observation.usage_details"]) == {
        "input": 300, "output": 100}
    assert json.loads(generation["langfuse.observation.cost_details"]) == {
        "input": 0.003, "output": 0.002}
    assert generation["langfuse.observation.prompt.name"] == "decision"
    assert generation["langfuse.observation.prompt.version"] == 7


def test_flush_delivers_live_scores_with_observation_and_session_subjects(live):
    emitter, _, scores = live
    with emitter.trace("request") as trace:
        with trace.generation("answer") as answer:
            answer.score("helpful", 1, data_type="BOOLEAN", score_id="feedback-1")
    emitter.score("resolved", "yes", session_id="review-session", data_type="CATEGORICAL",
                  score_id="session-1", metadata={"reviewed": True})
    emitter.flush()
    assert len(scores) == 2
    observation, session = scores
    assert observation["id"] == "feedback-1"
    assert observation["traceId"] == trace.id
    assert observation["observationId"] == answer.id
    assert observation["value"] == 1
    assert observation["dataType"] == "BOOLEAN"
    assert session["id"] == "session-1"
    assert session["sessionId"] == "review-session"
    assert session["value"] == "yes"
    assert session["metadata"] == {"reviewed": True}
    assert all(s["environment"] == "sdk-test" for s in scores)
