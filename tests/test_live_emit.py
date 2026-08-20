"""The live-emission seam — wall-clock traces off the Spool's machinery (portal #208).

A Companion App, a playground submission, a workbench run and an experiment task all emit
*now*, so they need no backdating and they belong on the Langfuse SDK — which is exactly
what the Spool may never use. These tests drive the seam against a fake SDK client and
assert the story that lands: one trace, its children nested under it, its attributes
propagated onto every span (there is no trace entity under v4 to hold them), and its scores
attached to the right subject.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from langfuse_synth_core.live import emit


class _FakeSpan:
    def __init__(self, client, kw, parent=None):
        self.client = client
        self.kw = kw
        self.parent = parent
        self.trace_id = "trace-abc"
        self.id = f"span-{len(client.spans)}"
        self.updates: list[dict] = []
        client.spans.append(self)
        client.events.append(f"start:{kw.get('name')}")

    def update(self, **kw):
        self.updates.append(kw)
        return self

    @contextmanager
    def start_as_current_observation(self, **kw):
        child = _FakeSpan(self.client, kw, parent=self)
        yield child
        self.client.events.append(f"end:{kw.get('name')}")

    def start_observation(self, **kw):
        return _FakeSpan(self.client, kw, parent=self)


class _FakeClient:
    """Stands in for the Langfuse SDK client — the seam's one external dependency."""

    def __init__(self):
        self.spans: list[_FakeSpan] = []
        self.scores: list[dict] = []
        self.events: list[str] = []
        self.flushes = 0
        self.shutdowns = 0

    @contextmanager
    def start_as_current_observation(self, **kw):
        root = _FakeSpan(self, kw)
        yield root
        self.events.append(f"end:{kw.get('name')}")

    def create_score(self, **kw):
        self.scores.append(kw)

    def flush(self):
        self.flushes += 1
        self.events.append("flush")

    def shutdown(self):
        self.shutdowns += 1


@pytest.fixture
def client():
    return _FakeClient()


@pytest.fixture
def propagated():
    """Captures what the seam propagates as trace-level attributes."""
    seen: list[dict] = []

    @contextmanager
    def fake_propagate(**kw):
        seen.append(kw)
        yield

    fake_propagate.seen = seen  # type: ignore[attr-defined]
    return fake_propagate


@pytest.fixture
def emitter(client, propagated):
    return emit.LiveEmitter("http://lf", client=client, propagate=propagated)


def test_a_live_trace_lands_as_a_root_observation_carrying_the_overall_io(emitter, client):
    with emitter.trace("playground_submission", input={"amount": 10}) as trace:
        trace.update(output={"decision": "approve"})

    root = client.spans[0]
    assert root.kw["name"] == "playground_submission"
    assert root.kw["as_type"] == "span"
    assert root.kw["input"] == {"amount": 10}
    # v4 puts the overall request and response on the ROOT observation — there is no trace
    # body to hold them, and the deprecated trace-IO helpers are forbidden outright.
    assert root.updates == [{"output": {"decision": "approve"}}]


def test_trace_level_attributes_are_propagated_to_every_span(emitter, propagated):
    with emitter.trace("submission", user_id="playground_user", session_id="sess-1",
                       tags=["playground"], environment="production"):
        pass

    attrs = propagated.seen[0]
    assert attrs["trace_name"] == "submission"
    assert attrs["user_id"] == "playground_user"
    assert attrs["session_id"] == "sess-1"
    assert attrs["tags"] == ["playground"]
    assert attrs["environment"] == "production"


def test_a_generation_nests_under_the_trace_and_carries_its_model_columns(emitter, client):
    with emitter.trace("submission") as trace:
        with trace.generation("decision", model="claude-sonnet-4",
                              input=[{"role": "system", "content": "you are"}],
                              usage={"input": 120, "output": 34},
                              cost={"total": 0.004}) as gen:
            gen.update(output="approve")

    root, generation = client.spans
    assert generation.parent is root                 # nesting is real parent span context
    assert generation.kw["as_type"] == "generation"
    assert generation.kw["model"] == "claude-sonnet-4"
    assert generation.kw["usage_details"] == {"input": 120, "output": 34}
    assert generation.kw["cost_details"] == {"total": 0.004}
    assert generation.updates == [{"output": "approve"}]


def test_the_caller_gets_the_trace_id_back_for_its_deep_link(emitter):
    with emitter.trace("submission") as trace:
        trace_id = trace.id

    assert trace_id == "trace-abc"


