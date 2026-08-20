"""The read seam — the one place any kit reads Langfuse (portal #208, v4-only since #213).

These pin the seam at its own boundary: a faked HTTP transport answers the shapes the v4
read APIs really return (captured from a live Cloud project and from the Langfuse SDK's
generated client, which is the wire contract), and the assertions are about the *normalised*
rows the seam hands a kit.

The seam carried a second arm through the migration, reading the deprecated endpoints and
probing for which generation to use. #213 removed it, and the tests for it went with it —
what is left is the v4 half plus a guard that the module names no deprecated endpoint at
all.
"""

from __future__ import annotations

import re

import pytest

from langfuse_synth_core import read


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def _transport(routes: dict, *, calls: list | None = None):
    """A fake ``request_retry`` that answers ``routes`` keyed by request path.

    A route value is either a payload dict (200) or a ``(status, payload)`` pair; an
    unrouted path answers 404, which is exactly how a cut-over target answers a
    deprecated endpoint.
    """

    def fake(method, url, *, params=None, auth=None, timeout=30, throttle_s=0.0, **kw):
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path
        if calls is not None:
            calls.append((path, dict(params or {})))
        route = routes.get(path)
        if route is None:
            return _Resp(404, {"message": "not found"})
        if callable(route):
            route = route(dict(params or {}))
        if isinstance(route, tuple):
            return _Resp(route[0], route[1])
        return _Resp(200, route)

    return fake


V4_ONLY = {  # a cut-over target: every deprecated path 404s, so only these answer
    "/api/public/v3/scores": {"data": [], "meta": {"limit": 50}},
}


def test_v4_scores_normalise_the_single_typed_value_and_subject(monkeypatch):
    routes = dict(V4_ONLY)
    routes["/api/public/v3/scores"] = {
        "data": [
            {"id": "s1", "name": "resolution", "dataType": "CATEGORICAL", "value": "escalated",
             "timestamp": "2026-06-04T12:00:00.000Z", "source": "API", "environment": "production",
             "comment": "hand-off", "subject": {"kind": "trace", "id": "t1"}},
            {"id": "s2", "name": "resolution", "dataType": "NUMERIC", "value": 0.75,
             "timestamp": "2026-06-05T12:00:00.000Z", "source": "API", "environment": "production",
             "subject": {"kind": "observation", "id": "o1", "traceId": "t2"}},
        ],
        "meta": {"limit": 50},
    }
    monkeypatch.setattr(read, "request_retry", _transport(routes))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    rows = reader.scores(name="resolution")

    assert [r.id for r in rows] == ["s1", "s2"]
    categorical, numeric = rows
    # A categorical score's value is its label, and it never poses as a number.
    assert categorical.string_value == "escalated"
    assert categorical.numeric_value is None
    assert categorical.trace_id == "t1"
    assert categorical.comment == "hand-off"
    # A numeric score's value is a float, and an observation-scoped score still names its trace.
    assert numeric.numeric_value == pytest.approx(0.75)
    assert numeric.string_value is None
    assert numeric.observation_id == "o1"
    assert numeric.trace_id == "t2"


# Shaped exactly as the SDK's generated client documents ObservationV2, with the two
# corrections a live Cloud project forced (see the fixtures at the foot of this file).
V4_GENERATION_ROW = {
    "id": "o1", "traceId": "t1", "type": "GENERATION", "name": "answer",
    "startTime": "2026-06-04T12:00:00.000Z", "endTime": "2026-06-04T12:00:03.000Z",
    "parentObservationId": "root1", "input": [{"role": "system", "content": "you are"}],
    "output": {"text": "ok"}, "providedModelName": "claude-sonnet-4",
    "usageDetails": {"input": 100, "output": 20}, "costDetails": {"total": 0.004},
    "totalCost": 0.004, "promptName": "analyst-copilot", "promptVersion": 3,
    "userId": "u-1", "sessionId": "sess-1", "tags": ["golden"], "traceName": "answer_question",
    "environment": "production",
}


def test_v4_observations_normalise_the_model_usage_cost_and_prompt_columns(monkeypatch):
    routes = dict(V4_ONLY)
    routes["/api/public/v2/observations"] = {"data": [V4_GENERATION_ROW], "meta": {}}
    calls: list = []
    monkeypatch.setattr(read, "request_retry", _transport(routes, calls=calls))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    obs = reader.observations(trace_id="t1", type="GENERATION")

    assert len(obs) == 1
    o = obs[0]
    assert o.model == "claude-sonnet-4"          # v4 calls this providedModelName
    assert o.total_cost == 0.004                  # v4 calls this totalCost
    assert o.usage_details == {"input": 100, "output": 20}
    assert (o.prompt_name, o.prompt_version) == ("analyst-copilot", 3)
    assert o.session_id == "sess-1" and o.tags == ["golden"]
    assert not o.is_root                          # it has a parent span
    # The io / model / usage / prompt columns are opt-in field groups on the v4 API; asking
    # for observations without asking for them returns a row with none of them populated.
    _path, params = calls[-1]
    assert "io" in params["fields"] and "usage" in params["fields"] and "prompt" in params["fields"]


