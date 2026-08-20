"""Builders for the Spool's wire objects — one Python API, two wire formats (spec §2, §3).

Each builder keeps its name and its arguments; a kit composes exactly the same call tree
whichever wire the Spool is written on. What changes is what comes back, selected by
:mod:`langfuse_synth_core.seed.writepath`:

* **batch** (the default, today's behaviour) — the ``{id, type, timestamp, body}`` envelope
  ``/api/public/ingestion`` expects. Envelope ids are derived from the object id + type so
  re-runs are idempotent. Field names match the OpenAPI bodies exactly (TraceBody /
  CreateSpanBody / CreateGenerationBody / CreateEventBody / ScoreBody).
* **otlp** (portal #206) — an OTLP span, for Langfuse v4's observations-first model. See
  :mod:`langfuse_synth_core.seed.otlp` for the mapping and why this is raw OTLP rather than
  the Langfuse SDK.

**Scores are the exception and stay on one path.** Score creation survives the v4 cutover on
the legacy ingestion endpoint and is the only envelope type that does, so ``score_event``
emits the same envelope on both paths. That is a decision, not an oversight: do not "tidy"
the last legacy call away.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from ..timegen import iso
from . import otlp
from .writepath import on_otlp


# The metered/billable envelope types, grouped by Langfuse's line items (traces,
# observations, scores). These are exactly the event types this module emits, so they are
# the single source of truth for the Spool-count primitive (#35). Note ``observation-create``
# only ships while ``RICH_OBSERVATION_TYPES`` (see below) is on — its default — and it stays
# in the observation set either way, so the count survives the flag in both directions.
#
# Dataset items and experiment (dataset-run) items are deliberately absent: they are NOT
# metered as line items and are created through the separate ``/api/public/datasets`` and
# ``/api/public/dataset-run-items`` REST endpoints, never as ingestion envelopes. Counting
# by this whitelist therefore excludes them by construction.
TRACE_EVENT_TYPES = frozenset({"trace-create"})
OBSERVATION_EVENT_TYPES = frozenset(
    {"span-create", "generation-create", "event-create", "observation-create"}
)
SCORE_EVENT_TYPES = frozenset({"score-create"})


def _envelope_id(obj_id: str, etype: str) -> str:
    return hashlib.blake2b(f"{etype}:{obj_id}".encode(), digest_size=16).hexdigest()


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def trace_event(
    *,
    trace_id: str,
    timestamp: datetime,
    name: str,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    environment: str = "production",
    metadata: dict | None = None,
    input=None,
    output=None,
) -> dict:
    """The trace shell. On the OTLP path there is no trace entity to ingest, so this mints
    the trace's **root observation**: it carries the shell fields as trace-level attributes
    (copied onto every span of the trace at finalisation) and the overall input and output,
    which v4 reads from the root observation. Deprecated trace input/output is never used,
    and its SDK compatibility helpers are deliberately not introduced."""
    if on_otlp():
        return otlp.trace_root_span(
            trace_id=trace_id, timestamp=timestamp, name=name, user_id=user_id,
            session_id=session_id, tags=tags, environment=environment, metadata=metadata,
            input=input, output=output,
        )
    body = _clean(
        {
            "id": trace_id,
            "timestamp": iso(timestamp),
            "name": name,
            "userId": user_id,
            "sessionId": session_id,
            "tags": tags,
            "environment": environment,
            "metadata": metadata,
            "input": input,
            "output": output,
        }
    )
    return {"id": _envelope_id(trace_id, "trace-create"), "type": "trace-create",
            "timestamp": iso(timestamp), "body": body}


def span_event(
    *,
    obs_id: str,
    trace_id: str,
    name: str,
    start: datetime,
    end: datetime,
    parent_id: str | None = None,
    environment: str = "production",
    input=None,
    output=None,
    level: str | None = None,
    status_message: str | None = None,
    metadata: dict | None = None,
) -> dict:
    if on_otlp():
        return otlp.observation_span(
            obs_id=obs_id, trace_id=trace_id, name=name, obs_type="span", start=start,
            end=end, parent_id=parent_id, environment=environment, input=input,
            output=output, level=level, status_message=status_message, metadata=metadata,
        )
    body = _clean(
        {
            "id": obs_id,
            "traceId": trace_id,
            "name": name,
            "startTime": iso(start),
            "endTime": iso(end),
            "parentObservationId": parent_id,
            "environment": environment,
            "input": input,
            "output": output,
            "level": level,
            "statusMessage": status_message,
            "metadata": metadata,
        }
    )
    return {"id": _envelope_id(obs_id, "span-create"), "type": "span-create",
            "timestamp": iso(start), "body": body}


# The agent-graph observation types (AGENT | TOOL | RETRIEVER | CHAIN | ...) ship natively
# since the kits' OTLP cutover (portal #210). On the OTLP wire they ride the
# ``langfuse.observation.type`` span attribute; on the batch path they become an
# ``observation-create`` envelope whose body carries the typed value. Measured before
# flipping: the flag moves NO counts on either path — same ``count_spool``, same line count
# — only the wire kind changes (``observation-create`` was already in
# ``OBSERVATION_EVENT_TYPES`` for this moment). Beware the batch caveat: legacy
# ``/api/public/ingestion`` servers accept only SPAN | GENERATION | EVENT bodies (confirmed
# 400 on server 3.179.1), so a kit still writing a batch Spool must keep this off — after
# #210 no shipped kit is, and the off position survives as the escape hatch (the intended
# type then degrades into ``metadata.observation_type``, identically on both paths).
RICH_OBSERVATION_TYPES = True


def observation_event(
    *,
    obs_id: str,
    trace_id: str,
    name: str,
    obs_type: str,  # one of otlp.OBSERVATION_TYPES; either case, checked either way
    start: datetime,
    end: datetime | None = None,
    parent_id: str | None = None,
    environment: str = "production",
    input=None,
    output=None,
    level: str | None = None,
    status_message: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """A typed agent-graph observation (AGENT/TOOL/RETRIEVER/…). Emits a native typed
    ``observation-create`` when ``RICH_OBSERVATION_TYPES`` is on; otherwise degrades to a
    ``span-create`` that records the intended type in ``metadata.observation_type`` so the
    structure (agent nesting, tool calls) and filterability survive on older servers.

    ``obs_type`` may be spelled either way — kits use the batch enum's uppercase and core
    lowercases for the wire — but it must be one of the ten types Langfuse recognises. A
    value outside that vocabulary raises here rather than reaching a wire that would accept
    it and show something else (portal #217; see :func:`otlp.checked_observation_type`)."""
    wire_type = otlp.checked_observation_type(obs_type.lower())
    md = dict(metadata or {})
    if on_otlp():
        # Same rule as the batch path: typed natively while the flag is on (the default),
        # degrading into ``metadata.observation_type`` identically on both paths when an
        # operator turns it off.
        if RICH_OBSERVATION_TYPES:
            emitted_type = wire_type
        else:
            emitted_type = "span"
            md.setdefault("observation_type", wire_type)
        return otlp.observation_span(
            obs_id=obs_id, trace_id=trace_id, name=name, obs_type=emitted_type, start=start,
            end=end, parent_id=parent_id, environment=environment, input=input,
            output=output, level=level, status_message=status_message, metadata=md,
        )
    base = {
        "id": obs_id,
        "traceId": trace_id,
        "name": name,
        "startTime": iso(start),
        "endTime": iso(end) if end else None,
        "parentObservationId": parent_id,
        "environment": environment,
        "input": input,
        "output": output,
        "level": level,
        "statusMessage": status_message,
    }
    if RICH_OBSERVATION_TYPES:
        # The batch enum is the uppercase spelling of the same vocabulary, so this branch
        # up-cases where the OTLP one down-cases. Both accept a kit's either spelling.
        body = _clean({**base, "type": wire_type.upper(), "metadata": md or None})
        return {"id": _envelope_id(obs_id, "observation-create"), "type": "observation-create",
                "timestamp": iso(start), "body": body}
    md.setdefault("observation_type", wire_type)
    body = _clean({**base, "metadata": md})
    return {"id": _envelope_id(obs_id, "span-create"), "type": "span-create",
            "timestamp": iso(start), "body": body}


def generation_event(
    *,
    obs_id: str,
    trace_id: str,
    name: str,
    start: datetime,
    end: datetime,
    model: str,
    usage_details: dict,
    cost_details: dict,
    completion_start: datetime | None = None,
    parent_id: str | None = None,
    environment: str = "production",
    input=None,
    output=None,
    level: str | None = None,
    status_message: str | None = None,
    metadata: dict | None = None,
    prompt_name: str | None = None,
    prompt_version: int | None = None,
    model_parameters: dict | None = None,
) -> dict:
    if on_otlp():
        return otlp.observation_span(
            obs_id=obs_id, trace_id=trace_id, name=name, obs_type="generation", start=start,
            end=end, parent_id=parent_id, environment=environment, input=input,
            output=output, level=level, status_message=status_message, metadata=metadata,
            extra=otlp.generation_extra_attrs(
                model=model, usage_details=usage_details, cost_details=cost_details,
                completion_start=completion_start or start, model_parameters=model_parameters,
                prompt_name=prompt_name, prompt_version=prompt_version,
            ),
        )
    body = _clean(
        {
            "id": obs_id,
            "traceId": trace_id,
            "name": name,
            "startTime": iso(start),
            "endTime": iso(end),
            "completionStartTime": iso(completion_start or start),
            "parentObservationId": parent_id,
            "environment": environment,
            "model": model,
            "modelParameters": model_parameters,
            "usageDetails": usage_details,
            "costDetails": cost_details,
            "input": input,
            "output": output,
            "level": level,
            "statusMessage": status_message,
            "metadata": metadata,
            "promptName": prompt_name,
            "promptVersion": prompt_version,
        }
    )
    return {"id": _envelope_id(obs_id, "generation-create"), "type": "generation-create",
            "timestamp": iso(start), "body": body}


def event_event(
    *,
    obs_id: str,
    trace_id: str,
    name: str,
    start: datetime,
    parent_id: str | None = None,
    environment: str = "production",
    level: str | None = None,
    metadata: dict | None = None,
    input=None,
    output=None,
) -> dict:
    """Zero-duration discrete marker (cache hit, guardrail trip) — spec §3."""
    if on_otlp():
        return otlp.observation_span(
            obs_id=obs_id, trace_id=trace_id, name=name, obs_type="event", start=start,
            parent_id=parent_id, environment=environment, input=input, output=output,
            level=level, metadata=metadata,
        )
    body = _clean(
        {
            "id": obs_id,
            "traceId": trace_id,
            "name": name,
            "startTime": iso(start),
            "parentObservationId": parent_id,
            "environment": environment,
            "level": level,
            "metadata": metadata,
            "input": input,
            "output": output,
        }
    )
    return {"id": _envelope_id(obs_id, "event-create"), "type": "event-create",
            "timestamp": iso(start), "body": body}


def score_event(
    *,
    score_id: str,
    name: str,
    value,
    data_type: str,
    timestamp: datetime,
    trace_id: str | None = None,
    observation_id: str | None = None,
    session_id: str | None = None,
    comment: str | None = None,
    config_id: str | None = None,
    environment: str = "production",
) -> dict:
    """Score on a trace / observation / session. ``value`` is a string for CATEGORICAL,
    numeric for NUMERIC/BOOLEAN (BOOLEAN must be 0 or 1) — per the ScoreBody contract."""
    body = _clean(
        {
            "id": score_id,
            "name": name,
            "value": value,
            "dataType": data_type,
            "traceId": trace_id,
            "observationId": observation_id,
            "sessionId": session_id,
            "comment": comment,
            "configId": config_id,
            "environment": environment,
        }
    )
    return {"id": _envelope_id(score_id, "score-create"), "type": "score-create",
            "timestamp": iso(timestamp), "body": body}
