"""Companion Adapter — the scenario-agnostic runtime shell (Spec G · G2, #25/#140).

A **Companion Surface** (a kit's live/playable app) plugs into the portal's live-asset
runtime through this shell. The shell owns the six compatibility responsibilities the portal
already enforces, and holds **zero brand knowledge and zero scenario knowledge** (D1) — this
module names no kit, scenario, or domain term, and the contract suite greps for exactly that.
A kit *calls into* it — toolbox, not
framework (the T2 house style) — supplying only its app factory and its manifest values
(port comes from invocation; ``health_path`` and the ``requires_secrets`` list are the kit's
manifest declarations) and inheriting invocation, bind, health, shutdown, secret intake, and
ready Langfuse + LLM clients.

The eight rows of ``CompanionAdapterContract`` (#25) map onto this module as:

1. **invocation** — :func:`parse_invocation` parses exactly ``--config {config} --host
   0.0.0.0 --port <port>`` and nothing else, so a stray pipeline ``--set`` (which a live
   entrypoint cannot accept — the LAN-165 class of failure) is rejected loudly (D3).
2. **bind + lifecycle** — :meth:`CompanionAdapter.serve` binds ``0.0.0.0:<port>`` and runs
   a uvicorn server whose signal handlers give the idle-reaper a graceful shutdown (D3,
   story 5/22). The portal never port-publishes; Caddy Host-routes over the ``live`` net.
3. **health** — :meth:`CompanionAdapter.mount_health` serves the declared ``health_path``
   with status < 400 once the server is up; its body carries the readiness report (story 9).
4. **secret intake** — the client builders read the worker-injected env and hand the Surface
   *ready clients only*; the Surface never sees a raw key, sentinel, or source marker (D4,
   story 2/29).
5. **Langfuse client** — bidirectional against the deployment's own connection: read the
   seeded pool (:meth:`reader`, the read seam — with :meth:`read_json` for the raw endpoints
   it does not model) AND emit live traces (:meth:`emitter`, the wall-clock live-emission
   seam) AND the SDK surface (:meth:`langfuse`, the ``lfclient`` seam), all bound to
   ``LANGFUSE_BASE_URL`` + the project keys (D5, story 6/24). The Spool's backdating writer
   is deliberately NOT here — a live surface stamps *now* (portal #208/#211/#213).
6. **LLM client** — :meth:`llm` returns a ready client via the G1 resolution module (#138);
   the adapter owns *resolution* (provider + key), the Surface owns *usage* — including,
   via :meth:`llm`'s optional ``model``, a per-request model a Surface's user picks (a
   deployment-pinned ``LLM_MODEL`` still outranks it) (D6). Legacy-safe: with
   ``LLM_PROVIDER``/``LLM_MODEL`` unset it resolves the historical Anthropic default
   byte-for-byte (story 38).

Plus the **readiness surface** (:meth:`readiness`) settled during decomposition: an
adapter-owned, scenario-agnostic report of "Langfuse write path ok / LLM client bound". It
is what the portal's adapter-lands smoke (slice G7, #143) asserts — keeping "traces land ·
LLM binds" an *adapter contract* rather than a portal-owned scenario assertion. It never
makes a billable LLM call (it only *constructs* the client) and never leaks secret material.

The web-server deps (FastAPI/uvicorn) ride the core ``[companion]`` extra, imported lazily
inside :meth:`serve`/:meth:`mount_health` — this module imports on a bare runtime install,
matching how core lazy-imports ``langfuse``/``anthropic``/``openai``.

What a live surface signs up to overall — invocation, env, health/readiness, links, and
run-state access — is stated once in ``CONTRACT.md`` §"The live surface"; this module is
the adapter half of that section.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from . import llm as _llm

# ---------------------------------------------------------------------------
# 1. Invocation contract (D3) — the fixed launch string, and nothing else
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Invocation:
    """The parsed live-launch arguments: ``--config`` path, bind ``host`` and ``port``."""

    config: str
    host: str
    port: int


def parse_invocation(argv: list[str] | None = None) -> Invocation:
    """Parse the fixed companion invocation ``--config {config} --host 0.0.0.0 --port <p>``.

    Scenario-agnostic: it accepts *exactly* these three flags and nothing else. The portal
    templates only ``{config}`` and never appends pipeline ``--set`` flags (D3); an
    unexpected flag is therefore a contract violation and argparse rejects it (exit 2) rather
    than silently swallowing it. ``--host`` defaults to ``0.0.0.0`` — the value D3 fixes and
    the portal always passes explicitly — while ``--port`` is per-deployment and required.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", "-c", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    ns = parser.parse_args(argv)
    return Invocation(config=ns.config, host=ns.host, port=ns.port)


