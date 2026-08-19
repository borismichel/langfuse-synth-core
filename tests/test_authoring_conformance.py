"""``synth-authoring conformance`` — the Contract as executable checks (portal #198).

The suite proves a kit honors the Contract instead of documenting that it should (the
#187 retargeting-gate precedent), so these tests mirror the ticket's acceptance criteria:

* a target-shape kit (``synth-authoring new --companion --anchors``) passes clean;
* a stateless kit passes — statelessness is a legitimate contract citizen, so the
  anchors checks skip rather than fail (the support-kit case);
* deliberately breaking a contract rule surfaces as a finding that cites ``CONTRACT.md``;
* advisory mode reports the same findings but never blocks (exit 0 — the #181
  runbook-advisories precedent for the pre-portal kits).

Runs under the ``[authoring]`` extra plus the companion web deps (fastapi/starlette),
both of which core's own dev env installs; the module skips without them.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="conformance ships in the [authoring] extra; not on a runtime-only job",
)

needs_companion_deps = pytest.mark.skipif(
    importlib.util.find_spec("fastapi") is None
    or importlib.util.find_spec("httpx") is None,
    reason="the companion serve checks drive a TestClient (fastapi/httpx)",
)


# --------------------------------------------------------------------------------------
# A minimal in-memory manifest in the target shape, mutated per test.
# --------------------------------------------------------------------------------------
def target_manifest() -> dict:
    return {
        "schema_version": 1,
        "slug": "probe-kit",
        "name": "Probe Kit",
        "tagline": "conformance fixture",
        "vertical": "testing",
        "story": "fixture",
        "langfuse_features": ["tracing"],
        "target": {"project_hint": "demo", "supports": ["cloud_eu"]},
        "base_config": {"default": "config/demo.yaml"},
        "config_schema": {
            "type": "object",
            "properties": {
                "generation.target_traces": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100000,
                    "default": 100,
                    "title": "Target traces",
                    "description": "volume knob",
                }
            },
            "required": [],
        },
        "pipeline": [
            {"id": "seed", "run": "synth seed --config {config}"},
            {"id": "verify", "run": "synth verify --config {config}", "fatal": True},
        ],
        "artifacts": [
            {"path": "DEMO_SCRIPT.md", "render": "markdown", "title": "Presenter Runbook"}
        ],
        "llm": {"providers": ["anthropic"]},
        "live_components": [
            {
                "name": "companion",
                "command": "synth companion --config {config} --host 0.0.0.0 --port 8080",
                "port": 8080,
                "health_path": "/healthz",
                "requires_secrets": [
                    "LANGFUSE_PUBLIC_KEY",
                    "LANGFUSE_SECRET_KEY",
                    "LLM_API_KEY",
                ],
                "routes": [{"path": "/", "title": "console"}],
            }
        ],
    }


# --------------------------------------------------------------------------------------
# Manifest declarations (static) — CONTRACT.md §"The live surface"
# --------------------------------------------------------------------------------------
def test_target_manifest_has_no_manifest_findings():
    from langfuse_synth_core.authoring.conformance import manifest_findings

    assert manifest_findings(target_manifest()) == []


def test_health_path_equal_to_root_is_a_finding():
    """EV/Lender's legacy `health_path: /` collides with the index — the target shape
    points health at the adapter readiness route, which must differ from `/`."""
    from langfuse_synth_core.authoring.conformance import manifest_findings

    doc = target_manifest()
    doc["live_components"][0]["health_path"] = "/"
    findings = manifest_findings(doc)
    assert len(findings) == 1
    assert "health_path" in findings[0]
    assert 'CONTRACT.md §"The live surface"' in findings[0]


def test_missing_health_path_is_a_finding():
    from langfuse_synth_core.authoring.conformance import manifest_findings

    doc = target_manifest()
    del doc["live_components"][0]["health_path"]
    findings = manifest_findings(doc)
    assert len(findings) == 1
    assert "health_path" in findings[0]


def test_command_without_config_placeholder_is_a_finding():
    from langfuse_synth_core.authoring.conformance import manifest_findings

    doc = target_manifest()
    doc["live_components"][0]["command"] = (
        "synth companion --config config/demo.yaml --host 0.0.0.0 --port 8080"
    )
    findings = manifest_findings(doc)
    assert any("{config}" in f for f in findings)
    assert any('CONTRACT.md §"The container invocation"' in f for f in findings)


def test_stateless_manifest_without_live_components_is_clean():
    from langfuse_synth_core.authoring.conformance import manifest_findings

    doc = target_manifest()
    del doc["live_components"]
    assert manifest_findings(doc) == []


# --------------------------------------------------------------------------------------
# The live command (executable) — CONTRACT.md §"The container invocation"
# --------------------------------------------------------------------------------------
def test_target_live_command_parses_clean():
    from langfuse_synth_core.authoring.conformance import live_command_findings

    assert live_command_findings(target_manifest()) == []


def test_live_command_with_a_baked_in_set_flag_is_a_finding():
    """The LAN-165 class: a `--set` in a live command kills the surface on argv parse."""
    from langfuse_synth_core.authoring.conformance import live_command_findings

    doc = target_manifest()
    doc["live_components"][0]["command"] = (
        "synth companion --config {config} --host 0.0.0.0 --port 8080 --set a=1"
    )
    findings = live_command_findings(doc)
    assert len(findings) == 1
    assert 'CONTRACT.md §"The container invocation"' in findings[0]


def test_live_command_port_must_match_the_declared_port():
    from langfuse_synth_core.authoring.conformance import live_command_findings

    doc = target_manifest()
    doc["live_components"][0]["command"] = (
        "synth companion --config {config} --host 0.0.0.0 --port 9999"
    )
    findings = live_command_findings(doc)
    assert len(findings) == 1
    assert "9999" in findings[0] and "8080" in findings[0]


def test_live_command_must_bind_all_interfaces():
    from langfuse_synth_core.authoring.conformance import live_command_findings

    doc = target_manifest()
    doc["live_components"][0]["command"] = (
        "synth companion --config {config} --host 127.0.0.1 --port 8080"
    )
    findings = live_command_findings(doc)
    assert len(findings) == 1
    assert "0.0.0.0" in findings[0]


def test_live_command_must_invoke_the_kit_synth_entrypoint():
    from langfuse_synth_core.authoring.conformance import live_command_findings

    doc = target_manifest()
    doc["live_components"][0]["command"] = (
        "uvicorn synth.app:app --host 0.0.0.0 --port 8080"
    )
    findings = live_command_findings(doc)
    assert len(findings) == 1
    assert "synth" in findings[0]


def test_live_command_without_a_companion_verb_is_a_finding():
    """`synth --config …` has no verb token — a distinct defect from a non-synth command,
    and the finding must say so rather than mis-report a parse failure."""
    from langfuse_synth_core.authoring.conformance import live_command_findings

    doc = target_manifest()
    doc["live_components"][0]["command"] = (
        "synth --config {config} --host 0.0.0.0 --port 8080"
    )
    findings = live_command_findings(doc)
    assert len(findings) == 1
    assert "verb" in findings[0]


def test_kit_chosen_verb_names_are_fine():
    """EV/Lender use `playground`; the verb name is kit-chosen, only the shape is fixed."""
    from langfuse_synth_core.authoring.conformance import live_command_findings

    doc = target_manifest()
    doc["live_components"][0]["command"] = (
        "synth playground --config {config} --host 0.0.0.0 --port 8080"
    )
    assert live_command_findings(doc) == []


# --------------------------------------------------------------------------------------
# The companion serve checks (executable) — CONTRACT.md §"The live surface"
# --------------------------------------------------------------------------------------
@needs_companion_deps
def test_target_shape_companion_serves_health_and_index_offline():
    from langfuse_synth_core.authoring.conformance import companion_findings

    def create_app(adapter):
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse

        app = FastAPI()

        @app.get("/", response_class=HTMLResponse)
        async def _root() -> str:
            return "<h1>console</h1>"

        return app

    findings, notes = companion_findings(target_manifest(), create_app)
    assert findings == []


@needs_companion_deps
def test_companion_index_needing_the_network_is_a_finding():
    """`renders its index without network access or secrets` — an index route that dials
    out through the adapter fails against the offline probe adapter."""
    from langfuse_synth_core.authoring.conformance import companion_findings

    def create_app(adapter):
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse

        app = FastAPI()

        @app.get("/", response_class=HTMLResponse)
        async def _root() -> str:
            adapter.read_json("/api/public/projects")  # a network read at render time
            return "<h1>console</h1>"

        return app

    findings, _ = companion_findings(target_manifest(), create_app)
    assert len(findings) == 1
    assert "index" in findings[0]


@needs_companion_deps
def test_companion_factory_not_taking_the_adapter_is_a_finding():
    """The EV/Lender migration-debt shape `create_app(cfg, adapter)` is not the target
    `create_app(adapter)` — surfaced, and advisory for the pre-portal kits."""
    from langfuse_synth_core.authoring.conformance import companion_findings

    def create_app(cfg, adapter):  # legacy two-arg shape
        raise AssertionError("never built")

    findings, _ = companion_findings(target_manifest(), create_app)
    assert len(findings) == 1
    assert "create_app(adapter)" in findings[0]


@needs_companion_deps
def test_companion_checks_skip_when_no_live_components():
    from langfuse_synth_core.authoring.conformance import companion_findings

    doc = target_manifest()
    del doc["live_components"]
    findings, notes = companion_findings(doc, lambda adapter: None)
    assert findings == []
    assert any("no live components" in n for n in notes)


@needs_companion_deps
def test_companion_check_scrubs_secrets_from_the_environment(monkeypatch):
    """The serve check must not let ambient secrets leak into the surface under test."""
    from langfuse_synth_core.authoring.conformance import companion_findings

    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-ambient")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient")
    seen: dict = {}

    def create_app(adapter):
        import os

        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse

        seen["secret"] = os.environ.get("LANGFUSE_SECRET_KEY")
        seen["anthropic"] = os.environ.get("ANTHROPIC_API_KEY")
        app = FastAPI()

        @app.get("/", response_class=HTMLResponse)
        async def _root() -> str:
            return "ok"

        return app

    findings, _ = companion_findings(target_manifest(), create_app)
    assert findings == []
    assert seen == {"secret": None, "anthropic": None}
    # ...and the ambient environment is restored afterwards.
    import os

    assert os.environ["LANGFUSE_SECRET_KEY"] == "sk-ambient"


# --------------------------------------------------------------------------------------
# Anchors (executable, opt-in) — CONTRACT.md §"Per-run anchors (opt-in)"
# --------------------------------------------------------------------------------------
def _payload_class(fallback: Path):
    from langfuse_synth_core.anchors import AnchorsIO

    @dataclass
    class RunState(AnchorsIO):
        FALLBACK_STATE_DIR: ClassVar[Path] = fallback

        base_url: str = ""
        target_traces: int = 0
        summary: dict = field(default_factory=dict)

    return RunState


def test_anchors_payload_on_the_core_mechanism_passes(tmp_path):
    from langfuse_synth_core.authoring.conformance import anchors_findings

    findings, notes = anchors_findings([_payload_class(tmp_path / "spool")])
    assert findings == []


def test_anchors_written_where_the_state_dir_env_points(tmp_path, monkeypatch):
    """The executable half: a fabricated payload SAVES under SYNTH_STATE_DIR and loads
    back — proving the write lands where the env points, at call time."""
    from langfuse_synth_core.authoring.conformance import anchors_findings

    cls = _payload_class(tmp_path / "fallback")
    findings, notes = anchors_findings([cls])
    assert findings == []
    # The round-trip really ran (it is a note-worthy skip when fabrication fails).
    assert not any("round-trip skipped" in n for n in notes)


def test_a_payload_that_ignores_the_env_is_a_finding(tmp_path):
    from langfuse_synth_core.anchors import AnchorsIO
    from langfuse_synth_core.authoring.conformance import anchors_findings

    hardwired = tmp_path / "hardwired"

    @dataclass
    class Pinned(AnchorsIO):
        FALLBACK_STATE_DIR: ClassVar[Path] = hardwired
        base_url: str = ""

        @classmethod
        def state_dir(cls) -> Path:  # the defect: env ignored
            return hardwired

        @classmethod
        def state_path(cls) -> str:
            return str(hardwired / ".synth_state.json")

    findings, _ = anchors_findings([Pinned])
    assert findings
    assert any("SYNTH_STATE_DIR" in f for f in findings)
    assert any('CONTRACT.md §"Per-run anchors (opt-in)"' in f for f in findings)


def test_a_payload_that_raises_becomes_a_finding_not_a_crash(tmp_path):
    """A broken payload (e.g. no FALLBACK_STATE_DIR set) must surface as a finding —
    the suite reports, it never tracebacks out of a kit's CI step."""
    from langfuse_synth_core.anchors import AnchorsIO
    from langfuse_synth_core.authoring.conformance import anchors_findings

    @dataclass
    class Broken(AnchorsIO):  # FALLBACK_STATE_DIR deliberately missing
        base_url: str = ""

    findings, _ = anchors_findings([Broken])
    assert len(findings) == 1
    assert "Broken" in findings[0]


