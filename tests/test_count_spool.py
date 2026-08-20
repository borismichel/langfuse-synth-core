"""Spool-count primitive (#35) — the measured billable set read off a materialized Spool.

``count_spool`` is the read-side sibling of ``import_spool``: it walks the same NDJSON
spool and tallies what Langfuse actually meters — observations (OTLP spans), scores
(`score-create` envelopes), and a *derived* trace term. Experiment runs and dataset items
are not billed as line items and never appear in a Spool (they go through separate REST
endpoints), so they are excluded by construction. These lock that contract offline, no
network.

The count carries ``total`` — the billable volume the cap gate measures against (portal
#220). Only the primitive can say that the trace term is a **view** over a minted root
observation that is already inside ``observations``, rather than an ingested object, so
``total`` is its to define and not the caller's to sum.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from langfuse_synth_core.seed.count import count_spool
from langfuse_synth_core.seed.events import (
    generation_event,
    observation_event,
    score_event,
    span_event,
    trace_event,
)

TS = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
TID_A = "5b8aa1cfd0e34f7a9c2b6d15e0473f88"
TID_B = "1111222233334444555566667777888a"


def _spool(tmp_path: Path, events: list[dict]) -> Path:
    """A real Spool, written and finalised by the real ``Ingestor``."""
    from langfuse_synth_core.seed.ingest import Ingestor

    tmp_path.mkdir(parents=True, exist_ok=True)
    spool = tmp_path / "events.ndjson"
    ing = Ingestor(base_url="http://x", public_key="p", secret_key="s", spool_path=spool)
    ing.open_spool()
    ing.extend(events)
    ing.close_spool()
    return spool


def _story(tid: str) -> list[dict]:
    """One trace shaped like a real kit's: a shell, two observations, one score."""
    return [
        trace_event(trace_id=tid, timestamp=TS, name="decision"),
        span_event(obs_id="aaaa1111bbbb2222", trace_id=tid, name="agent",
                   start=TS, end=TS),
        generation_event(obs_id="cccc3333dddd4444", trace_id=tid, name="llm",
                         start=TS, end=TS, model="m", usage_details={}, cost_details={}),
        score_event(score_id=f"s-{tid[:4]}", name="quality", value=1,
                    data_type="NUMERIC", timestamp=TS, trace_id=tid),
    ]


def test_tallies_the_metered_set(tmp_path: Path):
    """Two traces, three observations each (the kit's two plus the root core mints), one
    score each. The trace term is derived from distinct trace ids and is NOT in ``total``."""
    spool = _spool(tmp_path, _story(TID_A) + _story(TID_B))
    assert count_spool(spool) == {"traces": 2, "observations": 6, "scores": 2, "total": 8}


def test_the_same_trace_id_across_many_spans_counts_once(tmp_path: Path):
    spool = _spool(tmp_path, [
        span_event(obs_id=f"aaaa1111bbbb{n:04d}", trace_id=TID_A, name="step",
                   start=TS, end=TS)
        for n in range(5)
    ])
    assert count_spool(spool) == {"traces": 1, "observations": 5, "scores": 0, "total": 5}


def test_a_typed_observation_counts_like_any_other(tmp_path: Path):
    """The agent-graph types are a property of the span, not a separate metered kind."""
    spool = _spool(tmp_path, [
        trace_event(trace_id=TID_A, timestamp=TS, name="decision"),
        observation_event(obs_id="aaaa1111bbbb2222", trace_id=TID_A, name="agent",
                          obs_type="AGENT", start=TS, end=TS),
    ])
    assert count_spool(spool) == {"traces": 1, "observations": 2, "scores": 0, "total": 2}


def test_excludes_non_billable_lines(tmp_path: Path):
    """Dataset items / experiment (dataset-run) items are not metered line items — they
    ride separate REST endpoints and must never inflate the measured billable set. Nor may
    an `sdk-log`, or an envelope type the Spool no longer writes at all."""
    spool = tmp_path / "events.ndjson"
    spool.write_text("".join(
        json.dumps(e, separators=(",", ":")) + "\n" for e in [
            {"type": "score-create", "id": "s1"},
            {"type": "dataset-item-create", "id": "d1"},
            {"type": "dataset-run-item-create", "id": "r1"},
            {"type": "sdk-log", "id": "l1"},
            # The retired batch envelope types are not smuggled back in through the tally.
            {"type": "trace-create", "id": "t1"},
            {"type": "span-create", "id": "o1"},
        ]
    ), encoding="utf-8")
    assert count_spool(spool) == {"traces": 0, "observations": 0, "scores": 1, "total": 1}


def test_ignores_blank_lines_and_empty_spool(tmp_path: Path):
    spool = _spool(tmp_path, [span_event(obs_id="aaaa1111bbbb2222", trace_id=TID_A,
                                         name="step", start=TS, end=TS)])
    spool.write_text(spool.read_text(encoding="utf-8") + "\n   \n", encoding="utf-8")
    assert count_spool(spool) == {"traces": 1, "observations": 1, "scores": 0, "total": 1}

    empty = tmp_path / "empty.ndjson"
    empty.write_text("", encoding="utf-8")
    assert count_spool(empty) == {"traces": 0, "observations": 0, "scores": 0, "total": 0}


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        count_spool(tmp_path / "nope.ndjson")


def test_accepts_str_path(tmp_path: Path):
    spool = _spool(tmp_path, [span_event(obs_id="aaaa1111bbbb2222", trace_id=TID_A,
                                         name="step", start=TS, end=TS)])
    assert count_spool(str(spool))["total"] == 1


def test_the_derived_trace_term_is_reported_but_never_billed(tmp_path: Path):
    """The #220 invariant, stated on its own: a trace is a *view* over the root observation
    core mints for it, and that root is already inside ``observations``. Adding the trace
    term to ``total`` would bill every trace twice. This is also why the fleet's cutover off
    the batch path moved no deployment's measured volume — the minted roots raised
    ``observations`` by exactly the trace count the retired ``trace-create`` term dropped."""
    counts = count_spool(_spool(tmp_path, _story(TID_A) + _story(TID_B)))
    assert counts["traces"] == 2
    assert counts["total"] == counts["observations"] + counts["scores"]
    assert counts["total"] != sum(counts[k] for k in ("traces", "observations", "scores"))
