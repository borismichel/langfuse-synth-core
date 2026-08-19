"""The Spool write-path flag (portal #206) — expand half of the v4 expand–contract move.

Core gains an OTLP write path *beside* batch ingestion. Which one a Spool is written on is
selected here, and the default is today's behaviour: a kit that repins and changes nothing
keeps emitting batch envelopes. These lock the selection contract — default, env default,
kit-set override, and precedence between them — because #210 cuts kits over one at a time
by flipping this flag, and reverts by flipping it back.
"""

from __future__ import annotations

import pytest

from langfuse_synth_core.seed import writepath


@pytest.fixture(autouse=True)
def _clear_override():
    writepath.set_spool_write_path(None)
    yield
    writepath.set_spool_write_path(None)


def test_defaults_to_the_batch_path(monkeypatch):
    monkeypatch.delenv(writepath.WRITE_PATH_ENV, raising=False)
    assert writepath.spool_write_path() == writepath.BATCH
    assert writepath.on_otlp() is False


def test_env_selects_the_otlp_path(monkeypatch):
    monkeypatch.setenv(writepath.WRITE_PATH_ENV, "otlp")
    assert writepath.spool_write_path() == writepath.OTLP
    assert writepath.on_otlp() is True


def test_kit_set_override_wins_over_the_env_default(monkeypatch):
    monkeypatch.setenv(writepath.WRITE_PATH_ENV, "batch")
    writepath.set_spool_write_path(writepath.OTLP)
    assert writepath.spool_write_path() == writepath.OTLP


def test_context_manager_restores_the_previous_selection(monkeypatch):
    monkeypatch.delenv(writepath.WRITE_PATH_ENV, raising=False)
    with writepath.use_spool_write_path(writepath.OTLP):
        assert writepath.on_otlp() is True
    assert writepath.spool_write_path() == writepath.BATCH


def test_an_unknown_value_fails_loudly_rather_than_silently_falling_back(monkeypatch):
    monkeypatch.setenv(writepath.WRITE_PATH_ENV, "grpc")
    with pytest.raises(ValueError, match="grpc"):
        writepath.spool_write_path()
