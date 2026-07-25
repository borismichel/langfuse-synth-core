"""The kit-dev skills ship in the ``[authoring]`` extra, versioned with the library (#37).

Two things are proven here: (1) the ``authoring-a-demo-kit`` orchestrator skill ships as
package data under ``langfuse_synth_core.authoring`` — so contract, validator, and skill
can never drift, being one version in one repo; and (2) the ``synth-authoring skills``
locate/install surface makes the shipped skill reachable in a coding agent's skills dir.

The skill's *content* obligations from the acceptance criteria are asserted too — it walks
scaffold → trace tree → derivation → runbook → gates, teaches the model-free-seed rule and
the ``synth freeze`` frozen-fixture escape hatch, and delegates Langfuse craft to the
existing ``langfuse`` skill — so a rewrite that drops one of those load-bearing pieces
reddens CI rather than shipping a hollow skill.
"""

from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="kit-dev skills ship in the [authoring] extra; install it to exercise them",
)

SKILL_NAME = "authoring-a-demo-kit"


@pytest.fixture(scope="module")
def skill_body() -> str:
    from langfuse_synth_core.authoring import skills

    return skills.read_skill(SKILL_NAME)


# ── shipping / co-versioning ────────────────────────────────────────────────────────────
def test_orchestrator_skill_ships_as_package_data():
    """SKILL.md resolves via importlib.resources on any install (source/wheel/editable)."""
    from langfuse_synth_core.authoring import skills

    assert SKILL_NAME in skills.list_skills()


def test_skill_frontmatter_names_the_skill():
    """The frontmatter carries name == the dir slug and a non-empty description (so Claude
    Code can index it)."""
    from langfuse_synth_core.authoring import skills

    meta = skills.skill_frontmatter(SKILL_NAME)
    assert meta["name"] == SKILL_NAME
    assert meta.get("description", "").strip(), "skill needs a triggering description"


# ── content obligations (acceptance criteria) ───────────────────────────────────────────
def test_skill_walks_scaffold_to_gates(skill_body: str):
    """AC1: the orchestrator walks scaffold → trace tree → derivation → runbook → gates."""
    lowered = skill_body.lower()
    for beat in ("synth-authoring new", "trace tree", "derivation", "runbook", "gate"):
        assert beat in lowered, f"skill must walk the {beat!r} beat"


def test_skill_teaches_model_free_seed_and_freeze_escape_hatch(skill_body: str):
    """AC2: the model-free-seed rule + the author-time-LLM-frozen-fixture escape hatch,
    re-blessed via ``synth freeze``, taught as a first-class pattern."""
    lowered = skill_body.lower()
    assert "model-free" in lowered
    assert "fixture" in lowered
    assert "synth-authoring freeze" in lowered


def test_skill_delegates_langfuse_craft(skill_body: str):
    """AC3: Langfuse craft (observation/evaluator type choice) is delegated to the existing
    ``langfuse`` skill, not duplicated here."""
    lowered = skill_body.lower()
    assert "langfuse` skill" in lowered or "langfuse skill" in lowered
    assert "observation" in lowered and "evaluator" in lowered


# ── locate / install surface ────────────────────────────────────────────────────────────
def test_skills_cli_lists_the_orchestrator(capsys):
    """``synth-authoring skills`` names the shipped skill and prints its path."""
    from langfuse_synth_core.authoring.cli import main

    rc = main(["skills"])
    out = capsys.readouterr().out
    assert rc == 0
    assert SKILL_NAME in out


def test_skills_cli_install_copies_into_dest(tmp_path):
    """``synth-authoring skills --install --dest DIR`` copies the skill (SKILL.md +
    references) into DIR so a coding agent can discover it under .claude/skills."""
    from langfuse_synth_core.authoring.cli import main

    dest = tmp_path / "skills"
    rc = main(["skills", "--install", "--dest", str(dest)])
    assert rc == 0
    installed = dest / SKILL_NAME / "SKILL.md"
    assert installed.is_file()
    assert (dest / SKILL_NAME / "references").is_dir()


def test_skills_cli_install_refuses_overwrite_without_force(tmp_path):
    """A second install into a populated dest fails loudly unless --force (never a silent
    clobber of an author's edited copy)."""
    from langfuse_synth_core.authoring.cli import main

    dest = tmp_path / "skills"
    assert main(["skills", "--install", "--dest", str(dest)]) == 0
    assert main(["skills", "--install", "--dest", str(dest)]) != 0
    assert main(["skills", "--install", "--dest", str(dest), "--force"]) == 0
