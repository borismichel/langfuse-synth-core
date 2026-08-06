"""The shared per-run anchors IO (portal #199).

These tests pin the mechanism the Contract promises (CONTRACT.md §"Per-run anchors
(opt-in)"): the state file's name, its location resolved from ``SYNTH_STATE_DIR`` at call
time, and the save/load/exists surface a kit's payload dataclass inherits. The location
tests mirror the kit-local ``tests/test_state_location.py`` twins this module replaces —
EV and Lender delete theirs when they re-pin, so the law has to live here.

The serialization test locks the on-disk byte format: EV and Lender migrate mid-flight
(a state file written by the kit-local twin must load through the core mixin, and the
golden spools must not move), so ``save`` may not change a byte.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from langfuse_synth_core.anchors import (
    STATE_DIR_ENV,
    STATE_FILENAME,
    AnchorsIO,
    state_dir,
    state_path,
)

FALLBACK = Path("/kit/.synth_spool")


@dataclass
class _Anchors(AnchorsIO):
    """A minimal kit-style payload — the fields are kit territory, the IO is core's."""

    FALLBACK_STATE_DIR: ClassVar[Path] = FALLBACK

    project_name: str
    run_date: str = ""
    summary: dict = field(default_factory=dict)


# ── location: SYNTH_STATE_DIR else the kit's fallback, resolved at call time ────────────


def test_state_dir_defaults_to_the_kit_fallback(monkeypatch):
    monkeypatch.delenv(STATE_DIR_ENV, raising=False)
    assert state_dir(FALLBACK) == FALLBACK


def test_state_dir_honours_synth_state_dir(monkeypatch):
    monkeypatch.setenv(STATE_DIR_ENV, "/app/.synth_spool")
    assert state_dir(FALLBACK) == Path("/app/.synth_spool")
    assert state_path(FALLBACK) == "/app/.synth_spool/.synth_state.json"


def test_state_dir_is_resolved_at_call_time(monkeypatch):
    monkeypatch.setenv(STATE_DIR_ENV, "/first")
    assert state_dir(FALLBACK) == Path("/first")
    monkeypatch.setenv(STATE_DIR_ENV, "/second")
    assert state_dir(FALLBACK) == Path("/second")


def test_state_path_joins_the_canonical_filename():
    assert state_path(FALLBACK) == str(FALLBACK / STATE_FILENAME)
    assert STATE_FILENAME == ".synth_state.json"


def test_mixin_classmethods_resolve_through_the_class_fallback(monkeypatch):
    monkeypatch.delenv(STATE_DIR_ENV, raising=False)
    assert _Anchors.state_dir() == FALLBACK
    assert _Anchors.state_path() == str(FALLBACK / STATE_FILENAME)
    monkeypatch.setenv(STATE_DIR_ENV, "/app/.synth_spool")
    assert _Anchors.state_dir() == Path("/app/.synth_spool")


# ── save / load / exists ─────────────────────────────────────────────────────────────────


def test_save_load_exists_roundtrip_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    assert not _Anchors.exists()
    _Anchors(project_name="demo-x", run_date="2026-08-06").save()
    assert _Anchors.exists()
    assert (tmp_path / STATE_FILENAME).exists()
    loaded = _Anchors.load()
    assert loaded.project_name == "demo-x"
    assert loaded.run_date == "2026-08-06"


def test_save_creates_the_state_dir_if_absent(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "deep" / "spool"))
    _Anchors(project_name="demo-x").save()
    assert _Anchors.exists()


def test_explicit_path_wins_over_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "env"))
    explicit = tmp_path / "explicit" / STATE_FILENAME
    _Anchors(project_name="demo-x").save(str(explicit))
    assert not _Anchors.exists()
    assert _Anchors.exists(str(explicit))
    assert _Anchors.load(str(explicit)).project_name == "demo-x"


def test_load_tolerates_unknown_keys(monkeypatch, tmp_path):
    """A state file written by an older or newer payload schema still loads (the Lender
    twin's behavior, adopted for every kit): unknown keys are dropped, not fatal."""
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    payload = {"project_name": "demo-x", "retired_field": 7, "summary": {"n": 1}}
    (tmp_path / STATE_FILENAME).write_text(json.dumps(payload))
    loaded = _Anchors.load()
    assert loaded.project_name == "demo-x"
    assert loaded.summary == {"n": 1}
    assert not hasattr(loaded, "retired_field")


def test_save_byte_format_matches_the_kit_twins(monkeypatch, tmp_path):
    """The exact serialization the kit-local twins wrote: ``json.dumps(asdict(self),
    indent=2)`` — field order = declaration order, two-space indent, no trailing newline."""
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path))
    state = _Anchors(project_name="demo-x", run_date="2026-08-06", summary={"n": 1})
    state.save()
    expected = json.dumps(
        {"project_name": "demo-x", "run_date": "2026-08-06", "summary": {"n": 1}}, indent=2
    )
    assert (tmp_path / STATE_FILENAME).read_text() == expected
