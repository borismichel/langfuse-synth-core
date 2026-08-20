"""The **live-emission seam** — wall-clock traces from a kit's live surfaces (portal #208).

Four surfaces emit at *now*: a Companion App's submissions, the playground, workbench runs,
and experiment tasks. Today they reach for the Spool's event builders and its ``Ingestor``,
which couples a live surface to machinery built for a constraint it does not have — the
Spool is weeks of **backdated** history, and everything about that path exists to keep
producer-supplied timestamps and producer-minted ids intact.

This seam is the other side of that line:

* **It may use the Langfuse SDK, and does.** The SDK stamps wall clock and offers no
  start-time parameter — disqualifying for the Spool, exactly right here — and it brings
  context propagation, nesting through real parent span context, and OTLP transport with
  the v4 ingestion header for free.
* **It has no Spool.** Nothing here writes an NDJSON line, nothing is replayed by
  ``import-spool``, and nothing is byte-compared against a golden. The determinism line
  (``CONTRACT.md``) puts live surfaces outside the golden-gate on purpose: they call models
  and they land at the wall clock, so they can never be a pure function of a seed. A test
  asserts this module imports nothing from :mod:`langfuse_synth_core.seed`.
* **It takes no timestamp.** Not an omission — a live emitter that accepted one would be a
  second, unblessed backdating path, and the whole migration turns on there being exactly
  one of those.

Under v4 there is no trace entity to create: a trace is the set of observations sharing a
trace id, its overall input and output live on the **root** observation, and its
correlating attributes (name, user, session, tags, environment) must be copied onto every
span for filtering and aggregation to see them. :meth:`LiveEmitter.trace` does all three —
it opens the root observation, propagates the trace attributes across everything nested
inside it, and flushes when the block ends so the trace is on its way before the surface
answers the user.

```python
emitter = adapter.emitter()                       # or LiveEmitter.from_env(base_url)
with emitter.trace("playground_submission", user_id="playground_user",
                   session_id=session, tags=["playground"], input=application) as trace:
    with trace.generation("decision", model=model, input=messages, prompt=prompt) as gen:
        result = llm.complete(...)
        gen.update(output=result.text, usage={"input": result.input_tokens,
                                              "output": result.output_tokens})
    trace.update(output=decision)
emitter.score("user_disagreement", 1, trace_id=trace.id, data_type="BOOLEAN")
```

Scores stay on their own path — :meth:`LiveEmitter.score` calls the SDK's ``create_score``,
which posts a ``score-create`` envelope to the legacy ingestion endpoint. That endpoint is
the one that survives the v4 cutover, and this is the same decision the Spool's
``score_event`` records: scores move only if and when Langfuse says so.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Sequence

from ..ingestion import INGESTION_VERSION, INGESTION_VERSION_HEADER
from ..observation_types import checked_observation_type


class LiveEmitter:
    """A wall-clock emitter bound to one deployment's Langfuse connection."""

    def __init__(self, base_url: str, *, public_key: str | None = None,
                 secret_key: str | None = None, environment: str | None = None,
                 flush_on_exit: bool = True, client: Any = None,
                 propagate: Callable[..., Any] | None = None):
        self.base_url = base_url.rstrip("/")
        env = os.environ.get
        self.public_key = public_key if public_key is not None else env("LANGFUSE_PUBLIC_KEY")
        self.secret_key = secret_key if secret_key is not None else env("LANGFUSE_SECRET_KEY")
        self.environment = environment
        self.flush_on_exit = flush_on_exit
        self._client = client
        self._propagate = propagate

    @classmethod
    def from_env(cls, base_url: str | None = None, **kw) -> "LiveEmitter":
        """An emitter for the deployment's own connection, from the standard env vars.

        ``LANGFUSE_BASE_URL`` is the portal-injected target; a caller with a config object
        (a headless ``synth`` verb) passes its ``target.base_url`` instead.
        """
        base = base_url or os.environ.get("LANGFUSE_BASE_URL") or ""
        if not base:
            raise ValueError("no Langfuse base URL — pass one or set LANGFUSE_BASE_URL")
        return cls(base, **kw)

    # -- the SDK, imported lazily so a bare runtime install still imports this module --
    @property
    def client(self) -> Any:
        """The Langfuse SDK client, constructed once per emitter.

        Built with the **real-time ingestion header** the Spool's writer already sends
        (``x-langfuse-ingestion-version: 4``). Without it a v4 target processes exported
        spans on the slow path — up to 15 minutes before they are readable (portal #205) —
        and a live surface whose whole promise is "your submission is in Langfuse *now*"
        would be answering a link to an empty trace. ``additional_headers`` is wired into
        the SDK's default OTLP exporter, which is why the SDK floor is pinned rather than
        resolved (``pyproject.toml``).
        """
        if self._client is None:
            from langfuse import Langfuse

            self._client = Langfuse(
                host=self.base_url, public_key=self.public_key, secret_key=self.secret_key,
                additional_headers={INGESTION_VERSION_HEADER: INGESTION_VERSION})
        return self._client

    @property
    def propagate(self) -> Callable[..., Any]:
        """The SDK's ``propagate_attributes`` — how trace-level attributes reach every span."""
        if self._propagate is None:
            from langfuse import propagate_attributes

            self._propagate = propagate_attributes
        return self._propagate

    # -- one live trace ----------------------------------------------------
    @contextmanager
    def trace(self, name: str, *, user_id: str | None = None, session_id: str | None = None,
              tags: Sequence[str] | None = None, environment: str | None = None,
              input: Any = None, metadata: dict | None = None,
              version: str | None = None) -> Iterator["LiveTrace"]:
        """Emit one wall-clock trace: a root observation with everything nested inside it.

        The block's exit ends the root observation and then (by default) flushes, so the
        whole trace is delivered before the surface answers its user — including when the
        body raised. ``flush()`` guarantees *delivery*, not readability: Langfuse's
        ingestion is asynchronous, so a read-back immediately after may still miss it.
        """
        attributes = _pruned({
            "trace_name": name, "user_id": user_id, "session_id": session_id,
            "tags": list(tags) if tags else None,
            "environment": environment or self.environment,
            "metadata": metadata, "version": version,
        })
        try:
            with self.propagate(**attributes):
                with self.client.start_as_current_observation(
                        name=name, as_type="span", input=input) as root:
                    yield LiveTrace(root, self)
        finally:
            # AFTER the root observation has ended, never inside it: a flush issued while
            # the root span is still open delivers the children and leaves the trace's own
            # observation sitting in the buffer. And in a `finally`, because a submission
            # that raised mid-request is usually the trace someone needs to see.
            if self.flush_on_exit:
                self.flush()

    # -- scores ------------------------------------------------------------
    def score(self, name: str, value: float | str, *, trace_id: str | None = None,
              observation_id: str | None = None, session_id: str | None = None,
              dataset_run_id: str | None = None, data_type: str | None = None,
              comment: str | None = None, score_id: str | None = None,
              config_id: str | None = None, metadata: Any = None) -> None:
        """Attach a score to a trace, an observation, a session or a dataset run.

        Exactly one subject, as the data model requires. Rides the SDK's ``create_score``,
        which posts a ``score-create`` envelope on the legacy ingestion endpoint — the one
        envelope type that survives the v4 cutover.
        """
        self.client.create_score(**_pruned({
            "name": name, "value": value, "trace_id": trace_id,
            "observation_id": observation_id, "session_id": session_id,
            "dataset_run_id": dataset_run_id, "data_type": data_type, "comment": comment,
            "score_id": score_id, "config_id": config_id, "metadata": metadata,
            "environment": self.environment,
        }))

    # -- delivery ----------------------------------------------------------
    def flush(self) -> None:
        """Deliver everything buffered. A short-lived process MUST call this before exit."""
        self.client.flush()

    def shutdown(self) -> None:
        """Flush and stop the SDK's background workers — for a surface that is going away."""
        self.client.shutdown()


