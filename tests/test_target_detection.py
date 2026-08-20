"""Target detection — Cloud from the URL, reachability from the target (#211, #213).

The URL half was duplicated byte-for-byte in two kits. The probed half asked which read-API
generation the target served while there were two; the seam reads v4 and only v4 since #213,
so what is left to ask is whether the target answers at all — which is still worth one round
trip, because "cannot read this project, here is why" on a `verify`'s first line beats the
same traceback on every check.
"""

from __future__ import annotations

from langfuse_synth_core import read
from langfuse_synth_core.target import TargetProfile, post_throttle_seconds


class _StubReader:
    def __init__(self):
        self.pings = 0

    def ping(self) -> None:
        self.pings += 1


def test_cloud_is_recognised_from_the_url_and_throttled():
    for url in ("https://cloud.langfuse.com", "https://us.cloud.langfuse.com/"):
        profile = TargetProfile.detect(url)
        assert profile.is_cloud
        assert profile.post_throttle_s > 0
        assert profile.base_url == url.rstrip("/")


def test_self_hosted_is_not_throttled():
    profile = TargetProfile.detect("http://langfuse.internal:3000/")
    assert not profile.is_cloud
    assert profile.post_throttle_s == 0.0
    assert post_throttle_seconds("http://langfuse.internal:3000") == 0.0


def test_detection_makes_no_request_and_leaves_reachability_unknown():
    """Unknown is a third state, not a default: a kit that only writes never pays for the
    probe, and the label must not claim an answer nobody asked for."""
    profile = TargetProfile.detect("https://cloud.langfuse.com")
    assert profile.reachable is None
    assert profile.label == "Langfuse Cloud"


def test_a_target_that_answers_is_named_in_the_label():
    profile = TargetProfile.detect("https://cloud.langfuse.com").resolved(_StubReader())
    assert profile.reachable is True
    assert profile.label == "Langfuse Cloud, v4 read APIs"


def test_a_self_hosted_target_reads_the_same_way():
    """There is no generation left to recognise (portal #213) — a self-hosted host is read
    through the same v4 APIs, and the label differs only in the host half."""
    profile = TargetProfile.detect("http://localhost:3000").resolved(_StubReader())
    assert profile.label == "self-hosted Langfuse, v4 read APIs"


def test_resolving_twice_asks_once():
    """A `verify` resolves on the way into its first read; every later read must be free."""
    resolved = TargetProfile.detect("https://cloud.langfuse.com").resolved(_StubReader())

    class _Exploding:
        def ping(self):
            raise AssertionError("probed a target that already knew")

    assert resolved.resolved(_Exploding()) is resolved


def test_the_reader_a_profile_builds_inherits_its_throttle_and_auth(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    profile = TargetProfile.detect("https://cloud.langfuse.com").resolved(_StubReader())
    reader = profile.reader()

    assert reader.throttle == profile.post_throttle_s
    assert reader.auth == ("pk", "sk")


def test_an_unresolved_profile_probes_through_the_reader_it_builds(monkeypatch):
    """The end-to-end path a kit actually takes: detect, resolve, and the seam's own ping is
    what answers — on a **current** endpoint. The probe used to call a deprecated one, which
    made the reachability check itself the last legacy call in the stack (portal #213)."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    calls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"data": [], "meta": {}}

    def fake_request(method, url, **kw):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(read, "request_retry", fake_request)

    profile = TargetProfile.detect("https://cloud.langfuse.com").resolved()

    assert profile.reachable is True
    assert calls == ["https://cloud.langfuse.com/api/public/v2/observations"]


def test_an_unreadable_target_reports_its_reason_rather_than_raising(monkeypatch):
    """A `verify` is a report. Bad keys or a wrong host must come back as failed checks
    with the reason on each line, not as a traceback in place of the report — so the
    profile stays unresolved and each read fails inside its own check (portal #211)."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    class _Resp:
        status_code = 403

        def json(self):
            return {}

    monkeypatch.setattr(read, "request_retry", lambda *a, **k: _Resp())

    profile, reason = TargetProfile.detect("https://cloud.langfuse.com").try_resolve()

    assert profile.reachable is None
    assert "403" in reason
    assert profile.label == "Langfuse Cloud"     # says nothing it does not know


def test_try_resolve_is_a_no_op_on_a_reachable_target():
    profile, reason = TargetProfile.detect("http://localhost:3000").try_resolve(_StubReader())
    assert profile.reachable is True and reason == ""
