"""``synth-authoring new`` — the runnable-green walking-skeleton generator (#36).

Mirrors the ticket's acceptance criteria against a scaffold emitted into ``tmp_path``:

* ``new`` emits the full file floor; the companion stub appears only on request;
* the scaffold's ``usecase.yaml`` passes ``synth-authoring validate`` with no edits
  (incl. ``generation.target_traces``);
* the scaffold's ``seed`` passes the determinism golden gate on first generation, under
  the deny-LLM egress block (``new`` blessed the initial golden — runnable-green);
* ``seed``/``verify`` are wired through the library; the derivation hook is identity.

These run under the ``[authoring]`` extra (the generator, validator, and golden gate all
ship behind it); the module skips on a bare runtime install, where the boundary is proved
elsewhere (``test_authoring_boundary``).
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="synth-authoring new ships in the [authoring] extra; not on a runtime-only job",
)

# The file floor every scaffold must emit (relative to the kit root).
FLOOR = (
    "usecase.yaml",
    "Dockerfile",
    "pyproject.toml",
    "DEMO_SCRIPT.md",
    "config/demo.yaml",
    "src/synth/__init__.py",
    "src/synth/cli.py",
    "src/synth/config.py",
    "src/synth/materialize.py",
    "src/synth/seed.py",
    "src/synth/verify.py",
    "tests/golden_seed.py",
    "tests/test_determinism.py",
    "tests/test_validate.py",
    "tests/test_retargeting.py",
    ".github/workflows/publish.yml",
    ".github/workflows/ci.yml",
    ".gitignore",
)


@pytest.fixture(scope="module")
def kit(tmp_path_factory):
    """Scaffold one kit once (the freeze subprocess is the slow part) and reuse it."""
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    dest = tmp_path_factory.mktemp("kits") / "wobble-demo"
    result = scaffold_kit("wobble-demo", dest)
    return result


# --- AC: `new` emits the full file floor -------------------------------------------------
def test_emits_the_full_file_floor(kit):
    for rel in FLOOR:
        assert (kit.dest / rel).is_file(), f"missing floor file: {rel}"


# --- AC (portal #161): the scaffolded kit is born depot-first ----------------------------
def test_readme_is_depot_first(kit):
    """The portal serves the README as the kit's catalog "Overview" doc, so the scaffold
    leads with the cartridge/demo story and demotes all developer content below a marked
    section — no install/CLI instructions above the fold."""
    readme = (kit.dest / "README.md").read_text()
    marker = "## Development and running outside the depot"
    assert marker in readme
    top, dev = readme.split(marker, 1)
    assert "cartridge" in top, "the delivery-model framing is stated up top"
    assert "deferred" in top, "the standalone-run decision is referenced, not made"
    assert "pip install" not in top, "no install instructions above the fold"
    assert "pip install" in dev, "dev content demoted, not deleted"


def test_manifest_declares_readme_as_the_overview_doc(kit):
    """Sync captures ``assets.docs`` at the pinned ref; without this entry the depot-first
    README would never reach the portal's docs reader."""
    import yaml

    doc = yaml.safe_load((kit.dest / "usecase.yaml").read_text())
    assert {"path": "README.md", "title": "Overview"} in doc["assets"]["docs"]


def test_render_markdown_runbook_is_declared_and_present(kit):
    """The Presenter Runbook stub exists AND the manifest declares it as render: markdown."""
    import yaml

    assert (kit.dest / "DEMO_SCRIPT.md").is_file()
    doc = yaml.safe_load((kit.dest / "usecase.yaml").read_text())
    md = [a for a in doc["artifacts"] if a.get("render") == "markdown"]
    assert md and any(a["path"] == "DEMO_SCRIPT.md" for a in md)


# --- AC (portal #181): the stub carries the runbook-executability rule -------------------
def test_runbook_stub_states_the_executability_rule(kit):
    """The one-line rule sits where the story beats get written, and the scaffolded kit
    produces no runbook advisories (the walking skeleton lints clean)."""
    import yaml

    from langfuse_synth_core.authoring.validate import runbook_advisories

    stub = (kit.dest / "DEMO_SCRIPT.md").read_text()
    assert "reachable from the delivered surfaces" in stub
    assert "Developer mode" in stub
    doc = yaml.safe_load((kit.dest / "usecase.yaml").read_text())
    assert runbook_advisories(doc, kit.dest) == []


