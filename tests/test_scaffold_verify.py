"""Generated seed/verify contracts, with Langfuse replaced at its HTTP boundary."""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from langfuse_synth_core.authoring.scaffold import scaffold_kit


@pytest.fixture(scope="module", params=[False, True], ids=["plain", "companion-anchors"])
def kit(tmp_path_factory, request):
    return scaffold_kit("verify-demo", tmp_path_factory.mktemp("verify-kit"),
                        with_companion=request.param, with_anchors=request.param)


@pytest.fixture
def runtime(kit, monkeypatch, tmp_path):
    monkeypatch.chdir(kit.dest)
    monkeypatch.syspath_prepend(str(kit.dest / "src"))
    for name in list(sys.modules):
        if name == "synth" or name.startswith("synth."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setenv("SYNTH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SYNTH_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-demo")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-demo")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.test")
    config = importlib.import_module("synth.config")
    cfg = config.load_config(kit.dest / "config/demo.yaml", overrides=[
        "generation.target_traces=8", "generation.as_of_date=2026-09-20",
    ])
    return SimpleNamespace(seed=importlib.import_module("synth.seed").run_seed,
                           verify=importlib.import_module("synth.verify").run_verify,
                           cfg=cfg, root=tmp_path, kit=kit)


def test_missing_receipt_fails_without_querying_unrelated_project(runtime, monkeypatch):
    import requests

    def unexpected(*args, **kwargs):
        pytest.fail("missing local receipt must fail before any network read")

    monkeypatch.setattr(requests, "request", unexpected)
    report = runtime.verify(runtime.cfg, log=lambda _: None)
    assert not report.ok
    assert "receipt" in report.checks[0].name
    assert "seed" in report.checks[0].detail


class FakeLangfuse:
    """Accept real ingestion requests and answer v4 read shapes from their payloads."""
    def __init__(self):
        self.observations = []
        self.scores = []
        self.visible = True
        self.trace_queries = []

    def request(self, method, url, **kwargs):
        import json
        from datetime import datetime, timezone

        import requests

        params = kwargs.get("params", {})
        body = kwargs.get("json", {})
        if method == "POST":
            if url.endswith("/ingestion"):
                for event in body["batch"]:
                    score = event["body"]
                    self.scores.append({**score, "subject": {
                        "kind": "trace", "id": score["traceId"],
                    }})
            else:
                for resource in body["resourceSpans"]:
                    for scope in resource["scopeSpans"]:
                        for span in scope["spans"]:
                            self.observations.append({
                                "id": span["spanId"], "traceId": span["traceId"],
                                "parentObservationId": span.get("parentSpanId"),
                                "startTime": datetime.fromtimestamp(
                                    int(span["startTimeUnixNano"]) / 1e9, timezone.utc,
                                ).isoformat(),
                            })
            payload = {}
        elif url.endswith("/projects"):
            payload = {"data": [{"id": "project-demo", "name": "demo"}]}
        else:
            trace_id = params.get("traceId")
            rows = self.scores if url.endswith("/scores") else self.observations
            if not url.endswith("/scores"):
                self.trace_queries.append(trace_id)
            payload = {"data": [r for r in rows if not trace_id or (
                r.get("traceId") == trace_id
            )] if self.visible else []}
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(payload).encode()
        return response


@pytest.fixture
def api(monkeypatch):
    import requests

    fake = FakeLangfuse()
    monkeypatch.setattr(requests, "request", fake.request)
    monkeypatch.setattr(requests, "post", lambda url, **kw: fake.request("POST", url, **kw))
    return fake


def seed(runtime):
    return runtime.seed(runtime.cfg, spool_path=runtime.root / "events.ndjson", log=lambda _: None)


def test_success_reads_bounded_exact_records_and_preserves_optional_anchors(runtime, api):
    import json

    seed(runtime)
    report = runtime.verify(runtime.cfg, log=lambda _: None)
    assert report.ok
    assert len(api.trace_queries) == 5
    assert all(api.trace_queries), "verification must never query arbitrary project traces"
    receipt = json.loads((runtime.root / "state/.synth_verify.json").read_text())
    assert len(receipt["examples"]) == 5
    if (runtime.kit.dest / "src/synth/state.py").exists():
        anchors = json.loads((runtime.root / "state/.synth_state.json").read_text())
        assert anchors["target_traces"] == 8
        assert "examples" not in anchors


