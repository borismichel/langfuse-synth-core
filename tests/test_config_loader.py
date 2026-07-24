"""The shared config loader — data-model middle field (Ring 2, #33).

The loader mechanism (read YAML + apply ``--set dotted.key=value`` overrides + validate)
is scenario-agnostic and moved into the lib. The only per-kit *value* is the model it
validates into, passed as a ``model_factory`` so the lib never imports pydantic or knows
the kit's config shape. These lock that contract; the EV kit's own overrides suite proves
the same mechanism end-to-end against its real pydantic Config.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from langfuse_synth_core.config import apply_overrides, load_config, set_dotted


def test_set_dotted_creates_missing_intermediate_mappings():
    raw: dict = {}
    set_dotted(raw, "a.b.c", 1)
    assert raw == {"a": {"b": {"c": 1}}}


def test_set_dotted_overwrites_a_non_mapping_intermediate():
    raw = {"a": 5}
    set_dotted(raw, "a.b", 9)
    assert raw == {"a": {"b": 9}}


def test_apply_overrides_coerces_types_like_yaml():
    raw = {"generation": {}, "flags": {}}
    apply_overrides(raw, [
        "generation.target_traces=800",   # int
        "generation.ratio=1.5",           # float
        "flags.enabled=false",            # bool
        "flags.label=hello",              # str
    ])
    assert raw["generation"]["target_traces"] == 800
    assert isinstance(raw["generation"]["target_traces"], int)
    assert raw["generation"]["ratio"] == 1.5
    assert raw["flags"]["enabled"] is False
    assert raw["flags"]["label"] == "hello"


@pytest.mark.parametrize("bad", ["nokey", "=value", "  =v"])
def test_apply_overrides_rejects_malformed(bad):
    with pytest.raises(ValueError):
        apply_overrides({}, [bad])


def test_apply_overrides_none_is_a_noop():
    raw = {"x": 1}
    assert apply_overrides(raw, None) == {"x": 1}


def test_load_config_reads_yaml_applies_overrides_and_validates(tmp_path: Path):
    cfg_file = tmp_path / "demo.yaml"
    cfg_file.write_text("generation:\n  target_traces: 100\n  seed: 42\n")

    # A stand-in kit "model": the loader must stay ignorant of pydantic / the kit shape.
    seen = {}

    def model_factory(raw: dict) -> dict:
        seen["raw"] = raw
        return {"validated": raw}

    result = load_config(cfg_file, model_factory, ["generation.target_traces=900"])
    assert result == {"validated": {"generation": {"target_traces": 900, "seed": 42}}}
    # The override was applied to the raw dict BEFORE the factory saw it.
    assert seen["raw"]["generation"]["target_traces"] == 900


def test_load_config_without_overrides_passes_yaml_through(tmp_path: Path):
    cfg_file = tmp_path / "demo.yaml"
    cfg_file.write_text("a: 1\nb: two\n")
    assert load_config(cfg_file, lambda raw: raw) == {"a": 1, "b": "two"}
