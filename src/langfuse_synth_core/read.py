"""The **read seam** — the one place any kit reads Langfuse (portal #208, spec H #204).

Langfuse platform v4 retired every read endpoint the kits used: `/traces`, `/observations`,
`/sessions`, `/v2/scores` and the dataset-run reads. Their replacements are not drop-in —
`/v2/observations` is cursor-paginated and has no trace entity at all, `/v3/scores` carries
**one typed value** instead of a `value`/`stringValue` pair, and dataset runs became
**experiments**. Re-pointing a URL does not carry a kit over, which is why the remap lives
here, once, behind a normalised surface, instead of three times in three kits' `verify`
steps:

* :class:`Trace` — under v4 a trace is not an entity; it is the set of observations sharing
  a trace id, and its trace-level fields (name, user, session, tags) are attributes
  denormalised onto those observations. The seam assembles it.
* :class:`Observation` — a `/api/public/v2/observations` row, carrying the model / usage /
  cost / prompt-link columns a `verify` asserts.
* :class:`Score` — the v3 single typed `value` split back into
  :attr:`Score.numeric_value` / :attr:`Score.string_value`, and the v3 ``subject``
  discriminator flattened to `trace_id` / `observation_id` / `session_id` /
  `experiment_id`.
* :class:`Experiment` / :class:`ExperimentItem` — dataset runs, read through the
  Experiments API.

**This seam reads v4 and only v4** (portal #213). It carried a second arm through the
migration — the deprecated endpoints, probed for and preferred while they were the faster
read of a batch-written Spool. Both halves of that reasoning are gone: the Spool is written
over OTLP with `x-langfuse-ingestion-version: 4`, so v4 answers it in seconds, and a
deprecated call is now a call that stops working on 2026-11-16. The probe went with the arm
— it *was* a deprecated call, made once per reader, so a seam that only reads v4 could not
keep a legacy endpoint as its way of asking which generation to use.

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
    """A trace: under v4, the set of observations sharing a trace id."""

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


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------
class LangfuseReader:
    """Read a Langfuse project through whichever read API generation it serves."""

    def __init__(self, base_url: str, *, auth: tuple[str, str] | None = None,
                 throttle: float = 0.0, attempts: int = 8):
        self.base_url = base_url.rstrip("/")
        self.auth = auth if auth is not None else auth_from_env()
        self.throttle = throttle
        # How hard to try. The default suits a `verify` sweep, where every read is expected
        # to succeed and Cloud's 429s are the thing being ridden out. A live surface reading
        # to *render* — one that degrades to an offline view when the instance is not there
        # — should ask for `attempts=1`: three minutes of backoff before falling back makes
        # the resilience worse, not better (portal #211).
        self.attempts = attempts

    @classmethod
    def from_env(cls, base_url: str, **kw) -> "LangfuseReader":
        """A reader authenticated from the standard `LANGFUSE_*` env vars."""
        return cls(base_url, auth=auth_from_env(), **kw)

    # -- reachability ------------------------------------------------------
    def ping(self) -> None:
        """Prove the target answers: one cheap, **current** read of a single observation.

        Raises :class:`ReadError` on anything but a success — bad keys, a wrong host, a
        server error. It does not try to interpret which: a caller that wants to report an
        unreadable target rather than raise on it should catch, not guess.

        There was a generation probe here until #213, and it called a deprecated endpoint
        once per reader — which made the seam's own liveness check the last legacy call in
        the stack. This asks the same question on the endpoint the seam actually reads.
        """
        self._get("/api/public/v2/observations", {"limit": 1, "fields": "core"})

    # -- traces ------------------------------------------------------------
    def trace(self, trace_id: str, *, with_scores: bool = True) -> Trace | None:
        """One trace with its observations (and, by default, its scores) — or ``None``.

        A trace that is not there answers ``None`` rather than raising: "does this trace
        exist?" is an assertion every kit's `verify` makes, and a 404 is the answer to it.

        The trace is *assembled*: there is no trace row under v4, so the seam reads the
        observations sharing the id and lifts the trace-level attributes off the root one.
        Scores are a second read, and one the caller can decline.
        """
        observations = self.observations(trace_id=trace_id)
        if not observations:
            return None
        scores = self.scores(trace_id=trace_id) if with_scores else []
        return _trace_from_observations(trace_id, observations, scores)

    def traces(self, *, limit: int = _PAGE_SIZE, session_id: str | None = None,
               user_id: str | None = None, name: str | None = None,
               environment: str | None = None, limit_pages: int = _MAX_PAGES) -> list[Trace]:
        """Up to ``limit`` traces, newest-API-first — a **sample**, never a project total.

        There is no trace list to page under v4: the seam scans observations and groups them
        by trace id, so a trace here carries the observations the scan saw. Ask
        :meth:`trace` for the complete set. That difference is *visible* by design — no
        caller is handed a number that looks like a whole-project count. (Counting a whole
        project is the portal's problem, and it uses the Metrics API for exactly this
        reason.)
        """
        rows = self.observations(session_id=session_id, user_id=user_id, name=name,
                                 environment=environment, limit_pages=limit_pages)
        grouped: dict[str, list[Observation]] = {}
        for obs in rows:
            if obs.trace_id:
                grouped.setdefault(obs.trace_id, []).append(obs)
        return [_trace_from_observations(tid, obs, [])
                for tid, obs in list(grouped.items())[:limit]]

    def session(self, session_id: str) -> Session:
        """One session as the traces it groups.

        A session is not an entity under v4 either: it is a `sessionId` attribute copied
        onto observations, so the seam reads the observations carrying it. A session with
        nothing in it answers an empty :class:`Session`, not an error.
        """
        rows = self.observations(session_id=session_id)
        seen = {o.trace_id for o in rows if o.trace_id}
        return Session(id=session_id, trace_ids=sorted(seen))

    # -- observations ------------------------------------------------------
    def observations(self, *, trace_id: str | None = None, type: str | None = None,
                     name: str | None = None, parent_id: str | None = None,
                     user_id: str | None = None, session_id: str | None = None,
                     environment: str | None = None,
                     limit_pages: int = _MAX_PAGES) -> list[Observation]:
        """Observations matching the given filters, normalised and paginated to the end.

        Every filter here is served server-side: under v4 the trace-level attributes are
        denormalised onto the observation, so ``session_id`` and ``user_id`` are columns of
        the thing being filtered rather than of a trace row that no longer exists.
        """
        params = {"fields": _OBSERVATION_FIELDS, "traceId": trace_id, "type": type,
                  "name": name, "parentObservationId": parent_id, "userId": user_id,
                  "sessionId": session_id, "environment": environment}
        rows = self._cursor_pages("/api/public/v2/observations", params, limit_pages)
        return [_observation_from_v4(r) for r in rows]

    # -- scores ------------------------------------------------------------
    def scores(self, *, name: str | None = None, trace_id: str | None = None,
               session_id: str | None = None, observation_id: str | None = None,
               experiment_id: str | None = None, data_type: str | None = None,
               limit_pages: int = _MAX_PAGES) -> list[Score]:
        """Scores matching the given filters, normalised and paginated to the end."""
        params = {"fields": "details,subject", "name": name, "traceId": trace_id,
                  "sessionId": session_id, "observationId": observation_id,
                  "experimentId": experiment_id, "dataType": data_type}
        rows = self._cursor_pages("/api/public/v3/scores", params, limit_pages)
        return [_score_from_v3(r) for r in rows]

    # -- experiments (dataset runs) ----------------------------------------
    def experiments(self, *, dataset_name: str | None = None, name: str | None = None,
                    limit_pages: int = _MAX_PAGES) -> list[Experiment]:
        """The experiments (dataset runs) on a dataset, normalised.

        v4 renamed the entity and moved the endpoint: a dataset run is an **experiment**,
        listed by dataset **id** (which the seam resolves from the name through the one
        dataset endpoint the migration left alone) and always within a time window, because
        the Experiments API rejects a request without one.
        """
        dataset_id = self._dataset_id(dataset_name) if dataset_name else None
        params = {"datasetId": dataset_id, "name": name, "fromStartTime": _TIME_FLOOR}
        rows = self._cursor_pages("/api/public/experiments", params, limit_pages)
        return [_experiment_from_v4(r, dataset_name=dataset_name) for r in rows]

    def experiment_items(self, experiment: Experiment,
                         limit_pages: int = _MAX_PAGES) -> list[ExperimentItem]:
        """The items of one experiment — each carrying the trace that ran it.

        Takes the :class:`Experiment` rather than a bare id: the row carries the dataset
        name and run name a caller may want to report alongside the items, and passing the
        row keeps the two from being looked up twice.
        """
        params = {"experimentId": experiment.id, "fromStartTime": _TIME_FLOOR}
        rows = self._cursor_pages("/api/public/experiment-items", params, limit_pages)
        return [_experiment_item_from_v4(r) for r in rows]

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


class ReadError(RuntimeError):
    """A read the seam could not complete — carries the status so a caller can branch."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def auth_from_env() -> tuple[str, str]:
    """HTTP Basic credentials for the public API, from the standard env vars."""
    return (os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", ""))


def _pruned(params: dict) -> dict:
    """Drop unset filters — an explicit ``None`` is a filter Langfuse rejects, not a wildcard."""
    return {k: v for k, v in params.items() if v is not None}
