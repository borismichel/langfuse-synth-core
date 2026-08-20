"""The kit-declared ``units_per_trace`` advisory override (#35) — runtime pieces.

``units_per_trace`` lets a kit tell the operator, up front, roughly how many billable
units a single trace expands into (default ~11 = 10 observations + 1 sampled score), so
the deploy wizard can show an *advisory* estimate ``target_traces x units_per_trace``
before a run. It is advisory ONLY: the measured Spool count (``count_spool``) is what
binds, so a wrong ``units_per_trace`` is harmless. These lock that contract.

The runtime pieces (canonical key, default, resolver, estimate) ship in the runtime
library — no ``[authoring]`` extra — because the estimate is computed at deploy time
wherever the lib runs, exactly like the ``target_traces`` derivation hook.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from langfuse_synth_core.derivation import (
    DEFAULT_UNITS_PER_TRACE,
    UNITS_PER_TRACE_KEY,
    advisory_estimate,
    resolve_units_per_trace,
)
from langfuse_synth_core.seed.count import count_spool
from langfuse_synth_core.seed.events import (
    generation_event,
    score_event,
    trace_event,
)


def test_default_units_per_trace_is_ten_obs_plus_one_score():
    assert DEFAULT_UNITS_PER_TRACE == 11


def test_canonical_key_is_the_dotted_generation_key():
    assert UNITS_PER_TRACE_KEY == "generation.units_per_trace"


def test_estimate_uses_the_default_when_unspecified():
    assert advisory_estimate(1_000) == 11_000


def test_estimate_honors_an_explicit_override():
    assert advisory_estimate(500, units_per_trace=20) == 10_000


def test_resolve_reads_the_declared_key_else_defaults():
    assert resolve_units_per_trace({UNITS_PER_TRACE_KEY: 20}) == 20
    assert resolve_units_per_trace({}) == DEFAULT_UNITS_PER_TRACE
    assert resolve_units_per_trace(None) == DEFAULT_UNITS_PER_TRACE


def test_resolve_rejects_non_positive_and_bool():
    with pytest.raises(ValueError):
        resolve_units_per_trace({UNITS_PER_TRACE_KEY: 0})
    with pytest.raises(ValueError):
        resolve_units_per_trace({UNITS_PER_TRACE_KEY: -5})
    # bool is an int subclass — guard against True/False slipping through.
    with pytest.raises(ValueError):
        resolve_units_per_trace({UNITS_PER_TRACE_KEY: True})


def test_estimate_rejects_bad_inputs():
    with pytest.raises(ValueError):
        advisory_estimate(-1)
    with pytest.raises(ValueError):
        advisory_estimate(100, units_per_trace=0)
    with pytest.raises(ValueError):
        advisory_estimate(True)


def test_advisory_never_binds_the_measured_count(tmp_path: Path):
    """The heart of the contract: a wildly wrong ``units_per_trace`` moves the ESTIMATE
    but never the MEASURED count. The bytes on disk are the ground truth ``count_spool``
    reads; the advisory is decoupled from them, so its inaccuracy is harmless."""
    from langfuse_synth_core.seed.ingest import Ingestor

    ts = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
    # A real spool: 2 traces, each with 1 generation + 1 score. Core mints a root
    # observation per trace, so measured = {traces 2, observations 4, scores 2}.
    events = []
    for t in ("1" * 32, "2" * 32):
        events.append(trace_event(trace_id=t, timestamp=ts, name="d"))
        events.append(generation_event(obs_id=t[:16], trace_id=t, name="llm",
                                        start=ts, end=ts, model="m",
                                        usage_details={}, cost_details={}))
        events.append(score_event(score_id=f"s{t[:4]}", name="q", value=1,
                                  data_type="NUMERIC", timestamp=ts, trace_id=t))
    spool = tmp_path / "events.ndjson"
    ing = Ingestor(base_url="http://x", public_key="p", secret_key="s", spool_path=spool)
    ing.open_spool()
    ing.extend(events)
    ing.close_spool()

    measured = count_spool(spool)
    measured_total = measured["total"]
    assert measured == {"traces": 2, "observations": 4, "scores": 2, "total": 6}

    # A deliberately absurd advisory: the estimate is off by orders of magnitude...
    absurd = advisory_estimate(target_traces=2, units_per_trace=1_000)
    assert absurd == 2_000 and absurd != measured_total
    # ...yet the measured count is unchanged — it never consulted the advisory.
    assert count_spool(spool) == measured