V4_ROOT_ROW = {
    "id": "root1", "traceId": "t1", "type": "SPAN", "name": "answer_question",
    "startTime": "2026-06-04T11:59:59.000Z", "endTime": "2026-06-04T12:00:04.000Z",
    "parentObservationId": None, "input": {"question": "why?"}, "output": {"text": "ok"},
    "userId": "u-1", "sessionId": "sess-1", "tags": ["golden"], "traceName": "answer_question",
    "environment": "production",
}


def test_v4_assembles_a_trace_out_of_the_observations_that_share_its_id(monkeypatch):
    routes = dict(V4_ONLY)
    routes["/api/public/v2/observations"] = {"data": [V4_ROOT_ROW, V4_GENERATION_ROW],
                                             "meta": {}}
    routes["/api/public/v3/scores"] = {
        "data": [{"id": "s1", "name": "answer_quality", "dataType": "NUMERIC", "value": 0.9,
                  "timestamp": "2026-06-04T12:00:00.000Z",
                  "subject": {"kind": "trace", "id": "t1"}}],
        "meta": {"limit": 100}}
    monkeypatch.setattr(read, "request_retry", _transport(routes))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    trace = reader.trace("t1")

    # Under v4 there is no trace entity: name, user, session and tags are attributes copied
    # onto the spans, and the overall input/output live on the root observation.
    assert trace.id == "t1"
    assert trace.name == "answer_question"
    assert (trace.user_id, trace.session_id, trace.tags) == ("u-1", "sess-1", ["golden"])
    assert trace.input == {"question": "why?"} and trace.output == {"text": "ok"}
    assert trace.timestamp == read.parse_ts("2026-06-04T11:59:59.000Z")
    assert [o.name for o in trace.observations] == ["answer_question", "answer"]
    assert trace.root.id == "root1"
    # Scores hang off a trace on either generation, so an assertion about them is portable.
    assert [s.name for s in trace.scores] == ["answer_quality"]


def test_an_absent_trace_reads_as_none(monkeypatch):
    routes = dict(V4_ONLY)
    routes["/api/public/v2/observations"] = {"data": [], "meta": {}}
    monkeypatch.setattr(read, "request_retry", _transport(routes))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    # "Does this trace exist?" is an assertion three kits make; absence is an answer here,
    # not an exception to be caught at each call site.
    assert reader.trace("nope") is None


def test_v4_derives_the_trace_list_from_observations_and_bounds_it(monkeypatch):
    rows = []
    for i in range(3):
        rows.append({**V4_ROOT_ROW, "id": f"root{i}", "traceId": f"t{i}",
                     "sessionId": f"sess-{i % 2}"})
        rows.append({**V4_GENERATION_ROW, "id": f"gen{i}", "traceId": f"t{i}",
                     "parentObservationId": f"root{i}", "sessionId": f"sess-{i % 2}"})
    routes = dict(V4_ONLY)
    routes["/api/public/v2/observations"] = {"data": rows, "meta": {}}
    monkeypatch.setattr(read, "request_retry", _transport(routes))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    traces = reader.traces(limit=3)

    # There is no trace row to list under v4, so the seam groups observations by trace id.
    assert [t.id for t in traces] == ["t0", "t1", "t2"]
    assert {t.session_id for t in traces} == {"sess-0", "sess-1"}
    assert len(traces[0].observations) == 2


def test_a_session_reads_as_its_trace_ids(monkeypatch):
    routes = dict(V4_ONLY)
    routes["/api/public/v2/observations"] = {
        "data": [{**V4_ROOT_ROW, "id": "root0", "traceId": "t0", "sessionId": "sess-1"},
                 {**V4_ROOT_ROW, "id": "root1", "traceId": "t1", "sessionId": "sess-1"}],
        "meta": {}}
    monkeypatch.setattr(read, "request_retry", _transport(routes))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    session = reader.session("sess-1")

    assert session.id == "sess-1"
    assert sorted(session.trace_ids) == ["t0", "t1"]


def test_v4_reads_dataset_runs_through_the_experiments_api(monkeypatch):
    routes = dict(V4_ONLY)
    routes["/api/public/datasets/certification-suite"] = {"id": "ds-1",
                                                          "name": "certification-suite"}
    routes["/api/public/experiments"] = {
        "data": [{"id": "exp-1", "name": "candidate-a - 2026-06-04", "datasetId": "ds-1",
                  "itemCount": 72, "startTime": "2026-06-04T10:00:00.000Z",
                  "endTime": "2026-06-04T10:05:00.000Z"}],
        "meta": {}}
    routes["/api/public/experiment-items"] = {
        "data": [{"id": "obs-1", "traceId": "t-1", "experimentId": "exp-1",
                  "experimentName": "candidate-a - 2026-06-04", "experimentItemId": "ri-1",
                  "startTime": "2026-06-04T10:00:01.000Z", "level": "DEFAULT",
                  "environment": "production"}],
        "meta": {}}
    calls: list = []
    monkeypatch.setattr(read, "request_retry", _transport(routes, calls=calls))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    runs = reader.experiments(dataset_name="certification-suite")

    assert [r.name for r in runs] == ["candidate-a - 2026-06-04"]
    assert runs[0].item_count == 72 and runs[0].dataset_id == "ds-1"
    # The Experiments API refuses a request with no start-time bound, so the seam always
    # sends one rather than letting a caller discover the 400.
    _path, params = next((c for c in calls if c[0] == "/api/public/experiments"), (None, {}))
    assert params.get("fromStartTime")

    items = reader.experiment_items(runs[0])
    assert [i.trace_id for i in items] == ["t-1"]
    assert items[0].id == "ri-1"


