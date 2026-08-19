"""Backdate probe id strategy — data-model middle field (Ring 2, #33).

The probe trace is throwaway (tagged ``synth-probe``), so its id must be UNIQUE per run,
nonce-salted — NOT deterministic — so it never collides across deployments or lands on a
tombstoned id on a re-used project. The kits' own probe tests share this guard; here it is
locked at the lib level too.
"""

from __future__ import annotations

import pytest

import langfuse_synth_core.probe as probe_mod
from langfuse_synth_core import read as read_mod
from langfuse_synth_core.probe import probe_ids
from langfuse_synth_core.rng import Rng
from langfuse_synth_core.seed import writepath
from langfuse_synth_core.seed.otlp import trace_root_span_id


def test_probe_ids_are_unique_per_run_with_the_same_seed():
    rng = Rng(42)
    t1, o1 = probe_ids(rng)
    t2, o2 = probe_ids(rng)
    assert t1 != t2 and o1 != o2


def test_probe_ids_keep_w3c_trace_context_widths():
    tid, obs = probe_ids(Rng(42))
    assert len(tid) == 32 and int(tid, 16) >= 0   # 16-byte trace id, valid hex
    assert len(obs) == 16 and int(obs, 16) >= 0   # 8-byte observation id, valid hex


def test_probe_ids_are_not_the_fixed_deterministic_id():
    # The pre-fix collision id (seed 42, deterministic) — must never be reproduced.
    old_collision = "ebc16bd0f806178ea49c5e8d0d546015"
    for _ in range(50):
        tid, _obs = probe_ids(Rng(42))
        assert tid != old_collision


# --- the probe on both write paths (portal #206) ---------------------------
# The probe is the migration's smoke test: it seeds ONE backdated trace and reads it back to
# assert the timestamp round-tripped. Backdating is the single riskiest property of the
# whole Spool, and the OTLP path is where it could silently die — so the probe has to run
# on whichever path the deployment is about to seed on.

def _capture_probe_run(monkeypatch) -> dict:
    """Run the probe against a fake host; return what it posted and what it asked back."""
    posted: dict = {"calls": []}

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr(probe_mod, "assert_demo_project", lambda *a, **k: ("pid", "demo"))
    monkeypatch.setattr(probe_mod.time, "sleep", lambda _s: None)

    import langfuse_synth_core.seed.ingest as ingest_mod

    def fake_post(url, json=None, auth=None, headers=None, timeout=None):
        posted["calls"].append({"url": url, "body": json})
        return type("R", (), {"status_code": 200, "text": "",
                              "json": lambda self: {}})()

    monkeypatch.setattr(ingest_mod.requests, "post", fake_post)

    # The read-back goes through the read seam (portal #208), so the fake answers whichever
    # generation the target is pretending to serve — the probe never names an endpoint.
    def fake_read(method, url, *, params=None, auth=None, timeout=30, throttle_s=0.0, **kw):
        posted.setdefault("read_urls", []).append(url)
        legacy = posted.get("read_api", "legacy") == "legacy"
        if url.endswith("/api/public/traces"):                       # the generation probe
            return _read_resp(200 if legacy else 404, {"data": [], "meta": {"totalPages": 1}})
        if "/api/public/traces/" in url:
            if not legacy:
                return _read_resp(404, {})
            return _read_resp(200, {
                "id": url.rsplit("/", 1)[-1], "timestamp": posted["expect_iso"],
                "observations": [{"id": "x", "startTime": posted["expect_iso"]}]})
        if url.endswith("/api/public/v2/observations"):
            return _read_resp(200 if not legacy else 404, {
                "data": [{"id": "x", "traceId": params.get("traceId"), "type": "SPAN",
                          "name": "probe", "startTime": posted["expect_iso"]}],
                "meta": {}})
        if url.endswith("/api/public/v3/scores"):
            return _read_resp(200, {"data": [], "meta": {"limit": 100}})
        return _read_resp(404, {})

    monkeypatch.setattr(read_mod, "request_retry", fake_read)
    return posted


def _read_resp(status, payload):
    return type("R", (), {"status_code": status, "json": lambda self: payload})()


def _run(posted, monkeypatch) -> bool:
    """The host echoes back whatever timestamp the probe sent, so a PASS means the probe
    read the backdate it wrote — and a collapsed timestamp would fail."""
    real_now = probe_mod.now_utc()
    from datetime import timedelta

    posted["expect_iso"] = (real_now - timedelta(days=3, hours=2)).isoformat()
    monkeypatch.setattr(probe_mod, "now_utc", lambda: real_now)
    return probe_mod.run_backdate_probe("http://lf.local", "demo", 42, log=lambda _m: None)


def test_the_probe_passes_on_the_batch_path(monkeypatch):
    posted = _capture_probe_run(monkeypatch)
    with writepath.use_spool_write_path(writepath.BATCH):
        assert _run(posted, monkeypatch) is True
    urls = {c["url"] for c in posted["calls"]}
    assert urls == {"http://lf.local/api/public/ingestion"}


@pytest.mark.parametrize("read_api", ["legacy", "v4"])
def test_the_probe_reads_its_backdate_back_on_either_read_api(monkeypatch, read_api):
    """The probe is the migration's smoke test, so it must keep working on a target that has
    cut over — where `/api/public/traces/{id}` is a 404 and the trace is a set of
    observations. It reads through the seam and never names an endpoint itself (#208)."""
    posted = _capture_probe_run(monkeypatch)
    posted["read_api"] = read_api
    with writepath.use_spool_write_path(writepath.OTLP):
        assert _run(posted, monkeypatch) is True

    read_urls = " ".join(posted["read_urls"])
    assert ("/api/public/v2/observations" in read_urls) == (read_api == "v4")


def test_the_probe_writes_a_backdated_span_over_otlp(monkeypatch):
    posted = _capture_probe_run(monkeypatch)
    with writepath.use_spool_write_path(writepath.OTLP):
        assert _run(posted, monkeypatch) is True

    export = next(c for c in posted["calls"]
                  if c["url"] == "http://lf.local/api/public/otel/v1/traces")
    spans = export["body"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = next(s for s in spans if s["spanId"] == trace_root_span_id(s["traceId"]))
    # The probe's whole point: the span goes out stamped in the past, not at wall-clock.
    from datetime import datetime, timedelta, timezone

    sent = datetime.fromtimestamp(int(root["startTimeUnixNano"]) / 1e9, tz=timezone.utc)
    assert probe_mod.now_utc() - sent > timedelta(days=3)


def test_the_probe_reports_a_collapsed_timestamp_as_a_failure(monkeypatch):
    posted = _capture_probe_run(monkeypatch)
    with writepath.use_spool_write_path(writepath.OTLP):
        real_now = probe_mod.now_utc()
        posted["expect_iso"] = real_now.isoformat()   # host normalised it onto today
        monkeypatch.setattr(probe_mod, "now_utc", lambda: real_now)
        assert probe_mod.run_backdate_probe("http://lf.local", "demo", 42,
                                            log=lambda _m: None) is False