# ---------------------------------------------------------------------------
# Readiness surface — "Langfuse write path ok / LLM client bound", secret-free
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReadinessReport:
    """Adapter-owned, scenario-agnostic readiness — what the adapter-lands smoke asserts.

    ``detail`` is guaranteed **secret-free**: it carries only the base URL (which arrives as
    plain env, not from Infisical — D4), the resolved provider/model ids (non-secret), the
    *names* of the declared secrets and whether each is present (never a value), and, on
    failure, the exception *type name* only (never its message, which could echo a URL or
    key). No raw key, sentinel, or source marker ever reaches here.
    """

    langfuse_write_ok: bool
    llm_bound: bool
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True only when both the write path and the LLM client bound."""
        return self.langfuse_write_ok and self.llm_bound

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable form served in the health body / read by the smoke."""
        return {
            "ready": self.ok,
            "langfuse_write_ok": self.langfuse_write_ok,
            "llm_bound": self.llm_bound,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# The structural seam — a Protocol a duck-typed adapter object satisfies
# ---------------------------------------------------------------------------
@runtime_checkable
class CompanionAdapterContract(Protocol):
    """The structural contract the portal's live runtime relies on (the #25 eight rows).

    Mirrors the ``Config`` Protocol house style: the seam is published as a Protocol so a
    duck-typed object satisfies it structurally. Core ships :class:`CompanionAdapter` as the
    concrete shell that satisfies it; a kit may substitute its own object of the same shape.
    """

    def langfuse(self) -> Any: ...
    def emitter(self, **kw: Any) -> Any: ...
    def reader(self, **kw: Any) -> Any: ...
    def read_json(self, path: str, params: dict | None = ..., *, throttle: float = ...) -> dict: ...
    def llm(self, model: str | None = ...) -> _llm.LLMClient: ...
    def readiness(self) -> ReadinessReport: ...
    def mount_health(self, app: Any) -> None: ...
    def serve(self, app: Any, *, host: str, port: int) -> None: ...


# ---------------------------------------------------------------------------
# The concrete runtime shell
# ---------------------------------------------------------------------------
class CompanionAdapter:
    """The scenario-agnostic compatibility shell a Companion Surface plugs into.

    Construct it with the kit's config object (any object satisfying core's ``Config``
    Protocol — the adapter reads only ``cfg.target.base_url`` off it) and its manifest
    values, then ask it for ready clients + a bound server + a health route. It adds no
    scenario code and stores no brand constant.
    """

    #: Default health route when a kit declares none. Portal-probeable, status < 400 when up.
    DEFAULT_HEALTH_PATH = "/healthz"

    def __init__(
        self,
        cfg: Any,
        *,
        requires_secrets: list[str] | tuple[str, ...] = (),
        health_path: str = DEFAULT_HEALTH_PATH,
        llm_model_default: str | None = None,
    ):
        self.cfg = cfg
        self.requires_secrets = tuple(requires_secrets)
        self.health_path = health_path
        self.llm_model_default = llm_model_default
        self._langfuse: Any = None
        # Keyed by the model the caller asked for (``None`` = the adapter's own default), so a
        # Surface with a per-request model selector still constructs one SDK client per model.
        self._llm: dict[str | None, _llm.LLMClient] = {}

    # -- connection identity (D5): the deployment's own base URL ----------
    @property
    def base_url(self) -> str:
        """The deployment's Langfuse base URL — ``LANGFUSE_BASE_URL`` env (injected as plain
        env, not Infisical) with the kit config's ``target.base_url`` (core ``Config``
        Protocol) as the local-dev fallback. Trailing slash stripped so callers can
        concatenate paths safely."""
        return (os.environ.get("LANGFUSE_BASE_URL") or self.cfg.target.base_url).rstrip("/")

    # -- 4/5. secret intake -> ready Langfuse clients (bidirectional) -----
    def langfuse(self) -> Any:
        """The Langfuse SDK client (prompts/datasets/query surfaces), bound to the
        deployment's connection. Reuses core's ``lfclient`` construction pattern; the SDK is
        imported lazily so this module loads on a bare runtime install. Cached per adapter."""
        if self._langfuse is None:
            from langfuse import Langfuse

            self._langfuse = Langfuse(
                host=self.base_url,
                public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
                secret_key=os.environ.get("LANGFUSE_SECRET_KEY"),
            )
        return self._langfuse

    def emitter(self, **kw: Any) -> Any:
        """The **live-emission** client: wall-clock traces for this Surface's submissions
        (portal #208). A live surface emits at *now*, so it belongs on the Langfuse SDK and
        not on the Spool's backdating machinery — see
        :mod:`langfuse_synth_core.live.emit`, and the determinism line in ``CONTRACT.md``.
        A fresh emitter each call, keyed to the deployment's connection."""
        from ..live.emit import LiveEmitter

        return LiveEmitter(self.base_url, public_key=os.environ.get("LANGFUSE_PUBLIC_KEY"),
                           secret_key=os.environ.get("LANGFUSE_SECRET_KEY"), **kw)

    def reader(self, **kw: Any) -> Any:
        """The **read seam**: the seeded pool read back as normalised rows, on whichever
        Langfuse read API generation the deployment's target serves (portal #208). This is
        what a Surface composes its pool reads on; :meth:`read_json` stays for the raw
        endpoints the seam does not model (projects, prompts, annotation queues)."""
        from ..read import LangfuseReader

        return LangfuseReader(self.base_url, auth=(os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
                                                   os.environ.get("LANGFUSE_SECRET_KEY", "")),
                              **kw)

    def read_json(self, path: str, params: dict | None = None, *, throttle: float = 0.0) -> dict:
        """A single authenticated **read** against the Langfuse public REST API — the read
        direction of the bidirectional client (the ``lfread`` seam). The Surface composes
        its seeded-pool reads on this primitive (e.g. paginated scores/traces)."""
        from .. import lfread

        return lfread.get_json(self.base_url, path, params, throttle=throttle)

    # -- 6. LLM credential resolution (D6) -> ready client ----------------
    def llm(self, model: str | None = None) -> _llm.LLMClient:
        """The ready LLM client for the environment-selected provider (G1 resolution, #138).
        The adapter owns *resolution* (which provider, which key); the Surface owns *usage*
        (what to prompt — and, via ``model``, which model). Legacy-safe: unset
        ``LLM_PROVIDER``/``LLM_MODEL`` -> the historical Anthropic default, byte-for-byte
        (story 38). Cached per requested model.

        ``model`` is the *caller default* for this call, standing in for the adapter's
        ``llm_model_default`` — for a Surface whose user picks the model per request. The
        precedence is unchanged from the resolution module: a deployment-pinned ``LLM_MODEL``
        still outranks it, so a Surface selector can never escape a pinned deployment.
        """
        if model not in self._llm:
            self._llm[model] = _llm.get_llm(model or self.llm_model_default)
        return self._llm[model]

    # -- readiness surface ------------------------------------------------
    def readiness(self) -> ReadinessReport:
        """Report "Langfuse write path ok / LLM client bound" without a billable LLM call
        and without leaking secret material. Both probes are best-effort: on failure the
        flag is False and only the exception *type name* is recorded (never its message)."""
        lf_ok, lf_err = self._safe(self._probe_langfuse_write)
        llm_ok, llm_err = self._safe(self._probe_llm_bound)

        lf_detail: dict[str, Any] = {"ok": lf_ok, "base_url": self.base_url}
        if lf_err:
            lf_detail["error"] = lf_err
        llm_detail: dict[str, Any] = {"ok": llm_ok}
        llm_detail.update(self._llm_identity())
        if llm_err:
            llm_detail["error"] = llm_err

        detail = {
            "langfuse": lf_detail,
            "llm": llm_detail,
            # names + presence only — never a secret value.
            "secrets_present": {name: name in os.environ for name in self.requires_secrets},
        }
        return ReadinessReport(langfuse_write_ok=lf_ok, llm_bound=llm_ok, detail=detail)

    def _probe_langfuse_write(self) -> None:
        """Prove the write path with an empty OTLP export — auth + endpoint reachability,
        zero spans emitted (the seeded pool is untouched). Raises on any failure.

        The Spool's writer is constructed here rather than exposed on the Adapter. It was a
        public accessor while a kit might still want it; a Surface emits at *now* and rides
        :meth:`emitter`, so the only thing left that needs a Spool writer is this probe
        (portal #211, removed with the batch path in #213)."""
        from ..seed.ingest import Ingestor

        Ingestor.from_env(self.base_url).write_ping()

    def _probe_llm_bound(self) -> None:
        """Prove the LLM client binds by *constructing* the provider SDK client from the
        resolved key — NO completion is made, so no billable call and no token spend."""
        self.llm().bind()

    def _llm_identity(self) -> dict[str, str]:
        """Non-secret provider/model ids for the readiness detail (best-effort)."""
        try:
            provider = _llm.resolve_provider()
            model = _llm.resolve_model(provider, self.llm_model_default)
            return {"provider": provider, "model": model}
        except Exception:  # noqa: BLE001 — identity is diagnostic only; never fail readiness
            return {}

    @staticmethod
    def _safe(probe: Callable[[], None]) -> tuple[bool, str | None]:
        """Run a probe; return ``(ok, error_type_name_or_None)``. The exception *message* is
        deliberately dropped — it could echo a base URL or a key — leaving only the class
        name as a secret-free diagnostic."""
        try:
            probe()
            return True, None
        except Exception as exc:  # noqa: BLE001 — readiness reports failure, never raises
            return False, type(exc).__name__

    # -- 3. health --------------------------------------------------------
    def mount_health(self, app: Any) -> None:
        """Mount the declared ``health_path`` on the (FastAPI/Starlette) app: a GET returning
        status 200 with the readiness report as its JSON body. Liveness (server up) is the
        < 400 the portal probes; the body's ``langfuse_write_ok``/``llm_bound`` are what the
        adapter-lands smoke inspects. Idempotent probes only (no billable LLM call)."""
        from starlette.responses import JSONResponse

        async def _health(_request: Any) -> Any:
            return JSONResponse(self.readiness().as_dict())

        app.add_route(self.health_path, _health, methods=["GET"])

    # -- 2. bind + lifecycle ----------------------------------------------
    def make_server(self, app: Any, *, host: str = "0.0.0.0", port: int) -> Any:
        """Build (but do not run) the uvicorn ``Server`` bound to ``host:port``. Exposed so
        tests — and any caller needing cooperative shutdown — can drive the run loop and set
        ``server.should_exit`` (the flag uvicorn's own SIGTERM/SIGINT handlers set)."""
        import uvicorn

        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        return uvicorn.Server(config)

    def serve(
        self, app: Any, *, host: str = "0.0.0.0", port: int, mount_health: bool = True,
    ) -> None:
        """Bind ``0.0.0.0:<port>`` and run the surface with graceful shutdown on signal.

        Mounts the health route first (unless the kit already did), then runs the uvicorn
        server, whose signal handlers flip ``should_exit`` so the idle-reaper retires the
        container cleanly at TTL/idle (D3, story 5/22). Blocks until shutdown."""
        if mount_health:
            self.mount_health(app)
        self.make_server(app, host=host, port=port).run()

    # -- 1. the full inherit path (acceptance #1) -------------------------
    def run(
        self, app_factory: Callable[["CompanionAdapter"], Any], *, host: str, port: int,
    ) -> None:
        """Everything inherited: build the Surface via the kit's ``app_factory`` (handed this
        adapter for its ready clients), then bind/health/serve. ``host``/``port`` come from
        :func:`parse_invocation`; the kit's ``synth`` dispatcher wires the two together."""
        self.serve(app_factory(self), host=host, port=port)
