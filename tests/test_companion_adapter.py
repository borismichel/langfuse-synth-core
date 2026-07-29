"""Companion Adapter contract suite (Spec G · G2, #25/#140).

The synth-core analogue of the portal's ``test_worker_live.py`` seam: it boots the Adapter
against a **fake seeded env + fake provider** (no real Langfuse, no real SDK, no network) and
asserts every row of ``CompanionAdapterContract`` (#25):

    invocation · bind 0.0.0.0:<port> · health < 400 · secret intake (ready clients only) ·
    bidirectional Langfuse client · sentinel -> ready LLM client · graceful shutdown ·
    readiness surface (secret-free, no billable LLM call) · zero brand/scenario knowledge.

Fakes mirror the G1 suite's technique: ``sys.modules`` is monkeypatched so the real
anthropic/openai/langfuse SDKs are never imported, and the raw-REST read/write seams are
patched at their call sites.
"""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from langfuse_synth_core.companion import (
    CompanionAdapter,
    CompanionAdapterContract,
    Invocation,
    ReadinessReport,
    parse_invocation,
)
from langfuse_synth_core.companion import adapter as adapter_mod

BASE_URL = "http://langfuse.seeded.local"


# ---------------------------------------------------------------------------
# Fakes: a seeded env + a provider, with zero real network / SDK
# ---------------------------------------------------------------------------
class _FakeTarget:
    base_url = "http://config-fallback.local"


class _FakeConfig:
    target = _FakeTarget()


class _FakeResp:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.headers = {}
        self.text = ""

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