class Clock:
    def __init__(self, after_sleep=lambda: None):
        self.now = 0.0
        self.sleeps = []
        self.after_sleep = after_sleep

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        self.after_sleep()


def test_delayed_visibility_is_polled_until_expected_data_appears(runtime, api):
    seed(runtime)
    api.visible = False
    clock = Clock(lambda: setattr(api, "visible", True))
    report = runtime.verify(runtime.cfg, timeout_seconds=3, poll_interval=2,
                            clock=clock, sleep=clock.sleep, log=lambda _: None)
    assert report.ok
    assert clock.sleeps == [2]


def test_unrelated_data_times_out_with_specific_missing_id_diagnostics(runtime, api):
    seed(runtime)
    api.observations = [{"id": "old", "traceId": "old-trace"}]
    api.scores = [{"id": "old-score", "name": "quality", "traceId": "old-trace"}]
    clock = Clock()
    report = runtime.verify(runtime.cfg, timeout_seconds=3, poll_interval=2,
                            clock=clock, sleep=clock.sleep, log=lambda _: None)
    assert not report.ok
    assert clock.sleeps == [2, 1]
    assert "timeout" in " ".join(check.detail for check in report.checks).lower()
    assert all(not check.ok for check in report.checks)
    assert "trace" in report.checks[0].detail
    assert "score" in report.checks[1].detail


@pytest.mark.parametrize("mismatch", ["score-id", "relationship", "value", "old-run"])
def test_matching_project_data_must_have_expected_id_relationship_value_and_run(
    runtime, api, mismatch,
):
    seed(runtime)
    if mismatch == "score-id":
        api.scores[0]["id"] = "unrelated-id"
    elif mismatch == "relationship":
        api.scores[0]["subject"]["id"] = "unrelated-trace"
    elif mismatch == "value":
        api.scores[0]["value"] = -1
    else:
        api.observations[0]["startTime"] = "2020-01-01T00:00:00Z"
    report = runtime.verify(runtime.cfg, timeout_seconds=0, log=lambda _: None)
    assert not report.ok
    assert "mismatch" in " ".join(check.detail for check in report.checks)


@pytest.mark.parametrize("mode", ["dry-run", "spool-only", "failed-seed"])
def test_new_attempt_cannot_reuse_a_previous_successful_receipt(runtime, api, mode):
    from langfuse_synth_core.seed.ingest import IngestError

    seed(runtime)
    if mode == "failed-seed":
        runtime.cfg.target.project_hint = "nonexistent"
        with pytest.raises(IngestError):
            seed(runtime)
    else:
        runtime.seed(runtime.cfg, dry_run=mode == "dry-run", do_import=False,
                     spool_path=runtime.root / "new-events.ndjson", log=lambda _: None)
    report = runtime.verify(runtime.cfg, log=lambda _: None)
    assert not report.ok
    assert report.checks[0].name == "run_receipt"


@pytest.mark.parametrize("change", ["generation", "project", "empty-id", "broken-json"])
def test_wrong_configuration_or_malformed_receipt_fails_before_network_reads(
    runtime, api, monkeypatch, change,
):
    import json

    seed(runtime)
    if change == "generation":
        runtime.cfg.generation.seed += 1
    elif change == "project":
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-other-project")
    else:
        path = runtime.root / "state/.synth_verify.json"
        receipt = json.loads(path.read_text())
        receipt["examples"][0]["trace_id"] = None
        path.write_text("invalid json" if change == "broken-json" else json.dumps(receipt))
    api.trace_queries.clear()
    report = runtime.verify(runtime.cfg, timeout_seconds=0, log=lambda _: None)
    assert not report.ok
    assert report.checks[0].name == "run_receipt"
    assert api.trace_queries == []