def test_required_fields_without_defaults_are_fabricated(tmp_path):
    """EV/Lender payloads have required str/int/dict fields — fabrication must cover
    them so the write round-trip still runs."""
    from langfuse_synth_core.anchors import AnchorsIO
    from langfuse_synth_core.authoring.conformance import anchors_findings

    @dataclass
    class EvLike(AnchorsIO):
        FALLBACK_STATE_DIR: ClassVar[Path] = tmp_path / "spool"

        base_url: str
        run_date: str
        drift_window_days: int
        prompt_versions: dict
        summary: dict = field(default_factory=dict)

    findings, notes = anchors_findings([EvLike])
    assert findings == []
    assert not any("round-trip skipped" in n for n in notes)


def test_stateless_kit_skips_anchors_with_a_note():
    from langfuse_synth_core.authoring.conformance import find_anchor_payloads

    assert find_anchor_payloads("synth_no_such_state_module_xyz") is None


# --------------------------------------------------------------------------------------
# The whole suite against a real scaffolded kit (the flagship), plus the CLI verdict.
# --------------------------------------------------------------------------------------
@pytest.fixture()
def kit_on_path(tmp_path_factory, request):
    """Scaffold a kit and make ITS `synth` package the importable one for the test."""
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    def _make(slug: str, **kw):
        dest = tmp_path_factory.mktemp("kits") / slug
        result = scaffold_kit(slug, dest, **kw)
        src = str(result.dest / "src")
        sys.path.insert(0, src)

        def _cleanup():
            if src in sys.path:
                sys.path.remove(src)
            for name in [n for n in sys.modules if n == "synth" or n.startswith("synth.")]:
                del sys.modules[name]

        request.addfinalizer(_cleanup)
        return result.dest

    return _make