@pytest.fixture
def seeded_env(monkeypatch):
    """A fully wired fake deployment env: base URL + project keys + a provider key, with the
    legacy provider selection (LLM_PROVIDER/LLM_MODEL unset)."""
    monkeypatch.setenv("LANGFUSE_BASE_URL", BASE_URL)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-seeded")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-seeded")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-resolved-key")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Inject a fake ``anthropic`` SDK that records construction + whether a completion was
    ever requested (to prove readiness makes no billable call)."""
    calls = {"constructed_with": None, "completions": 0}

    class FakeMessages:
        def create(self, **kw):  # a real (billable) completion — must NOT run in readiness
            calls["completions"] += 1
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="hi")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    class FakeAnthropic:
        def __init__(self, api_key=None):
            calls["constructed_with"] = api_key
            self.messages = FakeMessages()

    monkeypatch.setitem(__import__("sys").modules, "anthropic",
                        SimpleNamespace(Anthropic=FakeAnthropic))
    return calls


@pytest.fixture
def fake_langfuse_sdk(monkeypatch):
    """Inject a fake ``langfuse`` SDK capturing the host/key wiring."""
    seen = {}

    class FakeLangfuse:
        def __init__(self, host=None, public_key=None, secret_key=None):
            seen.update(host=host, public_key=public_key, secret_key=secret_key)

    monkeypatch.setitem(__import__("sys").modules, "langfuse",
                        SimpleNamespace(Langfuse=FakeLangfuse))
    return seen


@pytest.fixture
def adapter(seeded_env):
    return CompanionAdapter(
        _FakeConfig(),
        requires_secrets=["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LLM_API_KEY"],
        health_path="/healthz",
        llm_model_default="claude-sonnet-4-6",
    )


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ---------------------------------------------------------------------------
# Row: invocation contract (D3) — exactly --config/--host/--port, no --set
# ---------------------------------------------------------------------------
def test_invocation_parses_the_fixed_launch_string():
    inv = parse_invocation(["--config", "usecase.yaml", "--host", "0.0.0.0", "--port", "8123"])
    assert inv == Invocation(config="usecase.yaml", host="0.0.0.0", port=8123)


def test_invocation_defaults_bind_all_interfaces():
    # The portal always passes --host explicitly, but the default is 0.0.0.0 (bind-all), the
    # value the Caddy wildcard proxy needs — never 127.0.0.1.
    inv = parse_invocation(["--config", "usecase.yaml", "--port", "8000"])
    assert inv.host == "0.0.0.0"


def test_invocation_rejects_a_stray_set_flag():
    # The LAN-165 class of failure: the portal must never append pipeline --set to a live
    # entrypoint. The parser accepts only the three declared flags, so --set exits non-zero.
    with pytest.raises(SystemExit):
        parse_invocation(["--config", "usecase.yaml", "--port", "8000",
                          "--set", "generation.target_traces=800"])


def test_invocation_requires_config():
    with pytest.raises(SystemExit):
        parse_invocation(["--host", "0.0.0.0", "--port", "8000"])


def test_invocation_requires_port():
    # Port is per-deployment (unlike host, which D3 fixes to 0.0.0.0), so it is required —
    # no silent default that could mask a portal that failed to template it.
    with pytest.raises(SystemExit):
        parse_invocation(["--config", "usecase.yaml"])


# ---------------------------------------------------------------------------
# Row: connection identity (D5) — the deployment's own base URL
# ---------------------------------------------------------------------------
def test_base_url_prefers_injected_env_over_config(adapter):
    assert adapter.base_url == BASE_URL  # LANGFUSE_BASE_URL wins, no trailing slash


def test_base_url_falls_back_to_config(monkeypatch):
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    a = CompanionAdapter(_FakeConfig())
    assert a.base_url == "http://config-fallback.local"


# ---------------------------------------------------------------------------
# Row: Langfuse client — bidirectional (read seeded + emit live), bound to conn
# ---------------------------------------------------------------------------
def test_langfuse_sdk_client_bound_to_deployment_connection(adapter, fake_langfuse_sdk):
    client = adapter.langfuse()
    assert fake_langfuse_sdk == {
        "host": BASE_URL, "public_key": "pk-seeded", "secret_key": "sk-seeded",
    }
    assert adapter.langfuse() is client  # cached


def test_ingestor_write_path_bound_to_connection(adapter):
    ing = adapter.ingestor(dry_run=True)
    assert ing.base_url == BASE_URL
    assert (ing.public_key, ing.secret_key) == ("pk-seeded", "sk-seeded")


def test_read_path_hits_the_seeded_connection(adapter, monkeypatch):
    seen = {}

    def fake_request_retry(method, url, **kw):
        seen.update(method=method, url=url, auth=kw.get("auth"))
        return _FakeResp(200, {"data": [{"id": "trace-1"}]})

    monkeypatch.setattr("langfuse_synth_core.lfread.request_retry", fake_request_retry)
    out = adapter.read_json("/api/public/traces", {"limit": 1})
    assert out == {"data": [{"id": "trace-1"}]}
    assert seen["url"].startswith(BASE_URL)
    assert seen["auth"] == ("pk-seeded", "sk-seeded")  # read is authenticated to the project


# ---------------------------------------------------------------------------
# Row: LLM — sentinel -> ready client; legacy Anthropic default (story 38)
# ---------------------------------------------------------------------------
def test_llm_resolves_ready_client_legacy_default(adapter):
    client = adapter.llm()
    assert (client.provider, client.model) == ("anthropic", "claude-sonnet-4-6")
    assert adapter.llm() is client  # cached


def test_llm_binds_without_a_billable_call(adapter, fake_anthropic):
    # Constructing the SDK client is "the LLM binds"; it must not run a completion. `bind()`
    # is the public "construct, don't complete" accessor the readiness probe uses.
    adapter.llm().bind()
    assert fake_anthropic["constructed_with"] == "anthropic-resolved-key"
    assert fake_anthropic["completions"] == 0


# -- per-call model: the boundary amendment surfaced by the second migrated kit (G5, #144) --
# A Surface may let its *user* pick the model per request (a model selector), while provider
# selection and key resolution stay adapter-owned. Split: the adapter resolves WHICH PROVIDER
# and WHICH KEY (deployment-owned); the Surface names WHICH MODEL (scenario-owned). Without
# this the Surface would have to reach past the adapter into the resolution module for its own
# client — bending the Surface to hide a seam that is one argument short.
def test_llm_accepts_a_per_call_model_the_surface_names(adapter):
    client = adapter.llm("some-other-model")
    assert (client.provider, client.model) == ("anthropic", "some-other-model")
    # …and the adapter's own default is untouched by an override elsewhere.
    assert adapter.llm().model == "claude-sonnet-4-6"


def test_llm_caches_per_requested_model(adapter):
    a1, a2 = adapter.llm("model-a"), adapter.llm("model-a")
    assert a1 is a2                      # same request -> same client (one SDK construction)
    assert adapter.llm("model-b") is not a1   # a different request -> its own client
    assert adapter.llm() is not a1            # the default is its own entry, never aliased


def test_deployment_pinned_model_still_wins_over_a_per_call_override(adapter, monkeypatch):
    # LLM_MODEL is the deployment's pin (the portal injects it when the manifest declares a
    # model for the selected provider). It outranks BOTH the adapter default and a per-call
    # model, exactly as it outranks the caller default in the resolution module — so a Surface
    # model selector can never escape a pinned deployment.
    monkeypatch.setenv("LLM_MODEL", "pinned-by-deployment")
    assert adapter.llm("some-other-model").model == "pinned-by-deployment"
    assert adapter.llm().model == "pinned-by-deployment"


# ---------------------------------------------------------------------------
# Row: readiness surface — "write ok / llm bound", secret-free, no billable call
# ---------------------------------------------------------------------------
def test_readiness_reports_both_paths_ready(adapter, fake_anthropic, monkeypatch):
    posts = {"count": 0, "url": None, "auth": None, "batch": None}

    def fake_post(url, json=None, auth=None, headers=None, timeout=None):
        posts.update(count=posts["count"] + 1, url=url, auth=auth,
                     batch=(json or {}).get("batch"))
        return _FakeResp(200, {})

    monkeypatch.setattr(requests, "post", fake_post)

    report = adapter.readiness()
    assert isinstance(report, ReadinessReport)
    assert report.langfuse_write_ok and report.llm_bound and report.ok
    # write probe hit the ingestion endpoint with an EMPTY batch (no data emitted).
    assert posts["count"] == 1
    assert posts["url"] == f"{BASE_URL}/api/public/ingestion"
    assert posts["batch"] == []
    # llm bound by construction only — zero completions.
    assert fake_anthropic["completions"] == 0


def test_readiness_detail_is_secret_free(adapter, fake_anthropic, monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(200, {}))
    detail = adapter.readiness().detail
    flat = repr(detail)
    # No secret VALUE ever appears — not the project keys, not the resolved LLM key.
    for secret in ("sk-seeded", "pk-seeded", "anthropic-resolved-key"):
        assert secret not in flat
    # Non-secret diagnostics ARE present: base URL, resolved provider/model, secret NAMES.
    assert detail["langfuse"]["base_url"] == BASE_URL
    assert detail["llm"]["provider"] == "anthropic"
    assert detail["secrets_present"]["LANGFUSE_PUBLIC_KEY"] is True
    assert detail["secrets_present"]["LLM_API_KEY"] is False  # declared but not in env


def test_readiness_write_failure_is_flagged_scrubbed(adapter, fake_anthropic, monkeypatch):
    def failing_post(*a, **k):
        return _FakeResp(401, {})  # bad/absent project key -> non-2xx

    monkeypatch.setattr(requests, "post", failing_post)
    report = adapter.readiness()
    assert report.langfuse_write_ok is False
    assert report.llm_bound is True  # the LLM side is independent
    # Only the exception TYPE name is recorded, never a message that could echo a URL/key.
    err = report.detail["langfuse"]["error"]
    assert err == "IngestError"
    assert BASE_URL not in err


def test_readiness_llm_bind_failure_is_flagged(adapter, monkeypatch):
    # No fake anthropic injected and no real SDK installed in a bare env -> bind fails. Force
    # the missing-SDK path deterministically by removing any anthropic module.
    monkeypatch.setitem(__import__("sys").modules, "anthropic", None)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(200, {}))
    report = adapter.readiness()
    assert report.langfuse_write_ok is True
    assert report.llm_bound is False
    assert "error" in report.detail["llm"]


# ---------------------------------------------------------------------------
# Row: bind 0.0.0.0:<port> + health < 400 + graceful shutdown (the live seam)
# ---------------------------------------------------------------------------
def test_make_server_binds_the_declared_host_and_port(adapter):
    from fastapi import FastAPI

    server = adapter.make_server(FastAPI(), host="0.0.0.0", port=8199)
    assert server.config.host == "0.0.0.0"  # bind-all, never port-published
    assert server.config.port == 8199


def test_serve_binds_health_and_shuts_down_gracefully(adapter, fake_anthropic, monkeypatch):
    from fastapi import FastAPI

    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(200, {}))
    app = FastAPI()

    @app.get("/")
    def _root():
        return {"surface": "up"}

    adapter.mount_health(app)  # serve() would also do this; mount explicitly for the probe
    port = _free_port()
    server = adapter.make_server(app, host="0.0.0.0", port=port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not getattr(server, "started", False) and time.time() < deadline:
            time.sleep(0.02)
        assert server.started, "server never bound"

        # health_path answers < 400 with the readiness body (the adapter-lands smoke reads it)
        resp = requests.get(f"http://127.0.0.1:{port}/healthz", timeout=5)
        assert resp.status_code < 400
        body = resp.json()
        assert body["ready"] is True
        assert body["langfuse_write_ok"] is True
        assert body["llm_bound"] is True
    finally:
        server.should_exit = True  # the flag uvicorn's SIGTERM/SIGINT handlers set
        thread.join(timeout=10)
    assert not thread.is_alive(), "server did not shut down gracefully"


# ---------------------------------------------------------------------------
# Row: knowledge = {} brand, {} scenario
# ---------------------------------------------------------------------------
def test_adapter_carries_zero_brand_or_scenario_symbols():
    src_dir = Path(adapter_mod.__file__).parent
    sources = "\n".join(
        (src_dir / name).read_text().lower()
        for name in ("adapter.py", "__init__.py")
    )
    # Brand + scenario vocabulary from BOTH gold-standard kits' Surfaces. None may leak into
    # the scenario-agnostic shell.
    banned = [
        "loan", "vehicle", "grant", "applicant", "lending", "dossier", "copilot",
        "workbench", "certification", "analyst", "playground", "langfuse bank",
    ]
    leaked = [w for w in banned if w in sources]
    assert not leaked, f"brand/scenario vocabulary leaked into the adapter: {leaked}"


def test_shell_structurally_satisfies_the_contract(adapter):
    assert isinstance(adapter, CompanionAdapterContract)


# ---------------------------------------------------------------------------
# The full inherit path (acceptance #1): app factory + manifest values only
# ---------------------------------------------------------------------------
def test_run_builds_surface_and_serves(adapter, monkeypatch):
    served = {}

    def fake_serve(app, *, host, port, **kw):
        served.update(app=app, host=host, port=port)

    monkeypatch.setattr(adapter, "serve", fake_serve)

    sentinel_app = object()

    def app_factory(a):
        # The Surface receives the adapter (its ready clients), supplies only its app.
        assert a is adapter
        return sentinel_app

    adapter.run(app_factory, host="0.0.0.0", port=9090)
    assert served == {"app": sentinel_app, "host": "0.0.0.0", "port": 9090}
