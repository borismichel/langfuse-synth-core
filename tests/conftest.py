"""Shared test fixtures.

One thing lives here: a helper for importing a module out of a *scaffolded* kit. Several
suites need it (the companion drift check, the retargeting gate) and a scaffolded kit is
never pip-installed during a test run, so it can only be reached by file path.

There was an autouse guard here too, isolating the Spool's write-path pin — a process
global a kit set in its own ``seed`` module (portal #206/#207). #213 removed the batch path
and the flag that selected it, so there is one write model and nothing left to isolate.
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