@needs_companion_deps
def test_scaffolded_companion_anchors_kit_passes_clean(kit_on_path):
    from langfuse_synth_core.authoring.conformance import run_conformance

    kit_dir = kit_on_path("conf-full", with_companion=True, with_anchors=True)
    report = run_conformance(kit_dir)
    assert report.findings == [], "\n".join(report.findings)
    assert report.ok


@needs_companion_deps
def test_scaffolded_stateless_kit_passes_with_the_anchors_skip_note(kit_on_path):
    """The support-kit case: companion, no anchors — stateless is a legitimate citizen."""
    from langfuse_synth_core.authoring.conformance import run_conformance

    kit_dir = kit_on_path("conf-stateless", with_companion=True)
    report = run_conformance(kit_dir)
    assert report.findings == [], "\n".join(report.findings)
    assert any("stateless" in n for n in report.notes)


@needs_companion_deps
def test_broken_contract_rule_surfaces_in_the_verdict(kit_on_path, capsys):
    """AC: deliberately breaking a contract rule surfaces in the kit's CI output —
    enforcing mode exits 1 and names the rule; advisory mode prints it but exits 0."""
    from langfuse_synth_core.authoring.conformance import run

    kit_dir = kit_on_path("conf-broken", with_companion=True)
    manifest = kit_dir / "usecase.yaml"
    doc = yaml.safe_load(manifest.read_text())
    doc["live_components"][0]["health_path"] = "/"  # the legacy collision
    manifest.write_text(yaml.safe_dump(doc, sort_keys=False))

    assert run([str(kit_dir)]) == 1
    out = capsys.readouterr().out
    assert "health_path" in out and "CONTRACT.md" in out

    assert run([str(kit_dir), "--advisory"]) == 0
    out = capsys.readouterr().out
    assert "advisory" in out and "health_path" in out


