"""The **read seam** — the one place any kit reads Langfuse (portal #208, spec H #204).

Langfuse platform v4 retires every read endpoint the kits use: `/traces`, `/observations`,
`/sessions`, `/v2/scores` and the dataset-run reads all `404` on a cut-over target. Their
replacements are not drop-in — `/v2/observations` is cursor-paginated and has no trace
entity at all, `/v3/scores` carries **one typed value** instead of a `value`/`stringValue`
pair, and dataset runs become **experiments**. Re-pointing a URL does not carry a kit over.

So the remap lives here, once, behind a normalised surface, instead of three times in three
kits' `verify` steps. What a caller gets back is the same dataclass on either generation:

* :class:`Trace` — under v4 a trace is not an entity; it is the set of observations sharing
  a trace id, and its trace-level fields (name, user, session, tags) are attributes
  denormalised onto those observations. The seam assembles it either way.
* :class:`Observation` — the v4 observations API, or the deprecated list, normalised to the
  same fields (including the model / usage / cost / prompt-link columns a `verify` asserts).
* :class:`Score` — the v3 single typed `value` split back into
  :attr:`Score.numeric_value` / :attr:`Score.string_value`, and the v3 ``subject``
  discriminator flattened to `trace_id` / `observation_id` / `session_id` /
  `experiment_id`.
* :class:`Experiment` / :class:`ExperimentItem` — dataset runs, read through the
  Experiments API on v4 and through `/datasets/{name}/runs` on legacy.

**Which generation answers is probed, not configured.** One deprecated endpoint is called
once per reader; a `404` means the target has cut over, and the v4 arm takes over by itself
on the day Cloud removes the old endpoints and not before. `SYNTH_LANGFUSE_READ_API`
pins a generation for a test or an operator who needs to force one.

The generation is preferred in that order — legacy while it lives — for the same reason
the portal's counter prefers it (#205): the v2/v3 read APIs serve data written by an
exporter that does not send `x-langfuse-ingestion-version: 4` with a delay of up to 15
minutes, and every Spool written on the batch path is exactly that data. Preferring v4
before a kit's write path has moved would read a demo back as half-empty.

**On the shape: one method per read, branching on the generation inside it.** That is the
same shape the write seam took for the same dual-path problem — ``seed/events.py`` branches
on ``on_otlp()`` inside each builder rather than growing two builder families — and the two
seams ship in the same release and lose their legacy halves in the same one (#213), where
the deletion is the branch and its normaliser in one known place.

This module owns **reads only**. Writes are two other seams: the Spool's
(:mod:`langfuse_synth_core.seed`, deterministic and backdated) and the live surfaces'
(:mod:`langfuse_synth_core.live.emit`, wall-clock).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

import requests

from .http import request_retry

#: Sort floor for observations whose start time did not come back on the requested fields.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: The two read-API generations a target can serve.
LEGACY = "legacy"
V4 = "v4"

#: Pin a generation instead of probing for one (``legacy`` | ``v4``).
READ_API_ENV = "SYNTH_LANGFUSE_READ_API"

_GENERATIONS = (LEGACY, V4)

#: The endpoint the generation is probed on: deprecated (so it ``404``s on a cut-over
#: target) and cheap (one row).
_PROBE_PATH = "/api/public/traces"

#: The Experiments API requires a start-time bound. This floor predates any Demo Depot
#: project by years, so "the window" is "everything".
_TIME_FLOOR = "2020-01-01T00:00:00Z"

#: The v4 observations API returns only its `core` + `basic` field groups unless asked.
#: A `verify` asserts input/output, model, usage/cost and the prompt link, so the seam asks
#: for those groups every time rather than handing back a row with silent holes in it.
_OBSERVATION_FIELDS = "core,basic,time,io,metadata,model,usage,prompt,metrics,trace_context"

#: Page size for every paginated read. Both generations cap well above this.
_PAGE_SIZE = 100

#: How many pages a paginated read follows before it stops. A `verify` samples; it does not
#: mirror a project.
_MAX_PAGES = 30


def _text(value: Any) -> str | None:
    """An unset string column, whichever way the API spells it.

    The v4 read API answers ``""`` for an unset prompt name, version string or trace name
    where the deprecated one answered ``null``. A caller asking "is this generation
    prompt-linked?" must get a falsy absence either way, not an empty name.
    """
    if value is None:
        return None
    text = str(value)
    return text or None


def _decoded_io(value: Any) -> Any:
    """Input/output as data, whichever way the API serialised it.

    The v4 observations endpoint returns `input`/`output` as **raw JSON strings** and
    rejects ``parseIoAsJson=true`` outright ("no longer supported … always returned as raw
    strings", observed on Cloud 2026-08-19); the deprecated endpoints returned parsed
    objects. Kits index into chat-shaped inputs, so the seam decodes — and leaves a plain
    string that is not JSON exactly as it is, which is what both generations mean by a
    string output.
    """
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def parse_ts(value: str | None) -> datetime | None:
    """Parse a Langfuse ISO timestamp (``…Z``) into an aware :class:`datetime`."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# The normalised rows — what a kit asserts against, on either generation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Score:
    """One score, with the v3 typed value split back out and its subject flattened."""

    id: str
    name: str
    data_type: str | None = None
    numeric_value: float | None = None
    string_value: str | None = None
    comment: str | None = None
    timestamp: datetime | None = None
    source: str | None = None
    environment: str | None = None
    trace_id: str | None = None
    observation_id: str | None = None
    session_id: str | None = None
    experiment_id: str | None = None
    config_id: str | None = None
    metadata: dict | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def value(self) -> float | str | None:
        """The score's value in its own type — a float for numeric/boolean scores, the
        label for categorical/text ones. This is what v3 returns and what a kit reads."""
        return self.numeric_value if self.numeric_value is not None else self.string_value


@dataclass(frozen=True)
class Observation:
    """One observation (one OTEL span under v4), with the columns a `verify` asserts."""

    id: str
    trace_id: str | None = None
    parent_id: str | None = None
    type: str | None = None
    name: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    input: Any = None
    output: Any = None
    metadata: Any = None
    level: str | None = None
    model: str | None = None
    model_parameters: Any = None
    usage_details: dict | None = None
    cost_details: dict | None = None
    total_cost: float | None = None
    prompt_name: str | None = None
    prompt_version: int | None = None
    environment: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    tags: list[str] = field(default_factory=list)
    trace_name: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_root(self) -> bool:
        """True when nothing parents this observation — under v4 that is the trace."""
        return not self.parent_id


@dataclass(frozen=True)
class Trace:
    """A trace: an entity on legacy, the set of observations sharing an id under v4."""

    id: str
    name: str | None = None
    timestamp: datetime | None = None
    user_id: str | None = None
    session_id: str | None = None
    environment: str | None = None
    tags: list[str] = field(default_factory=list)
    input: Any = None
    output: Any = None
    metadata: Any = None
    observations: list[Observation] = field(default_factory=list)
    scores: list[Score] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def root(self) -> Observation | None:
        """The root observation — the one carrying the overall input and output under v4."""
        return next((o for o in self.observations if o.is_root), None)


@dataclass(frozen=True)
class Session:
    """A session: the traces sharing a session id."""

    id: str
    trace_ids: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ExperimentItem:
    """One item of an experiment (one dataset-run item), and the trace it produced."""

    id: str
    experiment_id: str
    trace_id: str | None = None
    dataset_item_id: str | None = None
    observation_id: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Experiment:
    """An experiment (a dataset run): its identity plus what it takes to read its items."""

    id: str
    name: str
    dataset_name: str | None = None
    dataset_id: str | None = None
    description: str | None = None
    item_count: int | None = None
    created_at: datetime | None = None
    metadata: dict | None = None
    raw: dict = field(default_factory=dict, repr=False)


# ---------------------------------------------------------------------------
# Normalisation — pure, and the reason this seam exists
# ---------------------------------------------------------------------------
#: Score data types whose value is a number. The rest (`CATEGORICAL`, `TEXT`,
#: `CORRECTION`) carry a label — and the deprecated API sends `value: 0` beside it, which
#: is a placeholder, not a measurement.
_NUMERIC_TYPES = ("NUMERIC", "BOOLEAN")
_STRING_TYPES = ("CATEGORICAL", "TEXT", "CORRECTION")


def _observation_from_v4(row: dict) -> Observation:
    """Normalise a `/api/public/v2/observations` row.

    v4 renames two columns a `verify` reads — the model is `providedModelName` (the name as
    the producer sent it, before Langfuse matched it to a priced model) and the rolled-up
    cost is `totalCost` — and denormalises the trace-level attributes (user, session, tags,
    trace name) onto every observation, because under v4 there is no trace row to hold them.
    """
    return Observation(
        id=row.get("id", ""),
        trace_id=row.get("traceId"),
        parent_id=row.get("parentObservationId"),
        type=row.get("type"),
        name=row.get("name"),
        start_time=parse_ts(row.get("startTime")),
        end_time=parse_ts(row.get("endTime")),
        input=_decoded_io(row.get("input")),
        output=_decoded_io(row.get("output")),
        metadata=row.get("metadata"),
        level=_text(row.get("level")),
        # The live wire calls this `model`; the generated SDK type documents
        # `providedModelName`. Read whichever the server actually sent.
        model=_text(row.get("model") or row.get("providedModelName")),
        model_parameters=row.get("modelParameters"),
        usage_details=row.get("usageDetails"),
        cost_details=row.get("costDetails"),
        total_cost=row.get("totalCost"),
        prompt_name=_text(row.get("promptName")),
        prompt_version=row.get("promptVersion"),
        environment=_text(row.get("environment")),
        user_id=_text(row.get("userId")),
        session_id=_text(row.get("sessionId")),
        tags=list(row.get("tags") or []),
        trace_name=_text(row.get("traceName")),
        raw=row,
    )


def _observation_from_legacy(row: dict, *, trace: dict | None = None) -> Observation:
    """Normalise a deprecated `/api/public/observations` row onto the same dataclass.

    The legacy model keeps user, session and tags on the *trace*, so when the row was read
    as part of one, those come off the trace body — which is what makes an assertion written
    against the seam read the same on either generation.
    """
    trace = trace or {}
    return Observation(
        id=row.get("id", ""),
        trace_id=row.get("traceId"),
        parent_id=row.get("parentObservationId"),
        type=row.get("type"),
        name=row.get("name"),
        start_time=parse_ts(row.get("startTime")),
        end_time=parse_ts(row.get("endTime")),
        input=_decoded_io(row.get("input")),
        output=_decoded_io(row.get("output")),
        metadata=row.get("metadata"),
        level=_text(row.get("level")),
        model=_text(row.get("model")),
        model_parameters=row.get("modelParameters"),
        usage_details=row.get("usageDetails"),
        cost_details=row.get("costDetails"),
        total_cost=row.get("calculatedTotalCost") or (row.get("costDetails") or {}).get("total"),
        prompt_name=_text(row.get("promptName")),
        prompt_version=row.get("promptVersion"),
        environment=_text(row.get("environment")),
        user_id=trace.get("userId"),
        session_id=trace.get("sessionId"),
        tags=list(trace.get("tags") or []),
        trace_name=trace.get("name"),
        raw=row,
    )


def _trace_from_observations(trace_id: str, observations: list[Observation],
                             scores: list[Score]) -> Trace:
    """Assemble the v4 trace: the set of observations sharing an id.

    The root observation is the trace — it carries the overall input and output (the v4
    ingestion checklist puts them there) — and the trace-level attributes are read off
    whichever observation carries them, since they are copied onto every span.
    """
    ordered = sorted(observations, key=lambda o: (o.start_time or _EPOCH, o.id))
    root = next((o for o in ordered if o.is_root), ordered[0])

    def _first(attr: str):
        return next((getattr(o, attr) for o in ordered if getattr(o, attr)), None)

    return Trace(
        id=trace_id,
        name=_first("trace_name") or root.name,
        timestamp=ordered[0].start_time,
        user_id=_first("user_id"),
        session_id=_first("session_id"),
        environment=_first("environment"),
        tags=list(next((o.tags for o in ordered if o.tags), [])),
        input=root.input,
        output=root.output,
        metadata=root.metadata,
        observations=ordered,
        scores=list(scores),
        raw=root.raw,
    )


def _trace_from_legacy(body: dict) -> Trace:
    """Normalise a deprecated `/api/public/traces/{id}` body onto the same trace row.

    The legacy body embeds its observations and scores, and holds user / session / tags at
    the trace level; the seam pushes those down onto the observations so that an assertion
    about an observation's session reads the same on either generation.

    The *list* endpoint is a different shape from the single-trace GET: it answers
    ``observations`` and ``scores`` as lists of **ids**, not bodies. Those carry nothing to
    normalise, so a trace read from a list comes back with an empty observation set — ask
    :meth:`LangfuseReader.trace` for the bodies.
    """
    observations = [o for o in (body.get("observations") or []) if isinstance(o, dict)]
    scores = [s for s in (body.get("scores") or []) if isinstance(s, dict)]
    return Trace(
        id=body.get("id", ""),
        name=body.get("name"),
        timestamp=parse_ts(body.get("timestamp")),
        user_id=body.get("userId"),
        session_id=body.get("sessionId"),
        environment=body.get("environment"),
        tags=list(body.get("tags") or []),
        input=body.get("input"),
        output=body.get("output"),
        metadata=body.get("metadata"),
        observations=[_observation_from_legacy(o, trace=body) for o in observations],
        scores=[_score_from_legacy(s) for s in scores],
        raw=body,
    )


def _experiment_from_v4(row: dict, *, dataset_name: str | None = None) -> Experiment:
    """Normalise a `/api/public/experiments` row (v4's name for a dataset run)."""
    return Experiment(
        id=row.get("id", ""),
        name=row.get("name", ""),
        dataset_name=dataset_name,
        dataset_id=row.get("datasetId"),
        description=row.get("description"),
        item_count=row.get("itemCount"),
        created_at=parse_ts(row.get("startTime")),
        metadata=row.get("metadata"),
        raw=row,
    )


def _experiment_from_legacy(row: dict, *, dataset_name: str | None = None) -> Experiment:
    """Normalise a deprecated `/api/public/datasets/{name}/runs` row onto the same row.

    The legacy list carries no item count — that only appears on the run detail — so it is
    left unset rather than guessed at; :meth:`LangfuseReader.experiment_items` is the
    honest way to count a run's items on this arm.
    """
    return Experiment(
        id=row.get("id", ""),
        name=row.get("name", ""),
        dataset_name=dataset_name or row.get("datasetName"),
        dataset_id=row.get("datasetId"),
        description=row.get("description"),
        item_count=row.get("itemCount"),
        created_at=parse_ts(row.get("createdAt")),
        metadata=row.get("metadata"),
        raw=row,
    )


def _experiment_item_from_v4(row: dict) -> ExperimentItem:
    """Normalise a `/api/public/experiment-items` row.

    v4 identifies the item by ``experimentItemId`` and uses ``id`` for the observation the
    item's trace was recorded on; there is no dataset-item id on this shape, so that field
    stays unset. ``trace_id`` is the identity a kit actually asserts against, and it is the
    same on both generations.
    """
    return ExperimentItem(
        id=row.get("experimentItemId") or row.get("id", ""),
        experiment_id=row.get("experimentId", ""),
        trace_id=row.get("traceId"),
        dataset_item_id=None,
        observation_id=row.get("id"),
        raw=row,
    )


def _experiment_item_from_legacy(row: dict, *, experiment_id: str = "") -> ExperimentItem:
    """Normalise a deprecated run-detail ``datasetRunItems`` entry onto the same row."""
    return ExperimentItem(
        id=row.get("id", ""),
        experiment_id=row.get("datasetRunId") or experiment_id,
        trace_id=row.get("traceId"),
        dataset_item_id=row.get("datasetItemId"),
        observation_id=row.get("observationId"),
        raw=row,
    )


def _score_from_v3(row: dict) -> Score:
    """Normalise a `/api/public/v3/scores` row.

    Two shape changes to undo: v3 carries **one** `value` typed by `dataType` (where v2 had
    a numeric `value` plus a `stringValue`), and it names its target through a discriminated
    `subject` object (where v2 had flat `traceId` / `observationId` / `sessionId` /
    `datasetRunId` columns).
    """
    data_type = (row.get("dataType") or "").upper() or None
    raw_value = row.get("value")
    numeric = string = None
    if data_type in _STRING_TYPES:
        string = None if raw_value is None else str(raw_value)
    elif data_type in _NUMERIC_TYPES or isinstance(raw_value, (int, float, bool)):
        numeric = float(raw_value) if raw_value is not None else None
    elif raw_value is not None:
        string = str(raw_value)

    subject = row.get("subject") or {}
    kind = subject.get("kind")
    return Score(
        id=row.get("id", ""),
        name=row.get("name", ""),
        data_type=data_type,
        numeric_value=numeric,
        string_value=string,
        comment=row.get("comment"),
        timestamp=parse_ts(row.get("timestamp")),
        source=row.get("source"),
        environment=row.get("environment"),
        trace_id=subject.get("id") if kind == "trace" else subject.get("traceId"),
        observation_id=subject.get("id") if kind == "observation" else None,
        session_id=subject.get("id") if kind == "session" else None,
        experiment_id=subject.get("id") if kind == "experiment" else None,
        config_id=row.get("configId"),
        metadata=row.get("metadata"),
        raw=row,
    )


def _score_from_legacy(row: dict) -> Score:
    """Normalise a `/api/public/v2/scores` (or `/scores`) row onto the same dataclass."""
    data_type = (row.get("dataType") or "").upper() or None
    value = row.get("value")
    string_value = row.get("stringValue")
    numeric = float(value) if isinstance(value, (int, float, bool)) else None
    if string_value is None and isinstance(value, str):
        string_value = value
    if data_type in _STRING_TYPES or (data_type is None and string_value is not None):
        # A categorical score arrives as `value: 0` PLUS `stringValue`; reporting that 0 as
        # a measurement would drag any mean a kit computes towards zero.
        numeric = None
    return Score(
        id=row.get("id", ""),
        name=row.get("name", ""),
        data_type=data_type,
        numeric_value=numeric,
        string_value=string_value,
        comment=row.get("comment"),
        timestamp=parse_ts(row.get("timestamp")),
        source=row.get("source"),
        environment=row.get("environment"),
        trace_id=row.get("traceId"),
        observation_id=row.get("observationId"),
        session_id=row.get("sessionId"),
        experiment_id=row.get("datasetRunId"),
        config_id=row.get("configId"),
        metadata=row.get("metadata"),
        raw=row,
    )


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------
class LangfuseReader:
    """Read a Langfuse project through whichever read API generation it serves."""

    def __init__(self, base_url: str, *, auth: tuple[str, str] | None = None,
                 throttle: float = 0.0, read_api: str | None = None, attempts: int = 8):
        self.base_url = base_url.rstrip("/")
        self.auth = auth if auth is not None else auth_from_env()
        self.throttle = throttle
        # How hard to try. The default suits a `verify` sweep, where every read is expected
        # to succeed and Cloud's 429s are the thing being ridden out. A live surface reading
        # to *render* — one that degrades to an offline view when the instance is not there
        # — should ask for `attempts=1`: three minutes of backoff before falling back makes
        # the resilience worse, not better (portal #211).
        self.attempts = attempts
        pinned = read_api or os.environ.get(READ_API_ENV) or None
        self._read_api = _validated(pinned) if pinned else None

    @classmethod
    def from_env(cls, base_url: str, **kw) -> "LangfuseReader":
        """A reader authenticated from the standard `LANGFUSE_*` env vars."""
        return cls(base_url, auth=auth_from_env(), **kw)

    # -- which generation answers -----------------------------------------
    @property
    def read_api(self) -> str:
        """:data:`LEGACY` or :data:`V4`, probed once per reader and then cached."""
        if self._read_api is None:
            self._read_api = self._probe_read_api()
        return self._read_api

    def _probe_read_api(self) -> str:
        """Ask the target which generation it serves. A `404` is the answer "v4"; anything
        other than that or a success is a *failure to read the target at all* — bad keys, a
        wrong host, a server error — and it is raised rather than resolved into an arm.
        Guessing here would report a credentials failure as an empty demo."""
        resp = self._request(_PROBE_PATH, {"limit": 1})
        if resp.status_code == 404:
            return V4
        if resp.status_code >= 400:
            raise ReadError(
                f"cannot read {self.base_url} — GET {_PROBE_PATH} answered "
                f"{resp.status_code}; check the keys, the host, and the project.",
                status_code=resp.status_code)
        return LEGACY

    # -- traces ------------------------------------------------------------
    def trace(self, trace_id: str, *, with_scores: bool = True) -> Trace | None:
        """One trace with its observations (and, by default, its scores) — or ``None``.

        A trace that is not there answers ``None`` rather than raising: "does this trace
        exist?" is an assertion every kit's `verify` makes, and a 404 is the answer to it.

        Under v4 the trace is *assembled*: there is no trace row, so the seam reads the
        observations sharing the id and lifts the trace-level attributes off the root one.
        Scores are fetched on both generations so that what a caller sees does not depend on
        which arm answered — legacy embeds them in the trace body, v4 needs a second read.
        """
        if self.read_api == V4:
            observations = self.observations(trace_id=trace_id)
            if not observations:
                return None
            scores = self.scores(trace_id=trace_id) if with_scores else []
            return _trace_from_observations(trace_id, observations, scores)
        resp = self._request(f"/api/public/traces/{trace_id}")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise ReadError(f"GET /api/public/traces/{trace_id} -> {resp.status_code}",
                            status_code=resp.status_code)
        return _trace_from_legacy(resp.json())

    def traces(self, *, limit: int = _PAGE_SIZE, session_id: str | None = None,
               user_id: str | None = None, name: str | None = None,
               environment: str | None = None, limit_pages: int = _MAX_PAGES) -> list[Trace]:
        """Up to ``limit`` traces, newest-API-first — a **sample**, never a project total.

        Under v4 there is no trace list to page: the seam scans observations and groups them
        by trace id, so a trace here carries the observations the scan saw. Ask
        :meth:`trace` for the complete set. This is the one place the two generations differ
        in more than field names, and it differs *visibly* — no caller is handed a number
        that looks like a whole-project count. (Counting a whole project is the portal's
        problem, and it uses the Metrics API for exactly this reason.)
        """
        if self.read_api == V4:
            rows = self.observations(session_id=session_id, user_id=user_id, name=name,
                                     environment=environment, limit_pages=limit_pages)
            grouped: dict[str, list[Observation]] = {}
            for obs in rows:
                if obs.trace_id:
                    grouped.setdefault(obs.trace_id, []).append(obs)
            return [_trace_from_observations(tid, obs, [])
                    for tid, obs in list(grouped.items())[:limit]]
        params = {"sessionId": session_id, "userId": user_id, "name": name,
                  "environment": environment}
        out: list[Trace] = []
        for row in self._numbered_pages("/api/public/traces", params, limit_pages):
            out.append(_trace_from_legacy(row))
            if len(out) >= limit:
                break
        return out

    def session(self, session_id: str) -> Session:
        """One session as the traces it groups — the same answer on either generation.

        Under v4 a session is not an entity either: it is a `sessionId` attribute copied
        onto observations, so the seam reads the observations carrying it. A session with
        nothing in it answers an empty :class:`Session`, not an error.
        """
        if self.read_api == V4:
            rows = self.observations(session_id=session_id)
            seen = {o.trace_id for o in rows if o.trace_id}
            return Session(id=session_id, trace_ids=sorted(seen))
        resp = self._request(f"/api/public/sessions/{session_id}")
        if resp.status_code == 404:
            return Session(id=session_id)
        if resp.status_code >= 400:
            raise ReadError(f"GET /api/public/sessions/{session_id} -> {resp.status_code}",
                            status_code=resp.status_code)
        body = resp.json()
        return Session(id=body.get("id", session_id),
                       trace_ids=[t.get("id") for t in (body.get("traces") or []) if t.get("id")],
                       raw=body)

    # -- observations ------------------------------------------------------
    def observations(self, *, trace_id: str | None = None, type: str | None = None,
                     name: str | None = None, parent_id: str | None = None,
                     user_id: str | None = None, session_id: str | None = None,
                     environment: str | None = None,
                     limit_pages: int = _MAX_PAGES) -> list[Observation]:
        """Observations matching the given filters, normalised and paginated to the end.

        ``session_id`` filters server-side under v4 (the attribute is on the observation)
        and client-side on legacy, where it is a trace-level field the observations list
        does not carry.
        """
        if self.read_api == V4:
            params = {"fields": _OBSERVATION_FIELDS, "traceId": trace_id, "type": type,
                      "name": name, "parentObservationId": parent_id, "userId": user_id,
                      "sessionId": session_id, "environment": environment}
            rows = self._cursor_pages("/api/public/v2/observations", params, limit_pages)
            return [_observation_from_v4(r) for r in rows]
        if session_id is not None:
            # The deprecated observations list has no session filter — session is a
            # trace-level field there — so the session's traces are read first and their
            # observations filtered here. Under v4 the same call filters server-side.
            rows = [o for t in self.traces(session_id=session_id, limit_pages=limit_pages)
                    for o in (self.trace(t.id, with_scores=False) or t).observations]
            return [o for o in rows
                    if (trace_id is None or o.trace_id == trace_id)
                    and (type is None or (o.type or "").upper() == type.upper())
                    and (name is None or o.name == name)
                    and (parent_id is None or o.parent_id == parent_id)
                    and (user_id is None or o.user_id == user_id)
                    and (environment is None or o.environment == environment)]
        params = {"traceId": trace_id, "type": type, "name": name,
                  "parentObservationId": parent_id, "userId": user_id,
                  "environment": environment}
        rows = self._numbered_pages("/api/public/observations", params, limit_pages)
        return [_observation_from_legacy(r) for r in rows]

    # -- scores ------------------------------------------------------------
    def scores(self, *, name: str | None = None, trace_id: str | None = None,
               session_id: str | None = None, observation_id: str | None = None,
               experiment_id: str | None = None, data_type: str | None = None,
               limit_pages: int = _MAX_PAGES) -> list[Score]:
        """Scores matching the given filters, normalised and paginated to the end."""
        if self.read_api == V4:
            params = {"fields": "details,subject", "name": name, "traceId": trace_id,
                      "sessionId": session_id, "observationId": observation_id,
                      "experimentId": experiment_id, "dataType": data_type}
            rows = self._cursor_pages("/api/public/v3/scores", params, limit_pages)
            return [_score_from_v3(r) for r in rows]
        params = {"name": name, "traceId": trace_id, "sessionId": session_id,
                  "observationId": observation_id, "datasetRunId": experiment_id,
                  "dataType": data_type}
        rows = self._numbered_pages("/api/public/v2/scores", params, limit_pages)
        return [_score_from_legacy(r) for r in rows]

    # -- experiments (dataset runs) ----------------------------------------
    def experiments(self, *, dataset_name: str | None = None, name: str | None = None,
                    limit_pages: int = _MAX_PAGES) -> list[Experiment]:
        """The experiments (dataset runs) on a dataset, normalised.

        v4 renamed the entity and moved the endpoint: a dataset run is an **experiment**,
        listed by dataset **id** (which the seam resolves from the name through the one
        dataset endpoint the migration left alone) and always within a time window, because
        the Experiments API rejects a request without one.
        """
        if self.read_api == V4:
            dataset_id = self._dataset_id(dataset_name) if dataset_name else None
            params = {"datasetId": dataset_id, "name": name, "fromStartTime": _TIME_FLOOR}
            rows = self._cursor_pages("/api/public/experiments", params, limit_pages)
            return [_experiment_from_v4(r, dataset_name=dataset_name) for r in rows]
        rows = self._numbered_pages(f"/api/public/datasets/{dataset_name}/runs",
                                    {"name": name} if name else {}, limit_pages)
        return [_experiment_from_legacy(r, dataset_name=dataset_name) for r in rows]

    def experiment_items(self, experiment: Experiment,
                         limit_pages: int = _MAX_PAGES) -> list[ExperimentItem]:
        """The items of one experiment — each carrying the trace that ran it.

        Takes the :class:`Experiment` rather than an id because the two generations address
        a run differently: v4 by experiment id, legacy by ``(dataset name, run name)``. The
        row carries both, so a caller never has to know which arm answered.
        """
        if self.read_api == V4:
            params = {"experimentId": experiment.id, "fromStartTime": _TIME_FLOOR}
            rows = self._cursor_pages("/api/public/experiment-items", params, limit_pages)
            return [_experiment_item_from_v4(r) for r in rows]
        body = self._get(f"/api/public/datasets/{experiment.dataset_name}/runs/{experiment.name}")
        return [_experiment_item_from_legacy(r, experiment_id=experiment.id)
                for r in (body.get("datasetRunItems") or [])]

    def _dataset_id(self, dataset_name: str) -> str | None:
        """Resolve a dataset name to its id — `/api/public/datasets/{name}` is on no
        deprecation list, so this is the one stable bridge from a kit's configured dataset
        name to the id the Experiments API filters by."""
        body = self._get(f"/api/public/datasets/{dataset_name}")
        return body.get("id")

    # -- HTTP + pagination -------------------------------------------------
    def _request(self, path: str, params: dict | None = None) -> requests.Response:
        return request_retry("GET", f"{self.base_url}{path}", params=_pruned(params or {}),
                             auth=self.auth, timeout=30, throttle_s=self.throttle,
                             attempts=self.attempts)

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._request(path, params)
        if resp.status_code >= 400:
            raise ReadError(f"GET {path} -> {resp.status_code}", status_code=resp.status_code)
        return resp.json()

    def _cursor_pages(self, path: str, params: dict, limit_pages: int) -> Iterator[dict]:
        """Follow the v4 APIs' base64url cursor until it stops coming back."""
        cursor = None
        for _ in range(max(1, limit_pages)):
            payload = self._get(path, {**params, "limit": _PAGE_SIZE, "cursor": cursor})
            rows = payload.get("data") or []
            yield from rows
            cursor = (payload.get("meta") or {}).get("cursor")
            if not rows or not cursor:
                return

    def _numbered_pages(self, path: str, params: dict, limit_pages: int) -> Iterator[dict]:
        """Follow the deprecated APIs' numbered pages until `totalPages` is reached."""
        page = 1
        while page <= max(1, limit_pages):
            payload = self._get(path, {**params, "limit": _PAGE_SIZE, "page": page})
            rows = payload.get("data") or []
            yield from rows
            meta = payload.get("meta") or {}
            if not rows or page >= meta.get("totalPages", page):
                return
            page += 1


class ReadError(RuntimeError):
    """A read the seam could not complete — carries the status so a caller can branch."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def auth_from_env() -> tuple[str, str]:
    """HTTP Basic credentials for the public API, from the standard env vars."""
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


def _validated(generation: str) -> str:
    value = generation.strip().lower()
    if value not in _GENERATIONS:
        raise ValueError(f"unknown Langfuse read API {generation!r} — expected one of "
                         f"{list(_GENERATIONS)}. (Set {READ_API_ENV} or pass read_api=.)")
    return value


def _pruned(params: dict) -> dict:
    """Drop unset filters — an explicit ``None`` is a filter Langfuse rejects, not a wildcard."""
    return {k: v for k, v in params.items() if v is not None}
