"""Companion Adapter — the thin, scenario-agnostic compatibility shell (runtime).

A **Companion Surface** (a kit's live/playable app) plugs into the portal's live-asset
runtime through the **Companion Adapter** shell. The Adapter owns exactly six
responsibilities and holds **zero brand knowledge and zero scenario knowledge** (D1):

  1. invocation contract       — how the portal starts and addresses the surface
  2. bind + lifecycle          — bind ``0.0.0.0:<port>`` + graceful shutdown (TTL-reap safe)
  3. health                    — the health contract the portal polls
  4. secret intake             — read ``requires_secrets`` env; hand out ready clients only
  5. Langfuse client           — read the seeded pool + emit live traces (bidirectional)
  6. LLM credential resolution — resolve the deployment's selected provider into a client

plus an adapter-owned **readiness surface** ("Langfuse write path ok / LLM client bound")
that the portal's adapter-lands smoke asserts.

The public surface, finalized here in Spec G · G2 (#140) — Spec A shipped only a placeholder
Protocol whose signatures were explicitly illustrative:

* :class:`CompanionAdapter` — the concrete runtime shell a kit instantiates and inherits from.
* :class:`CompanionAdapterContract` — the structural :class:`~typing.Protocol` the shell
  satisfies (the #25 eight rows), so the seam a duck-typed adapter object satisfies stays a
  Protocol, mirroring the ``Config`` house style.
* :class:`Invocation` / :func:`parse_invocation` — the fixed ``--config/--host/--port`` parse.
* :class:`ReadinessReport` — the secret-free readiness the health body carries.
* :mod:`llm` — the G1 (#138) LLM-resolution module the adapter hands out.
"""

from __future__ import annotations

from . import llm
from .adapter import (
    CompanionAdapter,
    CompanionAdapterContract,
    Invocation,
    ReadinessReport,
    parse_invocation,
)

__all__ = [
    "CompanionAdapter",
    "CompanionAdapterContract",
    "Invocation",
    "ReadinessReport",
    "parse_invocation",
    "llm",
]
