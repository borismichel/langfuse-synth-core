"""``synth-authoring conformance`` — the Contract as executable checks (portal #198).

``synth-authoring validate`` lints the manifest's *shape*; this suite proves the kit
*honors* the Contract's policy half — the #187 retargeting-gate precedent (prove it,
don't document it), generalized. Every finding cites the ``CONTRACT.md`` section it
enforces, so a red check is a pointer into the one authoritative document rather than a
rule restated in this tool's own words (portal #196).

The six check groups, and where each rule lives:

* **Manifest declarations** — the live surface declares what the portal relies on
  (ports, health paths; the readiness route must differ from ``/``) and the command
  templates ``{config}`` — §"The live surface" / §"The container invocation".
* **The live command** (executable) — the declared command parses under the fixed
  companion invocation (``--config/--host/--port``), binds ``0.0.0.0`` on the declared
  port, and *rejects* pipeline ``--set`` overrides — the LAN-165 class of failure,
  §"The container invocation".
* **The companion surface** (executable) — the kit's target-shape app factory
  (``create_app(adapter)``) builds against an offline probe adapter and serves both its
  declared health path (the adapter readiness report) and its index, with every secret
  scrubbed from the environment and every adapter client denied — §"The live surface".
* **Per-run anchors** (executable, opt-in) — applied only to kits that declare anchors
  (a payload on the core ``AnchorsIO`` mechanism): the state file resolves from
  ``SYNTH_STATE_DIR`` at *call* time and a fabricated payload really writes/loads there.
  A stateless kit skips these with a note — statelessness is a legitimate contract
  citizen — §"Per-run anchors (opt-in)".
* **The observation-type vocabulary** (static) — every observation type the kit names is
  one of the ten Langfuse recognises. The OTLP wire accepts an unknown value and silently
  files it as something else, where batch ingestion answered ``400`` — §"The spool".
* **Legacy Langfuse surfaces** (static) — whether the kit still reaches an API Langfuse
  removes on 2026-11-16: a deprecated endpoint named in its shipped sources, the
  ``meta.totalItems`` counting technique beside one, and a Spool still written on the batch
  path — §"Reserved-verb semantics (the pipeline)" / §"The spool". This one shipped as a
  nudge and **blocks since #211**: all three kits read through the seam now, so a kit that
  names an endpoint at all is a kit that stops working at the cutover.

``--advisory`` is the pre-portal-kit switch (the #181 runbook-advisories precedent): it
prints the same findings but always exits 0, so EV/Lender run the suite in CI without their
converged-shape debt blocking them, and post-portal kits run it enforcing.

This module rides the ``[authoring]`` extra; the companion serve checks additionally need
the companion web deps and degrade to a printed skip without them.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import importlib
import io
import json
import os
import re
import shlex
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import yaml
from jsonschema import Draft7Validator

from langfuse_synth_core.anchors import STATE_DIR_ENV, STATE_FILENAME, AnchorsIO
from langfuse_synth_core.authoring.validate import load_schema, validate_doc
from langfuse_synth_core.companion.adapter import parse_invocation
from langfuse_synth_core.observation_types import (
    OBSERVATION_TYPES,
    unknown_observation_type,
)

# The target-shape import points (CONTRACT.md §"The target shape, and migration debt"):
# what `synth-authoring new` emits and where the suite looks by default. A kit that keeps
# these elsewhere overrides them with --companion-app / --state-module.
DEFAULT_COMPANION_FACTORY = "synth.companion.app:create_app"
DEFAULT_STATE_MODULE = "synth.state"

# Substituted for the manifest's `{config}` placeholder before parsing — never opened.
PROBE_CONFIG = "conformance-probe.yaml"

# The CONTRACT.md sections findings cite, assembled once so a typo'd heading is
# impossible (both sides cite the document instead of restating it — portal #196).
_CITE_INVOCATION = 'CONTRACT.md §"The container invocation"'
_CITE_LIVE_SURFACE = 'CONTRACT.md §"The live surface"'
_CITE_ANCHORS = 'CONTRACT.md §"Per-run anchors (opt-in)"'
_CITE_TARGET_SHAPE = 'CONTRACT.md §"The target shape, and migration debt"'
_CITE_FILESYSTEM = 'CONTRACT.md §"Filesystem conventions"'
_CITE_SPOOL = 'CONTRACT.md §"The spool"'
_CITE_VERBS = 'CONTRACT.md §"Reserved-verb semantics (the pipeline)"'

# Env var prefixes/names scrubbed around the companion serve checks: the surface must
# build and render with no secret and no deployment selection present (§"The live
# surface" — secret intake is the adapter's job, and the check's adapter is offline).
_SCRUB_PREFIXES = ("LANGFUSE_", "LLM_", "PORTAL_", "LIVE_")
_SCRUB_NAMES = frozenset({"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_METADATA_USER_ID"})


@dataclass
class ConformanceReport:
    """The suite's verdict, in two finding channels plus notes and green check lines.

    ``findings`` is convergence debt — the shape a kit is growing towards. ``--advisory``
    turns those into nudges so a pre-portal kit can adopt the suite before it has converged.

    ``migration_findings`` is the v4 channel, and ``--advisory`` does **not** reach it
    (portal #213). Two rules live here: the kit reaches a Langfuse endpoint that stops
    answering on 2026-11-16, or it names an observation type outside the vocabulary the wire
    accepts silently. Neither is debt anyone is working through — the first is a kit that
    stops working at the cutover, the second is a demo that already tells the wrong story.
    A switch that let either through would make "conformance is enforcing" untrue of exactly
    the checks the migration turned it on for.
    """

    findings: list[str] = field(default_factory=list)
    migration_findings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    @property
    def all_findings(self) -> list[str]:
        """Every finding, migration channel first — findings are read worst-first."""
        return [*self.migration_findings, *self.findings]

    @property
    def ok(self) -> bool:
        return not (self.findings or self.migration_findings)


# --------------------------------------------------------------------------------------
# Manifest declarations (static) — §"The live surface" / §"The container invocation"
# --------------------------------------------------------------------------------------
def _live_components(doc: dict) -> list[dict]:
    comps = doc.get("live_components")
    return [c for c in comps if isinstance(c, dict)] if isinstance(comps, list) else []


def manifest_findings(doc: dict) -> list[str]:
    """Declaration checks beyond the blocking lint: the live-surface fields the portal
    relies on, stated per component so a finding names its manifest location."""
    findings: list[str] = []
    for i, comp in enumerate(_live_components(doc)):
        loc = f"live_components/{i}"
        command = comp.get("command")
        if isinstance(command, str) and "{config}" not in command:
            findings.append(
                f"at {loc}/command: does not template the `{{config}}` placeholder — the "
                f"portal supplies the resolved config path there, and nothing else is "
                f'templated ({_CITE_INVOCATION})'
            )
        health = comp.get("health_path")
        if health is None:
            findings.append(
                f"at {loc}: declares no `health_path` — the portal GETs it at admission "
                f"and in steady state, and the admission smoke reads the adapter "
                f'readiness report from its body ({_CITE_LIVE_SURFACE})'
            )
        elif not (isinstance(health, str) and health.startswith("/")):
            findings.append(
                f"at {loc}/health_path: {health!r} is not an absolute route path "
                f'({_CITE_LIVE_SURFACE})'
            )
        elif health == "/":
            findings.append(
                f"at {loc}/health_path: `/` collides with the surface's own index — the "
                f"target shape points health_path at the adapter's readiness route, "
                f'which must differ from `/` ({_CITE_LIVE_SURFACE})'
            )
    return findings


# --------------------------------------------------------------------------------------
# The live command (executable) — §"The container invocation"
# --------------------------------------------------------------------------------------
def _parse_quietly(args: list[str]) -> Any | None:
    """`parse_invocation` without argparse's stderr/exit — None when the args are
    rejected (which is the *desired* outcome for a `--set` probe)."""
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return parse_invocation(args)
    except SystemExit:
        return None


def _synth_index(tokens: list[str]) -> int | None:
    """Index of the ``synth`` entrypoint token (bare or pathed), or None."""
    for i, tok in enumerate(tokens):
        if tok == "synth" or tok.rsplit("/", 1)[-1] == "synth":
            return i
    return None


def live_command_findings(doc: dict) -> list[str]:
    """Prove each declared live command parses clean under the fixed companion
    invocation — and that a pipeline ``--set`` override is rejected (the D3 hard edge
    that surfaces the LAN-165 class immediately instead of mid-demo).

    Its limit, stated so it is not mistaken for an execution proof (the retarget-gate
    house rule): the command string is parsed with the adapter's own ``parse_invocation``
    — the fixed shape a target-shape kit dispatches through — not by executing the kit's
    runtime dispatcher. A kit whose companion verb parses argv with its own, laxer parser
    would pass here and still be out of contract; the target shape closes that gap by
    routing the verb through the adapter, which is exactly what the scaffold emits."""
    findings: list[str] = []
    for i, comp in enumerate(_live_components(doc)):
        loc = f"live_components/{i}/command"
        command = comp.get("command")
        if not isinstance(command, str):
            continue  # shape errors are the schema lint's territory
        try:
            tokens = shlex.split(command.replace("{config}", PROBE_CONFIG))
        except ValueError as exc:
            findings.append(
                f"at {loc}: does not shell-parse ({exc}) "
                f'({_CITE_INVOCATION})'
            )
            continue
        idx = _synth_index(tokens)
        if idx is None:
            findings.append(
                f"at {loc}: does not invoke the kit's `synth` entrypoint — the companion "
                f"invocation is `synth <verb> --config {{config}} --host 0.0.0.0 --port "
                f'<port>`, verb name kit-chosen ({_CITE_INVOCATION})'
            )
            continue
        verb = tokens[idx + 1] if idx + 1 < len(tokens) else None
        if verb is None or verb.startswith("-"):
            findings.append(
                f"at {loc}: no companion verb after `synth` — the verb name is "
                f"kit-chosen (EV/Lender use `playground`) but the invocation shape "
                f'`synth <verb> --config …` is fixed ({_CITE_INVOCATION})'
            )
            continue
        tail = tokens[idx + 2 :]
        inv = _parse_quietly(tail)
        if inv is None:
            findings.append(
                f"at {loc}: does not parse under the fixed companion invocation — the "
                f"adapter accepts exactly `--config/--host/--port`, so a stray flag "
                f"(e.g. a pipeline `--set`) is rejected at argv parse, the LAN-165 "
                f'failure class ({_CITE_INVOCATION})'
            )
            continue
        if inv.host != "0.0.0.0":
            findings.append(
                f"at {loc}: binds host {inv.host!r} — a live surface must bind 0.0.0.0; "
                f"the portal never port-publishes and reaches the container over the "
                f'internal live network ({_CITE_LIVE_SURFACE})'
            )
        declared_port = comp.get("port")
        if isinstance(declared_port, int) and inv.port != declared_port:
            findings.append(
                f"at {loc}: passes --port {inv.port} but the component declares port "
                f"{declared_port} — the portal probes the declared port "
                f'({_CITE_LIVE_SURFACE})'
            )
        if _parse_quietly(tail + ["--set", "conformance.probe=1"]) is not None:
            findings.append(
                f"at {loc}: accepts a pipeline `--set` override — a live command must "
                f"reject config overrides; runtime configuration of a live component "
                f'rides env only ({_CITE_INVOCATION})'
            )
    return findings


# --------------------------------------------------------------------------------------
# The companion surface (executable) — §"The live surface"
# --------------------------------------------------------------------------------------
class OfflineDenied(RuntimeError):
    """A surface under conformance asked its adapter for a network client."""


@contextlib.contextmanager
def _scrubbed_secret_env():
    """Remove every portal-injected secret/selection var for the duration, restoring
    the ambient environment afterwards — the serve checks prove the surface needs none."""
    removed = {
        name: os.environ.pop(name)
        for name in list(os.environ)
        if name.startswith(_SCRUB_PREFIXES) or name in _SCRUB_NAMES
    }
    try:
        yield
    finally:
        os.environ.update(removed)


def _offline_adapter(health_path: str) -> Any:
    """A ``CompanionAdapter`` that mounts the real health route but denies every client
    and never dials: readiness reports not-ready (liveness is what the check asserts),
    and any client ask from the surface raises :class:`OfflineDenied`."""
    from langfuse_synth_core.companion.adapter import CompanionAdapter, ReadinessReport

    class _OfflineProbeAdapter(CompanionAdapter):
        def langfuse(self) -> Any:
            raise OfflineDenied("adapter.langfuse() asked for during offline conformance")

        def read_json(
            self, path: str, params: dict | None = None, *, throttle: float = 0.0
        ) -> dict:
            raise OfflineDenied("adapter.read_json() asked for during offline conformance")

        def llm(self, model: str | None = None) -> Any:
            raise OfflineDenied("adapter.llm() asked for during offline conformance")

        def readiness(self) -> ReadinessReport:
            return ReadinessReport(
                langfuse_write_ok=False,
                llm_bound=False,
                detail={"conformance": "offline probe adapter — no client, no secret"},
            )

    class _Target:
        base_url = "http://conformance-offline.invalid:9"

    class _Cfg:
        target = _Target()

    return _OfflineProbeAdapter(_Cfg(), health_path=health_path)


def companion_findings(doc: dict, factory: Callable[..., Any]) -> tuple[list[str], list[str]]:
    """Build the surface via ``factory`` against the offline adapter and prove it serves
    its declared health path and renders its index with no network and no secret."""
    findings: list[str] = []
    notes: list[str] = []
    comps = _live_components(doc)
    if not comps:
        notes.append("no live components declared — companion serve checks skipped")
        return findings, notes
    try:
        from starlette.testclient import TestClient
    except ImportError:
        notes.append(
            "companion web deps (starlette/httpx) not installed — serve checks skipped; "
            "install the core [companion] extra to run them"
        )
        return findings, notes
    if len(comps) > 1:
        notes.append("multiple live components declared — only the first is exercised")

    comp = comps[0]
    health_path = comp.get("health_path")
    if not (isinstance(health_path, str) and health_path.startswith("/")):
        health_path = "/healthz"  # declaration findings already cover the manifest side

    with _scrubbed_secret_env():
        adapter = _offline_adapter(health_path)
        try:
            app = factory(adapter)
        except TypeError:
            findings.append(
                "companion app factory does not take the target `create_app(adapter)` "
                "shape — the surface builds on the adapter alone, inheriting its ready "
                f'clients ({_CITE_TARGET_SHAPE})'
            )
            return findings, notes
        except OfflineDenied as exc:
            findings.append(
                f"companion app factory needs a live client to build ({exc}) — a "
                f"surface must construct without network access or secrets "
                f'({_CITE_LIVE_SURFACE})'
            )
            return findings, notes
        adapter.mount_health(app)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(health_path)
        if resp.status_code >= 400:
            findings.append(
                f"declared health path {health_path!r} is not served (status "
                f"{resp.status_code}) — the portal treats status < 400 as up "
                f'({_CITE_LIVE_SURFACE})'
            )
        else:
            try:
                body = resp.json()
            except ValueError:
                body = None
            if not (
                isinstance(body, dict)
                and {"ready", "langfuse_write_ok", "llm_bound"} <= set(body)
            ):
                findings.append(
                    f"health body at {health_path!r} is not the adapter readiness "
                    f"report — the admission smoke reads ready/langfuse_write_ok/"
                    f'llm_bound from it ({_CITE_LIVE_SURFACE})'
                )

        resp = client.get("/")
        if resp.status_code >= 400 or not resp.content:
            findings.append(
                f"index route `/` does not render offline (status {resp.status_code}) "
                f"— a surface serves its routes at `/` without network access or "
                f'secrets ({_CITE_LIVE_SURFACE})'
            )
    return findings, notes


# --------------------------------------------------------------------------------------
# Per-run anchors (executable, opt-in) — §"Per-run anchors (opt-in)"
# --------------------------------------------------------------------------------------
def find_anchor_payloads(state_module: str = DEFAULT_STATE_MODULE) -> list[type] | None:
    """The kit's anchors payload classes (dataclasses on the core ``AnchorsIO``
    mechanism), or ``None`` when the kit declares no anchors — i.e. the state module
    does not exist. Statelessness is a legitimate contract citizen, not a gap."""
    try:
        mod = importlib.import_module(state_module)
    except ModuleNotFoundError as exc:
        missing = exc.name or state_module
        if state_module == missing or state_module.startswith(missing + "."):
            return None
        raise
    return [
        obj
        for obj in vars(mod).values()
        if isinstance(obj, type)
        and issubclass(obj, AnchorsIO)
        and obj is not AnchorsIO
        and dataclasses.is_dataclass(obj)
    ]


@contextlib.contextmanager
def _state_dir_env(value: str | None):
    previous = os.environ.get(STATE_DIR_ENV)
    try:
        if value is None:
            os.environ.pop(STATE_DIR_ENV, None)
        else:
            os.environ[STATE_DIR_ENV] = value
        yield
    finally:
        if previous is None:
            os.environ.pop(STATE_DIR_ENV, None)
        else:
            os.environ[STATE_DIR_ENV] = previous


# Fabrication table for required payload fields (no default): a neutral instance per
# annotation base type. Anything richer is kit territory the suite cannot invent.
_FABRICABLE: dict[str, Callable[[], Any]] = {
    "str": str, "int": int, "float": float, "bool": bool,
    "dict": dict, "list": list, "tuple": tuple,
}


def _fabricate_kwargs(cls: type) -> dict[str, Any] | None:
    """Constructor kwargs for a payload's required fields, or None when a field's type
    is beyond the neutral-value table (the write round-trip is then skipped, noted)."""
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:
            continue
        annotation = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", "")
        base = annotation.split("[", 1)[0].strip()
        maker = _FABRICABLE.get(base)
        if maker is None:
            return None
        kwargs[f.name] = maker()
    return kwargs


def anchors_findings(payloads: list[type]) -> tuple[list[str], list[str]]:
    """Prove each anchors payload writes where the state-dir env points — resolved at
    call time, with the dev fallback intact — including a real save/load round-trip
    when the payload's required fields can be fabricated. A payload that raises while
    probed (e.g. no ``FALLBACK_STATE_DIR`` set) becomes a finding, never a traceback
    out of a kit's CI step."""
    findings: list[str] = []
    notes: list[str] = []
    for cls in payloads:
        label = f"{cls.__module__}.{cls.__qualname__}"
        try:
            _probe_payload(cls, label, findings, notes)
        except Exception as exc:  # noqa: BLE001 — the suite reports, it never crashes
            findings.append(
                f"{label}: raised while probing the anchors mechanism "
                f"({type(exc).__name__}: {exc}) — the payload must resolve and write "
                f"its state file via the core `AnchorsIO` mechanism ({_CITE_ANCHORS})"
            )
    return findings, notes


