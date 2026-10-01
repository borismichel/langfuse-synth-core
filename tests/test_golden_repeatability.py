"""Recipe repeatability is independent of the fixed-hash golden snapshot."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="repeatability ships in the [authoring] extra",
)

FIXTURES = str(Path(__file__).resolve().parent / "fixtures")


def _spec(tmp_path, seed_ref):
    from langfuse_synth_core.authoring.golden import GoldenSpec

    return GoldenSpec(
        seed_ref=seed_ref,
        target_traces=5,
        golden_path=tmp_path / "spool.golden",
        params={"seed": 1, "persona": "ada"},
        search_paths=(FIXTURES,),
    )


def test_unordered_recipe_fails_repeatability_even_without_a_golden(tmp_path):
    from langfuse_synth_core.authoring.golden import RepeatabilityMismatch, assert_repeatable

    spec = _spec(tmp_path, "hashy_kit:seed")
    with pytest.raises(RepeatabilityMismatch, match="PYTHONHASHSEED=0.*PYTHONHASHSEED=1"):
        assert_repeatable(spec)
    assert not spec.golden_path.exists()


@pytest.mark.parametrize("existing", [False, True])
def test_freeze_cannot_bless_or_overwrite_a_process_dependent_recipe(tmp_path, existing):
    from langfuse_synth_core.authoring.golden import (
        RepeatabilityMismatch,
        freeze,
        materialize_spool,
    )

    spec = _spec(tmp_path, "hashy_kit:seed")
    if existing:
        spec.golden_path.write_bytes(b"previous trusted snapshot")
    # The legacy fixed-hash path stays stable, but is insufficient to bless output.
    assert materialize_spool(spec) == materialize_spool(spec)
    with pytest.raises(RepeatabilityMismatch, match="freeze.*cannot bless"):
        freeze(spec)
    if existing:
        assert spec.golden_path.read_bytes() == b"previous trusted snapshot"
    else:
        assert not spec.golden_path.exists()


def test_repeatable_recipe_preserves_fixed_hash_snapshots_and_content_drift(tmp_path):
    from langfuse_synth_core.authoring.golden import (
        GoldenMismatch,
        assert_golden,
        assert_repeatable,
        freeze,
        materialize_spool,
    )

    spec = _spec(tmp_path, "tiny_kit:seed")
    assert_repeatable(spec)
    fixed_hash_snapshot = materialize_spool(spec)
    freeze(spec)
    assert spec.golden_path.read_bytes() == fixed_hash_snapshot
    assert_golden(spec)
    spec.golden_path.write_bytes(b"outdated content")
    assert_repeatable(spec)
    with pytest.raises(GoldenMismatch, match="pool changed on purpose"):
        assert_golden(spec)


def test_repeatability_keeps_egress_block_on_alternate_hash_seeds(tmp_path):
    from langfuse_synth_core.authoring.egress import EgressBlockedError
    from langfuse_synth_core.authoring.golden import GoldenSpec, assert_repeatable

    (tmp_path / "egress_recipe.py").write_text(
        'import os, socket\n'
        'def seed(target_traces, params):\n'
        '    if os.environ["PYTHONHASHSEED"] != "0":\n'
        '        socket.create_connection(("example.com", 443))\n'
        '    return b"stable"\n'
    )
    spec = GoldenSpec(
        seed_ref="egress_recipe:seed",
        target_traces=1,
        golden_path=tmp_path / "unused.golden",
        search_paths=(str(tmp_path),),
    )
    with pytest.raises(EgressBlockedError):
        assert_repeatable(spec)


def test_generated_repeatability_gate_rejects_an_unordered_recipe(tmp_path):
    import os
    import subprocess
    import sys

    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    kit = scaffold_kit("repeatability-demo", tmp_path / "kit")
    (kit.dest / "tests" / "golden_seed.py").write_text(
        (Path(FIXTURES) / "hashy_kit.py").read_text()
    )
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([
            str(kit.dest / "src"), str(kit.dest / "tests"),
            str(Path(__file__).resolve().parents[1] / "src"),
        ]),
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q",
         "tests/test_determinism.py::test_recipe_is_repeatable_across_process_hash_seeds"],
        cwd=kit.dest, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "RepeatabilityMismatch" in result.stdout