class LiveObservation:
    """One in-flight observation, and what nests inside it.

    Nesting is explicit — a child is opened *on its parent*, not on whatever the SDK's
    ambient context happens to be — because a live surface is a web server: two submissions
    can be in flight in the same process, and an agent graph assembled out of ambient
    context would interleave them. Every level answers the same four openers, so a tree of
    any depth is written the way it reads.
    """

    def __init__(self, obs: Any, emitter: "LiveEmitter"):
        self._obs = obs
        self._emitter = emitter

    @property
    def id(self) -> str:
        """This observation's id — what a score attaches to."""
        return self._obs.id

    @property
    def trace_id(self) -> str:
        """The id of the trace this observation belongs to."""
        return self._obs.trace_id

    def update(self, **fields: Any) -> "LiveObservation":
        """Update this observation — where its output goes once the work has finished."""
        self._obs.update(**fields)
        return self

    @contextmanager
    def observation(self, name: str, *, as_type: str = "span",
                    **fields: Any) -> Iterator["LiveObservation"]:
        """Nest an observation of any v4 type under this one.

        ``as_type`` reaches Langfuse exactly as written — the SDK does not normalise it and
        Langfuse does not refuse it, so an unrecognised value (``AGENT`` included) is
        silently shown as something else. Checked here for that reason (portal #217); the
        backdated seam takes the same vocabulary through a case-forgiving door.
        """
        with self._obs.start_as_current_observation(
                name=name, as_type=checked_observation_type(as_type),
                **_pruned(fields)) as obs:
            yield LiveObservation(obs, self._emitter)

    def span(self, name: str, **fields: Any):
        """Nest a plain span — a step of the surface's work."""
        return self.observation(name, as_type="span", **fields)

    def event(self, name: str, **fields: Any):
        """Nest a point-in-time event."""
        return self.observation(name, as_type="event", **fields)

    def generation(self, name: str, *, model: str | None = None, usage: dict | None = None,
                   cost: dict | None = None, prompt: Any = None, **fields: Any):
        """Nest a model call, with the columns the demo's cost and usage curves read.

        ``usage`` / ``cost`` are the SDK's ``usage_details`` / ``cost_details`` under the
        names a kit already uses for them on the Spool's builders, so the two seams read
        alike from kit code.
        """
        return self.observation(name, as_type="generation", model=model,
                                usage_details=usage, cost_details=cost, prompt=prompt,
                                **fields)

    def score(self, name: str, value: float | str, **kw: Any) -> None:
        """Score this observation."""
        kw.setdefault("observation_id", self.id)
        kw.setdefault("trace_id", self.trace_id)
        self._emitter.score(name, value, **kw)


class LiveTrace(LiveObservation):
    """One in-flight live trace: its root observation, and what nests inside it.

    Under v4 the trace *is* that root observation — it carries the overall input and
    output — so this is a :class:`LiveObservation` with the trace's identity on the front.
    """

    @property
    def id(self) -> str:
        """The trace id — what a surface hands back as a deep link or scores later."""
        return self._obs.trace_id

    @property
    def observation_id(self) -> str:
        """The root observation's own id (the trace's id is :attr:`id`)."""
        return self._obs.id

    def score(self, name: str, value: float | str, **kw: Any) -> None:
        """Score this trace (the subject defaults to it, not to the root observation)."""
        kw.setdefault("trace_id", self.id)
        self._emitter.score(name, value, **kw)


def _pruned(fields: dict) -> dict:
    """Drop unset values — an explicit ``None`` is a value to the SDK, not an omission."""
    return {k: v for k, v in fields.items() if v is not None}
