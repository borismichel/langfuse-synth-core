"""The retargeting gate — a kit config must honor ``LANGFUSE_BASE_URL`` (portal #187).

The rule, and why it needed a gate of its own, are in ``CONTRACT.md`` §"Retargeting". This
module pins both halves of it: an injected ``LANGFUSE_BASE_URL`` wins, and with the var absent
the committed file value still applies.

The end-to-end test at the bottom runs the gate against a freshly scaffolded kit — the one that
was red before #187, because the scaffold emitted a plain ``base_url`` field with no env read.
"""

from __future__ import annotations

import importlib.util
import os
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="the retargeting gate ships in langfuse-synth-core[authoring]",
)

FILE_BASE_URL = "http://from-the-config-file:3000"


# --- fake kit loaders: the two config shapes, honest and broken ---------------------------
def _honest_loader(path, overrides=None):
    """A kit whose ``base_url`` lets the env win (what EV/Lender do, via a property)."""
    resolved = os.environ.get("LANGFUSE_BASE_URL", FILE_BASE_URL).rstrip("/")
    return SimpleNamespace(target=SimpleNamespace(base_url=resolved))


def _env_ignoring_loader(path, overrides=None):
    """The #187 defect shape: the file value, full stop — the env var is never read."""
    return SimpleNamespace(target=SimpleNamespace(base_url=FILE_BASE_URL))


def _env_only_loader(path, overrides=None):
    """Honors the env but has lost the file default — the opposite failure."""
    return SimpleNamespace(target=SimpleNamespace(base_url=os.environ.get("LANGFUSE_BASE_URL", "")))


# --- resolve_base_url: the seam the gate reads through ------------------------------------
def test_resolve_base_url_reports_what_the_kit_resolves_under_an_injected_env(tmp_path):
    from langfuse_synth_core.authoring.retarget import resolve_base_url

    injected = "http://langfuse.internal:3050"
    assert resolve_base_url(_honest_loader, tmp_path / "demo.yaml", injected) == injected
    assert resolve_base_url(_env_ignoring_loader, tmp_path / "demo.yaml", injected) == FILE_BASE_URL


def test_resolve_base_url_with_none_unsets_the_var_even_when_ambient(tmp_path, monkeypatch):
    """The fallback probe must see NO ``LANGFUSE_BASE_URL`` — including when the machine
    running the gate has one exported (a demo host does)."""
    from langfuse_synth_core.authoring.retarget import resolve_base_url

    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://ambient-leak:3000")
    assert resolve_base_url(_honest_loader, tmp_path / "demo.yaml", None) == FILE_BASE_URL


def test_the_gate_leaves_the_ambient_environment_untouched(tmp_path, monkeypatch):
    from langfuse_synth_core.authoring.retarget import assert_retargetable, resolve_base_url

    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://ambient:3000")
    assert_retargetable(_honest_loader, tmp_path / "demo.yaml")
    resolve_base_url(_honest_loader, tmp_path / "demo.yaml", None)
    assert os.environ["LANGFUSE_BASE_URL"] == "http://ambient:3000"

    monkeypatch.delenv("LANGFUSE_BASE_URL")
    assert_retargetable(_honest_loader, tmp_path / "demo.yaml")
    assert "LANGFUSE_BASE_URL" not in os.environ


# --- assert_retargetable: red/green on the two failure shapes -----------------------------
def test_passes_a_kit_whose_base_url_lets_the_env_win(tmp_path):
    from langfuse_synth_core.authoring.retarget import assert_retargetable

    assert_retargetable(_honest_loader, tmp_path / "demo.yaml")  # no exception == green


def test_fails_a_kit_that_ignores_the_env_and_says_why(tmp_path):
    from langfuse_synth_core.authoring.retarget import assert_retargetable

    with pytest.raises(AssertionError) as excinfo:
        assert_retargetable(_env_ignoring_loader, tmp_path / "demo.yaml")
    message = str(excinfo.value)
    assert "LANGFUSE_BASE_URL" in message, "the failure must name the var the portal injects"
    assert FILE_BASE_URL in message, "and show what the kit resolved instead"


def test_fails_a_kit_that_has_lost_the_config_file_default(tmp_path):
    """Env-wins is half the contract: with no env var the file value must still be used, or
    a kit run outside the portal (the author's laptop, the golden gate) resolves nothing."""
    from langfuse_synth_core.authoring.retarget import assert_retargetable

    with pytest.raises(AssertionError):
        assert_retargetable(_env_only_loader, tmp_path / "demo.yaml")


def test_a_kit_hardcoded_to_one_probe_value_still_fails(tmp_path):
    """The gate injects two distinct probes, so returning a constant that happens to equal
    the probe cannot fake a pass."""
    from langfuse_synth_core.authoring.retarget import PROBE_BASE_URLS, assert_retargetable

    def hardcoded(path, overrides=None):
        return SimpleNamespace(target=SimpleNamespace(base_url=PROBE_BASE_URLS[0]))

    assert len(set(PROBE_BASE_URLS)) >= 2, "one probe cannot distinguish a constant"
    with pytest.raises(AssertionError):
        assert_retargetable(hardcoded, tmp_path / "demo.yaml")


# --- end to end: the scaffolded kit is retargetable ---------------------------------------
def test_a_freshly_scaffolded_kit_is_retargetable_by_the_portal(tmp_path, load_kit_module):
    """The gate applied to `synth-authoring new` output — red before #187: the scaffold's
    `Target.base_url` was a plain field, so a portal deployment could never move it."""
    from langfuse_synth_core.authoring.retarget import assert_retargetable
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    kit = scaffold_kit("retarget-demo", tmp_path / "retarget-demo")
    config_mod = load_kit_module(
        kit.dest / "src" / "synth" / "config.py", "scaffold_retarget_config"
    )
    assert_retargetable(config_mod.load_config, kit.dest / "config" / "demo.yaml")