def _probe_payload(cls: type, label: str, findings: list[str], notes: list[str]) -> None:
    """The per-payload checks; appends to the caller's finding/note lists."""
    with tempfile.TemporaryDirectory() as probe_a, tempfile.TemporaryDirectory() as probe_b:
        with _state_dir_env(probe_a):
            dir_a = Path(cls.state_dir())
            name_a = Path(cls.state_path()).name
        with _state_dir_env(probe_b):
            dir_b = Path(cls.state_dir())
        if name_a != STATE_FILENAME:
            findings.append(
                f"{label}: state file is named {name_a!r}, not the canonical "
                f"{STATE_FILENAME!r} ({_CITE_ANCHORS})"
            )
        if dir_a != Path(probe_a) or dir_b != Path(probe_b):
            findings.append(
                f"{label}: resolves its state dir to {dir_a} ignoring "
                f"{STATE_DIR_ENV} — the location must resolve from the env at call "
                f"time, so anchors land on the spool the portal mounted "
                f"({_CITE_ANCHORS})"
            )
        with _state_dir_env(None):
            fallback = Path(cls.state_dir())
        if str(fallback) in {probe_a, probe_b}:
            findings.append(
                f"{label}: has no dev fallback with {STATE_DIR_ENV} unset — the env "
                f"var overrides the fallback, it does not replace it "
                f"({_CITE_ANCHORS})"
            )

    kwargs = _fabricate_kwargs(cls)
    if kwargs is None:
        notes.append(f"{label}: payload not auto-constructible — write round-trip skipped")
        return
    with tempfile.TemporaryDirectory() as target, _state_dir_env(target):
        cls(**kwargs).save()
        written = Path(target) / STATE_FILENAME
        if not written.is_file():
            findings.append(
                f"{label}: save() did not write {STATE_FILENAME} where "
                f"{STATE_DIR_ENV} points ({target}) ({_CITE_ANCHORS})"
            )
            return
        try:
            json.loads(written.read_text())
            assert cls.exists()
            cls.load()
        except Exception as exc:  # noqa: BLE001 — any failure is the finding
            findings.append(
                f"{label}: written anchors do not load back "
                f"({type(exc).__name__}) — every later reader (verify, the live "
                f"surface, the runbook) must agree on the file ({_CITE_ANCHORS})"
            )


