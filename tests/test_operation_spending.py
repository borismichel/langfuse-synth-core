"""The public Adapter boundary against an actual, credential-free HTTP responder."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from langfuse_synth_core.companion.adapter import CompanionAdapter

INSTANCE = "bde5ea54-d6dc-4e43-95a4-3696886c9620"
TOKEN = "a" * 64


@pytest.fixture
def bridge(monkeypatch):
    replies = [
        (
            200,
            {
                "allowed": True,
                "provider": "openai",
                "source": "shared",
                "checked_at": "2026-10-23T12:00:00+00:00",
                "accounting": "hourly-estimate",
                "scope": "shared-company",
            },
        )
    ]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(
                (
                    self.path,
                    dict(self.headers),
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            status, body = replies[0]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kw):
        if host == "caddy" and port == 8081:
            return original("127.0.0.1", server.server_port, *args, **kw)
        return original(host, port, *args, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setenv("PORTAL_LIVE_INSTANCE_ID", INSTANCE)
    monkeypatch.setenv(
        "DEPOT_OPERATION_CHECK_URL",
        f"http://caddy:8081/api/v1/live-assets/{INSTANCE}/operation-permit",
    )
    monkeypatch.setenv("DEPOT_OPERATION_TOKEN", TOKEN)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    yield replies, requests
    server.shutdown()
    server.server_close()
    thread.join()


def test_paid_dispatch_requires_a_fresh_check(bridge):
    from langfuse_synth_core.companion.spending import SpendingDenied

    replies, requests = bridge
    adapter = CompanionAdapter(SimpleNamespace())
    assert requests == []  # construction cannot check or call a provider
    decision = adapter.check_spending()
    assert (decision.provider, decision.source, decision.scope) == (
        "openai",
        "shared",
        "shared-company",
    )
    assert requests[0][2] == {"provider": "openai"}
    assert requests[0][1]["X-Depot-Operation-Token"] == TOKEN
    replies[:] = [(409, {"reason": "cap_exhausted"})]
    with pytest.raises(SpendingDenied, match="cap_exhausted"):
        adapter.check_spending("openai")
    assert len(requests) == 2
    assert TOKEN not in repr(adapter) + repr(decision)


@pytest.mark.parametrize(
    "status,body",
    [
        (200, {"allowed": True}),
        (302, {}),
        (503, {"reason": TOKEN}),
        (
            200,
            {
                "allowed": True,
                "provider": "anthropic",
                "source": "shared",
                "checked_at": "2026-10-23T12:00:00+00:00",
                "accounting": "hourly-estimate",
                "scope": "shared-company",
            },
        ),
        (200, {"padding": "x" * 5000}),
    ],
)
def test_unverifiable_responses_fail_closed_without_exposing_body(bridge, status, body):
    from langfuse_synth_core.companion.spending import SpendingUnavailable

    replies, requests = bridge
    replies[:] = [(status, body)]
    with pytest.raises(SpendingUnavailable) as error:
        CompanionAdapter(SimpleNamespace()).check_spending("openai")
    assert str(error.value) == "check_unavailable"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/",
        "http://caddy:8081/api/v1/admin",
        f"http://caddy:8081/api/v1/live-assets/{INSTANCE}/operation-permit?x=1",
    ],
)
def test_caller_cannot_redirect_capability_to_another_destination(bridge, monkeypatch, url):
    from langfuse_synth_core.companion.spending import SpendingUnavailable

    monkeypatch.setenv("DEPOT_OPERATION_CHECK_URL", url)
    with pytest.raises(SpendingUnavailable):
        CompanionAdapter(SimpleNamespace()).check_spending()
    assert bridge[1] == []


def test_missing_capability_and_unknown_provider_fail_closed(monkeypatch):
    from langfuse_synth_core.companion.spending import SpendingUnavailable

    monkeypatch.delenv("DEPOT_OPERATION_TOKEN", raising=False)
    for provider in (None, "caller-supplied"):
        with pytest.raises(SpendingUnavailable):
            CompanionAdapter(SimpleNamespace()).check_spending(provider)


def test_manifest_accepts_only_versioned_spending_opt_in():
    import jsonschema
    from pathlib import Path

    schema = json.loads(
        (
            Path(__file__).parents[1] / "src/langfuse_synth_core/authoring/usecase.schema.json"
        ).read_text()
    )
    component = schema["properties"]["live_components"]["items"]
    sample = {
        "name": "ui",
        "command": "serve",
        "port": 8080,
        "requires_secrets": [],
        "spending_check": {"version": 1},
    }
    base = component
    jsonschema.validate(sample, base)
    for value in ({"version": 2}, {}, {"version": 1, "url": "http://elsewhere"}):
        sample["spending_check"] = value
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(sample, base)


def test_transport_failure_is_not_permission(bridge, monkeypatch):
    from langfuse_synth_core.companion import SpendingUnavailable

    # Unbound loopback port is an actual refused connection, not a transport mock.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    original = socket.getaddrinfo
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, target, *args, **kw: original("127.0.0.1", port, *args, **kw),
    )
    try:
        with pytest.raises(SpendingUnavailable, match="check_unavailable"):
            CompanionAdapter(SimpleNamespace()).check_spending("openai")
        assert bridge[1] == []
    finally:
        sock.close()


def test_invalid_primary_binding_is_safe_unavailability(bridge, monkeypatch):
    from langfuse_synth_core.companion import SpendingUnavailable
    monkeypatch.setenv('LLM_PROVIDER', 'invalid-provider-fixture')
    with pytest.raises(SpendingUnavailable, match='check_unavailable'):
        CompanionAdapter(SimpleNamespace()).check_spending()
    assert bridge[1] == []