def test_missing_manifest_is_a_finding(tmp_path, capsys):
    from langfuse_synth_core.authoring.conformance import run

    assert run([str(tmp_path)]) == 1
    assert "usecase.yaml" in capsys.readouterr().out


def test_scaffolded_ci_runs_the_conformance_suite_enforcing(kit_on_path):
    """AC: kits run the suite in CI. A scaffolded kit is born in the target shape, so
    its emitted workflow runs the suite enforcing — no `--advisory` carve-out."""
    kit_dir = kit_on_path("conf-ci")
    doc = yaml.safe_load((kit_dir / ".github" / "workflows" / "ci.yml").read_text())
    runs = [step.get("run", "") for step in doc["jobs"]["test"]["steps"]]
    assert "synth-authoring conformance ." in runs, runs


def test_cli_dispatches_conformance(kit_on_path):
    """`synth-authoring conformance <kit>` is wired into the dispatcher."""
    from langfuse_synth_core.authoring.cli import main

    kit_dir = kit_on_path("conf-cli", with_companion=True, with_anchors=True)
    assert main(["conformance", str(kit_dir)]) == 0


# --------------------------------------------------------------------------------------
# Legacy Langfuse endpoints (advisory) — the v4 migration tracker (portal #207)
# --------------------------------------------------------------------------------------
def _stateless_manifest() -> dict:
    doc = target_manifest()
    doc.pop("live_components")
    doc.pop("llm")
    return doc


