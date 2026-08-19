"""The OTLP wire model for the Spool — raw OpenTelemetry, deliberately not the SDK (#206).

Langfuse v4 makes the *observation* the primary entity: there is no separately ingested
trace, trace-level fields become span attributes copied onto every span of the trace, and
the overall request/response sit on the root observation. This module is the one place that
knows that — it turns the values a kit's event builders already supply into OTLP spans and
into the ``resourceSpans`` payload posted to ``/api/public/otel/v1/traces``.

**Why raw OTLP and not the Langfuse SDK.** The SDK stamps wall-clock and exposes no
start-time parameter. A Spool is weeks of backdated history, so the SDK would collapse every
demo onto the deploy date. Raw OTLP takes producer-supplied nanosecond timestamps *and*
producer-minted ids — core's BLAKE2b ids are already exactly the 32-hex trace / 16-hex span
widths OTLP accepts — so both survive verbatim. Langfuse's own migration guidance points
Python projects at the SDK; that guidance is wrong for this seam, and the deviation is
deliberate. Live, wall-clock surfaces are a different seam and may use the SDK.

**Two-pass finalisation.** A builder cannot know its trace's whole story at call time — kits
compose child observations first and prepend the trace shell afterwards, and a root span's
end time is only known once its last child is built. So a Spool is *finalised* before it is
posted: :func:`scan_trace` walks the spans once to learn each trace's shell attributes and last
end time, and :func:`finalize_span` rewrites each span with the trace-level attributes on,
parentless observations re-parented to the trace root, and the root stretched to cover its
children. Both are pure functions of the lines, so the Spool stays byte-deterministic and
the golden gate still binds.

Attribute names follow Langfuse's published OTEL mapping. Anything outside the
``langfuse.*`` namespace lands in Langfuse's ``metadata.attributes`` catch-all and is not
filterable, so every field we care about is mapped explicitly.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from ..timegen import iso

#: The OTLP/HTTP traces endpoint, relative to the Langfuse base URL. Langfuse accepts
#: OTLP over HTTP with JSON or protobuf; gRPC is not supported. We speak HTTP/JSON so the
#: Spool stays a readable, diffable NDJSON artifact and core stays dependency-free here.
OTEL_TRACES_PATH = "/api/public/otel/v1/traces"

#: Selects the v4 ingestion path so directly-ingested OTEL data is visible in real time
#: rather than delayed by up to ten minutes.
INGESTION_VERSION_HEADER = "x-langfuse-ingestion-version"

# -- attribute keys (Langfuse's documented OTEL mapping) ---------------------
TRACE_NAME = "langfuse.trace.name"
USER_ID = "langfuse.user.id"
SESSION_ID = "langfuse.session.id"
TAGS = "langfuse.trace.tags"
TRACE_METADATA_PREFIX = "langfuse.trace.metadata."
ENVIRONMENT = "langfuse.environment"

OBS_TYPE = "langfuse.observation.type"
OBS_INPUT = "langfuse.observation.input"
OBS_OUTPUT = "langfuse.observation.output"
OBS_LEVEL = "langfuse.observation.level"
OBS_STATUS_MESSAGE = "langfuse.observation.status_message"
OBS_METADATA_PREFIX = "langfuse.observation.metadata."
MODEL_NAME = "langfuse.observation.model.name"
MODEL_PARAMETERS = "langfuse.observation.model.parameters"
USAGE_DETAILS = "langfuse.observation.usage_details"
COST_DETAILS = "langfuse.observation.cost_details"
PROMPT_NAME = "langfuse.observation.prompt.name"
PROMPT_VERSION = "langfuse.observation.prompt.version"
COMPLETION_START_TIME = "langfuse.observation.completion_start_time"

#: The trace-level keys copied from the trace root onto every span of that trace, in this
#: order. v4 queries observations directly, so an attribute that sits only on the root is
#: unavailable when filtering or aggregating its children.
TRACE_LEVEL_KEYS = (TRACE_NAME, USER_ID, SESSION_ID, TAGS)

_SPAN_KIND_INTERNAL = 1
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class OtlpError(ValueError):
    """A Spool line that cannot be expressed as an OTLP span."""


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------
def unix_nano(dt: datetime) -> str:
    """Producer-supplied epoch nanoseconds, as the string proto3 JSON uses for int64.

    This is where backdating survives: the value is whatever the Recipe generated, never
    a wall-clock read.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = dt.astimezone(timezone.utc) - _EPOCH
    micros = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    return str(micros * 1_000)


