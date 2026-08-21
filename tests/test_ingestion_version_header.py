"""Every write carries ``x-langfuse-ingestion-version: 4`` (portal #213).

This is not a latency optimisation, and that is why it gets a suite of its own. Langfuse
processes exported spans on two paths. **Without** the header a v4 target files the write on
the legacy read path, where it is invisible to every v4 query endpoint and every v4
dashboard — while the legacy endpoints answer it perfectly happily. So the failure mode is a
deploy that looks entirely healthy, verifies green against a legacy read, and shows an empty
project to anyone looking at it through v4. Observed on Cloud, 2026-08-20.

Core has always set it. Nothing asserted that it *keeps* setting it, on every writer, which
is what this file is for: it enumerates the writers rather than sampling one, so a new write
path that forgets the header fails here instead of in a demo.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


from langfuse_synth_core import ingestion
from langfuse_synth_core.seed import ingest as ingest_mod
from langfuse_synth_core.seed.events import score_event, span_event, trace_event
from langfuse_synth_core.seed.ingest import Ingestor

TS = datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)
TID = "5b8aa1cfd0e34f7a9c2b6d15e0473f88"
HEADER = "x-langfuse-ingestion-version"


class _Recorder:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, url, json=None, auth=None, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}})
        return type("R", (), {"status_code": 200, "text": "", "json": lambda self: {}})()


def test_the_header_name_and_value_are_what_langfuse_selects_v4_on():
    assert ingestion.INGESTION_VERSION_HEADER == HEADER
    assert ingestion.INGESTION_VERSION == "4"


def test_every_post_an_import_makes_carries_it(tmp_path: Path, monkeypatch):
    """Both endpoints, in one import: the OTLP export and the score envelopes. Asserted over
    *every* call rather than a chosen one, so a third endpoint added later is covered by
    construction."""
    post = _Recorder()
    monkeypatch.setattr(ingest_mod.requests, "post", post)

    ing = Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk",
                   spool_path=tmp_path / "events.ndjson")
    ing.open_spool()
    ing.extend([
        trace_event(trace_id=TID, timestamp=TS, name="decision"),
        span_event(obs_id="aaaa1111bbbb2222", trace_id=TID, name="agent", start=TS, end=TS),
        score_event(score_id="s1", name="quality", value=1, data_type="NUMERIC",
                    timestamp=TS, trace_id=TID),
    ])
    ing.close_spool()
    ing.import_spool()

    assert {c["url"] for c in post.calls} == {
        "http://lf.local/api/public/otel/v1/traces",
        "http://lf.local/api/public/ingestion",
    }
    for call in post.calls:
        assert call["headers"].get(HEADER) == "4", call["url"]


def test_the_in_memory_flush_carries_it_too(monkeypatch):
    """The other send path — the one the backdate probe takes."""
    post = _Recorder()
    monkeypatch.setattr(ingest_mod.requests, "post", post)

    ing = Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk")
    ing.add(trace_event(trace_id=TID, timestamp=TS, name="decision"))
    ing.flush()

    assert post.calls and all(c["headers"].get(HEADER) == "4" for c in post.calls)


def test_the_readiness_write_ping_carries_it(monkeypatch):
    """The Companion Adapter's liveness probe writes nothing, but it is still a write — a
    probe that took the legacy path would prove reachability of the wrong path."""
    post = _Recorder()
    monkeypatch.setattr(ingest_mod.requests, "post", post)

    Ingestor(base_url="http://lf.local", public_key="pk", secret_key="sk").write_ping()

    assert len(post.calls) == 1
    assert post.calls[0]["headers"].get(HEADER) == "4"


def test_the_live_emission_seam_carries_it_through_the_sdk(monkeypatch):
    """The wall-clock writer, on the far side of the determinism line. It cannot share the
    ``Ingestor``'s transport — it goes through the Langfuse SDK's own OTLP exporter — so it
    reaches the same header through ``additional_headers`` (SDK floor 4.14)."""
    import sys

    from langfuse_synth_core.live import emit

    built: dict = {}

    class _SDK:
        def __init__(self, **kw):
            built.update(kw)

    monkeypatch.setitem(sys.modules, "langfuse",
                        type("m", (), {"Langfuse": _SDK, "propagate_attributes": None}))
    assert emit.LiveEmitter("http://lf.local", public_key="pk", secret_key="sk").client
    assert built["additional_headers"] == {HEADER: "4"}


def test_no_writer_is_left_out_of_this_file():
    """The suite's own guard. ``Ingestor._post`` is the single choke point every spool-side
    write goes through, so the header is set once — but only as long as that stays true. If
    a second requests.post appears in the module, this fails and someone has to decide
    whether it is a write and whether this file covers it."""
    import inspect

    source = inspect.getsource(ingest_mod)
    assert source.count("requests.post(") == 1, (
        "a second write path appeared in seed/ingest.py — does it set "
        f"{HEADER}, and is it asserted above?"
    )