def _kit_with_sources(tmp_path, **files: str) -> Path:
    """A bare kit tree carrying just the sources the endpoint scan reads."""
    for rel, text in files.items():
        path = tmp_path / "src" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_a_deprecated_read_endpoint_is_an_advisory(tmp_path):
    from langfuse_synth_core.authoring.conformance import legacy_langfuse_advisories

    kit = _kit_with_sources(
        tmp_path,
        **{"synth/verify.py": 'get_json(base, "/api/public/traces", {"limit": 1})\n'},
    )
    advisories = legacy_langfuse_advisories(kit)
    hit = [a for a in advisories if "/api/public/traces" in a]
    assert hit, advisories
    assert "synth/verify.py:1" in hit[0]
    assert "/api/public/v2/observations" in hit[0]  # names the replacement


def test_the_surviving_endpoints_are_not_flagged(tmp_path):
    from langfuse_synth_core.authoring.conformance import legacy_langfuse_advisories

    kit = _kit_with_sources(
        tmp_path,
        **{
            "synth/seed/prompts.py": (
                'PATCH("/api/public/v2/prompts/x/versions/1")\n'
                'GET("/api/public/dataset-items", {"limit": 1})\n'
                'GET("/api/public/score-configs")\n'
                'GET("/api/public/projects")\n'
                'GET("/api/public/v2/observations")\n'
                'GET("/api/public/v3/scores")\n'
                'POST("/api/public/otel/v1/traces")\n'
            ),
            "synth/seed/path.py": "set_spool_write_path(OTLP)\n",
        },
    )
    assert legacy_langfuse_advisories(kit) == []