def _checked_id(value: str, what: str, nibbles: int) -> str:
    """Assert an id is the hex width OTLP carries, and hand it back untouched.

    Producer-minted ids are the point: core's BLAKE2b ids are already 32-hex trace and
    16-hex span, exactly what OTLP accepts, so they pass through verbatim rather than being
    remapped. A kit that hand-rolls an id finds out here — at the builder, with the value in
    the message — rather than as an opaque server-side rejection mid-import.
    """
    if len(value) != nibbles or any(c not in "0123456789abcdefABCDEF" for c in value):
        raise OtlpError(
            f"{what} {value!r} is not the {nibbles}-hex-character id OTLP carries; "
            "mint ids with langfuse_synth_core.rng."
        )
    return value


def trace_root_span_id(trace_id: str) -> str:
    """The 16-hex span id of the root observation minted for ``trace_id``.

    Derived rather than stored so any builder can name the trace's root without holding
    state — a kit builds its child observations before it builds the trace shell.
    """
    return hashlib.blake2b(f"trace-root:{trace_id}".encode(), digest_size=8).hexdigest()


def _json_value(value) -> str:
    """Serialise an attribute value. A string passes through verbatim (Langfuse renders it
    as-is); anything structured becomes compact JSON, byte-stable for the golden gate."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"))


def string_attr(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def int_attr(key: str, value: int) -> dict:
    return {"key": key, "value": {"intValue": str(int(value))}}


def string_array_attr(key: str, values) -> dict:
    return {"key": key,
            "value": {"arrayValue": {"values": [{"stringValue": str(v)} for v in values]}}}


def _metadata_attrs(prefix: str, metadata: dict | None) -> list[dict]:
    return [string_attr(prefix + str(k), _json_value(v)) for k, v in (metadata or {}).items()]


# ---------------------------------------------------------------------------
# span construction
# ---------------------------------------------------------------------------
def observation_span(
    *,
    obs_id: str,
    trace_id: str,
    name: str,
    obs_type: str,
    start: datetime,
    end: datetime | None = None,
    parent_id: str | None = None,
    environment: str = "production",
    input=None,
    output=None,
    level: str | None = None,
    status_message: str | None = None,
    metadata: dict | None = None,
    extra: list[dict] | None = None,
) -> dict:
    """One observation as an OTLP span. ``end`` defaults to ``start`` (a discrete event)."""
    attributes = [string_attr(OBS_TYPE, obs_type), string_attr(ENVIRONMENT, environment)]
    if input is not None:
        attributes.append(string_attr(OBS_INPUT, _json_value(input)))
    if output is not None:
        attributes.append(string_attr(OBS_OUTPUT, _json_value(output)))
    if level is not None:
        attributes.append(string_attr(OBS_LEVEL, level))
    if status_message is not None:
        attributes.append(string_attr(OBS_STATUS_MESSAGE, status_message))
    attributes.extend(extra or [])
    attributes.extend(_metadata_attrs(OBS_METADATA_PREFIX, metadata))

    span = {
        "traceId": _checked_id(trace_id, "trace id", 32),
        "spanId": _checked_id(obs_id, "span id", 16),
        "name": name,
        "kind": _SPAN_KIND_INTERNAL,
        "startTimeUnixNano": unix_nano(start),
        "endTimeUnixNano": unix_nano(end if end is not None else start),
        "attributes": attributes,
    }
    if parent_id:
        # Nesting is parent span context under v4, never a body field.
        span["parentSpanId"] = _checked_id(parent_id, "parent span id", 16)
    return span


def trace_root_span(
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
    """The root observation of a trace: the trace's shell fields plus the overall input and
    output, which v4 reads from the root observation rather than from deprecated trace IO.

    Its end time is a placeholder equal to its start; :func:`apply` stretches it to cover
    the last child once the whole trace is known.
    """
    extra: list[dict] = [string_attr(TRACE_NAME, name)]
    if user_id is not None:
        extra.append(string_attr(USER_ID, user_id))
    if session_id is not None:
        extra.append(string_attr(SESSION_ID, session_id))
    if tags:
        extra.append(string_array_attr(TAGS, tags))
    extra.extend(_metadata_attrs(TRACE_METADATA_PREFIX, metadata))
    return observation_span(
        obs_id=trace_root_span_id(trace_id), trace_id=trace_id, name=name,
        obs_type="span", start=timestamp, end=timestamp, environment=environment,
        input=input, output=output, extra=extra,
    )


def generation_extra_attrs(
    *,
    model: str,
    usage_details: dict,
    cost_details: dict,
    completion_start: datetime | None = None,
    model_parameters: dict | None = None,
    prompt_name: str | None = None,
    prompt_version: int | None = None,
) -> list[dict]:
    """The generation-only attributes, in a fixed order so the Spool stays byte-stable."""
    extra = [string_attr(MODEL_NAME, model)]
    if model_parameters is not None:
        extra.append(string_attr(MODEL_PARAMETERS, _json_value(model_parameters)))
    extra.append(string_attr(USAGE_DETAILS, _json_value(usage_details)))
    extra.append(string_attr(COST_DETAILS, _json_value(cost_details)))
    if completion_start is not None:
        extra.append(string_attr(COMPLETION_START_TIME, iso(completion_start)))
    if prompt_name is not None:
        extra.append(string_attr(PROMPT_NAME, prompt_name))
    if prompt_version is not None:
        extra.append(int_attr(PROMPT_VERSION, prompt_version))
    return extra


# ---------------------------------------------------------------------------
# spool line classification
# ---------------------------------------------------------------------------
def is_span(line: dict) -> bool:
    """True for an OTLP span line (as opposed to a legacy ingestion envelope)."""
    return "spanId" in line


def is_trace_root(line: dict) -> bool:
    return is_span(line) and line["spanId"] == trace_root_span_id(line["traceId"])


# ---------------------------------------------------------------------------
# finalisation — the two passes
# ---------------------------------------------------------------------------
def scan_trace(line: dict, state: dict) -> None:
    """Pass one. Accumulate, per trace id, the shell attributes to copy onto every span and
    the latest end time seen. ``state`` is mutated in place so a whole Spool can be scanned
    by streaming it, holding only one small record per trace."""
    if not is_span(line):
        return
    entry = state.setdefault(line["traceId"], {"attrs": [], "end": "0", "root": False})
    end = line.get("endTimeUnixNano", "0")
    if int(end) > int(entry["end"]):
        entry["end"] = end
    if is_trace_root(line):
        entry["root"] = True
        entry["attrs"] = [
            a for a in line["attributes"]
            if a["key"] in TRACE_LEVEL_KEYS or a["key"].startswith(TRACE_METADATA_PREFIX)
        ]


def finalize_span(line: dict, state: dict) -> dict:
    """Pass two. Return the span as it goes on the wire: trace-level attributes copied on,
    a parentless observation re-parented to the trace root, and the root stretched to cover
    its children. Non-span lines (scores keep the ingestion endpoint) pass through."""
    if not is_span(line):
        return line
    entry = state.get(line["traceId"])
    if entry is None:
        return line
    span = dict(line)
    if is_trace_root(line):
        span["endTimeUnixNano"] = entry["end"]
    else:
        present = {a["key"] for a in span["attributes"]}
        copied = [a for a in entry["attrs"] if a["key"] not in present]
        if copied:
            span["attributes"] = copied + span["attributes"]
        if entry["root"] and not span.get("parentSpanId"):
            span["parentSpanId"] = trace_root_span_id(line["traceId"])
    return span


# ---------------------------------------------------------------------------
# the wire payload
# ---------------------------------------------------------------------------
def finalize(lines: list[dict]) -> list[dict]:
    """Both passes over an in-memory batch — the list-shaped twin of the streaming
    finalisation a spooled run does. Used by the probe and any other flush-without-spool
    caller, so they produce exactly the wire objects an imported Spool would."""
    state: dict = {}
    for line in lines:
        scan_trace(line, state)
    return [finalize_span(line, state) for line in lines]


def payload(spans: list[dict]) -> dict:
    """Wrap finalised spans as one OTLP ``ExportTraceServiceRequest`` body.

    An empty export carries no resource at all — that is the liveness ping, which must
    round-trip auth and the endpoint without writing an observation.
    """
    if not spans:
        return {"resourceSpans": []}
    return {"resourceSpans": [{"scopeSpans": [{"spans": spans}]}]}


def partial_failure(body) -> str | None:
    """The OTLP replacement for batch ingestion's per-event 207 errors.

    An OTLP export can succeed at the HTTP layer while rejecting spans, reported in
    ``partialSuccess``. Returns a description when spans were rejected, else ``None``.
    """
    if not isinstance(body, dict):
        return None
    partial = body.get("partialSuccess") or body.get("partial_success") or {}
    rejected = partial.get("rejectedSpans") or partial.get("rejected_spans") or 0
    try:
        rejected = int(rejected)
    except (TypeError, ValueError):
        return None
    if rejected <= 0:
        return None
    message = partial.get("errorMessage") or partial.get("error_message") or ""
    return f"{rejected} span(s) rejected by OTLP ingestion: {message[:300]}"
