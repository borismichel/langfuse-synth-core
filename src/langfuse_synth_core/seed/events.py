"""Builders for the Spool's wire objects — the v4 write model (spec §2, §3).

Every observation is an **OTLP span**, and a trace is its **root observation** (minted
here — v4 has no separately ingested trace entity). See
:mod:`langfuse_synth_core.seed.otlp` for the mapping and why this is raw OTLP rather than
the Langfuse SDK.

**Scores are the exception, and they are not legacy.** A score is still a `score-create`
envelope on ``POST /api/public/ingestion``. Langfuse's deprecated-API migration guide states
that the ingestion deprecation "applies only to trace and observation events" and that
`score-create` remains supported with no client change required, so this is the *supported*
write path for scores rather than debt to pay down. Do not "tidy" it away.

The batch write path — trace/observation envelopes on the same endpoint — is gone
(portal #213). It was the pre-v4 transport and its selector flag went with it; see
``docs/WRITE_PATHS.md`` for the record of that migration.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from ..timegen import iso
from . import otlp


#: The one envelope type the Spool still writes. Observations are spans on the OTLP wire
#: and are counted as such (:mod:`langfuse_synth_core.seed.count`), so this is the whole
#: envelope-side billable vocabulary — dataset items and experiment items ride separate
#: REST endpoints and are not metered as line items.
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
    """The trace shell, minted as the trace's **root observation** — there is no trace
    entity to ingest under v4. It carries the shell fields as trace-level attributes (copied
    onto every span of the trace at finalisation) and the overall input and output, which v4
    reads from the root observation. Deprecated trace input/output is never used, and its SDK
    compatibility helpers are deliberately not introduced."""
    return otlp.trace_root_span(
        trace_id=trace_id, timestamp=timestamp, name=name, user_id=user_id,
        session_id=session_id, tags=tags, environment=environment, metadata=metadata,
        input=input, output=output,
    )


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
    return otlp.observation_span(
        obs_id=obs_id, trace_id=trace_id, name=name, obs_type="span", start=start,
        end=end, parent_id=parent_id, environment=environment, input=input,
        output=output, level=level, status_message=status_message, metadata=metadata,
    )


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
    """A typed agent-graph observation (AGENT/TOOL/RETRIEVER/…), riding the
    ``langfuse.observation.type`` span attribute.

    ``obs_type`` may be spelled either way — kits use uppercase and core lowercases for the
    wire — but it must be one of the ten types Langfuse recognises. A value outside that
    vocabulary raises here rather than reaching a wire that would accept it and show
    something else (portal #217; see :func:`otlp.checked_observation_type`)."""
    wire_type = otlp.checked_observation_type(obs_type.lower())
    return otlp.observation_span(
        obs_id=obs_id, trace_id=trace_id, name=name, obs_type=wire_type, start=start,
        end=end, parent_id=parent_id, environment=environment, input=input,
        output=output, level=level, status_message=status_message,
        metadata=dict(metadata or {}),
    )


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
    return otlp.observation_span(
        obs_id=obs_id, trace_id=trace_id, name=name, obs_type="event", start=start,
        parent_id=parent_id, environment=environment, input=input, output=output,
        level=level, metadata=metadata,
    )


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
    """Score on a trace / observation / session, as a `score-create` envelope for
    ``POST /api/public/ingestion`` — the supported v4 write path for scores, not legacy debt
    (see the module docstring). ``value`` is a string for CATEGORICAL, numeric for
    NUMERIC/BOOLEAN (BOOLEAN must be 0 or 1) — per the ScoreBody contract."""
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
