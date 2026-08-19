"""Shared test fixtures.

Two things live here.

A helper for importing a module out of a *scaffolded* kit: several suites need it (the
companion drift check, the retargeting gate) and a scaffolded kit is never pip-installed
during a test run, so it can only be reached by file path.

And one autouse guard: the Spool's write path is a **process global** (a kit pins it in its
own ``seed`` module — portal #206/#207), so importing or running a scaffolded kit's seed
inside this suite would otherwise leave every later test writing OTLP. Each test starts with
no kit-set pin and whatever it sets is discarded afterwards.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def load_kit_module(monkeypatch):
    """Import a module from an emitted kit by file path, e.g. its ``src/synth/config.py``.

    Registered in ``sys.modules`` under ``name`` before execution — dataclass field resolution
    looks a class's own module up there, so an unregistered module raises deep inside
    ``dataclasses``. ``monkeypatch`` removes the entry again when the test ends.
    """

    def _load(path: str | Path, name: str):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    return _load


@pytest.fixture(autouse=True)
def isolated_spool_write_path():
    """No kit-set write-path pin leaks into (or out of) a test — see the module docstring."""
    from langfuse_synth_core.seed import writepath

    with writepath.use_spool_write_path(None):
        yield