def test_a_score_attaches_to_its_subject_and_names_its_type(emitter, client):
    emitter.score("user_disagreement", 1, trace_id="trace-abc", data_type="BOOLEAN",
                  comment="customer disputed the decision")

    assert client.scores == [{
        "name": "user_disagreement", "value": 1, "trace_id": "trace-abc",
        "data_type": "BOOLEAN", "comment": "customer disputed the decision",
    }]


def test_the_trace_is_flushed_when_the_block_ends(emitter, client):
    """A live surface is judged by whether the trace shows up seconds later; the SDK
    batches in the background, so the seam flushes rather than hoping."""
    with emitter.trace("submission"):
        assert client.flushes == 0

    assert client.flushes == 1


def test_the_root_observation_is_ended_before_the_flush(emitter, client):
    """Order matters and is easy to get wrong: flushing while the root span is still open
    delivers the children and leaves the trace's own observation behind in the buffer."""
    with emitter.trace("submission") as trace:
        with trace.span("step"):
            pass

    assert client.events == ["start:submission", "start:step", "end:step",
                             "end:submission", "flush"]


def test_a_failing_submission_still_flushes_what_it_emitted(emitter, client):
    """A surface that raises mid-request has still produced a trace worth seeing — that is
    usually the trace someone needs."""
    with pytest.raises(RuntimeError):
        with emitter.trace("submission"):
            raise RuntimeError("the model refused")

    assert client.flushes == 1


def test_the_sdk_client_is_built_with_the_real_time_ingestion_header(monkeypatch):
    """Without `x-langfuse-ingestion-version: 4` a v4 target takes the slow ingestion path
    and the trace is not readable for up to fifteen minutes (portal #205). A live surface
    hands its user a deep link the moment it answers, so the header is not an optimisation
    here — it is the difference between that link showing a trace and showing nothing."""
    from langfuse_synth_core import ingestion

    built = {}

    class _SDK:
        def __init__(self, **kw):
            built.update(kw)

    monkeypatch.setitem(__import__("sys").modules, "langfuse",
                        type("m", (), {"Langfuse": _SDK, "propagate_attributes": None}))

    emitter = emit.LiveEmitter("https://cloud.langfuse.com", public_key="pk", secret_key="sk")
    assert emitter.client is not None

    assert built["additional_headers"] == {
        ingestion.INGESTION_VERSION_HEADER: ingestion.INGESTION_VERSION}
    assert built["host"] == "https://cloud.langfuse.com"


def test_both_write_paths_send_the_same_ingestion_version(monkeypatch):
    """The Spool's exporter and the live seam's SDK client are on opposite sides of the
    determinism line and may not import one another, so the constant they share sits above
    both. This is the test that keeps the two from drifting."""
    from langfuse_synth_core import ingestion
    from langfuse_synth_core.seed import ingest, otlp

    assert otlp.INGESTION_VERSION_HEADER == ingestion.INGESTION_VERSION_HEADER
    assert ingest.Ingestor("http://x", "pk", "sk").ingestion_version == ingestion.INGESTION_VERSION


def test_a_grandchild_nests_under_its_own_parent_not_the_root(emitter, client):
    """An agent graph is more than two levels deep — EV's live submission nests
    `extract_fields` inside `load_application` — and a live surface is a web server, so a
    tree assembled out of the SDK's ambient context would interleave two submissions in
    flight. Every level opens its own children, explicitly (portal #211)."""
    with emitter.trace("credit_agent.assess_application") as trace:
        with trace.observation("credit_agent", as_type="agent") as agent:
            with agent.span("load_application") as load:
                with load.generation("extract_fields", model="haiku") as gen:
                    gen.update(output={"ok": True})

    by_name = {s.kw.get("name"): s for s in client.spans if s.kw.get("name")}
    assert by_name["credit_agent"].parent is client.spans[0]
    assert by_name["load_application"].parent is by_name["credit_agent"]
    assert by_name["extract_fields"].parent is by_name["load_application"]
    assert by_name["extract_fields"].updates == [{"output": {"ok": True}}]


def test_scoring_an_observation_names_it_as_the_subject(emitter, client):
    """A trace-level score and an observation-level score are different subjects, and the
    demo's story reads both — EV scores the decision generation, not the whole trace."""
    with emitter.trace("t") as trace:
        with trace.generation("decision", model="sonnet") as decision:
            decision.score("answer_quality", 0.9, data_type="NUMERIC")
        trace.score("user_disagreement", 1, data_type="BOOLEAN")

    obs_score, trace_score = client.scores
    assert obs_score["name"] == "answer_quality"
    assert obs_score["observation_id"] and obs_score["trace_id"]
    assert trace_score["name"] == "user_disagreement"
    assert "observation_id" not in trace_score
