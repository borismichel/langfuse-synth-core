"""The kit-dev skills ship in the ``[authoring]`` extra, versioned with the library (#37).

Two things are proven here: (1) the ``authoring-a-demo-kit`` orchestrator skill ships as
package data under ``langfuse_synth_core.authoring`` — so contract, validator, and skill
travel together as one version in one repo; and (2) the ``synth-authoring skills``
locate/install surface makes the shipped skill reachable in a coding agent's skills dir.

The skill's *content* obligations from the acceptance criteria are asserted too — it walks
audience → small walkthrough → scale → delivery, teaches the model-free-seed rule and
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


def test_skill_teaches_the_v4_write_path_without_restating_the_observation_model(
    skill_body, tmp_path,
):
    """AC (portal #207, revised by #213): the skill teaches the write path — OTLP, the
    score exception, the append-not-upsert consequence, and where the wire is documented —
    while the split it was built on holds: *which* observation type a step is stays the
    `langfuse` skill's. There is no pin to teach any more: the batch path is gone, so the
    skill must NOT still be telling authors to select one."""
    from langfuse_synth_core.authoring import skills

    skills.install_skills(tmp_path)
    reference = tmp_path / SKILL_NAME / "references" / "recipe-and-transport.md"
    assert "references/recipe-and-transport.md" in skill_body
    lowered = reference.read_text().lower()
    assert "set_spool_write_path" not in lowered
    assert "score-create" in lowered
    assert "docs/write_paths.md" in lowered
    assert "otlp" in lowered and "2026-11-16" in lowered
    assert "appends" in lowered and "non-resumable" in lowered
    assert "langfuse` skill" in lowered


def test_skill_teaches_the_observation_type_vocabulary_and_its_failure_mode(skill_body, tmp_path):
    """AC (portal #217): the skill owns the *vocabulary* — the ten values, that the wire
    spelling is lowercase and case-sensitive, and that an unrecognised value is not
    rejected but silently shown as something else. It does not take over *which* type a
    step is; that stays the `langfuse` skill's, and the check below proves both at once."""
    from langfuse_synth_core.authoring import skills

    skills.install_skills(tmp_path)
    craft = (tmp_path / SKILL_NAME / "references" / "langfuse-craft.md").read_text()
    pack = "\n".join(path.read_text() for path in (tmp_path / SKILL_NAME).rglob("*.md"))
    pack = pack.lower()

    for value in ("agent", "tool", "chain", "retriever", "embedding", "evaluator",
                  "guardrail", "span", "generation", "event"):
        assert value in pack, value
    assert "case-sensitive" in pack
    assert "silent" in pack or "silently" in pack
    assert "generation" in pack and "cost" in pack      # what a mistyped step becomes
    assert "langfuse` skill" in craft                   # *which* type is still delegated


def test_skill_seed_guidance_promises_no_idempotent_re_run(skill_body: str, tmp_path):
    """The property the platform no longer offers is not taught anywhere in the pack — read
    as an agent receives it, from an installed copy rather than the source tree."""
    from langfuse_synth_core.authoring import skills

    skills.install_skills(tmp_path)
    pack = "\n".join(path.read_text() for path in (tmp_path / SKILL_NAME).rglob("*.md"))
    lowered = pack.lower()
    for retired in ("idempotent upsert", "re-seeds idempotently", "deterministic upsert",
                    "re-run to resume", "safe to re-seed"):
        assert retired not in lowered, retired


def test_skill_states_the_runbook_executability_rule(skill_body: str):
    """Portal #181: presenter beats must be reachable from the delivered surfaces (a
    Companion route or the Langfuse UI), `synth` CLI is confined to a marked
    developer-mode section, and presenter-only controls are tucked away (the cosmetic
    corollary) — stated in the runbook phase AND the closing checklist, with EV's
    CLI-only eval beat (portal #180) as the recognizable counter-example."""
    lowered = skill_body.lower()
    assert "delivered surfaces" in lowered
    assert "developer-mode" in lowered
    assert "companion route" in " ".join(lowered.split())
    assert "tucked away" in lowered
    assert "#180" in skill_body, "the EV counter-example anchors the failure shape"
    checklist = skill_body.split('## What "done" looks like', 1)[1]
    assert "delivered surfaces" in checklist.lower()
    assert "tucked away" in checklist.lower()


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


def test_installed_workflow_starts_with_three_evidenced_beats_before_scaling(tmp_path):
    """The agent receives the story contract and separate readiness stages in order."""
    from langfuse_synth_core.authoring import skills

    skills.install_skills(tmp_path)
    body = (tmp_path / SKILL_NAME / "SKILL.md").read_text()
    stages = [
        "## 1. Frame the demo", "## 2. Reuse or scaffold", "## 3. Build the small walkthrough",
        "## 4. Check offline", "## 5. Scale the story", "## 6. Verify the live run",
        "## 7. Admit and rehearse",
    ]
    positions = [body.index(stage) for stage in stages]
    assert positions == sorted(positions)
    brief = body[positions[0]:positions[1]].lower()
    for field in ("audience", "problem", "visible failure", "presenter action", "payoff",
                  "screen", "expected result", "evidence"):
        assert field in brief
    assert "three" in brief
    offline = body[positions[3]:positions[4]]
    assert "synth-authoring conformance" in offline
    assert "synth verify" not in offline
    assert "synth seed --config" not in offline


def test_installed_reference_links_resolve_without_a_core_checkout(tmp_path):
    """Progressive disclosure remains usable after copying only the skill package."""
    import re
    from urllib.parse import unquote, urlsplit

    from langfuse_synth_core.authoring import skills

    skills.install_skills(tmp_path)
    root = tmp_path / SKILL_NAME
    for doc in root.rglob("*.md"):
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", doc.read_text()):
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            linked = (doc.parent / unquote(parsed.path)).resolve()
            assert linked.is_relative_to(root), f"{doc.name}: {target} escapes installed skill"
            assert linked.is_file(), f"{doc.name}: missing {target}"