def test_reference_dockerfile_is_non_root_uid_10001(kit):
    dockerfile = (kit.dest / "Dockerfile").read_text()
    assert "uid 10001" in dockerfile.lower() or "--uid 10001" in dockerfile
    assert "USER synth" in dockerfile


def test_reference_dockerfile_makes_the_runtime_write_paths_writable(kit):
    """`COPY . .` lands root-owned and the container then drops to uid 10001 — so unless
    the image creates and chowns the two runtime write paths (the spool at
    /app/.synth_spool, and /app/out where the worker collects artifacts), every scaffolded
    kit dies on `open_spool()` at its first deployment (portal #189). Being non-root — the
    test above — is exactly what breaks this, so this asserts the ownership prep happens
    before the USER drop; the built-image proof lives in ``test_scaffold_image.py``."""
    dockerfile = (kit.dest / "Dockerfile").read_text()
    before_user_drop = dockerfile.partition("USER synth")[0]
    chown = [line for line in before_user_drop.splitlines() if "chown" in line]
    assert chown, "no chown before USER synth — /app stays root-owned at runtime"
    assert any("synth:synth" in line and "/app" in line for line in chown)
    for path in ("/app/out", "/app/.synth_spool"):
        assert any(path in line for line in chown), f"{path} never created before the USER drop"


# --- Spec E · E7 (#102): the scaffold gets build+sign CI with no manual wiring -----------
def test_publish_workflow_triggers_on_tag_push(kit):
    import yaml

    doc = yaml.safe_load((kit.dest / ".github" / "workflows" / "publish.yml").read_text())
    # YAML parses the bare `on:` key as the boolean True.
    on = doc[True] if True in doc else doc["on"]
    assert on["push"]["tags"] == ["v*.*.*"]


def test_publish_workflow_calls_core_kit_publish_pinned_to_the_core_ref(kit):
    from langfuse_synth_core.authoring.scaffold import DEFAULT_CORE_REF

    workflow_src = (kit.dest / ".github" / "workflows" / "publish.yml").read_text()
    assert (
        "uses: borismichel/langfuse-synth-core/.github/workflows/kit-publish.yml@"
        f"{DEFAULT_CORE_REF}" in workflow_src
    )


def test_publish_workflow_grants_exactly_the_needed_permissions(kit):
    import yaml

    doc = yaml.safe_load((kit.dest / ".github" / "workflows" / "publish.yml").read_text())
    perms = doc["jobs"]["publish"]["permissions"]
    assert perms == {"contents": "read", "packages": "write", "id-token": "write"}


# --- portal #183: the scaffold gets a test-running CI workflow too -----------------------
# The publish workflow above only fires on a tag, so a kit scaffolded before this ran its
# suite nowhere but the author's laptop — and `protect main` (which requires a status check
# named `test`) could not be applied to it at all. `ci.yml` closes that: every scaffolded kit
# is born with the same `test` check EV and Lender have, so branch protection is applicable
# from the first push.


def _ci_doc(kit):
    import yaml

    return yaml.safe_load((kit.dest / ".github" / "workflows" / "ci.yml").read_text())


def test_ci_workflow_runs_on_push_and_pull_request(kit):
    doc = _ci_doc(kit)
    # YAML parses the bare `on:` key as the boolean True.
    on = doc[True] if True in doc else doc["on"]
    assert "push" in on and "pull_request" in on


def test_ci_job_is_named_test_so_protect_main_can_require_it(kit):
    """`protect main` on every kit repo requires a status check literally named `test` —
    the job key IS the check name, so renaming it silently makes the ruleset unsatisfiable."""
    assert list(_ci_doc(kit)["jobs"]) == ["test"]


def test_ci_runs_the_suite_on_a_github_hosted_runner(kit):
    """Infrastructure policy (CLAUDE.md, 2026-07-28): public repos build and test on
    GitHub-hosted runners, never self-hosted."""
    job = _ci_doc(kit)["jobs"]["test"]
    assert job["runs-on"] == "ubuntu-latest"
    steps = " ".join(step.get("run", "") for step in job["steps"])
    assert "pip install -e '.[dev]'" in steps
    assert "pytest -q" in steps