def test_the_dataset_runs_read_is_flagged_but_the_datasets_list_is_not(tmp_path):
    from langfuse_synth_core.authoring.conformance import legacy_langfuse_advisories

    kit = _kit_with_sources(
        tmp_path,
        **{
            "synth/verify.py": (
                'GET(f"/api/public/datasets/{name}/runs", {"limit": 50})\n'
                'GET("/api/public/v2/datasets")\n'
            ),
            "synth/seed.py": "set_spool_write_path(OTLP)\n",
        },
    )
    advisories = legacy_langfuse_advisories(kit)
    assert len(advisories) == 1, advisories
    assert "/runs" in advisories[0] and "experiment" in advisories[0]


def test_the_legacy_rest_create_endpoints_are_flagged(tmp_path):
    """`POST /spans|/generations|/events` go with batch ingestion — a kit that writes an
    observation itself, rather than through core's builders, has the same debt."""
    from langfuse_synth_core.authoring.conformance import legacy_langfuse_advisories

    kit = _kit_with_sources(
        tmp_path,
        **{"synth/emit.py": 'post(f"{base}/api/public/generations", body)\n',
           "synth/seed.py": "set_spool_write_path(OTLP)\n"},
    )
    advisories = legacy_langfuse_advisories(kit)
    assert len(advisories) == 1 and "/api/public/generations" in advisories[0]


def test_a_kit_still_on_the_batch_write_path_is_an_advisory(tmp_path):
    """The write half: no kit-set OTLP pin means the Spool is still batch-ingested."""
    from langfuse_synth_core.authoring.conformance import legacy_langfuse_advisories

    kit = _kit_with_sources(tmp_path, **{"synth/seed.py": "ingestor.import_spool()\n"})
    assert any("write path" in a and "batch" in a for a in legacy_langfuse_advisories(kit))


def test_the_totalitems_technique_is_flagged_alongside_a_dying_endpoint(tmp_path):
    """`meta.totalItems` is the counting technique the v4 read APIs do not offer — but
    the endpoints that survive still answer it, so it is only advised where a deprecated
    endpoint is read in the same file."""
    from langfuse_synth_core.authoring.conformance import legacy_langfuse_advisories

    kit = _kit_with_sources(
        tmp_path,
        **{
            "synth/verify.py": (
                'traces = get_json(base, "/api/public/traces", {"limit": 1})\n'
                'total = traces.get("meta", {}).get("totalItems")\n'
            ),
            "synth/items.py": (
                'items = get_json(base, "/api/public/dataset-items", {"limit": 1})\n'
                'n = items["meta"]["totalItems"]\n'
            ),
            "synth/seed.py": "set_spool_write_path(OTLP)\n",
        },
    )
    advisories = legacy_langfuse_advisories(kit)
    assert any("totalItems" in a and "verify.py" in a for a in advisories)
    assert not any("totalItems" in a and "items.py" in a for a in advisories)


def test_advisories_never_block_even_in_enforcing_mode(tmp_path, capsys):
    """AC: the legacy-endpoint check is advisory at this stage — every gold kit still
    reads a deprecated endpoint, and none of them may go red for it."""
    from langfuse_synth_core.authoring.conformance import run, run_conformance

    kit = _kit_with_sources(
        tmp_path, **{"synth/verify.py": 'get_json(base, "/api/public/traces")\n'}
    )
    (kit / "usecase.yaml").write_text(yaml.safe_dump(_stateless_manifest()))

    report = run_conformance(kit)
    assert report.advisories, "the deprecated read should surface"
    assert report.findings == [], report.findings
    assert report.ok

    assert run([str(kit)]) == 0                      # enforcing mode, still green
    out = capsys.readouterr().out
    assert "advisory" in out and "/api/public/traces" in out


