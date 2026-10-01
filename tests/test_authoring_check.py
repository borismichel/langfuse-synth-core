"""Author checks through the CLI, including calls outside the selected kit."""
import importlib.util
import json

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None, reason="requires authoring extra"
)


def _kit(tmp_path):
    from langfuse_synth_core.authoring.scaffold import scaffold_kit
    kit = tmp_path / "demo-kit"
    scaffold_kit("demo-kit", kit)
    return kit


def test_complete_check_from_outside_kit(tmp_path, monkeypatch, capsys):
    from langfuse_synth_core.authoring.cli import main
    kit = _kit(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["check", str(kit), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 1
    assert report["local_ready"] is True
    assert report["live_verification"] == report["admission"] == "not_run"
    assert [s["status"] for s in report["stages"]] == ["passed"] * 3


def test_invalid_manifest_reports_skipped_conformance_but_runs_tests(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main
    kit = _kit(tmp_path)
    (kit / "usecase.yaml").write_text("invalid: true\n")
    assert main(["check", str(kit), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    stages = {s["name"]: s for s in report["stages"]}
    assert stages["validate"]["status"] == "failed"
    assert stages["conformance"]["status"] == "skipped"
    assert stages["tests"]["status"] == "failed"
    assert "test_manifest" in stages["tests"]["output"]
    assert all(s["next_action"] for s in report["stages"][:2])


def test_tests_cannot_reach_network_or_inherit_credentials(tmp_path, monkeypatch, capsys):
    from langfuse_synth_core.authoring.cli import main
    kit = _kit(tmp_path)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "inherited-secret-sentinel")
    (kit / "tests" / "test_network.py").write_text(
        "import os, socket\n"
        "def test_offline():\n"
        "    assert 'LANGFUSE_SECRET_KEY' not in os.environ\n"
        "    socket.create_connection(('127.0.0.1', 54321))\n"
    )
    assert main(["check", str(kit), "--json"]) == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["stages"][-1]["status"] == "failed"
    assert "offline authoring check" in output
    assert "inherited-secret-sentinel" not in output


def test_missing_interpreter_is_unavailable(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main
    kit = _kit(tmp_path)
    assert main(["check", str(kit), "--python", str(tmp_path / "missing"), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stages"][0]["status"] == "unavailable"
    assert report["stages"][-1]["status"] == "unavailable"


def test_timeout_has_actionable_report(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main
    kit = _kit(tmp_path)
    (kit / "tests" / "test_slow.py").write_text(
        "import time\ndef test_slow():\n    time.sleep(10)\n"
    )
    assert main(["check", str(kit), "--timeout", "1", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stages"][-1]["status"] == "failed"
    assert "timeout" in report["stages"][-1]["next_action"].lower()


def test_skipped_checks_are_not_reported_ready(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main
    kit = _kit(tmp_path)
    (kit / "tests" / "test_skip.py").write_text(
        'import pytest\n@pytest.mark.skip(reason="missing fixture")\n'
        'def test_pending():\n    pass\n'
    )
    assert main(["check", str(kit), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["stages"][-1]["status"] == "skipped"
    assert not report["local_ready"]