def test_ci_python_matches_the_dockerfile_runtime(kit):
    """A kit whose CI tests on a different interpreter than its image runs proves nothing
    about the image; the Dockerfile's `python:3.N-slim` is the one runtime."""
    import re

    dockerfile = (kit.dest / "Dockerfile").read_text()
    runtime = re.search(r"FROM python:(\d+\.\d+)-slim", dockerfile).group(1)
    steps = _ci_doc(kit)["jobs"]["test"]["steps"]
    setup = [s for s in steps if "setup-python" in s.get("uses", "")]
    assert setup and setup[0]["with"]["python-version"] == runtime


# --- portal #183: build artifacts never enter a kit's history ----------------------------
def test_gitignore_covers_the_editable_install_egg_info(kit):
    """`pip install -e .` writes `src/<dist_name>.egg-info/` on the author's first run, before
    the first commit — untracked only if `.gitignore` already covers it (the support kit
    published one publicly because it did not)."""
    assert "*.egg-info/" in (kit.dest / ".gitignore").read_text().splitlines()


# --- AC (#141): `--companion` emits a runnable-green companion surface -------------------
# The G3 promise: `--companion` no longer emits a dead stub. It emits a working Surface on
# the Companion Adapter (#140) — a manifest `live_components` + `llm` block that validates, a
# `companion` verb in the kit CLI, the `[companion]` web deps, and an app that boots, binds
# 0.0.0.0, and answers its health path. Without `--companion`, the base scaffold is unchanged.


@pytest.fixture(scope="module")
def companion_kit(tmp_path_factory):
    """Scaffold one `--companion` kit once and reuse it (the freeze subprocess is slow)."""
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    dest = tmp_path_factory.mktemp("kits") / "live-demo"
    return scaffold_kit("live-demo", dest, with_companion=True)


def test_companion_surface_lands_under_the_synth_package(companion_kit):
    """The companion app is a subpackage of the installed `synth` kit (so `synth companion`
    can import it in the built container), not a loose top-level dir outside `src`."""
    assert (companion_kit.dest / "src" / "synth" / "companion" / "app.py").is_file()
    assert (companion_kit.dest / "src" / "synth" / "companion" / "__init__.py").is_file()


def test_companion_surface_only_on_request(tmp_path):
    """Without `--companion` there is no companion surface at all; with it, a working app
    (not the old raising stub) is emitted."""
    without = scaffold_kit_at(tmp_path, "plain-kit")
    assert not (without.dest / "src" / "synth" / "companion").exists()

    app_src = (
        scaffold_kit_at(tmp_path, "live-kit", with_companion=True).dest
        / "src" / "synth" / "companion" / "app.py"
    ).read_text()
    # The old stub raised "full companion authoring is Spec G"; the surface now works.
    assert "full companion authoring is Spec G" not in app_src
    assert "def create_app(" in app_src


def scaffold_kit_at(tmp_path, slug, **kw):
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    return scaffold_kit(slug, tmp_path / slug, **kw)


# --- AC: manifest gains a validate-passing live_components + llm block --------------------
def test_companion_manifest_has_live_components_and_llm_and_validates(companion_kit):
    import yaml

    from langfuse_synth_core.authoring.validate import validate_path

    errors = validate_path(companion_kit.dest / "usecase.yaml")
    assert errors == [], "\n".join(errors)

    doc = yaml.safe_load((companion_kit.dest / "usecase.yaml").read_text())
    assert isinstance(doc.get("live_components"), list) and doc["live_components"]
    assert doc["llm"]["providers"], "top-level llm block required by the LLM_API_KEY sentinel"


