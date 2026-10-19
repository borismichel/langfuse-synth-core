"""Companion LLM resolution (Spec G · G1, #138) — the kits' shared provider layer.

These tests pin the contract of the extracted `synth/llm.py` whose executable code the
gold-standard kits carry byte-identically: provider routing via LLM_PROVIDER, model
resolution via
LLM_MODEL with per-provider defaults, the provider→env-var key mapping (the
container-side half of the LLM_API_KEY sentinel model), lazy SDK client construction,
and — load-bearing (D6, story 38) — the legacy fallback: with LLM_PROVIDER/LLM_MODEL
unset, resolution is Anthropic with the kit-era default model, byte-for-byte.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from langfuse_synth_core.companion import llm


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------

def test_default_provider_is_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert llm.resolve_provider() == "anthropic"


def test_openai_opt_in_and_case_insensitive(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "OpenAI")
    assert llm.resolve_provider() == "openai"


def test_unknown_provider_raises_and_names_supported(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="anthropic, openai"):
        llm.resolve_provider()


def test_provider_key_env_mapping_is_canonical():
    # The container-side half of the LLM_API_KEY sentinel model: the worker injects the
    # resolved key under exactly these names, so the mapping is a wire contract.
    assert llm.API_KEY_ENV == {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
    }


# ---------------------------------------------------------------------------
# Model resolution
# ---------------------------------------------------------------------------

def test_model_resolution_precedence(monkeypatch):
    # LLM_MODEL wins over a caller default.
    monkeypatch.setenv("LLM_MODEL", "override-x")
    assert llm.resolve_model("anthropic", "caller-default") == "override-x"
    # Falls back to the caller default, then the provider built-in.
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert llm.resolve_model("anthropic", "caller-default") == "caller-default"
    assert llm.resolve_model("openai") == "gpt-4o"


def test_legacy_fallback_is_anthropic_kit_era_default(monkeypatch):
    # Story 38: LLM_PROVIDER/LLM_MODEL unset and no caller default -> Anthropic with the
    # kit-era default model, exactly as the kits resolve today.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    client = llm.get_llm()
    assert (client.provider, client.model) == ("anthropic", "claude-sonnet-4-6")


def test_get_llm_default_keeps_caller_model_for_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    client = llm.get_llm("claude-sonnet-4-6")
    assert (client.provider, client.model) == ("anthropic", "claude-sonnet-4-6")


def test_get_llm_openai_ignores_anthropic_caller_model(monkeypatch):
    # An Anthropic-config model id must not leak to OpenAI; fall back to its default.
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    client = llm.get_llm("claude-sonnet-4-6")
    assert (client.provider, client.model) == ("openai", "gpt-4o")


# ---------------------------------------------------------------------------
# Call shapes (fake SDK clients — the real SDKs are never imported here)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("options", [{}, {"response_schema": None}])
def test_complete_routes_to_anthropic_shape(options):
    client = llm.LLMClient("anthropic", "claude-sonnet-4-6")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=11, output_tokens=3),
        )

    client._impl = SimpleNamespace(messages=SimpleNamespace(create=create))
    res = client.complete(
        system="S", messages=[{"role": "user", "content": "hi"}], temperature=0, **options,
    )
    assert (res.text, res.input_tokens, res.output_tokens) == ("ok", 11, 3)
    assert captured["system"] == "S"  # Anthropic keeps system separate
    assert captured["max_tokens"] == 512
    assert "output_config" not in captured
    # anthropic SDK 1.0.0 removed the sampling parameters from `messages.create()`
    # (passing one is a TypeError, portal #231): the Anthropic branch must never forward
    # them, whatever the caller asked for.
    assert not {"temperature", "top_p", "top_k"} & captured.keys()


@pytest.mark.parametrize("options", [
    {}, {"response_schema": None}, {"provider_tracing": False}, {"provider_tracing": True},
])
def test_complete_routes_to_openai_shape(options):
    client = llm.LLMClient("openai", "gpt-4o")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3),
        )

    client._impl = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    res = client.complete(system="S", messages=[{"role": "user", "content": "hi"}], **options)
    assert (res.text, res.input_tokens, res.output_tokens) == ("ok", 11, 3)
    # OpenAI folds the system prompt into a leading message role.
    assert captured["messages"][0] == {"role": "system", "content": "S"}
    assert captured["messages"][1] == {"role": "user", "content": "hi"}
    # The sampling knob still reaches OpenAI — it is only the Anthropic branch that lost it.
    assert captured["temperature"] == 0
    assert "response_format" not in captured


def test_complete_openai_empty_system_sends_no_system_message():
    client = llm.LLMClient("openai", "gpt-4o")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=""))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0),
        )

    client._impl = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.complete(system="", messages=[{"role": "user", "content": "hi"}])
    assert captured["messages"] == [{"role": "user", "content": "hi"}]


# ---------------------------------------------------------------------------
# Lazy client construction (sentinel resolution as seen from inside the container)
# ---------------------------------------------------------------------------

def test_client_construction_reads_selected_providers_key_env(monkeypatch):
    # Inject a fake `anthropic` module so construction needs no real SDK: the client must
    # be built with the key read from the SELECTED provider's canonical env var.
    seen = {}

    class FakeAnthropic:
        def __init__(self, api_key=None):
            seen["api_key"] = api_key

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "resolved-key")
    client = llm.LLMClient("anthropic", "claude-sonnet-4-6")
    impl = client._client()
    assert isinstance(impl, FakeAnthropic)
    assert seen["api_key"] == "resolved-key"
    # Constructed once, then reused.
    assert client._client() is impl


def test_bind_constructs_client_without_completing(monkeypatch):
    # `bind()` is the public "construct, don't complete" accessor (Spec G · G2, #140): it
    # returns the underlying SDK client and makes no billable call.
    created = {"completions": 0}

    class FakeAnthropic:
        def __init__(self, api_key=None):
            created["api_key"] = api_key
            self.messages = SimpleNamespace(
                create=lambda **kw: created.__setitem__("completions", created["completions"] + 1)
            )

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    client = llm.LLMClient("anthropic", "claude-sonnet-4-6")
    impl = client.bind()
    assert isinstance(impl, FakeAnthropic)
    assert created["completions"] == 0
    assert client.bind() is impl  # cached, same as _client()


def test_openai_client_construction_reads_openai_key_env(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, api_key=None):
            seen["api_key"] = api_key

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("OPENAI_API_KEY", "resolved-openai-key")
    client = llm.LLMClient("openai", "gpt-4o")
    assert isinstance(client._client(), FakeOpenAI)
    assert seen["api_key"] == "resolved-openai-key"


def test_complete_requests_anthropic_json_schema(monkeypatch):
    """The public client translates a plain schema at the external SDK boundary."""
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text='{"accepted":true}')],
            usage=SimpleNamespace(input_tokens=12, output_tokens=5),
        )

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        Anthropic=lambda **kwargs: SimpleNamespace(messages=SimpleNamespace(create=create)),
    ))
    schema = {
        "type": "object", "properties": {"accepted": {"type": "boolean"}},
        "required": ["accepted"], "additionalProperties": False,
    }
    result = llm.LLMClient("anthropic", "claude-sonnet-4-6").complete(
        system="Decide.", messages=[{"role": "user", "content": "Example"}],
        response_schema=schema,
    )
    assert captured["output_config"] == {"format": {"type": "json_schema", "schema": schema}}
    assert result == llm.ChatResult('{"accepted":true}', 12, 5)


def test_complete_requests_openai_strict_json_schema(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"accepted":true}'))],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
        )

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(
        OpenAI=lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    ))
    schema = {
        "type": "object", "properties": {"accepted": {"type": "boolean"}},
        "required": ["accepted"], "additionalProperties": False,
    }
    result = llm.LLMClient("openai", "gpt-4o").complete(
        system="Decide.", messages=[{"role": "user", "content": "Example"}],
        response_schema=schema,
    )
    assert captured["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "strict": True, "schema": schema},
    }
    assert result == llm.ChatResult('{"accepted":true}', 12, 5)


def test_complete_can_disable_sdk_tracing_without_changing_later_requests(monkeypatch):
    """A caller that already records generations can opt out per completion."""
    tracing = []

    class Anthropic:
        def __init__(self, *, api_key=None, open_telemetry=True):
            self.open_telemetry = open_telemetry
            self.messages = SimpleNamespace(create=self.create)

        def with_options(self, *, open_telemetry=None):
            return Anthropic(open_telemetry=open_telemetry)

        def create(self, **kwargs):
            tracing.append(self.open_telemetry)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="ok")],
                usage=SimpleNamespace(input_tokens=11, output_tokens=3),
            )

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=Anthropic))
    client = llm.LLMClient("anthropic", "claude-sonnet-4-6")
    result = client.complete(system="S", messages=[], provider_tracing=False)
    client.complete(system="S", messages=[])
    client.complete(system="S", messages=[], provider_tracing=True)
    assert result == llm.ChatResult("ok", 11, 3)
    assert tracing == [False, True, True]


def test_complete_supports_anthropic_before_builtin_tracing(monkeypatch):
    """Older supported SDKs have neither the telemetry option nor built-in spans."""
    class Anthropic:
        def __init__(self, *, api_key=None):
            self.messages = SimpleNamespace(create=self.create)

        def with_options(self, *, timeout=None):
            raise AssertionError("No copy is needed when SDK tracing does not exist")

        def create(self, **kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="legacy")],
                usage=SimpleNamespace(input_tokens=11, output_tokens=3),
            )

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=Anthropic))
    result = llm.LLMClient("anthropic", "claude-sonnet-4-6").complete(
        system="S", messages=[], provider_tracing=False,
    )
    assert result == llm.ChatResult("legacy", 11, 3)


def test_complete_refuses_to_ignore_sdk_tracing_it_cannot_disable(monkeypatch):
    class Anthropic:
        open_telemetry = {"traces": {"content_mode": "metadata_only"}}

        def __init__(self, *, api_key=None):
            self.messages = SimpleNamespace(create=self.create)

        def with_options(self, *, timeout=None):
            return self

        def create(self, **kwargs):
            pytest.fail("A request must not run with unwanted SDK tracing")

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=Anthropic))
    with pytest.raises(RuntimeError, match="cannot disable.*tracing"):
        llm.LLMClient("anthropic", "claude-sonnet-4-6").complete(
            system="S", messages=[], provider_tracing=False,
        )
