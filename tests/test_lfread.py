"""The Langfuse read client — verify split (Ring 2, #33), now fronting the read seam (#208).

Auth + paginated GET of scores/traces. These lock pagination-following and the shape kits
read today without a live server, by faking the transport the read client rides on.

The v2-vs-legacy scores-endpoint probe these tests used to pin is **gone**: platform v4
`404`s both of those endpoints, so choosing between them is no longer a meaningful
question. :mod:`langfuse_synth_core.read` resolves the API *generation* instead, and
``get_all_scores`` is now a thin compatibility front onto it.
"""

from __future__ import annotations

from langfuse_synth_core import lfread, read


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


def test_get_all_scores_follows_pagination(monkeypatch):
    pages = {
        1: {"data": [{"id": "a", "value": 1}, {"id": "b", "value": 1}],
            "meta": {"totalPages": 2}},
        2: {"data": [{"id": "c", "value": 1}], "meta": {"totalPages": 2}},
    }

    def fake_request(method, url, *, params=None, auth=None, timeout=30, throttle_s=0.0):
        params = params or {}
        if url.endswith("/api/public/traces"):      # the read-generation probe
            return _Resp(200, {"data": [], "meta": {"totalPages": 1}})
        return _Resp(200, pages[params["page"]])

    monkeypatch.setattr(read, "request_retry", fake_request)
    rows = lfread.get_all_scores("http://x", "user_disagreement")
    assert [r["id"] for r in rows] == ["a", "b", "c"]


def test_get_all_scores_serves_the_legacy_row_shape_off_a_v4_target(monkeypatch):
    """A kit that has not been rewired yet still reads `value` / `stringValue` / `traceId`.

    On a cut-over target those columns do not exist — v3 answers one typed `value` and a
    `subject` object — so the compatibility front rebuilds them. This is what keeps the
    depot demoable while the kits move one at a time (#210/#211).
    """

    def fake_request(method, url, *, params=None, auth=None, timeout=30, throttle_s=0.0):
        if url.endswith("/api/public/traces"):
            return _Resp(404, {"message": "not found"})
        if url.endswith("/api/public/v3/scores"):
            return _Resp(200, {"data": [
                {"id": "s1", "name": "resolution", "dataType": "CATEGORICAL",
                 "value": "escalated", "timestamp": "2026-06-04T12:00:00.000Z",
                 "comment": "hand-off", "subject": {"kind": "trace", "id": "t1"}},
            ], "meta": {"limit": 100}})
        raise AssertionError(f"unexpected read: {url}")

    monkeypatch.setattr(read, "request_retry", fake_request)
    rows = lfread.get_all_scores("http://x", "resolution")

    assert rows[0]["stringValue"] == "escalated"
    assert rows[0]["traceId"] == "t1"
    assert rows[0]["comment"] == "hand-off"
    assert rows[0]["timestamp"].startswith("2026-06-04T12:00:00")


def test_the_legacy_scores_path_probe_is_gone():
    """It probed `/v2/scores` and fell back to `/scores`; v4 removes both (#208)."""
    assert not hasattr(lfread, "scores_path")


def test_parse_ts_handles_the_z_suffix():
    ts = lfread.parse_ts("2026-06-04T12:00:00.000Z")
    assert ts.year == 2026 and ts.tzinfo is not None
