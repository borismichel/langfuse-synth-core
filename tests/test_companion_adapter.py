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

import asyncio
from concurrent.futures import ThreadPoolExecutor
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
    NativeCompanionAdapterContract,
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


def test_custom_langfuse_factory_owns_the_only_sdk_client(seeded_env, fake_langfuse_sdk):
    calls = []
    custom_client = object()

    def factory(**connection):
        calls.append(connection)
        return custom_client

    adapter = CompanionAdapter(_FakeConfig(), langfuse_factory=factory)
    assert adapter.langfuse() is custom_client
    assert adapter.langfuse() is custom_client
    assert calls == [{"host": BASE_URL, "public_key": "pk-seeded", "secret_key": "sk-seeded"}]
    assert fake_langfuse_sdk == {}  # No second SDK client/tracer is constructed.


@pytest.mark.parametrize("emitter_first", [False, True])
def test_live_emission_uses_the_custom_langfuse_client(seeded_env, monkeypatch, emitter_first):
    custom_client = object()

    def default_sdk(**kwargs):
        pytest.fail("live emission must retain the custom tracer and masking client")

    monkeypatch.setitem(__import__("sys").modules, "langfuse",
                        SimpleNamespace(Langfuse=default_sdk))
    adapter = CompanionAdapter(_FakeConfig(), langfuse_factory=lambda **_: custom_client)
    if not emitter_first:
        assert adapter.langfuse() is custom_client

    emitter = adapter.emitter(environment="acceptance", flush_on_exit=False)

    assert emitter.client is adapter.langfuse() is custom_client
    assert adapter.emitter().client is custom_client
    assert emitter.environment == "acceptance"
    assert emitter.flush_on_exit is False


def test_the_spool_writer_is_not_on_the_adapter_surface(adapter):
    """A Surface emits at *now* and rides `emitter()`. The Spool's writer backdates weeks of
    history and has no business on a wall-clock surface, so it is not reachable from the
    Adapter — the readiness probe constructs its own (portal #213)."""
    assert not hasattr(adapter, "ingestor")


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


def test_native_factory_preserves_async_tools_and_resolved_binding(seeded_env, monkeypatch):
    bindings = []

    class NativeModel:
        def bind_tools(self, tools):
            self.tools = tools
            return self

        async def ainvoke(self, message):
            return {"answer": message, "tools": self.tools}

    def factory(**binding):
        bindings.append(binding)
        return NativeModel()

    monkeypatch.setenv("LLM_MODEL", "deployment-pinned")
    adapter = CompanionAdapter(_FakeConfig(), native_llm_factory=factory)
    client = adapter.native_llm("caller-model")
    assert asyncio.run(client.bind_tools(["lookup"]).ainvoke("hello")) == {
        "answer": "hello", "tools": ["lookup"],
    }
    assert bindings == [{
        "provider": "anthropic", "model": "deployment-pinned",
        "api_key": "anthropic-resolved-key",
    }]
    assert adapter.native_llm("another-caller-model") is client


@pytest.mark.parametrize("missing", [
    "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "ANTHROPIC_API_KEY",
])
def test_custom_factories_fail_before_binding_when_credentials_missing(
    seeded_env, monkeypatch, missing,
):
    monkeypatch.delenv(missing)
    calls = []

    def factory(**binding):
        calls.append(binding)
        return object()

    adapter = CompanionAdapter(
        _FakeConfig(), langfuse_factory=factory, native_llm_factory=factory,
    )
    accessor = adapter.native_llm if missing == "ANTHROPIC_API_KEY" else adapter.langfuse
    with pytest.raises(ValueError, match="credential"):
        accessor()
    assert calls == []


@pytest.mark.parametrize("accessor_name", ["langfuse", "native_llm"])
def test_concurrent_first_requests_share_one_factory_client(seeded_env, accessor_name):
    def factory(**_):
        time.sleep(0.03)  # An SDK constructor can yield while initializing telemetry.
        return object()

    adapter = CompanionAdapter(
        _FakeConfig(), langfuse_factory=factory, native_llm_factory=factory,
    )
    accessor = getattr(adapter, accessor_name)
    start = threading.Barrier(4)

    def request(_):
        start.wait(timeout=2)
        return accessor()

    with ThreadPoolExecutor(max_workers=4) as executor:
        clients = list(executor.map(request, range(4)))
    assert all(client is clients[0] for client in clients)


