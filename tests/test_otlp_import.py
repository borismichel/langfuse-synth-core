"""Importing an OTLP Spool (portal #206) — where it goes, and what happens when it fails.

Two behaviours here are new, and both are consequences of OTLP rather than choices:

* **Delivery semantics.** Batch ingestion reported per-event errors inside a 207 and core
  raised on them. OTLP has no equivalent: an export can succeed at the HTTP layer and still
  reject spans, reported in ``partialSuccess``. That is the replacement error contract, and
  it is tested rather than assumed.
* **Non-resumability.** OTLP has no idempotent upsert — identical re-posts duplicate
  observations where batch ingestion produced one row. So an OTLP import may not be re-run
  over a partly written project; it must fail and name the recovery.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import langfuse_synth_core.seed.ingest as ingest_mod
from langfuse_synth_core.seed.events import score_event, span_event, trace_event
from langfuse_synth_core.seed.ingest import Ingestor, IngestError, NonResumableImportError

TS = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
TID = "5b8aa1cfd0e34f7a9c2b6d15e0473f88"


class _Recorder:
    """Stands in for the network: records every POST and replies as told."""

    def __init__(self, replies=None):
        self.calls: list[dict] = []
        self._replies = list(replies or [])

    def __call__(self, url, json=None, auth=None, headers=None, timeout=None):
        self.calls.append({"url": url, "body": json, "auth": auth, "headers": headers or {}})
        reply = self._replies.pop(0) if self._replies else (200, {})
        status, body = reply
        return type("R", (), {"status_code": status, "text": "", "json": lambda self: body})()


def _spooled(tmp_path: Path, events: list[dict]) -> Ingestor:
    ing = Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk",
                   spool_path=tmp_path / "events.ndjson")
    ing.open_spool()
    ing.extend(events)
    ing.close_spool()
    return ing


def _one_trace() -> list[dict]:
    return [
        trace_event(trace_id=TID, timestamp=TS, name="credit_decision"),
        span_event(obs_id="aaaa1111bbbb2222", trace_id=TID, name="agent", start=TS, end=TS),
    ]


def test_spans_go_to_the_otlp_endpoint_and_scores_keep_the_ingestion_endpoint(
        tmp_path: Path, monkeypatch):
    post = _Recorder()
    monkeypatch.setattr(ingest_mod.requests, "post", post)
    ing = _spooled(tmp_path, _one_trace() + [
        score_event(score_id="s1", name="quality", value=1, data_type="NUMERIC",
                    timestamp=TS, trace_id=TID)])
    assert ing.import_spool() == 3

    by_url = {c["url"]: c for c in post.calls}
    otel = by_url["http://lf.local/api/public/otel/v1/traces"]
    assert [s["spanId"] for s in otel["body"]["resourceSpans"][0]["scopeSpans"][0]["spans"]] \
        == [ingest_mod.otlp.trace_root_span_id(TID), "aaaa1111bbbb2222"]
    assert otel["auth"] == ("pk", "sk")
    assert otel["headers"]["x-langfuse-ingestion-version"] == "4"

    ingestion = by_url["http://lf.local/api/public/ingestion"]
    assert [e["type"] for e in ingestion["body"]["batch"]] == ["score-create"]


def test_rejected_spans_are_raised_even_though_the_export_returned_200(
        tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ingest_mod.requests, "post", _Recorder(
        [(200, {"partialSuccess": {"rejectedSpans": 2, "errorMessage": "bad trace id"}})]))
    ing = _spooled(tmp_path, _one_trace())
    with pytest.raises(IngestError, match="2 span"):
        ing.import_spool()


def test_a_clean_export_is_not_mistaken_for_a_partial_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ingest_mod.requests, "post", _Recorder(
        [(200, {"partialSuccess": {}})]))
    assert _spooled(tmp_path, _one_trace()).import_spool() == 2


def test_a_second_import_refuses_rather_than_doubling_every_observation(
        tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ingest_mod.requests, "post", _Recorder())
    ing = _spooled(tmp_path, _one_trace())
    ing.import_spool()

    again = Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk",
                     spool_path=tmp_path / "events.ndjson")
    with pytest.raises(NonResumableImportError) as exc:
        again.import_spool()
    assert "non-resumable" in str(exc.value).lower()
    assert "clear" in str(exc.value).lower()  # names the recovery procedure


def test_an_interrupted_import_refuses_to_resume(tmp_path: Path, monkeypatch):
    def wedge(*a, **k):
        raise ingest_mod.requests.RequestException("connection reset")

    monkeypatch.setattr(ingest_mod.requests, "post", wedge)
    ing = _spooled(tmp_path, _one_trace())
    ing.max_retries = 1
    with pytest.raises(IngestError):
        ing.import_spool()

    monkeypatch.setattr(ingest_mod.requests, "post", _Recorder())
    resumed = Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk",
                       spool_path=tmp_path / "events.ndjson")
    with pytest.raises(NonResumableImportError):
        resumed.import_spool()


def test_re_importing_after_clearing_langfuse_is_the_documented_way_through(
        tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ingest_mod.requests, "post", _Recorder())
    ing = _spooled(tmp_path, _one_trace())
    ing.import_spool()

    again = Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk",
                     spool_path=tmp_path / "events.ndjson")
    assert again.import_spool(confirm_cleared=True) == 2


def test_regenerating_the_spool_clears_the_refusal(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ingest_mod.requests, "post", _Recorder())
    _spooled(tmp_path, _one_trace()).import_spool()
    # `generate-spool` re-running is a clean slate: a fresh Spool may be imported again.
    fresh = _spooled(tmp_path, _one_trace())
    assert fresh.import_spool() == 2


def test_a_scores_only_spool_stays_idempotently_re_runnable(tmp_path: Path, monkeypatch):
    """The guard is about OTLP's missing upsert, so it is scoped to Spools that carry spans.
    `score-create` envelopes carry deterministic ids and upsert, so a Spool of nothing but
    scores has nothing for a re-post to duplicate and is not locked."""
    from langfuse_synth_core.seed.events import score_event

    monkeypatch.setattr(ingest_mod.requests, "post", _Recorder())
    scores = [score_event(score_id="a1b2c3d4e5f60718", name="q", value=1,
                          data_type="NUMERIC", timestamp=TS, trace_id=TID)]
    _spooled(tmp_path, scores).import_spool()
    again = Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk",
                     spool_path=tmp_path / "events.ndjson")
    assert again.import_spool() == 1


def test_the_write_ping_exercises_the_otlp_endpoint_without_emitting_a_span(monkeypatch):
    post = _Recorder()
    monkeypatch.setattr(ingest_mod.requests, "post", post)
    Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk").write_ping()
    assert post.calls[0]["url"] == "http://lf.local/api/public/otel/v1/traces"
    assert post.calls[0]["body"] == {"resourceSpans": []}


def test_the_marker_never_lands_in_the_spool_itself(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ingest_mod.requests, "post", _Recorder())
    ing = _spooled(tmp_path, _one_trace())
    ing.import_spool()
    lines = (tmp_path / "events.ndjson").read_text(encoding="utf-8").splitlines()
    assert all("spanId" in json.loads(line) for line in lines)


def test_a_spool_that_opens_with_a_score_is_still_recognised_as_otlp(
        tmp_path: Path, monkeypatch):
    """Scores stay ingestion envelopes on both paths, so the first line of an OTLP Spool is
    not necessarily a span — and misreading it would quietly disable the guard."""
    monkeypatch.setattr(ingest_mod.requests, "post", _Recorder())
    ing = _spooled(tmp_path, [
        score_event(score_id="s1", name="quality", value=1, data_type="NUMERIC",
                    timestamp=TS, trace_id=TID),
        *_one_trace(),
    ])
    ing.import_spool()
    again = Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk",
                     spool_path=tmp_path / "events.ndjson")
    with pytest.raises(NonResumableImportError):
        again.import_spool()
