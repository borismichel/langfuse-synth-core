"""Spool-count primitive (#35) — the measured billable set read off a materialized Spool.

``count_spool`` is the read-side sibling of ``import_spool``: it walks the same NDJSON
spool and tallies the events Langfuse actually meters — traces, observations, scores —
by envelope ``type``. Experiment runs and dataset items are not billed as line items and
never appear as billable envelope types (they go through separate REST endpoints), so a
whitelist excludes them by construction. These lock that contract offline, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from langfuse_synth_core.seed.count import count_spool
from langfuse_synth_core.seed.events import (
    generation_event,
    score_event,
    span_event,
    trace_event,
)
from datetime import datetime, timezone


def _write(path: Path, events: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events),
        encoding="utf-8",
    )


def test_tallies_billable_types(tmp_path: Path):
    spool = tmp_path / "events.ndjson"
    _write(spool, [
        {"type": "trace-create", "id": "t1"},
        {"type": "trace-create", "id": "t2"},
        {"type": "span-create", "id": "o1"},
        {"type": "generation-create", "id": "o2"},
        {"type": "event-create", "id": "o3"},
        {"type": "score-create", "id": "s1"},
    ])
    assert count_spool(spool) == {"traces": 2, "observations": 3, "scores": 1}


def test_all_observation_subtypes_count_as_observations(tmp_path: Path):
    """span / generation / event / observation -create all meter as observations."""
    spool = tmp_path / "events.ndjson"
    _write(spool, [
        {"type": "span-create", "id": "a"},
        {"type": "generation-create", "id": "b"},
        {"type": "event-create", "id": "c"},
        {"type": "observation-create", "id": "d"},
    ])
    assert count_spool(spool) == {"traces": 0, "observations": 4, "scores": 0}


def test_excludes_experiment_runs_and_dataset_items(tmp_path: Path):
    """Dataset items / experiment (dataset-run) items are not metered line items — they
    ride separate REST endpoints and must never inflate the measured billable set."""
    spool = tmp_path / "events.ndjson"
    _write(spool, [
        {"type": "trace-create", "id": "t1"},
        {"type": "generation-create", "id": "o1"},
        {"type": "score-create", "id": "s1"},
        # non-billable noise that a naive line-count would wrongly include:
        {"type": "dataset-item-create", "id": "d1"},
        {"type": "dataset-run-item-create", "id": "r1"},
        {"type": "sdk-log", "id": "l1"},
    ])
    assert count_spool(spool) == {"traces": 1, "observations": 1, "scores": 1}


def test_ignores_blank_lines_and_empty_spool(tmp_path: Path):
    spool = tmp_path / "events.ndjson"
    spool.write_text('{"type":"trace-create","id":"t"}\n\n   \n', encoding="utf-8")
    assert count_spool(spool) == {"traces": 1, "observations": 0, "scores": 0}

    empty = tmp_path / "empty.ndjson"
    empty.write_text("", encoding="utf-8")
    assert count_spool(empty) == {"traces": 0, "observations": 0, "scores": 0}


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        count_spool(tmp_path / "nope.ndjson")


def test_accepts_str_path(tmp_path: Path):
    spool = tmp_path / "events.ndjson"
    _write(spool, [{"type": "trace-create", "id": "t"}])
    assert count_spool(str(spool)) == {"traces": 1, "observations": 0, "scores": 0}


def test_counts_real_event_envelopes(tmp_path: Path):
    """Cross-check against envelopes built by the real events.py builders: whatever the
    builders emit as trace/observation/score is exactly what count_spool tallies."""
    ts = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
    events = [
        trace_event(trace_id="t1", timestamp=ts, name="decision"),
        span_event(obs_id="o1", trace_id="t1", name="retrieve", start=ts, end=ts),
        generation_event(obs_id="o2", trace_id="t1", name="llm", start=ts, end=ts,
                         model="m", usage_details={}, cost_details={}),
        score_event(score_id="s1", name="quality", value=1, data_type="NUMERIC",
                    timestamp=ts, trace_id="t1"),
    ]
    spool = tmp_path / "events.ndjson"
    _write(spool, events)
    assert count_spool(spool) == {"traces": 1, "observations": 2, "scores": 1}
