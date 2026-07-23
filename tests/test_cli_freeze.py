"""`synth freeze` CLI — blesses the golden in one intentional step (#28).

INTEGRATION RISK: the `synth` console script and `authoring/cli.py` dispatcher are
shared CLI surface (#27 `validate`, #11 `new`). These tests pin only the `freeze`
subcommand's behaviour.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="the synth CLI ships in the [authoring] extra; not installed on a runtime-only job",
)

FIXTURES = str(Path(__file__).resolve().parent / "fixtures")


def test_synth_freeze_blesses_the_golden(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    golden = tmp_path / "cli.golden"
    rc = main(
        [
            "freeze",
            "tiny_kit:seed",
            "--golden", str(golden),
            "--target-traces", "4",
            "--params", '{"seed": 3, "persona": "lin"}',
            "--search-path", FIXTURES,
        ]
    )
    assert rc == 0
    assert golden.exists() and golden.stat().st_size > 0
    assert "blessed golden" in capsys.readouterr().out


def test_synth_freeze_then_gate_is_green(tmp_path):
    """The CLI-blessed golden is the same oracle the gate reads."""
    from langfuse_synth_core.authoring.cli import main
    from langfuse_synth_core.authoring.golden import GoldenSpec, assert_golden

    golden = tmp_path / "cli.golden"
    main(
        [
            "freeze",
            "tiny_kit:seed",
            "--golden", str(golden),
            "--target-traces", "4",
            "--search-path", FIXTURES,
        ]
    )
    assert_golden(
        GoldenSpec(
            seed_ref="tiny_kit:seed",
            target_traces=4,
            golden_path=golden,
            params={},
            search_paths=(FIXTURES,),
        )
    )


def test_synth_freeze_rejects_a_planted_llm_call(tmp_path):
    from langfuse_synth_core.authoring.cli import main
    from langfuse_synth_core.authoring.egress import EgressBlockedError

    golden = tmp_path / "llm.golden"
    with pytest.raises(EgressBlockedError):
        main(
            [
                "freeze",
                "tiny_kit_llm:seed",
                "--golden", str(golden),
                "--target-traces", "2",
                "--search-path", FIXTURES,
            ]
        )
    assert not golden.exists()
