"""The Langfuse read client — verify split (Ring 2, #33).

Auth + paginated GET of scores/traces. These lock pagination-following and the
v2/legacy scores-endpoint probe without a live server, by faking the shared
``request_retry`` the read client rides on.
"""

from __future__ import annotations

import requests

from langfuse_synth_core import lfread


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code}")
            err.response = self  # type: ignore[attr-defined]
            raise err


def test_auth_from_env_reads_the_standard_vars(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    assert lfread.auth_from_env() == ("pk", "sk")


def test_get_all_scores_follows_pagination(monkeypatch):
    pages = {
        1: {"data": [{"id": "a"}, {"id": "b"}], "meta": {"totalPages": 2}},
        2: {"data": [{"id": "c"}], "meta": {"totalPages": 2}},
    }

    def fake_request(method, url, *, params, auth, timeout, throttle_s=0.0):
        # First call is the scores_path probe (page 1, limit 1); then the real pages.
        if params.get("limit") == 1:
            return _Resp(200, {"data": []})
        return _Resp(200, pages[params["page"]])

    monkeypatch.setattr(lfread, "request_retry", fake_request)
    rows = lfread.get_all_scores("http://x", "user_disagreement")
    assert [r["id"] for r in rows] == ["a", "b", "c"]


def test_scores_path_falls_back_to_legacy_on_404(monkeypatch):
    def fake_request(method, url, *, params, auth, timeout, throttle_s=0.0):
        if url.endswith("/api/public/v2/scores"):
            return _Resp(404)
        return _Resp(200, {"data": []})

    monkeypatch.setattr(lfread, "request_retry", fake_request)
    assert lfread.scores_path("http://x") == "/api/public/scores"


def test_scores_path_prefers_v2_when_served(monkeypatch):
    monkeypatch.setattr(lfread, "request_retry",
                        lambda *a, **k: _Resp(200, {"data": []}))
    assert lfread.scores_path("http://x") == "/api/public/v2/scores"


def test_parse_ts_handles_the_z_suffix():
    ts = lfread.parse_ts("2026-06-04T12:00:00.000Z")
    assert ts.year == 2026 and ts.tzinfo is not None