@pytest.mark.parametrize("accessor_name", ["langfuse", "native_llm"])
def test_factory_must_return_a_client(seeded_env, accessor_name):
    adapter = CompanionAdapter(
        _FakeConfig(), langfuse_factory=lambda **_: None, native_llm_factory=lambda **_: None,
    )
    with pytest.raises(ValueError, match="return a client"):
        getattr(adapter, accessor_name)()


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
    posts = {"count": 0, "url": None, "auth": None, "body": None}

    def fake_post(url, json=None, auth=None, headers=None, timeout=None):
        posts.update(count=posts["count"] + 1, url=url, auth=auth, body=json)
        return _FakeResp(200, {})

    monkeypatch.setattr(requests, "post", fake_post)

    report = adapter.readiness()
    assert isinstance(report, ReadinessReport)
    assert report.langfuse_write_ok and report.llm_bound and report.ok
    # write probe hit the OTLP traces endpoint with NO spans (no data emitted).
    assert posts["count"] == 1
    assert posts["url"] == f"{BASE_URL}/api/public/otel/v1/traces"
    assert posts["body"] == {"resourceSpans": []}
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


def test_readiness_checks_custom_clients_without_default_sdk_or_inference(seeded_env, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "anthropic", None)
    monkeypatch.setitem(__import__("sys").modules, "langfuse", None)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(200, {}))
    model = SimpleNamespace(ainvoke=lambda *_: pytest.fail("health ran inference"))
    adapter = CompanionAdapter(
        _FakeConfig(), langfuse_factory=lambda **_: object(),
        native_llm_factory=lambda **_: model,
    )
    report = adapter.readiness()
    assert report.ok
    assert adapter.native_llm() is model


