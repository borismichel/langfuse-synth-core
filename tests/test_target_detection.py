"""Target detection — Cloud from the URL, the read-API generation from the target (#211).

The URL half was duplicated byte-for-byte in two kits; the generation half is new, and it
is the acceptance criterion "target detection recognises a v4 host". A host name cannot
answer that question — Cloud cuts over on 2026-11-16 and a self-hosted target whenever its
operator upgrades it — so it is probed, and the probe is what these tests pin.
"""

from __future__ import annotations

from langfuse_synth_core import read
from langfuse_synth_core.target import TargetProfile, post_throttle_seconds


class _StubReader:
    def __init__(self, generation: str):
        self.read_api = generation
        self.probes = 0


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


def test_detection_makes_no_request_and_leaves_the_generation_unknown():
    """Unknown is a third state, not a default: a kit that only writes never pays for the
    probe, and `is_v4` must not claim an answer nobody asked for."""
    profile = TargetProfile.detect("https://cloud.langfuse.com")
    assert profile.read_api is None
    assert not profile.is_v4
    assert profile.label == "Langfuse Cloud"


def test_a_v4_host_is_recognised_and_named_in_the_label():
    profile = TargetProfile.detect("https://cloud.langfuse.com").resolved(_StubReader(read.V4))
    assert profile.is_v4
    assert profile.read_api == read.V4
    assert profile.label == "Langfuse Cloud, v4 read APIs"


def test_a_legacy_host_is_recognised_too():
    profile = TargetProfile.detect("http://localhost:3000").resolved(_StubReader(read.LEGACY))
    assert not profile.is_v4
    assert profile.label == "self-hosted Langfuse, legacy read APIs"


def test_resolving_twice_asks_once():
    """A `verify` resolves on the way into its first read; every later read must be free."""
    resolved = TargetProfile.detect("https://cloud.langfuse.com").resolved(_StubReader(read.V4))

    class _Exploding:
        @property
        def read_api(self):
            raise AssertionError("probed a target that already knew")

    assert resolved.resolved(_Exploding()) is resolved


def test_the_reader_a_profile_builds_inherits_its_throttle_and_generation(monkeypatch):
    """A reader built off a resolved profile must not re-probe — the profile already paid."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    def explode(*a, **k):
        raise AssertionError("the reader probed despite a resolved profile")

    monkeypatch.setattr(read, "request_retry", explode)

    profile = TargetProfile.detect("https://cloud.langfuse.com").resolved(_StubReader(read.V4))
    reader = profile.reader()

    assert reader.throttle == profile.post_throttle_s
    assert reader.read_api == read.V4
    assert reader.auth == ("pk", "sk")


def test_an_unresolved_profile_probes_through_the_reader_it_builds(monkeypatch):
    """The end-to-end path a kit actually takes: detect, resolve, and the seam's own probe
    is what answers — one deprecated endpoint, and a 404 means the target has cut over."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    calls = []

    class _Resp:
        status_code = 404

        def json(self):
            return {}

    def fake_request(method, url, **kw):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr(read, "request_retry", fake_request)

    profile = TargetProfile.detect("https://cloud.langfuse.com").resolved()

    assert profile.is_v4
    assert calls and calls[0].endswith(read._PROBE_PATH)


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

    assert profile.read_api is None
    assert not profile.is_v4
    assert "403" in reason
    assert profile.label == "Langfuse Cloud"     # says nothing it does not know


def test_try_resolve_is_a_no_op_on_a_reachable_target(monkeypatch):
    profile, reason = TargetProfile.detect("http://localhost:3000").try_resolve(
        _StubReader(read.V4))
    assert profile.is_v4 and reason == ""