def test_a_scaffolded_kit_reaches_no_legacy_endpoint(kit_on_path):
    """The two halves of #207 agree: what `synth-authoring new` emits is what the
    conformance suite calls v4-native — born on the OTLP write path, reading the v4 APIs."""
    from langfuse_synth_core.authoring.conformance import run_conformance

    kit_dir = kit_on_path("conf-v4-native")
    report = run_conformance(kit_dir)
    assert report.advisories == [], "\n".join(report.advisories)


# --------------------------------------------------------------------------------------
# The observation-type vocabulary (static, blocking) — portal #217
# --------------------------------------------------------------------------------------
def test_a_type_outside_the_vocabulary_is_a_blocking_finding(tmp_path):
    """The safety net the wire removed. Batch ingestion answered `400` on an unknown
    observation type; OTLP accepts it and files the observation as something else, so the
    rejection has to happen at authoring time instead."""
    from langfuse_synth_core.authoring.conformance import observation_type_findings

    kit = _kit_with_sources(
        tmp_path,
        **{"synth/seed/traces.py": (
            "events.append(observation_event(\n"
            '    obs_id=oid, trace_id=tid, name="rank_docs",\n'
            '    obs_type="genration", start=s, end=e,\n'
            "))\n"
        )},
    )
    findings, _ = observation_type_findings(kit)
    assert len(findings) == 1, findings
    assert "synth/seed/traces.py:3" in findings[0]
    assert "genration" in findings[0]


def test_the_finding_names_the_silent_degradation(tmp_path):
    """AC: an author refused a valid-looking value learns why — Langfuse would have taken
    it and shown a generation, not that some list exists."""
    from langfuse_synth_core.authoring.conformance import observation_type_findings

    kit = _kit_with_sources(tmp_path, **{"synth/x.py": 'observation_event(obs_type="llm")\n'})
    findings, _ = observation_type_findings(kit)
    text = findings[0].lower()
    assert "generation" in text and "model" in text  # what a mistyped step becomes
    assert "guardrail" in text                        # the vocabulary is spelled out
    assert "contract.md" in text                      # cites the rule's home, per #196


def test_the_spelling_the_gold_kits_use_is_accepted(tmp_path):
    """Kits name these with the batch enum's uppercase and core lowercases for the wire, so
    the check reads the value core will write — not the literal's case."""
    from langfuse_synth_core.authoring.conformance import observation_type_findings

    kit = _kit_with_sources(
        tmp_path,
        **{"synth/seed/traces.py": (
            'observation_event(obs_type="AGENT", name="credit_agent")\n'
            'observation_event(obs_type="RETRIEVER", name="policy_search")\n'
            'observation_event(obs_type="tool", name="rate_lookup")\n'
        )},
    )
    findings, notes = observation_type_findings(kit)
    assert findings == [], findings
    assert notes == []


def test_a_default_and_an_assignment_are_checked_too(tmp_path):
    """The other two places a kit spells a type: a helper's default and a local it passes
    through. Lender's trace builders are shaped exactly like this."""
    from langfuse_synth_core.authoring.conformance import observation_type_findings

    kit = _kit_with_sources(
        tmp_path,
        **{"synth/steps.py": (
            'def _step(name, obs_type: str = "retreiver"):\n'
            "    ...\n"
            "\n"
            "def _tool(name):\n"
            '    obs_type = "TOLL"\n'
            "    return observation_event(name=name, obs_type=obs_type)\n"
        )},
    )
    findings, _ = observation_type_findings(kit)
    assert len(findings) == 2, findings
    assert any("retreiver" in f and ":1" in f for f in findings)
    assert any("TOLL" in f.upper() and ":5" in f for f in findings)


def test_a_type_this_scan_cannot_see_is_not_invented(tmp_path):
    """Its stated limit, the same house rule the endpoint scan carries: a value assembled at
    runtime is invisible here. The builder's own guard is what catches those, at seed time."""
    from langfuse_synth_core.authoring.conformance import observation_type_findings

    kit = _kit_with_sources(
        tmp_path,
        **{"synth/steps.py": (
            "for kind in KINDS:\n"
            "    observation_event(name=kind, obs_type=kind.upper())\n"
        )},
    )
    findings, notes = observation_type_findings(kit)
    assert findings == []
    assert notes and "nothing" in notes[0]      # and the suite claims no pass for it


