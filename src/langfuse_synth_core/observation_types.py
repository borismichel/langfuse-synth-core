"""The observation-type vocabulary Langfuse recognises, and the guard for it (portal #217).

This sits above both seams on purpose. It is not a property of the Spool's write path nor of
the live one — it is a fact about the *target*, and both seams put a value on the same
attribute: the Spool through :mod:`langfuse_synth_core.seed.otlp`, live surfaces through the
SDK's ``as_type``. Kept here, neither seam has to import the other to share it, and the
determinism line stays where ``CONTRACT.md`` draws it.

**Why a guard exists at all.** Batch ingestion accepted three observation types and rejected
everything else with a ``400``. The OTLP wire that replaces it accepts anything: an
unrecognised value is not refused, it is quietly filed as something else. So the rejection
that used to come from the server has to come from here instead.
"""

from __future__ import annotations

#: The ten values Langfuse accepts on ``langfuse.observation.type`` — the three the batch
#: path carried, then the agent-graph types that were OTel-only. Lowercase, and
#: **case-sensitive**: ``AGENT`` is not ``agent`` on the wire, it is a ``SPAN``.
OBSERVATION_TYPES = (
    "span", "generation", "event",
    "agent", "tool", "chain", "retriever", "embedding", "evaluator", "guardrail",
)


class UnknownObservationType(ValueError):
    """An observation type outside the vocabulary Langfuse recognises."""


def unknown_observation_type(obs_type: str) -> str:
    """Why ``obs_type`` is refused — the one wording every site that refuses one uses.

    Both refusals quote this verbatim: the builders and the live seam, which raise at the
    call, and ``synth-authoring conformance``, which reads the value out of a kit's sources
    and adds only where it found it. Restating the reason at either site is how the two would
    come to explain the same rule differently (``CONTRACT.md``, portal #196).
    """
    return (
        f"observation type {obs_type!r} is not one of the ten Langfuse recognises "
        f"({', '.join(OBSERVATION_TYPES)}) — lowercase and case-sensitive on the wire, "
        "though core lowercases whatever a kit hands its event builders. Langfuse does not "
        "reject an unrecognised type: it files the observation as a SPAN, or as a "
        "GENERATION when the observation carries a model, and reports nothing. A mistyped "
        "step therefore lands in the cost and usage views and quietly changes what the demo "
        "shows (confirmed against Langfuse Cloud, 2026-08-19)"
    )


def checked_observation_type(obs_type: str) -> str:
    """Assert an observation type is one Langfuse recognises, and hand it back untouched.

    **Case-sensitive**, because the wire is. That strictness is for the callers that write
    the value as given — the OTLP span builder, and the live seam handing ``as_type`` to the
    Langfuse SDK. It is deliberately *not* what a kit meets at
    :func:`~langfuse_synth_core.seed.events.observation_event`, which lowercases first: a
    kit's uppercase spelling is the batch enum's, and predates this wire.
    """
    if obs_type in OBSERVATION_TYPES:
        return obs_type
    raise UnknownObservationType(unknown_observation_type(obs_type) + ".")