# --------------------------------------------------------------------------------------
# Legacy Langfuse endpoints (blocking) — the v4 migration gate (portal #207, #211)
# --------------------------------------------------------------------------------------
# Langfuse Cloud goes v4-only on 2026-11-16: batch ingestion stops accepting everything but
# scores, and the v3 list endpoints 404. This check answers one question for the whole
# fleet — *does this kit still reach a legacy Langfuse endpoint?* — so the migration cannot
# silently regress and a kit's remaining debt is visible in its own CI.
#
# It shipped **advisory** because every gold kit still read a deprecated endpoint, and a
# blocking check would have gone red across the fleet on the day it landed and taught people
# to ignore it. #211 moved all three kits onto the read seam and the live-emission seam, so
# there is nothing left to be lenient about: this **blocks** now. A kit that reaches for a
# removed endpoint is a kit that stops working on 2026-11-16, and the whole point of the
# seams is that no kit has to name an endpoint to begin with.
#
# Its limit, stated so it is not mistaken for an execution proof (the same house rule as
# `live_command_findings`): this reads the kit's *sources*. A URL assembled at runtime, or
# reached through a helper that names no path, is invisible to it — and a path mentioned in
# a docstring counts even though nothing calls it. Whole-line comments are skipped so a
# note recording a decision is not reported as debt.

