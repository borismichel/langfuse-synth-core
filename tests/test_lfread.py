"""The raw-REST primitives that survive the v4 read migration (#208, retired down in #211).

What is left in ``lfread`` is auth, one authenticated GET, and timestamp parsing — the
endpoints the migration left alone are read with these. Everything with a generation to
remap lives in :mod:`langfuse_synth_core.read` and is tested in ``test_read_seam.py``.
"""

from __future__ import annotations

import pytest

from langfuse_synth_core import lfread


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.HTTPError(f"{self.status_code}")
            err.response = self  # type: ignore[attr-defined]
            raise err


def test_auth_from_env_reads_the_standard_vars(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    assert lfread.auth_from_env() == ("pk", "sk")


def test_get_json_authenticates_and_raises_on_a_bad_status(monkeypatch):
    seen = {}

    def fake_request(method, url, *, params=None, auth=None, timeout=30, throttle_s=0.0):
        seen.update(url=url, params=params, auth=auth)
        return _Resp(200, {"data": [{"id": "q1"}]})

    monkeypatch.setattr(lfread, "request_retry", fake_request)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    body = lfread.get_json("http://x/", "/api/public/annotation-queues", {"limit": 100})

    assert body["data"] == [{"id": "q1"}]
    assert seen["url"] == "http://x/api/public/annotation-queues"
    assert seen["params"] == {"limit": 100}
    assert seen["auth"] == ("pk", "sk")

    monkeypatch.setattr(lfread, "request_retry",
                        lambda *a, **k: _Resp(500, {"message": "boom"}))
    with pytest.raises(Exception):
        lfread.get_json("http://x", "/api/public/annotation-queues")


def test_the_legacy_scores_path_probe_is_gone():
    """It probed `/v2/scores` and fell back to `/scores`; v4 removes both (#208)."""
    assert not hasattr(lfread, "scores_path")


def test_the_legacy_score_row_compatibility_front_is_retired():
    """`get_all_scores` rendered the seam's rows back into the deprecated dict shape so a
    not-yet-rewired kit kept working. All three kits read `reader.scores(...)` now (#211),
    so the shim is gone — and with it the categorical score that reported `value: 0`
    beside its label. This is the breaking change behind core v3.0.0."""
    assert not hasattr(lfread, "get_all_scores")


def test_parse_ts_handles_the_z_suffix():
    ts = lfread.parse_ts("2026-06-04T12:00:00.000Z")
    assert ts.year == 2026 and ts.tzinfo is not None
