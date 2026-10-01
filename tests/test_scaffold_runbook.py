"""A generated seed delivers its declared Presenter Runbook (portal #262)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="the scaffolder requires the authoring extra",
)


def _seed(kit: Path, output: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(kit / "src"), *sys.path]),
        "SYNTH_OUT_DIR": str(output),
    }
    return subprocess.run(
        [sys.executable, "-m", "synth.cli", "seed", "--config", "config/demo.yaml",
         "--dry-run", "--set", "generation.target_traces=2"],
        cwd=kit, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("companion", [False, True])
def test_generated_seed_delivers_declared_runbook(tmp_path, companion):
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    kit = tmp_path / "demo"
    scaffold_kit("runbook-demo", kit, with_companion=companion)
    expected = "# Presenter Runbook\n\n1. Find the failed retrieval.\n2. Compare the repair.\n"
    (kit / "DEMO_SCRIPT.md").write_text(expected)
    output = tmp_path / "artifacts"
    result = _seed(kit, output)

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = yaml.safe_load((kit / "usecase.yaml").read_text())
    runbook = next(a for a in manifest["artifacts"] if a["render"] == "markdown")
    assert (output / runbook["path"]).read_text() == expected


@pytest.mark.parametrize("failure", ["missing-source", "unwritable-output"])
def test_seed_fails_actionably_before_spooling_when_runbook_cannot_be_delivered(tmp_path, failure):
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    kit = tmp_path / "demo"
    scaffold_kit("runbook-demo", kit)
    output = tmp_path / "artifacts"
    if failure == "missing-source":
        (kit / "DEMO_SCRIPT.md").unlink()
    else:
        # A file cannot serve as an output directory, regardless of the test user's uid.
        output.write_text("occupied")
    result = _seed(kit, output)

    assert result.returncode != 0
    assert "Presenter Runbook" in result.stderr
    assert "DEMO_SCRIPT.md" in result.stderr
    assert "Restore" in result.stderr if failure == "missing-source" else (
        "SYNTH_OUT_DIR" in result.stderr and str(output) in result.stderr
    )
    assert not (kit / ".synth_spool" / "events.ndjson").exists()