#: The endpoint families Langfuse removes, and what replaces each under v4. Every pattern
#: is anchored on the full ``/api/public/…`` path so the surviving neighbours — score
#: configs, prompts, datasets, dataset items, projects, health, and the v4 endpoints
#: themselves — never match.
_DEPRECATED_ENDPOINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"/api/public/(spans|generations|events)\b"),
        "the legacy REST create endpoints go with batch ingestion; core writes every "
        "observation as an OTLP span",
    ),
    (
        re.compile(r"/api/public/traces"),
        "read /api/public/v2/observations instead — v4 has no trace entity, so a trace is "
        "its root observation (filter by traceId, or isRootObservation)",
    ),
    (re.compile(r"/api/public/observations"), "read /api/public/v2/observations instead"),
    (
        re.compile(r"/api/public/sessions"),
        "read /api/public/v2/observations filtered by sessionId instead",
    ),
    (re.compile(r"/api/public/(v2/)?scores"), "read /api/public/v3/scores instead"),
    (re.compile(r"/api/public/metrics"), "read /api/public/v2/metrics instead"),
    (
        re.compile(r"/api/public/datasets/[^\s\"']+/runs"),
        "read /api/public/experiments then /api/public/experiment-items instead",
    ),
    (
        re.compile(r"/api/public/dataset-run-items"),
        "read /api/public/experiment-items instead (the POST has no v4 successor — dataset "
        "runs are created through the experiment runner)",
    ),
)

