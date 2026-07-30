"""The runbook-executability advisory lint (portal #181).

Kits are cartridges delivered as-is through the depot: the presenter has no shell, and
the portal's invocation contract templates only ``{config}`` — so a ``synth`` CLI command
in a presenter beat is unreachable in a deployed kit. ``synth-authoring validate`` flags
fenced ``synth`` command blocks inside the declared runbook artifact(s) when they sit
outside a clearly-marked developer-mode section.

Advisory severity by design: the lint nudges, it NEVER fails validation — the exit code
and the importable error API (``validate_doc``/``validate_path``, what the portal's sync
imports) are unaffected.
"""

import importlib.util
import textwrap

import pytest

pytest.importorskip("jsonschema", reason="authoring extra not installed")
pytest.importorskip("yaml", reason="authoring extra not installed")

import yaml  # noqa: E402

from langfuse_synth_core.authoring import cli  # noqa: E402
from langfuse_synth_core.authoring.validate import runbook_advisories  # noqa: E402

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="authoring extra not installed",
)

MANIFEST: dict = {
    "schema_version": 1,
    "slug": "demo-uc",
    "name": "Demo UC",
    "tagline": "a demo",
    "target": {"project_hint": "demo", "supports": ["cloud_eu"]},
    "pipeline": [
        {"id": "seed", "run": "synth seed {config}"},
        {"id": "verify", "run": "synth verify {config}"},
    ],
    "artifacts": [{"path": "DEMO_SCRIPT.md", "render": "markdown"}],
}


def _kit(tmp_path, runbook: str | None, runbook_rel: str = "DEMO_SCRIPT.md"):
    """Lay out a minimal kit dir: usecase.yaml + (optionally) the runbook source."""
    manifest = tmp_path / "usecase.yaml"
    manifest.write_text(yaml.safe_dump(MANIFEST))
    if runbook is not None:
        target = tmp_path / runbook_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(runbook))
    return manifest


# --- AC: presenter-facing `synth` command blocks are flagged -----------------------------


def test_presenter_facing_synth_block_is_flagged(tmp_path):
    _kit(
        tmp_path,
        """\
        # Runbook

        ## The beats

        ```bash
        synth seed --config config/demo.yaml
        ```
        """,
    )
    advisories = runbook_advisories(MANIFEST, tmp_path)
    assert len(advisories) == 1
    assert "advisory" in advisories[0]
    assert "DEMO_SCRIPT.md:6" in advisories[0]
    assert "developer-mode" in advisories[0]


def test_ev_pre_180_regression_shape_is_flagged(tmp_path):
    """Regression fixture: EV's pre-#180 runbook wired the eval beat only to CLI verbs —
    a fenced `synth experiment` block inside presenter beat 6. That shape must flag
    (EV has since merged its fix; this pins the failure shape the lint exists for)."""
    _kit(
        tmp_path,
        """\
        # DEMO_SCRIPT.md — EV-subsidy regression (the golden path)

        ## The beats

        ### 6 · Run production → red, validate development → green, then promote
        ```bash
        synth experiment --config config/demo.yaml                       # production → RED
        synth experiment --config config/demo.yaml --label development   # development → GREEN
        ```

        ## Reset
        Spin up a fresh project and re-run `synth seed` — the seed is deterministic.
        """,
    )
    advisories = runbook_advisories(MANIFEST, tmp_path)
    assert len(advisories) == 1
    assert "DEMO_SCRIPT.md:7" in advisories[0]


# --- AC: a clearly-marked developer-mode section is exempt -------------------------------


@pytest.mark.parametrize(
    "heading",
    [
        "## Developer mode — not a presenter beat",  # Lender's spelling
        "## Developer mode — the same beats from a shell",  # EV's spelling
        "## Dev-mode appendix",
    ],
)
def test_dev_mode_section_is_exempt(tmp_path, heading):
    _kit(
        tmp_path,
        f"""\
        # Runbook

        ## The beats

        Open the dashboard; click the eval trigger in the Companion footer.

        {heading}

        ```bash
        synth experiment --config config/demo.yaml
        ```
        """,
    )
    assert runbook_advisories(MANIFEST, tmp_path) == []


def test_dev_mode_subsections_stay_exempt(tmp_path):
    _kit(
        tmp_path,
        """\
        ## Developer mode — not a presenter beat

        ### Certify from a shell

        ```bash
        synth certify --config config/demo.yaml --gate
        ```
        """,
    )
    assert runbook_advisories(MANIFEST, tmp_path) == []


