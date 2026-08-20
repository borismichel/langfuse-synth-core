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


# --- the OTLP path (portal #206) -------------------------------------------
# Under v4 there is no trace envelope to count, so the trace term is derived from distinct
# trace ids. The output SHAPE is unchanged, which is what keeps the plan-time estimate, the
# cap gate and the over-cap halt untouched — but the numbers move, because the OTLP path
# mints one root observation per trace.

def _otlp_spool(tmp_path: Path, events: list[dict]) -> Path:
    from langfuse_synth_core.seed.ingest import Ingestor

    spool = tmp_path / "events.ndjson"
    ing = Ingestor(base_url="http://x", public_key="p", secret_key="s", spool_path=spool)
    ing.open_spool()
    ing.extend(events)
    ing.close_spool()
    return spool


def test_otlp_traces_come_from_distinct_trace_ids(tmp_path: Path):
    from langfuse_synth_core.seed import writepath

    ts = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
    tid_a = "5b8aa1cfd0e34f7a9c2b6d15e0473f88"
    tid_b = "1111222233334444555566667777888a"
    with writepath.use_spool_write_path(writepath.OTLP):
        events = []
        for tid in (tid_a, tid_b):
            events += [
                trace_event(trace_id=tid, timestamp=ts, name="decision"),
                span_event(obs_id="aaaa1111bbbb2222", trace_id=tid, name="agent",
                           start=ts, end=ts),
                generation_event(obs_id="cccc3333dddd4444", trace_id=tid, name="llm",
                                 start=ts, end=ts, model="m", usage_details={},
                                 cost_details={}),
            ]
        events.append(score_event(score_id="s1", name="quality", value=1,
                                  data_type="NUMERIC", timestamp=ts, trace_id=tid_a))
        spool = _otlp_spool(tmp_path, events)

    # Two distinct trace ids; three observations each (the minted root plus the kit's two);
    # one score, still a legacy ingestion envelope.
    assert count_spool(spool) == {"traces": 2, "observations": 6, "scores": 1}


def test_the_same_trace_id_across_many_spans_counts_once(tmp_path: Path):
    from langfuse_synth_core.seed import writepath

    ts = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
    tid = "5b8aa1cfd0e34f7a9c2b6d15e0473f88"
    with writepath.use_spool_write_path(writepath.OTLP):
        spool = _otlp_spool(tmp_path, [
            span_event(obs_id=f"aaaa1111bbbb{n:04d}", trace_id=tid, name="step",
                       start=ts, end=ts)
            for n in range(5)
        ])
    assert count_spool(spool) == {"traces": 1, "observations": 5, "scores": 0}


@pytest.mark.parametrize("path_name", ["batch", "otlp"])
def test_rich_observation_types_move_no_counts(tmp_path: Path, path_name, monkeypatch):
    """The #210 addendum's measured fact, locked as a regression: flipping
    ``RICH_OBSERVATION_TYPES`` changes the wire kind of a typed observation and nothing
    about the tally, on either write path. ``observation-create`` was already in the
    observation whitelist for exactly this moment."""
    from langfuse_synth_core.seed import writepath
    from langfuse_synth_core.seed import events as events_mod

    ts = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
    tid = "5b8aa1cfd0e34f7a9c2b6d15e0473f88"

    def story() -> list[dict]:
        return [
            trace_event(trace_id=tid, timestamp=ts, name="decision"),
            events_mod.observation_event(
                obs_id="aaaa1111bbbb2222", trace_id=tid, name="agent",
                obs_type="AGENT", start=ts, end=ts,
            ),
            generation_event(obs_id="cccc3333dddd4444", trace_id=tid, name="llm",
                             start=ts, end=ts, model="m", usage_details={},
                             cost_details={}),
            score_event(score_id="s1", name="quality", value=1,
                        data_type="NUMERIC", timestamp=ts, trace_id=tid),
        ]

    with writepath.use_spool_write_path(path_name):
        rich = count_spool(_otlp_spool(tmp_path / "rich", story()))
        monkeypatch.setattr(events_mod, "RICH_OBSERVATION_TYPES", False)
        degraded = count_spool(_otlp_spool(tmp_path / "degraded", story()))

    assert rich == degraded
