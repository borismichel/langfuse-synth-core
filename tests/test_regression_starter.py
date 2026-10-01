"""The opt-in starter ships a usable story through the public scaffolding seam."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest
import yaml

from langfuse_synth_core.authoring.cli import main


def test_cli_selects_complete_starter_without_changing_basic_default(tmp_path):
    assert main(["new", "refund-demo", "--dir", str(tmp_path),
                 "--starter", "regression-recovery"]) == 0
    kit = tmp_path / "refund-demo"
    manifest = yaml.safe_load((kit / "usecase.yaml").read_text())
    knob = manifest["config_schema"]["properties"]["generation.target_traces"]
    assert knob["minimum"] == 3
    assert knob["default"] == 24
    assert "refund" in manifest["story"].lower()
    assert "live_components" not in manifest
    assert (kit / "tests/test_story.py").is_file()
    assert main(["new", "basic-demo", "--dir", str(tmp_path)]) == 0
    basic = yaml.safe_load((tmp_path / "basic-demo/usecase.yaml").read_text())
    assert basic["config_schema"]["properties"]["generation.target_traces"]["default"] == 1000


@pytest.fixture
def starter(tmp_path, monkeypatch):
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    kit = scaffold_kit("refund-demo", tmp_path / "kit", starter="regression-recovery")
    monkeypatch.chdir(kit.dest)
    monkeypatch.syspath_prepend(str(kit.dest / "src"))
    for name in list(sys.modules):
        if name == "synth" or name.startswith("synth."):
            monkeypatch.delitem(sys.modules, name)
    return kit


@pytest.mark.parametrize("count", [3, 4, 24, 101])
def test_starter_preserves_linked_story_at_supported_volumes(starter, count):
    build = importlib.import_module("synth.materialize").build_events
    events = build(count, {"seed": 42}, run_date=datetime(2026, 10, 1, tzinfo=timezone.utc))
    roots = [e for e in events if e.get("name", "").startswith("refund-")]
    assert len(roots) == count
    assert [r["name"] for r in roots[:3]] == [
        "refund-baseline", "refund-failure", "refund-recovery",
    ]
    attrs = [{a["key"]: a["value"].get("stringValue") for a in r["attributes"]}
             for r in roots[:3]]
    assert len({a["langfuse.session.id"] for a in attrs}) == 1
    assert attrs[1]["langfuse.trace.metadata.baseline_trace_id"] == roots[0]["traceId"]
    assert attrs[2]["langfuse.trace.metadata.previous_trace_id"] == roots[1]["traceId"]
    assert "30 days" in attrs[0]["langfuse.observation.output"]
    assert "14 days" in attrs[1]["langfuse.observation.output"]
    assert "30 days" in attrs[2]["langfuse.observation.output"]
    scores = [e["body"]["value"] for e in events if e.get("type") == "score-create"]
    assert scores[:3] == [1.0, 0.0, 1.0]
    assert [int(r["startTimeUnixNano"]) for r in roots[:3]] == sorted(
        int(r["startTimeUnixNano"]) for r in roots[:3]
    )
    minimum = build(3, {"seed": 42}, run_date=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert events[:len(minimum)] == minimum


def test_starter_refuses_volumes_that_drop_story_beats(starter):
    build = importlib.import_module("synth.materialize").build_events
    with pytest.raises(ValueError, match="at least 3"):
        build(2, {"seed": 42}, run_date=datetime(2026, 10, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize("damage", [None, "link", "output", "policy", "duration", "score"])
def test_seeded_story_verify_checks_current_scenario_relationships(starter, monkeypatch, damage):
    import requests
    from test_scaffold_verify import FakeLangfuse

    class StoryAPI(FakeLangfuse):
        def request(self, method, url, **kwargs):
            response = super().request(method, url, **kwargs)
            body = kwargs.get("json", {})
            for resource in body.get("resourceSpans", []):
                for scope in resource["scopeSpans"]:
                    for span in scope["spans"]:
                        attrs = {a["key"]: a["value"].get("stringValue")
                                 for a in span["attributes"]}
                        row = next(r for r in self.observations if r["id"] == span["spanId"])
                        def decoded(key):
                            value = attrs.get(key)
                            try:
                                return json.loads(value)
                            except (TypeError, ValueError):
                                return value
                        row.update(
                            name=span["name"], type=attrs["langfuse.observation.type"].upper(),
                            traceName=attrs.get("langfuse.trace.name"),
                            sessionId=attrs.get("langfuse.session.id"),
                            input=decoded("langfuse.observation.input"),
                            output=decoded("langfuse.observation.output"),
                            metadata={k.rsplit(".", 1)[-1]: v for k, v in attrs.items()
                                      if ".metadata." in k},
                            endTime=datetime.fromtimestamp(
                                int(span["endTimeUnixNano"]) / 1e9, timezone.utc,
                            ).isoformat(),
                        )
            return response

    api = StoryAPI()
    monkeypatch.setattr(requests, "request", api.request)
    monkeypatch.setattr(requests, "post", lambda url, **kw: api.request("POST", url, **kw))
    monkeypatch.setenv("SYNTH_STATE_DIR", str(starter.dest / "state"))
    monkeypatch.setenv("SYNTH_OUT_DIR", str(starter.dest / "out"))
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-demo")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-demo")
    cfg = importlib.import_module("synth.config").load_config(
        starter.dest / "config/demo.yaml", overrides=["generation.target_traces=3",
                                                     "generation.as_of_date=2026-10-01"],
    )
    importlib.import_module("synth.seed").run_seed(cfg, log=lambda _: None)
    assert (starter.dest / "out/DEMO_SCRIPT.md").is_file()
    if damage == "score":
        api.scores[1]["value"] = 1.0
    for row in api.observations:
        if row["name"] == "refund-recovery":
            if damage == "link":
                row["metadata"]["previous_trace_id"] = "unrelated"
            elif damage == "output":
                row["output"] = "No refund."
        if row["name"] == "retrieve-refund-policy":
            if damage == "policy":
                row["output"] = {"document": "unrelated policy"}
            elif damage == "duration":
                row["endTime"] = row["startTime"]
    report = importlib.import_module("synth.verify").run_verify(
        cfg, timeout_seconds=0, log=lambda _: None,
    )
    assert report.ok is (damage is None), [(c.name, c.detail) for c in report.checks]


@pytest.mark.parametrize("companion", [False, True])
def test_emitted_starter_passes_own_gates_and_delivers_three_beat_runbook(tmp_path, companion):
    from pathlib import Path
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    kit = scaffold_kit("refund-demo", tmp_path / "kit", starter="regression-recovery",
                       with_companion=companion, with_anchors=companion)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([
        str(kit.dest / "src"), str(Path(__file__).resolve().parents[1] / "src"),
    ]), "SYNTH_OUT_DIR": str(kit.dest / "out"),
        "SYNTH_STATE_DIR": str(kit.dest / "state")}
    result = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=kit.dest,
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run([
        sys.executable, "-m", "synth.cli", "seed", "--config", "config/demo.yaml",
        "--dry-run", "--set", "generation.target_traces=3",
    ], cwd=kit.dest, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    runbook = (kit.dest / "out/DEMO_SCRIPT.md").read_text()
    assert all(beat in runbook for beat in ["Beat 1", "Beat 2", "Beat 3"])
    assert "seeded fixture" in runbook
    assert "not deploy a fix" in runbook
