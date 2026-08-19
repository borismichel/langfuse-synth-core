"""``synth-authoring conformance`` — the Contract as executable checks (portal #198).

``synth-authoring validate`` lints the manifest's *shape*; this suite proves the kit
*honors* the Contract's policy half — the #187 retargeting-gate precedent (prove it,
don't document it), generalized. Every finding cites the ``CONTRACT.md`` section it
enforces, so a red check is a pointer into the one authoritative document rather than a
rule restated in this tool's own words (portal #196).

The four check groups, and where each rule lives:

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

**Advisory-first for the pre-portal kits** (the #181 runbook-advisories precedent):
``--advisory`` prints the same findings but always exits 0, so EV/Lender run the suite in
CI from day one without converged-shape violations blocking them. Post-portal kits run it
enforcing. This module rides the ``[authoring]`` extra; the companion serve checks
additionally need the companion web deps and degrade to a printed skip without them.
"""

from __future__ import annotations

import argparse
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
from typing import Any, Callable

import yaml
from jsonschema import Draft7Validator

from langfuse_synth_core.anchors import STATE_DIR_ENV, STATE_FILENAME, AnchorsIO
from langfuse_synth_core.authoring.validate import load_schema, validate_doc
from langfuse_synth_core.companion.adapter import parse_invocation

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
    """The suite's verdict: blocking findings, advisories, notes, green check lines.

    ``advisories`` is the nudge-never-block channel (the #181 runbook-advisories
    precedent): reported in every mode, never part of :attr:`ok`. The v4 legacy-endpoint
    check rides it, because every kit in the fleet still reads a deprecated endpoint while
    the migration is in flight and none of them may go red for it (portal #207).
    """

    findings: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


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

        def ingestor(self, **kw: Any) -> Any:
            raise OfflineDenied("adapter.ingestor() asked for during offline conformance")

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
# Legacy Langfuse endpoints (advisory) — the v4 migration tracker (portal #207)
# --------------------------------------------------------------------------------------
# Langfuse Cloud goes v4-only on 2026-11-16: batch ingestion stops accepting everything but
# scores, and the v3 list endpoints 404. This check answers one question for the whole
# fleet — *does this kit still reach a legacy Langfuse endpoint?* — so the migration cannot
# silently regress and a kit's remaining debt is visible in its own CI.
#
# ADVISORY at this stage, and that is the point: every gold kit still reads a deprecated
# endpoint until #211 moves verify onto the read seam, and the suite runs enforcing in at
# least one kit's CI. A blocking check here would go red across the fleet on the day it
# shipped and teach people to ignore it.
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
        re.compile(r"/api/public/ingestion"),
        "core posts OTLP spans to /api/public/otel/v1/traces on the v4 write path; only "
        "score creation stays on the ingestion endpoint",
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

#: The kit-set write-path pin (core ``seed.writepath``) that makes a Spool v4-native.
_OTLP_PIN = re.compile(r"""set_spool_write_path\(\s*(?:\w+\.)?(?:OTLP|["']otlp["'])""")

#: The counting technique the v4 read APIs cannot serve: they are cursor-paginated and
#: carry no total. Only advised in a file that also reads a dying endpoint — the endpoints
#: that survive (dataset items) still answer ``meta.totalItems`` under v4.
_TOTAL_ITEMS = re.compile(r"totalItems")


def _kit_sources(kit_dir: Path) -> list[Path]:
    """The kit's shipped Python sources — what a deployed container actually runs."""
    src = kit_dir / "src"
    root = src if src.is_dir() else kit_dir
    return sorted(path for path in root.rglob("*.py") if ".venv" not in path.parts)


def legacy_endpoint_advisories(kit_dir: str | Path) -> list[str]:
    """Advisory lines for every legacy Langfuse surface the kit still reaches: a deprecated
    endpoint in its sources, the ``meta.totalItems`` counting technique beside one, and a
    Spool still written on the batch path. Empty for a v4-native kit."""
    kit_dir = Path(kit_dir)
    sources = _kit_sources(kit_dir)
    if not sources:
        return []

    advisories: list[str] = []
    pinned_otlp = False
    for path in sources:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        label = path.relative_to(kit_dir).as_posix()
        hits: list[tuple[int, str]] = []
        totals: list[int] = []
        for lineno, line in enumerate(lines, start=1):
            if line.lstrip().startswith("#"):
                continue
            if _OTLP_PIN.search(line):
                pinned_otlp = True
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
            advisories.extend(text for _, text in sorted(hits, key=lambda h: h[0]))

    if not pinned_otlp:
        advisories.append(
            "the Spool is still written on the batch write path (legacy ingestion) — no "
            "kit-set `set_spool_write_path(OTLP)` in the kit's sources. Langfuse rejects every "
            "envelope type but `score-create` once the target is v4-only "
            f"(2026-11-16); core's docs/WRITE_PATHS.md carries the cutover ({_CITE_SPOOL})"
        )
    return advisories


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

    report.advisories.extend(legacy_endpoint_advisories(kit_dir))
    if not report.advisories:
        report.passed.append(
            "no legacy Langfuse endpoint reached: the Spool is written on the OTLP path "
            "and every read names a v4 API"
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
        help="report findings without failing (exit 0) — the pre-portal-kit mode: "
        "adoption never blocks CI before a kit has converged (portal #198)",
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
    """Run the suite and print the verdict. Enforcing mode exits 1 on findings;
    ``--advisory`` always exits 0 (the #181 nudge-never-block channel)."""
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
    for line in report.advisories:
        print(f"  ⚠ advisory {line}")
    marker = "⚠ advisory" if args.advisory else "✗"
    for line in report.findings:
        print(f"  {marker} {line}")

    if report.advisories:
        print(
            f"⚠ conformance: {len(report.advisories)} v4-migration advisory(ies) — "
            f"reported, never blocking at this stage (portal #207)"
        )
    if report.ok:
        print("✓ conformance: the Contract holds")
        return 0
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