#: ``POST /api/public/ingestion`` is deprecated **per event type**, not as an endpoint.
#: Langfuse's deprecated-API migration guide states the deprecation "applies only to trace
#: and observation events" and that `score-create` remains supported with no client change
#: required (portal #225, 2026-08-20) — so the score write path is current and correct, and
#: is not something this suite apologises for. These are the types that *are* going.
_INGESTION_ENDPOINT = re.compile(r"/api/public/ingestion")
_DEPRECATED_ENVELOPE_TYPES = re.compile(
    r"""["'](trace-create|span-create|generation-create|event-create|observation-create)["']"""
)
#: The one envelope type that keeps this endpoint. A file that posts to ingestion and names
#: this is visibly posting scores; a file that posts there and names **no** envelope type at
#: all is building one at runtime, which this scan cannot read — and unreadable is not the
#: same as clean, so it is reported rather than passed.
_SCORE_ENVELOPE = re.compile(r"""["']score-create["']""")

#: The counting technique the v4 read APIs cannot serve: they are cursor-paginated and
#: carry no total. Only advised in a file that also reads a dying endpoint — the endpoints
#: that survive (dataset items) still answer ``meta.totalItems`` under v4.
_TOTAL_ITEMS = re.compile(r"totalItems")


def _kit_sources(kit_dir: Path) -> list[Path]:
    """The kit's Python sources — what a deployed container runs, plus whatever sits beside
    it in a kit that keeps its package outside ``src/``.

    ``src/`` is the target shape and the whole checkout is the fallback: over-reading (a
    tool, a test) costs a nudge nobody has to act on, where missing the package would report
    a legacy kit clean. **That trade is the advisory channel's**, and it does not carry to a
    check that blocks — see :func:`_shipped_sources`.
    """
    src = kit_dir / "src"
    root = src if src.is_dir() else kit_dir
    return sorted(path for path in root.rglob("*.py") if ".venv" not in path.parts)


def _shipped_sources(kit_dir: Path) -> list[Path]:
    """:func:`_kit_sources` without the kit's tests — for the checks that block.

    Over-reading is cheap for an advisory and expensive for a finding: a test that names a
    deliberately wrong value (this suite's own tests do exactly that) would redden a kit's
    CI over a line no container ever runs. A kit on the target shape is unaffected either
    way — its tests already sit outside ``src/``.
    """
    return [
        path for path in _kit_sources(kit_dir)
        if "tests" not in path.parts and not path.name.startswith("test_")
    ]