def test_run_level_scores_read_by_experiment(monkeypatch):
    calls: list = []
    routes = dict(V4_ONLY)
    routes["/api/public/v3/scores"] = {
        "data": [{"id": "s1", "name": "rate_numeric_accuracy", "dataType": "NUMERIC",
                  "value": 0.62, "timestamp": "2026-06-04T10:05:00.000Z",
                  "subject": {"kind": "experiment", "id": "exp-1"}}],
        "meta": {"limit": 100}}
    monkeypatch.setattr(read, "request_retry", _transport(routes, calls=calls))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    rows = reader.scores(experiment_id="exp-1")

    assert rows[0].experiment_id == "exp-1"
    assert rows[0].numeric_value == pytest.approx(0.62)
    # v3 renamed the filter as well as the field: datasetRunId became experimentId.
    scores_call = next(c for c in calls if "scores" in c[0])
    assert "experimentId" in scores_call[1]


# --- shapes captured from a REAL Langfuse Cloud project, 2026-08-19 -------------------
# Three of these differ from what the SDK's generated types imply, and each one would have
# broken a kit's `verify` on a cut-over target. They are pinned here as fixtures.

def test_v4_io_arrives_as_json_strings_and_is_parsed_back(monkeypatch):
    """`parseIoAsJson=true` is REJECTED by the v2 observations endpoint ("no longer
    supported … always returned as raw strings"), so v4 hands back `input`/`output` as JSON
    text where legacy hands back objects. Every kit assertion that indexes into a chat-shaped
    input depends on the seam closing that gap."""
    routes = dict(V4_ONLY)
    routes["/api/public/v2/observations"] = {"data": [{
        **V4_GENERATION_ROW,
        "input": '[{"role": "system", "content": "you are"}]',
        "output": "the seam holds",          # not JSON — stays the string it is
    }], "meta": {}}
    monkeypatch.setattr(read, "request_retry", _transport(routes))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    o = reader.observations(trace_id="t1")[0]

    assert o.input == [{"role": "system", "content": "you are"}]
    assert o.output == "the seam holds"


def test_v4_names_the_model_column_model(monkeypatch):
    """The live v2 observations row carries `model` (with `providedModelName` documented but
    absent). The seam reads whichever is present rather than the one the docs promised."""
    row = {k: v for k, v in V4_GENERATION_ROW.items() if k != "providedModelName"}
    routes = dict(V4_ONLY)
    routes["/api/public/v2/observations"] = {"data": [{**row, "model": "claude-sonnet-4"}],
                                             "meta": {}}
    monkeypatch.setattr(read, "request_retry", _transport(routes))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    assert reader.observations(trace_id="t1")[0].model == "claude-sonnet-4"


def test_unset_string_columns_read_as_absent_not_as_empty_strings(monkeypatch):
    """v4 returns `""` for an unset prompt link, version or status. A kit asking "is this
    generation prompt-linked?" must get a falsy *absence*, not an empty string that reads as
    a name."""
    routes = dict(V4_ONLY)
    routes["/api/public/v2/observations"] = {"data": [{
        **V4_GENERATION_ROW, "promptName": "", "promptVersion": None, "traceName": "",
    }], "meta": {}}
    monkeypatch.setattr(read, "request_retry", _transport(routes))
    reader = read.LangfuseReader("http://lf", auth=("pk", "sk"))

    o = reader.observations(trace_id="t1")[0]
    assert o.prompt_name is None and o.prompt_version is None
    assert o.trace_name is None




def test_the_seam_names_no_deprecated_endpoint(monkeypatch):
    """The contract half of the migration, asserted on the module rather than on a call
    (portal #213). The seam is the one place a kit reaches Langfuse, so "no legacy endpoint
    remains" is provable here: if a deprecated path is not written down in this file, no kit
    can reach one through the seam.

    `/api/public/datasets/{name}` is the exception, and it is on no deprecation list — it is
    the stable bridge from a kit's configured dataset name to the id the Experiments API
    filters by.
    """
    import inspect

    source = inspect.getsource(read)
    paths = set(re.findall(r'"(/api/public/[^"{]*)', source))
    paths |= set(re.findall(r'f"(/api/public/[^"]*)', source))
    for path in paths:
        assert not re.match(
            r"/api/public/(traces|observations|sessions|spans|generations|events|metrics"
            r"|scores|v2/scores|ingestion)\b", path), path
