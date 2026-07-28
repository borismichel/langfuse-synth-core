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
    ".github/workflows/publish.yml",
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


def test_render_markdown_runbook_is_declared_and_present(kit):
    """The Presenter Runbook stub exists AND the manifest declares it as render: markdown."""
    import yaml

    assert (kit.dest / "DEMO_SCRIPT.md").is_file()
    doc = yaml.safe_load((kit.dest / "usecase.yaml").read_text())
    md = [a for a in doc["artifacts"] if a.get("render") == "markdown"]
    assert md and any(a["path"] == "DEMO_SCRIPT.md" for a in md)


def test_reference_dockerfile_is_non_root_uid_10001(kit):
    dockerfile = (kit.dest / "Dockerfile").read_text()
    assert "uid 10001" in dockerfile.lower() or "--uid 10001" in dockerfile
    assert "USER synth" in dockerfile


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


# --- AC: the companion stub appears only when explicitly requested -----------------------
def test_companion_stub_only_on_request(tmp_path):
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    without = scaffold_kit("plain-kit", tmp_path / "plain-kit")
    assert not (without.dest / "companion").exists()

    withcomp = scaffold_kit("live-kit", tmp_path / "live-kit", with_companion=True)
    assert (withcomp.dest / "companion" / "app.py").is_file()
    assert "Spec G" in (withcomp.dest / "companion" / "app.py").read_text()


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