def test_native_openai_binding_and_health_use_the_selected_provider(seeded_env, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-resolved-key")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(200, {}))
    bindings = []

    def factory(**binding):
        bindings.append(binding)
        return object()

    adapter = CompanionAdapter(
        _FakeConfig(), llm_model_default="anthropic-only-default", native_llm_factory=factory,
    )
    report = adapter.readiness()
    assert report.ok
    assert report.detail["llm"] == {"ok": True, "provider": "openai", "model": "gpt-4o"}
    assert bindings == [{"provider": "openai", "model": "gpt-4o", "api_key": "openai-resolved-key"}]


@pytest.mark.parametrize("failed_factory", ["langfuse_factory", "native_llm_factory"])
def test_factory_failure_keeps_health_alive_and_redacts_error(
    seeded_env, monkeypatch, failed_factory,
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResp(200, {}))

    def failing(**_):
        raise RuntimeError("secret sk-seeded anthropic-resolved-key")

    factories = {
        "langfuse_factory": lambda **_: object(), "native_llm_factory": lambda **_: object(),
    }
    factories[failed_factory] = failing
    adapter = CompanionAdapter(_FakeConfig(), **factories)
    app = FastAPI()
    adapter.mount_health(app)
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json()["ready"] is False
    detail = response.json()["detail"]
    component = "langfuse" if failed_factory == "langfuse_factory" else "llm"
    assert detail[component]["ok"] is False
    assert detail[component]["error"] == "RuntimeError"
    assert "sk-seeded" not in response.text
    assert "anthropic-resolved-key" not in response.text


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


# -- the v4 seams the adapter hands a Surface (portal #208) ------------------
def test_adapter_hands_out_a_reader_bound_to_the_deployment_connection(adapter):
    """The read seam replaces the Surface composing its own paginated REST reads on
    ``read_json``; it is bound to the same connection and authenticated the same way."""
    reader = adapter.reader()

    assert reader.base_url == BASE_URL
    assert reader.auth == ("pk-seeded", "sk-seeded")


def test_adapter_hands_out_a_wall_clock_emitter_not_the_spools_ingestor(adapter):
    """A Companion App emits at *now*, so it gets the live-emission seam — and only that
    one: the Spool's ``Ingestor`` came off the Adapter with the batch path (#211, #213)."""
    emitter = adapter.emitter()

    assert emitter.base_url == BASE_URL
    assert (emitter.public_key, emitter.secret_key) == ("pk-seeded", "sk-seeded")
    assert type(emitter).__name__ == "LiveEmitter"


def test_shell_structurally_satisfies_the_contract(adapter):
    assert isinstance(adapter, CompanionAdapterContract)
    assert isinstance(adapter, NativeCompanionAdapterContract)


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


def test_roles_preserve_explicit_models_and_ready_native_clients(
    seeded_env, monkeypatch, fake_anthropic
):
    import json

    monkeypatch.setenv("LLM_MODEL", "primary-chat-override")
    monkeypatch.setenv("OPENAI_API_KEY", "resolved-openai-fixture")
    monkeypatch.setenv(
        "LLM_ROLES_JSON",
        json.dumps(
            {
                "version": 1,
                "bindings": {
                    "judge": {"provider": "anthropic", "model": "explicit-judge"},
                    "speech": {"provider": "openai", "model": "explicit-speech"},
                },
            }
        ),
    )
    constructions = []

    def native_factory(**binding):
        constructions.append(binding)
        return SimpleNamespace(transcribe=lambda: "native speech result")

    subject = CompanionAdapter(_FakeConfig(), role_factories={"speech": native_factory})
    assert subject.llm().model == "primary-chat-override"
    assert subject.llm_for("judge").model == "explicit-judge"
    assert subject.llm_for("judge").provider == "anthropic"
    client = subject.native_llm_for("speech")
    assert client.transcribe() == "native speech result"
    assert subject.native_llm_for("speech") is client
    assert constructions == [
        {
            "provider": "openai",
            "model": "explicit-speech",
            "api_key": "resolved-openai-fixture",
        }
    ]
    with pytest.raises(ValueError, match="not enabled"):
        subject.role_binding("disabled_comparison")
    with pytest.raises(ValueError, match="factory"):
        subject.native_llm_for("judge")
    from langfuse_synth_core.companion import RoleCompanionAdapterContract

    assert isinstance(subject, RoleCompanionAdapterContract)


def test_readiness_includes_all_enabled_roles_without_calls_or_secret_errors(
    seeded_env, fake_anthropic, monkeypatch
):
    import json

    monkeypatch.setenv(
        "LLM_ROLES_JSON",
        json.dumps(
            {
                "version": 1,
                "bindings": {
                    "judge": {"provider": "anthropic", "model": "judge"},
                    "speech": {"provider": "openai", "model": "speech"},
                },
            }
        ),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResp())

    def factory(**kw):
        raise RuntimeError("must not leak " + kw["api_key"])

    subject = CompanionAdapter(_FakeConfig(), role_factories={"speech": factory})
    missing = subject.readiness().as_dict()
    assert missing["ready"] is False
    assert missing["llm_bound"] is False
    assert missing["detail"]["roles"] == {
        "judge": {"provider": "anthropic", "model": "judge", "ok": True},
        "speech": {
            "provider": "openai",
            "model": "speech",
            "ok": False,
            "error": "ValueError",
        },
    }
    monkeypatch.setenv("OPENAI_API_KEY", "resolved-sensitive-fixture")
    failed = subject.readiness().as_dict()
    assert failed["detail"]["roles"]["speech"]["error"] == "RuntimeError"
    assert "resolved-sensitive-fixture" not in json.dumps(failed)
    assert fake_anthropic["completions"] == 0


def test_role_only_surface_readiness_does_not_require_an_undeclared_primary(
    seeded_env, monkeypatch
):
    import json

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "resolved-speech-fixture")
    monkeypatch.setenv(
        "LLM_ROLES_JSON",
        json.dumps(
            {
                "version": 1,
                "bindings": {"speech": {"provider": "openai", "model": "explicit-speech"}},
            }
        ),
    )
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _FakeResp())
    subject = CompanionAdapter(
        _FakeConfig(),
        requires_secrets=["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"],
        role_factories={"speech": lambda **kw: SimpleNamespace(model=kw["model"])},
    )
    report = subject.readiness().as_dict()
    assert report["ready"] is True
    assert report["detail"]["llm"] == {"ok": True, "required": False}
