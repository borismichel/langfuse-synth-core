"""Companion LLM resolution (Spec G · G1, #138) — the kits' shared provider layer.

These tests pin the contract of the extracted `synth/llm.py` the gold-standard kits
carry byte-identically: provider routing via LLM_PROVIDER, model resolution via
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

def test_complete_routes_to_anthropic_shape():
    client = llm.LLMClient("anthropic", "claude-sonnet-4-6")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=11, output_tokens=3),
        )

    client._impl = SimpleNamespace(messages=SimpleNamespace(create=create))
    res = client.complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert (res.text, res.input_tokens, res.output_tokens) == ("ok", 11, 3)
    assert captured["system"] == "S"  # Anthropic keeps system separate


def test_complete_routes_to_openai_shape():
    client = llm.LLMClient("openai", "gpt-4o")
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3),
        )

    client._impl = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    res = client.complete(system="S", messages=[{"role": "user", "content": "hi"}])
    assert (res.text, res.input_tokens, res.output_tokens) == ("ok", 11, 3)
    # OpenAI folds the system prompt into a leading message role.
    assert captured["messages"][0] == {"role": "system", "content": "S"}
    assert captured["messages"][1] == {"role": "user", "content": "hi"}


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