def test_dev_mode_section_ends_at_next_same_level_heading(tmp_path):
    _kit(
        tmp_path,
        """\
        ## Developer mode — not a presenter beat

        ```bash
        synth verify --config config/demo.yaml
        ```

        ## Reset

        ```bash
        synth seed --config config/demo.yaml
        ```
        """,
    )
    advisories = runbook_advisories(MANIFEST, tmp_path)
    assert len(advisories) == 1
    assert "DEMO_SCRIPT.md:10" in advisories[0]


# --- AC: the heuristic keys on fenced command blocks, not inline mentions ----------------


def test_inline_mentions_and_non_synth_blocks_are_clean(tmp_path):
    _kit(
        tmp_path,
        """\
        ## Setup

        - Deploy the kit. The pipeline runs `synth seed` (generate + ingest) then
          `synth verify` — that is what the deploy runs, not a presenter step.

        ```bash
        docker run --rm ghcr.io/acme/demo-uc:latest
        synth-authoring validate usecase.yaml
        ```
        """,
    )
    assert runbook_advisories(MANIFEST, tmp_path) == []


def test_missing_runbook_source_is_silent(tmp_path):
    _kit(tmp_path, runbook=None)
    assert runbook_advisories(MANIFEST, tmp_path) == []


def test_gold_kit_template_source_is_resolved(tmp_path):
    """The gold kits commit the runbook as ``templates/<name lowercased>.j2`` and render
    it to the declared path at seed time — the lint must find that source too."""
    _kit(
        tmp_path,
        """\
        ## The beats

        ```bash
        synth memo --config {{ config_path }}
        ```
        """,
        runbook_rel="templates/demo_script.md.j2",
    )
    advisories = runbook_advisories(MANIFEST, tmp_path)
    assert len(advisories) == 1
    assert "templates/demo_script.md.j2:4" in advisories[0]


def test_one_logical_runbook_gets_one_lint_pass(tmp_path):
    """When both the declared path and the template-source convention exist, the declared
    path wins — one runbook never yields duplicate advisories."""
    flagged = """\
        ## The beats

        ```bash
        synth seed --config config/demo.yaml
        ```
        """
    _kit(tmp_path, flagged)
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "demo_script.md.j2").write_text(textwrap.dedent(flagged))
    advisories = runbook_advisories(MANIFEST, tmp_path)
    assert len(advisories) == 1
    assert "DEMO_SCRIPT.md:4" in advisories[0]


def test_non_markdown_artifacts_are_ignored(tmp_path):
    doc = {
        **MANIFEST,
        "artifacts": [
            {"path": "DEMO_SCRIPT.md", "render": "markdown"},
            {"path": "walkthrough.html", "render": "html"},
        ],
    }
    (tmp_path / "walkthrough.html").write_text("<pre>synth seed --config x</pre>")
    _kit(tmp_path, runbook="## Beats\n\nClick things in the Langfuse UI.\n")
    assert runbook_advisories(doc, tmp_path) == []


# --- AC: advisory severity — nudges, never blocks ----------------------------------------


def test_cli_prints_advisory_but_still_passes(tmp_path, capsys):
    path = _kit(
        tmp_path,
        """\
        ## The beats

        ```bash
        synth seed --config config/demo.yaml
        ```
        """,
    )
    rc = cli.main(["validate", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "valid" in out
    assert "advisory" in out


def test_cli_clean_runbook_prints_no_advisory(tmp_path, capsys):
    path = _kit(tmp_path, "## Beats\n\nWalk the Langfuse UI.\n")
    rc = cli.main(["validate", str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "advisory" not in out


def test_advisories_never_leak_into_the_blocking_error_api(tmp_path):
    """The portal's sync imports validate_path and 422s on any returned error — the
    advisory channel must stay out of it."""
    from langfuse_synth_core.authoring.validate import validate_path

    path = _kit(
        tmp_path,
        """\
        ## The beats

        ```bash
        synth seed --config config/demo.yaml
        ```
        """,
    )
    assert validate_path(path) == []


def test_runbook_advisories_is_an_exported_authoring_api():
    from langfuse_synth_core import authoring

    assert hasattr(authoring, "runbook_advisories")