def _readable(paths: list[Path], kit_dir: Path) -> list[tuple[str, str]]:
    """``(kit-relative label, text)`` for each source that can actually be read."""
    out: list[tuple[str, str]] = []
    for path in paths:
        try:
            out.append((path.relative_to(kit_dir).as_posix(), path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return out
def legacy_langfuse_findings(kit_dir: str | Path) -> list[str]:
    """One line for every legacy Langfuse surface the kit still reaches: a deprecated
    endpoint in its **shipped** sources, a deprecated ingestion *envelope type* posted to
    ``/api/public/ingestion``, and the ``meta.totalItems`` counting technique beside either.
    Empty for a v4-native kit.

    "Legacy" is assessed against Langfuse's published deprecation list, not against what
    looks old (portal #213). ``POST /api/public/ingestion`` is on this list only for trace
    and observation events; the `score-create` envelopes core writes there are the supported
    v4 score path, so they come back clean.

    That rule needs to read an envelope *type*, which a kit can build at runtime — so a file
    posting to ingestion while naming no readable type is reported too. Silent on a value
    this scan cannot see would be worse than a false positive here: the same endpoint keeps
    serving one type and refuses the rest, so "which one" is the whole question.

    Shipped sources only (:func:`_shipped_sources`), because this blocks: a test that stands
    up a canned deprecated-API server — which every kit's read-seam suite now does, on
    purpose, to prove its `verify` reads the same on both generations — names those
    endpoints all over the place and no container ever runs it.
    """
    kit_dir = Path(kit_dir)
    sources = _readable(_shipped_sources(kit_dir), kit_dir)
    if not sources:
        return []

    findings: list[str] = []
    for label, text in sources:
        lines = text.splitlines()
        hits: list[tuple[int, str]] = []
        totals: list[int] = []
        envelopes: list[tuple[int, str]] = []
        posts_to_ingestion = False
        ingestion_lineno = 0
        for lineno, line in enumerate(lines, start=1):
            if line.lstrip().startswith("#"):
                continue
            if _INGESTION_ENDPOINT.search(line):
                posts_to_ingestion = True
                ingestion_lineno = ingestion_lineno or lineno
            envelope = _DEPRECATED_ENVELOPE_TYPES.search(line)
            if envelope:
                envelopes.append((lineno, envelope.group(1)))
            for pattern, replacement in _DEPRECATED_ENDPOINTS:
                match = pattern.search(line)
                if match:
                    hits.append((
                        lineno,
                        f"at {label}:{lineno}: names the deprecated Langfuse endpoint "
                        f"`{match.group(0)}`, which stops answering once the target is "
                        f"v4-only (Langfuse Cloud, 2026-11-16) — {replacement} "
                        f"({_CITE_VERBS})",
                    ))
            if _TOTAL_ITEMS.search(line):
                totals.append(lineno)
        # The endpoint alone is not the finding — read-side code names event types when it
        # filters on what landed, and the endpoint is current for scores. What makes it one
        # is a file that *posts* there and either names a dying type, or names no type this
        # scan can read at all.
        if posts_to_ingestion:
            hits.extend(
                (
                    lineno,
                    f"at {label}:{lineno}: posts a `{etype}` envelope to "
                    f"`/api/public/ingestion` — the ingestion deprecation applies to trace "
                    f"and observation events, which stop being accepted once the target is "
                    f"v4-only (Langfuse Cloud, 2026-11-16). Core writes every observation as "
                    f"an OTLP span to /api/public/otel/v1/traces; only `score-create` stays "
                    f"on this endpoint, and it stays there deliberately ({_CITE_SPOOL})",
                )
                for lineno, etype in envelopes
            )
            if not envelopes and not _SCORE_ENVELOPE.search(text):
                hits.append((
                    ingestion_lineno,
                    f"at {label}:{ingestion_lineno}: posts to `/api/public/ingestion` and "
                    f"this scan cannot see which envelope type — no literal one is named in "
                    f"the file, so it is built at runtime. The endpoint keeps serving "
                    f"`score-create` past the cutover and refuses trace and observation "
                    f"events, so which it is decides whether this kit still works on "
                    f"2026-11-16. Post scores through core's `score_event`, or name the type "
                    f"where it can be read ({_CITE_SPOOL})",
                ))
        if hits:
            hits.extend(
                (
                    lineno,
                    f"at {label}:{lineno}: counts with `meta.totalItems` — the v4 read APIs "
                    f"are cursor-paginated and carry no total, so re-pointing a URL does "
                    f"not restore this number; count what you read, or aggregate it "
                    f"through the Metrics API ({_CITE_VERBS})",
                )
                for lineno in totals
            )
            findings.extend(text for _, text in sorted(hits, key=lambda h: h[0]))

    return findings


# --------------------------------------------------------------------------------------
# The observation-type vocabulary (static, blocking) — portal #217
# --------------------------------------------------------------------------------------
# Batch ingestion accepted three observation types and rejected the rest with a `400`. The
# OTLP wire that replaces it accepts anything: an unrecognised `langfuse.observation.type`
# is filed as a SPAN, or as a GENERATION when the span carries a model, with nothing
# reported. So a typo'd tool step that names a model lands in cost and usage views and the
# demo tells a different story than its author wrote.
#
# This restores the rejection at authoring time, and it BLOCKS — unlike the legacy-endpoint
# group above. That is the distinction: a deprecated endpoint is migration debt the whole
# fleet is working through, where a type outside the vocabulary is a defect in this kit,
# fixable in the same edit that surfaces it. The gold kits pass it today.
#
# Its limit, stated so it is not mistaken for an execution proof: this reads literals in the
# kit's *sources*. A type assembled at runtime, or read from data, is invisible here — those
# are caught at run time by `observation_types.checked_observation_type`, which the span
# builder and the live seam both run. The two layers cover each other; neither is redundant.

#: The two keywords a kit names an observation type under, and they are not read alike.
#: ``obs_type`` is core's event-builder keyword: core lowercases it for the wire, so a kit's
#: uppercase (the batch enum's spelling) is correct there. ``as_type`` is the Langfuse SDK's,
#: on the live seam — the SDK writes it verbatim, so ``AGENT`` really does land as a SPAN and
#: case is part of what is checked.
_NORMALISED_KEYWORD = "obs_type"
_VERBATIM_KEYWORD = "as_type"
_TYPE_KEYWORDS = (_NORMALISED_KEYWORD, _VERBATIM_KEYWORD)


def _defaults(args: ast.arguments) -> Iterator[tuple[str, ast.expr]]:
    """``(param_name, default_node)`` for every parameter of ``args`` that has a default."""
    positional = args.posonlyargs + args.args
    for arg, default in zip(positional[len(positional) - len(args.defaults):], args.defaults):
        yield arg.arg, default
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if default is not None:
            yield arg.arg, default


def _named_types(source: str) -> list[tuple[int, str, str]]:
    """``(lineno, keyword, literal)`` for every observation type this source names outright.

    Three places a kit spells one: the keyword at a call, a helper's default for it, and a
    local assigned before being passed through (Lender's trace builders are that shape).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # a kit that does not parse fails its own gates long before this one

    sites: list[tuple[int, str, str]] = []

    def record(keyword: str, node: ast.expr | None) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sites.append((node.lineno, keyword, node.value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg in _TYPE_KEYWORDS:
                    record(keyword.arg, keyword.value)
        elif isinstance(node, ast.arguments):
            for name, default in _defaults(node):
                if name in _TYPE_KEYWORDS:
                    record(name, default)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in _TYPE_KEYWORDS:
                    record(target.id, node.value)
    return sorted(set(sites))


def observation_type_findings(kit_dir: str | Path) -> tuple[list[str], list[str]]:
    """Findings for every observation type the kit names that Langfuse does not recognise.

    The refusal is :func:`observation_types.unknown_observation_type`'s, verbatim — this
    adds only where the value was found and, for the SDK keyword, that nothing will
    lowercase it.
    """
    kit_dir = Path(kit_dir)
    findings: list[str] = []
    checked = 0
    for label, source in _readable(_shipped_sources(kit_dir), kit_dir):
        for lineno, keyword, literal in _named_types(source):
            checked += 1
            written = literal.lower() if keyword == _NORMALISED_KEYWORD else literal
            if written in OBSERVATION_TYPES:
                continue
            verbatim = "" if keyword == _NORMALISED_KEYWORD else (
                f" `{_VERBATIM_KEYWORD}` reaches Langfuse through the SDK exactly as "
                f"written, so nothing lowercases this one for you."
            )
            findings.append(
                f"at {label}:{lineno}: {unknown_observation_type(literal)}.{verbatim} "
                f"({_CITE_SPOOL})"
            )
    notes = [] if checked else [
        "no observation type is named outright in the kit's sources — nothing for the "
        "vocabulary check to read, so it claims nothing (a kit of spans, generations and "
        "events names none; a type assembled at runtime is caught at seed time instead)"
    ]
    return findings, notes


# --------------------------------------------------------------------------------------
# The whole suite over one kit checkout
# --------------------------------------------------------------------------------------
def _resolve_factory(ref: str) -> tuple[Callable[..., Any] | None, str | None]:
    """Import a ``module.path:attr`` app-factory ref; on failure, the finding text."""
    module_name, _, attr = ref.partition(":")
    try:
        factory = getattr(importlib.import_module(module_name), attr or "create_app")
    except (ImportError, AttributeError) as exc:
        return None, (
            f"target-shape companion app factory `{ref}` is not importable "
            f"({type(exc).__name__}: {exc}) — the live surface builds via "
            f"`create_app(adapter)` in the kit's companion package "
            f'({_CITE_TARGET_SHAPE})'
        )
    return factory, None


def run_conformance(
    kit_dir: str | Path,
    *,
    companion_factory: str = DEFAULT_COMPANION_FACTORY,
    state_module: str = DEFAULT_STATE_MODULE,
) -> ConformanceReport:
    """Run every conformance check against the kit checkout at ``kit_dir``.

    The kit's package must be importable (kit CI installs it; the CLI also puts the
    kit's ``src/`` on ``sys.path``). Findings block or advise per the caller's mode —
    the report itself carries no verdict policy."""
    report = ConformanceReport()
    manifest = Path(kit_dir) / "usecase.yaml"
    if not manifest.is_file():
        report.findings.append(
            f"no usecase.yaml at {Path(kit_dir)} — the manifest at the repo root is the "
            f'only integration surface ({_CITE_FILESYSTEM})'
        )
        return report
    try:
        doc = yaml.safe_load(manifest.read_text())
    except yaml.YAMLError as exc:
        report.findings.append(f"usecase.yaml does not parse: {exc}")
        return report
    if not isinstance(doc, dict):
        report.findings.append("usecase.yaml is not a mapping / object")
        return report

    lint = validate_doc(doc, Draft7Validator(load_schema()))
    if lint:
        report.findings.extend(f"synth-authoring validate: {e.strip()}" for e in lint)
    else:
        report.passed.append(
            "manifest passes the blocking Contract lint (synth-authoring validate)"
        )

    declared = manifest_findings(doc)
    report.findings.extend(declared)
    commands = live_command_findings(doc)
    report.findings.extend(commands)
    if _live_components(doc) and not (declared or commands):
        report.passed.append(
            "live surface declared per contract; command parses clean under the fixed "
            "companion invocation and rejects `--set`"
        )

    if _live_components(doc):
        factory, err = _resolve_factory(companion_factory)
        if err:
            report.findings.append(err)
        else:
            findings, notes = companion_findings(doc, factory)
            report.findings.extend(findings)
            report.notes.extend(notes)
            if not findings:
                report.passed.append(
                    "companion serves its declared health path and renders its index "
                    "offline, secret-free"
                )
    else:
        report.notes.append("no live components declared — companion serve checks skipped")

    try:
        payloads = find_anchor_payloads(state_module)
    except Exception as exc:  # noqa: BLE001 — a broken state module is a finding
        report.findings.append(
            f"anchors state module `{state_module}` failed to import "
            f"({type(exc).__name__}: {exc})"
        )
        payloads = None
    else:
        if payloads is None:
            report.notes.append(
                f"stateless kit — no `{state_module}` module; anchors checks skipped "
                f"(statelessness is a legitimate contract citizen, {_CITE_ANCHORS})"
            )
        elif not payloads:
            report.findings.append(
                f"`{state_module}` exists but declares no anchors payload on the core "
                f"`AnchorsIO` mechanism — the read/write plumbing ships once, in core "
                f'({_CITE_ANCHORS})'
            )
        else:
            findings, notes = anchors_findings(payloads)
            report.findings.extend(findings)
            report.notes.extend(notes)
            if not findings:
                report.passed.append(
                    "anchors write where the state-dir env points, resolved at call time"
                )

    obs_findings, obs_notes = observation_type_findings(kit_dir)
    report.migration_findings.extend(obs_findings)
    report.notes.extend(obs_notes)
    if not (obs_findings or obs_notes):
        report.passed.append(
            "every observation type the kit names is one Langfuse recognises"
        )

    legacy = legacy_langfuse_findings(kit_dir)
    report.migration_findings.extend(legacy)
    if not legacy:
        report.passed.append(
            "no deprecated Langfuse endpoint reached: observations go out as OTLP spans, "
            "scores as `score-create`, and every read goes through the read seam"
        )
    return report


# --------------------------------------------------------------------------------------
# CLI — `synth-authoring conformance [kit_dir] [--advisory] ...`
# --------------------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The subcommand's arguments — shared between the dispatcher and :func:`run`."""
    parser.add_argument(
        "kit", nargs="?", default=".",
        help="path to the kit checkout to check (default: cwd)",
    )
    parser.add_argument(
        "--advisory", action="store_true",
        help="report convergence findings without failing — the pre-portal-kit mode: "
        "adoption never blocks CI before a kit has converged (portal #198). Does NOT "
        "cover the v4-migration checks, which always block (portal #213)",
    )
    parser.add_argument(
        "--companion-app", default=DEFAULT_COMPANION_FACTORY, metavar="module:factory",
        help=f"the kit's companion app factory (default: {DEFAULT_COMPANION_FACTORY})",
    )
    parser.add_argument(
        "--state-module", default=DEFAULT_STATE_MODULE, metavar="module",
        help=f"the kit's anchors payload module (default: {DEFAULT_STATE_MODULE}); "
        "a kit without one is stateless and skips the anchors checks",
    )


def execute(args: argparse.Namespace) -> int:
    """Run the suite and print the verdict.

    Enforcing mode exits 1 on any finding. ``--advisory`` downgrades the convergence
    findings to nudges (the #181 nudge-never-block channel) but never the v4-migration
    ones — see :class:`ConformanceReport`.
    """
    kit_dir = Path(args.kit)
    src = kit_dir / "src"
    inserted: str | None = None
    if src.is_dir() and str(src) not in sys.path:
        inserted = str(src)
        sys.path.insert(0, inserted)
    try:
        report = run_conformance(
            kit_dir,
            companion_factory=args.companion_app,
            state_module=args.state_module,
        )
    finally:
        if inserted is not None and inserted in sys.path:
            sys.path.remove(inserted)

    print(f"conformance: {kit_dir / 'usecase.yaml'}")
    for line in report.passed:
        print(f"  ✓ {line}")
    for line in report.notes:
        print(f"  · {line}")
    for line in report.migration_findings:
        print(f"  ✗ {line}")
    marker = "⚠ advisory" if args.advisory else "✗"
    for line in report.findings:
        print(f"  {marker} {line}")

    if report.ok:
        print("✓ conformance: the Contract holds")
        return 0
    if report.migration_findings:
        print(
            f"✗ conformance: {len(report.migration_findings)} v4-migration finding(s)"
            + (
                f" (plus {len(report.findings)} advisory)" if args.advisory and report.findings
                else f" of {len(report.all_findings)} finding(s)" if report.findings
                else ""
            )
            + " — the migration checks do not take --advisory (portal #213)"
        )
        return 1
    if args.advisory:
        print(
            f"⚠ conformance: {len(report.findings)} finding(s) — advisory-first kit, "
            f"reported without blocking (portal #198)"
        )
        return 0
    print(f"✗ conformance: {len(report.findings)} finding(s)")
    return 1


def run(argv: list[str] | None = None) -> int:
    """Standalone entry (`python -m` / tests); the dispatcher wires :func:`execute`."""
    parser = argparse.ArgumentParser(
        prog="synth-authoring conformance",
        description="the Contract as executable checks against one kit checkout",
    )
    add_arguments(parser)
    return execute(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(run())
