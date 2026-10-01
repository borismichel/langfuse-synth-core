"""Benchmark records and summaries through the installed authoring CLI boundary."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None, reason="requires authoring extra"
)


def test_start_a_fixed_brief_with_honest_pending_measurements(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    output = tmp_path / "run.json"
    assert main([
        "benchmark", "init", "new-spool", "--source", str(Path(__file__).parents[1]),
        "--out", str(output), "--agent", "codex", "--model", "test-model",
        "--settings", '{"reasoning": "high"}',
    ]) == 0
    record = json.loads(output.read_text())
    assert record["mode"] == "authoring"
    assert record["status"] == "pending"
    assert record["first_walkthrough"]["seconds"] is None
    assert record["human_interventions"] is None
    assert record["admission"]["status"] == record["rehearsal"]["status"] == "pending"
    assert main(["benchmark", "validate", str(output)]) == 0
    assert main(["benchmark", "brief", "new-spool"]) == 0
    assert "Presenter Runbook" in capsys.readouterr().out


def _start(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    path = tmp_path / "pending.json"
    assert main([
        "benchmark", "init", "new-spool", "--source", str(Path(__file__).parents[1]),
        "--out", str(path), "--agent", "codex", "--model", "test-model", "--settings", "{}",
    ]) == 0
    capsys.readouterr()
    return path, json.loads(path.read_text())


def test_summary_compares_revisions_but_separates_environments(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    path, record = _start(tmp_path, capsys)
    record.update(status="completed", source_dirty=False, human_interventions=2,
                  intervention_evidence=["session.txt"])
    record["first_walkthrough"] = {"status": "passed", "seconds": 120, "evidence": ["walk.mp4"]}
    record["admission"] = {"status": "passed", "evidence": ["admission.json"]}
    record["rehearsal"] = {"status": "failed", "evidence": ["rehearsal.txt"]}
    paths = []
    for index in range(3):
        record["run_id"] = f"run-{index}"
        record["source_revision"] = str(index) * 40
        if index == 2:
            record["environment"]["machine"] = "different-hardware"
        target = tmp_path / f"complete-{index}.json"
        target.write_text(json.dumps(record))
        paths.append(str(target))
    assert main(["benchmark", "summary", str(path), *paths]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["excluded"] == {"dirty": 1} or summary["excluded"] == {"pending": 1}
    assert len(summary["groups"]) == 2
    assert len(summary["groups"][0]["revisions"]) == 2
    metrics = summary["groups"][0]["revisions"]["0" * 40]
    assert metrics["walkthrough_seconds_mean"] == 120
    assert metrics["rehearsal_passed"] == 0
    assert main(["benchmark", "summary", paths[0], paths[0]]) == 2


@pytest.mark.parametrize("mutation", ["pending-complete", "evidence", "nan", "offline-live"])
def test_invalid_measurements_fail_validation(tmp_path, capsys, mutation):
    from langfuse_synth_core.authoring.cli import main

    path, record = _start(tmp_path, capsys)
    if mutation == "pending-complete":
        record["status"] = "completed"
    elif mutation == "evidence":
        record["admission"]["status"] = "passed"
    elif mutation == "nan":
        record["first_walkthrough"] = {
            "status": "passed", "seconds": float("nan"), "evidence": ["x"],
        }
    else:
        record.update(mode="offline", agent=None)
        record["rehearsal"] = {"status": "passed", "evidence": ["x"]}
    path.write_text(json.dumps(record))
    assert main(["benchmark", "validate", str(path)]) == 2


def test_offline_run_records_actual_checks_without_claiming_a_walkthrough(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    path = tmp_path / "offline.json"
    assert main([
        "benchmark", "offline", "--source", str(Path(__file__).parents[1]), "--out", str(path),
    ]) == 0
    record = json.loads(path.read_text())
    assert record["status"] == "completed"
    assert record["agent"] is None
    assert record["first_walkthrough"]["seconds"] is None
    assert record["admission"]["status"] == record["rehearsal"]["status"] == "pending"
    assert {c["name"] for c in record["offline_checks"]} == {
        "scaffold", "validate", "conformance", "kit-tests",
    }
    for check in record["offline_checks"]:
        assert check["seconds"] > 0
        assert check["exit_code"] == 0
        assert (path.parent / check["evidence"][0]).is_file()
    assert main(["benchmark", "validate", str(path)]) == 0
    assert main([
        "benchmark", "offline", "--source", str(Path(__file__).parents[1]), "--out", str(path),
    ]) == 2  # existing evidence must survive accidental reruns
