"""Ring 1a (#31): the byte-identical core lives in the lib and works on a bare runtime.

These exercise the extracted primitives directly — RNG/ID substreams, token pricing,
statistical distributions, event-envelope emission, the ISO formatting primitive, and
the scenario-agnostic UI chrome — plus the structural ``config`` contract they annotate
against. None of this needs the ``[authoring]`` extra: it is the runtime surface a
deployed kit carries. The authoritative behaviour-preservation proof is each kit's
byte-for-byte golden gate; this suite guards the seam from the library side.
"""
from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest

from langfuse_synth_core import config, distributions, pricing, rng
from langfuse_synth_core.live import paths, theme
from langfuse_synth_core.seed import events
from langfuse_synth_core.timegen import iso

CORE_MODULES = [
    "config",
    "timegen",
    "rng",
    "pricing",
    "distributions",
    "lfclient",
    "seed.events",
    "live.theme",
    "live.paths",
]


# --- a duck-typed config the shared core reads against (kits pass their own pydantic one)
class _Model:
    input_per_1k = 3.0
    output_per_1k = 15.0


class _Target:
    base_url = "https://cloud.langfuse.example"


class _Config:
    target = _Target()

    def model_by_role(self, role: str) -> _Model:
        return _Model()


@pytest.mark.parametrize("mod", CORE_MODULES)
def test_core_modules_import_on_bare_runtime(mod: str) -> None:
    # No [authoring] extra required. lfclient imports the langfuse SDK lazily, so
    # importing the module never pulls the SDK at import time.
    assert importlib.import_module(f"langfuse_synth_core.{mod}")


# --- RNG / ID substreams -------------------------------------------------------------
def test_ids_are_deterministic_for_a_seed() -> None:
    a, b = rng.Rng(42), rng.Rng(42)
    assert a.trace_id("app", 1) == b.trace_id("app", 1)
    assert a.obs_id("app", 1, "plan") == b.obs_id("app", 1, "plan")


def test_ids_diverge_across_seeds() -> None:
    assert rng.Rng(1).trace_id("x") != rng.Rng(2).trace_id("x")


def test_id_widths_are_w3c() -> None:
    r = rng.Rng(7)
    tid, oid = r.trace_id("t"), r.obs_id("o")
    hexdigits = set("0123456789abcdef")
    assert len(tid) == 32 and set(tid) <= hexdigits   # 16-byte trace id, lowercase hex
    assert len(oid) == 16 and set(oid) <= hexdigits   # 8-byte observation id


def test_substreams_are_independent_and_reproducible() -> None:
    r1 = rng.Rng(42).sub("timegen").uniform(0, 1)
    r2 = rng.Rng(42).sub("timegen").uniform(0, 1)
    other = rng.Rng(42).sub("scores").uniform(0, 1)
    assert r1 == r2
    assert r1 != other


# --- token pricing -------------------------------------------------------------------
def test_cost_details_uses_per_1k_rates_and_cache_multipliers() -> None:
    d = pricing.cost_details(_Model(), input_tokens=1000, output_tokens=500,
                             cache_read=1000, cache_creation=1000)
    assert d["input"] == 3.0
    assert d["output"] == 7.5
    assert d["cache_read_input_tokens"] == round(3.0 * pricing.CACHE_READ_MULT, 6)
    assert d["cache_creation_input_tokens"] == round(3.0 * pricing.CACHE_WRITE_MULT, 6)
    assert d["total"] == round(3.0 + 0.3 + 3.75 + 7.5, 6)


def test_usage_details_totals() -> None:
    u = pricing.usage_details(100, 40, cache_read=10, cache_creation=5, reasoning=7)
    assert u["total"] == 100 + 10 + 5 + 40 + 7


def test_model_for_role_reads_through_config_protocol() -> None:
    assert pricing.model_for_role(_Config(), "plan").input_per_1k == 3.0


# --- statistical distributions -------------------------------------------------------
def test_token_sampling_is_reproducible_under_a_seed() -> None:
    def sample():
        return distributions.sample_tokens(
            rng.Rng(42).sub("t"), "plan", visible_input=200, visible_output=80)
    s1, s2 = sample(), sample()
    assert s1 == s2
    inp, out, reasoning = s1
    assert inp >= 200 and out >= 1 and reasoning >= 0  # planner carries a reasoning budget


def test_text_tokens_estimate() -> None:
    assert distributions.text_tokens("x" * 40) == 10
    assert distributions.text_tokens([{"content": "y" * 8}]) == 2


# --- event emission ------------------------------------------------------------------
def test_trace_event_envelope_shape_and_iso_stamp() -> None:
    ts = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
    ev = events.trace_event(trace_id="a" * 32, timestamp=ts, name="decision")
    assert set(ev) == {"id", "type", "timestamp", "body"}
    assert ev["type"] == "trace-create"
    assert ev["timestamp"] == "2026-06-09T12:00:00.000Z"
    assert ev["body"]["id"] == "a" * 32


def test_envelope_ids_are_idempotent() -> None:
    ts = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
    e1 = events.trace_event(trace_id="a" * 32, timestamp=ts, name="x")
    e2 = events.trace_event(trace_id="a" * 32, timestamp=ts, name="different-name")
    # envelope id is keyed on object id + type, so re-emits upsert rather than duplicate
    assert e1["id"] == e2["id"]


# --- ISO formatting primitive --------------------------------------------------------
def test_iso_appends_millis_and_z_and_coerces_utc() -> None:
    naive = datetime(2026, 1, 2, 3, 4, 5, 6000)
    assert iso(naive) == "2026-01-02T03:04:05.006Z"


# --- the config contract is structural ----------------------------------------------
def test_duck_typed_config_satisfies_protocols() -> None:
    assert isinstance(_Model(), config.Model)
    assert isinstance(_Target(), config.Target)
    assert isinstance(_Config(), config.Config)


# --- scenario-agnostic UI primitives -------------------------------------------------
def test_theme_page_wraps_body() -> None:
    html = theme.page("<p>hi</p>", title="Demo")
    assert "<p>hi</p>" in html and "Demo" in html


def test_paths_local_is_bare_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_BASE_PATH", raising=False)
    assert paths.local("/submit") == "/submit"