def test_a_kit_that_types_no_observation_gets_a_note_not_a_tick(tmp_path):
    """A kit of spans, generations and events names no type at all — nothing was checked,
    so nothing is claimed."""
    from langfuse_synth_core.authoring.conformance import observation_type_findings

    kit = _kit_with_sources(tmp_path, **{"synth/x.py": "generation_event(model=m)\n"})
    findings, notes = observation_type_findings(kit)
    assert findings == []
    assert notes and "names none" in notes[0]


def test_the_vocabulary_finding_blocks_where_the_v4_advisories_do_not(tmp_path, capsys):
    """Two channels, and this one is the blocking channel: a mistyped type is a defect in
    the kit, not migration debt the fleet is working through."""
    from langfuse_synth_core.authoring.conformance import run, run_conformance

    kit = _kit_with_sources(
        tmp_path,
        **{"synth/seed.py": (
            "set_spool_write_path(OTLP)\n"
            'observation_event(obs_id=o, trace_id=t, name="x", obs_type="genration")\n'
        )},
    )
    (kit / "usecase.yaml").write_text(yaml.safe_dump(_stateless_manifest()))

    report = run_conformance(kit)
    assert any("genration" in f for f in report.findings), report.findings
    assert not report.ok

    assert run([str(kit)]) == 1
    assert run([str(kit), "--advisory"]) == 0     # the pre-portal-kit escape, unchanged
    assert "genration" in capsys.readouterr().out


def test_a_scaffolded_kit_names_a_valid_type_for_every_observation(kit_on_path):
    """AC: what `synth-authoring new` emits passes its own vocabulary check — and it does so
    *vacuously*, which is worth stating rather than hiding behind an empty list. The walking
    skeleton builds traces, generations and scores, none of which name a type. The value of
    the check here is the day someone grows the template a typed step."""
    from langfuse_synth_core.authoring.conformance import (
        observation_type_findings,
        run_conformance,
    )

    kit_dir = kit_on_path("conf-obs-types")
    findings, notes = observation_type_findings(kit_dir)
    assert findings == []
    assert notes and "names none" in notes[0]

    report = run_conformance(kit_dir)
    assert [f for f in report.findings if "observation type" in f] == []


def test_the_sdk_keyword_on_the_live_seam_is_read_case_sensitively(tmp_path):
    """The other keyword, and the one where case is the whole failure: `as_type` goes to the
    Langfuse SDK verbatim, so a live surface's `AGENT` really does land as a SPAN."""
    from langfuse_synth_core.authoring.conformance import observation_type_findings

    kit = _kit_with_sources(
        tmp_path,
        **{"synth/agent.py": (
            'with trace.observation(name="decide", as_type="AGENT") as obs:\n'
            "    ...\n"
            'with trace.observation(name="answer", as_type="generation") as gen:\n'
            "    ...\n"
        )},
    )
    findings, _ = observation_type_findings(kit)
    assert len(findings) == 1, findings
    assert "AGENT" in findings[0] and "synth/agent.py:1" in findings[0]
    assert "nothing lowercases this one" in findings[0]


def test_a_kits_own_tests_are_out_of_scope_for_a_check_that_blocks(tmp_path):
    """`_kit_sources` over-reads on purpose, and that trade belongs to the advisory channel.
    A kit's test naming a deliberately wrong type — as this suite's own tests do — must not
    redden its CI over a line no container runs."""
    from langfuse_synth_core.authoring.conformance import observation_type_findings

    kit = tmp_path
    (kit / "synth").mkdir(parents=True)
    (kit / "synth" / "steps.py").write_text('observation_event(obs_type="TOOL")\n')
    (kit / "tests").mkdir()
    (kit / "tests" / "test_steps.py").write_text('observation_event(obs_type="genration")\n')

    findings, _ = observation_type_findings(kit)     # no src/ — the whole checkout is read
    assert findings == [], findings