def test_companion_live_component_declares_the_adapter_contract_shape(companion_kit):
    """Command / port / health_path / requires_secrets match the gold kits' contract shape."""
    import yaml

    doc = yaml.safe_load((companion_kit.dest / "usecase.yaml").read_text())
    comp = doc["live_components"][0]
    assert comp["command"] == "synth companion --config {config} --host 0.0.0.0 --port 8080"
    assert comp["port"] == 8080
    assert comp["health_path"] == "/healthz"
    # LLM_API_KEY sentinel + the Langfuse project keys (no ANTHROPIC_API_KEY to mix).
    assert set(comp["requires_secrets"]) == {
        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LLM_API_KEY",
    }


def test_companion_manifest_and_app_constants_agree(companion_kit, load_kit_module):
    """The manifest's live_component and the emitted app's declared constants agree — no drift
    across the manifest/app boundary. Guards BOTH the health path AND requires_secrets: the
    manifest declares what the portal provisions, the app declares what the Adapter enforces
    at boot, and they must be the same set."""
    import yaml

    doc = yaml.safe_load((companion_kit.dest / "usecase.yaml").read_text())
    comp = doc["live_components"][0]
    app_mod = load_kit_module(
        companion_kit.dest / "src" / "synth" / "companion" / "app.py", "scaffold_companion_drift"
    )
    assert app_mod.HEALTH_PATH == comp["health_path"]
    assert set(app_mod.REQUIRES_SECRETS) == set(comp["requires_secrets"])


# --- AC: without --companion the base scaffold is unchanged (no block / verb / deps) ------
def test_base_scaffold_has_no_companion_leak(kit):
    import yaml

    doc = yaml.safe_load((kit.dest / "usecase.yaml").read_text())
    assert "live_components" not in doc
    assert "llm" not in doc
    assert "companion" not in (kit.dest / "src" / "synth" / "cli.py").read_text()
    assert "[companion]" not in (kit.dest / "pyproject.toml").read_text()


def test_base_scaffold_cli_and_pyproject_are_byte_identical_to_the_templates(kit):
    """The non-companion CLI/pyproject render exactly the base templates — the companion
    placeholders collapse to nothing, so today's output is untouched."""
    from importlib import resources

    from langfuse_synth_core.authoring.scaffold import DEFAULT_CORE_REF, slug_to_name

    ctx = {
        "__SLUG__": kit.slug,
        "__SLUG_UNDER__": kit.slug.replace("-", "_"),
        "__NAME__": slug_to_name(kit.slug),
        "__CORE_PIN__": DEFAULT_CORE_REF,
        "__CORE_EXTRA__": "",
        "__COMPANION_DISPATCH__": "",
    }

    def render(tmpl):
        text = resources.files("langfuse_synth_core.authoring").joinpath(
            "scaffold_files", tmpl
        ).read_text(encoding="utf-8")
        for k, v in ctx.items():
            text = text.replace(k, v)
        return text

    assert (kit.dest / "src" / "synth" / "cli.py").read_text() == render("cli.py.tmpl")
    assert (kit.dest / "pyproject.toml").read_text() == render("pyproject.toml.tmpl")


# --- AC: the kit CLI registers the companion verb through the Adapter's helper ------------
def test_companion_cli_wires_the_verb_to_the_app(companion_kit):
    cli_src = (companion_kit.dest / "src" / "synth" / "cli.py").read_text()
    assert 'if _argv[:1] == ["companion"]:' in cli_src
    assert "from .companion.app import main as companion_main" in cli_src


def test_companion_app_parses_via_the_adapter_helper(companion_kit):
    app_src = (companion_kit.dest / "src" / "synth" / "companion" / "app.py").read_text()
    assert "parse_invocation" in app_src
    assert "adapter.run(" in app_src


# --- AC: pyproject carries the companion web deps via the core [companion] extra ----------
def test_companion_pyproject_carries_the_web_deps(companion_kit):
    pyproject = (companion_kit.dest / "pyproject.toml").read_text()
    assert "langfuse-synth-core[companion]" in pyproject


