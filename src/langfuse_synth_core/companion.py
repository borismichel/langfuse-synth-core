"""Companion Adapter — the thin, scenario-agnostic compatibility shell (runtime).

Spec A owns ONLY that this shell installs in the synth-core *runtime* install and
fixes the contract seam, so a companion surface has its invocation/secret/health/
lifecycle contract available wherever the library runs. The adapter's real extraction,
internals, and migration are **Spec G (#25)**. Zero brand/scenario knowledge lives here.

Per the T10 verdict the shell owns six things:
  1. invocation contract       — how the portal starts and addresses the surface
  2. secret intake             — which `requires_secrets` it accepts (least privilege)
  3. Langfuse client           — read seeded data + emit live traces
  4. health                    — the health contract the portal polls
  5. lifecycle                 — start / stop / TTL-reap hooks
  6. LLM credential resolution — resolve the deployment's selected provider key

The Protocol below fixes the seam and the public name so downstream imports are stable.
Method signatures are illustrative; Spec G finalizes them when it implements the shell.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CompanionAdapter(Protocol):
    """Structural contract a companion surface's adapter satisfies (Spec G implements)."""

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        """Entry point the portal calls to start/address the live surface."""
        ...

    def intake_secrets(self, secrets: dict[str, str]) -> None:
        """Accept ONLY the declared `requires_secrets` (least privilege)."""
        ...

    def health(self) -> Any:
        """Report health on the contract the portal polls."""
        ...

    def start(self) -> None:
        """Lifecycle: bring the surface up."""
        ...

    def stop(self) -> None:
        """Lifecycle: tear the surface down (TTL-reap safe)."""
        ...

    def resolve_llm_credential(self, provider: str) -> str:
        """Resolve the deployment's selected provider key under its canonical env name."""
        ...
