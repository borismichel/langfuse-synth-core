"""Authoring golden runs preserve the state of the kit's last real seed."""
from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="the scaffolder requires the authoring extra",
)


@pytest.mark.parametrize("with_anchors", [False, True])
@pytest.mark.parametrize("inherited_state_dir", [False, True])
def test_freeze_and_golden_check_preserve_existing_seed_state(
    tmp_path, monkeypatch, with_anchors, inherited_state_dir,
):
    from langfuse_synth_core.authoring.golden import GoldenSpec, assert_golden, freeze
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    monkeypatch.delenv("SYNTH_STATE_DIR", raising=False)
    kit = scaffold_kit("isolated-golden", tmp_path / "kit", with_anchors=with_anchors)
    state_dir = tmp_path / "live-state" if inherited_state_dir else kit.dest / ".synth_spool"
    state_dir.mkdir(exist_ok=True)
    if inherited_state_dir:
        monkeypatch.setenv("SYNTH_STATE_DIR", str(state_dir))
    receipt = b'{"examples": [{"trace_id": "last-successful-live-trace"}]}\n'
    anchors = b'{"project_name": "last-successful-live-project"}\n'
    (state_dir / ".synth_verify.json").write_bytes(receipt)
    (state_dir / ".synth_state.json").write_bytes(anchors)
    spec = GoldenSpec(
        seed_ref="golden_seed:seed", target_traces=2,
        golden_path=tmp_path / "spool.ndjson",
        search_paths=(str(kit.dest / "tests"), str(kit.dest / "src")),
    )

    for authoring_action in (freeze, assert_golden):
        authoring_action(spec)
        assert (state_dir / ".synth_verify.json").read_bytes() == receipt
        assert (state_dir / ".synth_state.json").read_bytes() == anchors