# --- AC #1: boots via its DECLARED command string, binds 0.0.0.0 on the declared port, and
#           answers its health path < 400 — driven end to end, not by hand ------------------
def test_companion_boots_through_the_declared_command_string(companion_kit, monkeypatch):
    """Exercise the whole live path the portal would: the manifest's declared command string
    -> the kit's `synth companion` verb -> the Adapter's parse_invocation -> load_config ->
    adapter.run on the declared host/port -> a surface whose health path answers < 400.

    The emitted kit is not pip-installed, so its `src` is prepended to the path (as the built
    container's install would place it). `adapter.run` is intercepted at its seam to capture
    the declared bind/port without blocking; the captured surface is then bound on a free port
    (binding the literal 8080 would flake in CI) and its health path probed over HTTP.
    """
    import importlib
    import shlex
    import socket
    import sys
    import threading
    import time

    import requests
    import yaml

    from langfuse_synth_core.companion import CompanionAdapter

    # Put the emitted kit on the path and import ITS `synth` package fresh (not core's).
    monkeypatch.syspath_prepend(str(companion_kit.dest / "src"))
    for name in [m for m in list(sys.modules) if m == "synth" or m.startswith("synth.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    cli = importlib.import_module("synth.cli")

    # Drive the DECLARED command string verbatim, substituting only the {config} the portal
    # templates. Everything else — `synth companion --host 0.0.0.0 --port 8080` — is as shipped.
    doc = yaml.safe_load((companion_kit.dest / "usecase.yaml").read_text())
    comp = doc["live_components"][0]
    config_path = str(companion_kit.dest / "config" / "demo.yaml")
    tokens = shlex.split(comp["command"].replace("{config}", config_path))
    assert tokens[:2] == ["synth", "companion"]

    captured: dict = {}

    def capture_run(self, app_factory, *, host, port):
        app = app_factory(self)
        self.mount_health(app)  # adapter.run does this via serve(); do it here for the probe
        captured.update(adapter=self, app=app, host=host, port=port)

    monkeypatch.setattr(CompanionAdapter, "run", capture_run)

    assert cli.main(tokens[1:]) == 0  # `synth` is argv[0]; the CLI dispatches `companion`
    # Booted on the DECLARED bind + port from the command string (0.0.0.0 : 8080).
    assert captured["host"] == "0.0.0.0" and captured["port"] == comp["port"] == 8080

    # The booted surface answers its health path < 400 over HTTP (bound on a free port).
    sock = socket.socket()
    sock.bind(("0.0.0.0", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    server = captured["adapter"].make_server(captured["app"], host="0.0.0.0", port=free_port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not getattr(server, "started", False) and time.time() < deadline:
            time.sleep(0.02)
        assert server.started, "companion surface never bound 0.0.0.0"
        assert server.config.host == "0.0.0.0"  # bind-all, never port-published

        health = requests.get(f"http://127.0.0.1:{free_port}{comp['health_path']}", timeout=5)
        assert health.status_code < 400  # liveness the portal probes
        root = requests.get(f"http://127.0.0.1:{free_port}/", timeout=5)
        assert root.status_code == 200 and "<html" in root.text.lower()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive(), "companion surface did not shut down gracefully"


# --- AC #3: the surface handles zero raw secrets (ready clients only, via the adapter) ----
def test_companion_app_has_zero_raw_secret_handling(companion_kit):
    app_src = (companion_kit.dest / "src" / "synth" / "companion" / "app.py").read_text()
    # The Surface reads no raw secret material: the Adapter does secret intake and hands out
    # ready clients only. (Secret NAMES in REQUIRES_SECRETS are fine — they are not values.)
    assert "os.environ" not in app_src
    assert "os.getenv" not in app_src
    assert "getenv" not in app_src


# --- AC: the scaffold's usecase.yaml passes validate with no edits -----------------------
def test_scaffolded_manifest_passes_validate(kit):
    from langfuse_synth_core.authoring.validate import validate_path

    errors = validate_path(kit.dest / "usecase.yaml")
    assert errors == [], "\n".join(errors)


def test_manifest_exposes_the_canonical_target_traces_knob(kit):
    import yaml

    doc = yaml.safe_load((kit.dest / "usecase.yaml").read_text())
    knob = doc["config_schema"]["properties"].get("generation.target_traces")
    assert knob is not None and knob["type"] == "integer"


# --- AC: the scaffold's seed passes the determinism golden gate on first generation ------
def test_scaffolded_seed_passes_the_golden_gate(kit):
    """`new` blessed the golden; a fresh materialization is byte-identical => green."""
    from langfuse_synth_core.authoring.golden import GoldenSpec, assert_golden
    from langfuse_synth_core.authoring.scaffold import GOLDEN_TARGET_TRACES

    spec = GoldenSpec(
        seed_ref="golden_seed:seed",
        target_traces=GOLDEN_TARGET_TRACES,
        golden_path=kit.golden_path,
        params={},
        search_paths=(str(kit.dest / "tests"), str(kit.dest / "src")),
    )
    assert_golden(spec)  # no exception == byte-identical full payload, offline, egress-blocked


def test_blessed_golden_is_full_payload(kit):
    """The blessed golden is the whole Spool — traces, generations, and scores."""
    blob = kit.golden_path.read_bytes()
    assert b'"type":"trace-create"' in blob
    assert b'"type":"generation-create"' in blob
    assert b'"type":"score-create"' in blob


# --- AC: seed + verify wired through the library; derivation hook pre-wired to identity ---
def test_seed_and_verify_wire_through_the_library(kit):
    seed_src = (kit.dest / "src" / "synth" / "seed.py").read_text()
    verify_src = (kit.dest / "src" / "synth" / "verify.py").read_text()
    assert "from langfuse_synth_core.seed.ingest import Ingestor" in seed_src
    assert "from langfuse_synth_core.lfread import" in verify_src


# --- AC (portal #187): the emitted kit is retargetable, and carries the gate that says so ---
# The portal retargets one shipped config by injecting LANGFUSE_BASE_URL. The scaffold used to
# emit a plain `base_url` field, so every kit `new` produced was born undeployable — and no gate
# could see it, because validate lints the manifest and the golden gate seeds from a fixed file.


def test_scaffolded_config_honors_the_env_the_portal_retargets_with(kit, load_kit_module):
    """The behaviour, exercised through the emitted loader — not a grep for `os.environ`."""
    from langfuse_synth_core.authoring.retarget import assert_retargetable

    config_mod = load_kit_module(kit.dest / "src" / "synth" / "config.py", "scaffold_cfg_retarget")
    assert_retargetable(config_mod.load_config, kit.dest / "config" / "demo.yaml")


def test_scaffolded_config_file_uses_the_host_key_its_model_reads(kit):
    """`host` in the YAML and `host` in the dataclass must agree, or the committed default is
    silently dropped and every run resolves the hardcoded fallback instead."""
    import yaml

    doc = yaml.safe_load((kit.dest / "config" / "demo.yaml").read_text())
    assert "host" in doc["target"], "the emitted config must use the key the model reads"
    assert "base_url" not in doc["target"], "`base_url` is derived from `host`, never a stored key"


def test_scaffold_emits_the_retargeting_gate_into_the_kits_own_suite(kit):
    """The gate ships with the kit, so its CI (not just core's) fails if it regresses."""
    src = (kit.dest / "tests" / "test_retargeting.py").read_text()
    assert "assert_retargetable" in src
    assert "LANGFUSE_BASE_URL" in src


def test_derivation_hook_is_pre_wired_to_identity(kit):
    config_src = (kit.dest / "src" / "synth" / "config.py").read_text()
    assert "identity_derivation" in config_src
    assert "DERIVATION_HOOK = identity_derivation" in config_src


# --- guard rails -------------------------------------------------------------------------
def test_rejects_a_non_kebab_slug(tmp_path):
    from langfuse_synth_core.authoring.scaffold import ScaffoldError, scaffold_kit

    with pytest.raises(ScaffoldError):
        scaffold_kit("Not_A_Slug", tmp_path / "x")


def test_refuses_a_non_empty_destination(tmp_path):
    from langfuse_synth_core.authoring.scaffold import ScaffoldError, scaffold_kit

    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "keep.txt").write_text("existing")
    with pytest.raises(ScaffoldError):
        scaffold_kit("occupied", dest)


# --- the CLI surface dispatches `new` ----------------------------------------------------
def test_cli_new_scaffolds_and_returns_zero(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    rc = main(["new", "cli-kit", "--dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "cli-kit" / "usecase.yaml").is_file()
    assert "scaffolded kit" in capsys.readouterr().out
