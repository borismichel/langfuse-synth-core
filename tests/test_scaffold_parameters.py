"""Generated golden adapters exercise the same inputs as the runtime config loader."""
from dataclasses import replace

import pytest

from langfuse_synth_core.authoring.golden import GoldenSpec, materialize_spool
from langfuse_synth_core.authoring.scaffold import scaffold_kit


@pytest.fixture
def spec(tmp_path):
    kit = scaffold_kit("parameter-demo", tmp_path / "kit")
    return GoldenSpec(
        seed_ref="golden_seed:seed", target_traces=24, golden_path=kit.golden_path,
        search_paths=(str(kit.dest / "tests"), str(kit.dest / "src")),
    )


def test_seed_parameter_changes_the_generated_pool_and_repeats(spec):
    one = replace(spec, params={"seed": 1})
    other = replace(spec, params={"generation.seed": 999})
    assert materialize_spool(one) == materialize_spool(one)
    assert materialize_spool(one) != materialize_spool(other)


def test_explicit_default_inputs_preserve_the_golden(spec):
    default = replace(spec, params={"seed": 42, "as_of_date": "2026-01-01"})
    assert materialize_spool(default) == spec.golden_path.read_bytes()


def test_date_parameter_retargets_the_generated_window(spec):
    dated = replace(spec, params={"generation.as_of_date": "2026-09-01"})
    assert materialize_spool(dated) != materialize_spool(spec)
    assert materialize_spool(dated) == materialize_spool(dated)


@pytest.mark.parametrize("params, message", [
    ({"persona": "ada"}, "unsupported golden parameter"),
    ({"target_traces": 3}, "target_traces argument"),
    ({"generation.target_traces": 3}, "target_traces argument"),
    ({"seed": 1, "generation.seed": 2}, "specified more than once"),
    ({"seed": -1}, "non-negative integer"),
    ({"seed": True}, "non-negative integer"),
    ({"seed": "oops"}, "non-negative integer"),
    ({"as_of_date": None}, "YYYY-MM-DD"),
    ({"as_of_date": "not-a-date"}, "YYYY-MM-DD"),
])
def test_invalid_parameters_are_actionable(spec, params, message):
    with pytest.raises(RuntimeError, match=message):
        materialize_spool(replace(spec, params=params))
