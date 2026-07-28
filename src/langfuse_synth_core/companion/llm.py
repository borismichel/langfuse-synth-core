"""Companion LLM resolution — the kits' shared thin provider layer (Spec G · G1, #138).

Extracted from the gold-standard kits' shared ``synth/llm.py`` (LAN-378), whose
executable code is byte-identical between the kits (only docstrings differed). The
extraction keeps that executable code verbatim; this docstring and the function
docstrings are the rewritten, scenario-neutral parts. A
companion surface's demo-time model calls are the only real model calls a kit makes —
the seed path is deterministic and model-free. This module reads the provider selection
from the environment and routes those calls through the Anthropic **or** OpenAI SDK
behind one small interface. It is the container-side half of the ``LLM_API_KEY``
sentinel model: the worker injects the resolved key under the selected provider's
canonical env var, and this module picks it up from there.

Env contract (injected by the portal per the manifest's ``llm`` block):

- ``LLM_PROVIDER``  — ``anthropic`` (default when unset) or ``openai``.
- ``LLM_MODEL``     — model id for the selected provider; falls back to a caller
                      default, then the provider's built-in default.
- ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` — the selected provider's key.

Back-compat is load-bearing (D6, story 38): with ``LLM_PROVIDER`` unset the provider is
Anthropic and the model is the caller-supplied default, so existing deployments (which
inject only ``ANTHROPIC_API_KEY``) behave byte-for-byte as before.

The SDKs are imported lazily inside client construction — this module is importable on
a bare runtime install; the SDK deps ride the ``[companion]`` extra (G2).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_PROVIDER = "anthropic"

# Provider id -> canonical API-key env var. Mirrors the portal provider table
# (api/app/providers.py) and the manifest `llm.providers` enum.
API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

# Fallback model per provider when neither LLM_MODEL nor a caller default is given.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
}


@dataclass(frozen=True)
class ChatResult:
    """Provider-agnostic result of a single completion."""

    text: str
    input_tokens: int
    output_tokens: int


def resolve_provider() -> str:
    """Selected provider id, defaulting to Anthropic when ``LLM_PROVIDER`` is unset."""
    provider = (os.environ.get("LLM_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if provider not in API_KEY_ENV:
        known = ", ".join(sorted(API_KEY_ENV))
        raise ValueError(f"unsupported LLM_PROVIDER {provider!r}; supported: {known}")
    return provider


def resolve_model(provider: str, default: str | None = None) -> str:
    """Model id for ``provider``: ``LLM_MODEL`` wins, then ``default``, then built-in."""
    return os.environ.get("LLM_MODEL") or default or DEFAULT_MODELS[provider]


class LLMClient:
    """Uniform completion interface over the Anthropic and OpenAI chat SDKs."""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        self._impl = None  # lazily constructed SDK client

    def bind(self):
        """Construct (and cache) the provider SDK client from the resolved key WITHOUT
        running a completion — the public "is the client bindable?" accessor. The Companion
        Adapter's readiness probe (Spec G · G2, #140) uses it to prove the LLM binds with no
        billable call and no token spend. Returns the underlying SDK client."""
        return self._client()

    def _client(self):
        if self._impl is not None:
            return self._impl
        key = os.environ.get(API_KEY_ENV[self.provider])
        if self.provider == "anthropic":
            from anthropic import Anthropic

            self._impl = Anthropic(api_key=key)
        else:
            from openai import OpenAI

            self._impl = OpenAI(api_key=key)
        return self._impl

    def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        temperature: float = 0,
        max_tokens: int = 512,
    ) -> ChatResult:
        """Run one completion and return the text plus token usage.

        ``messages`` is a list of ``{"role", "content"}`` turns (no system role — the
        system prompt is passed separately, mirroring the Anthropic API shape). An empty
        ``system`` sends no system prompt.
        """
        client = self._client()
        if self.provider == "anthropic":
            resp = client.messages.create(
                model=self.model, system=system, messages=messages,
                temperature=temperature, max_tokens=max_tokens,
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            return ChatResult(text, resp.usage.input_tokens, resp.usage.output_tokens)

        # OpenAI: the system prompt is a leading message role.
        oai_messages = ([{"role": "system", "content": system}] if system else []) + list(messages)
        resp = client.chat.completions.create(
            model=self.model, messages=oai_messages,
            temperature=temperature, max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content or ""
        return ChatResult(text, resp.usage.prompt_tokens, resp.usage.completion_tokens)


def get_llm(model: str | None = None) -> LLMClient:
    """Construct the :class:`LLMClient` for the environment-selected provider.

    ``model`` is the caller's default (typically a config model id); it is used only
    when ``LLM_MODEL`` is unset, and is ignored for a non-Anthropic provider whose
    default differs (an Anthropic model id would not exist there).
    """
    provider = resolve_provider()
    # A caller default sourced from Anthropic config only applies to Anthropic; for
    # another provider fall back to LLM_MODEL or the provider's built-in default.
    caller_default = model if provider == DEFAULT_PROVIDER else None
    return LLMClient(provider, resolve_model(provider, caller_default))
