"""Agent bootstrap through the public CLI, using isolated discovery directories."""
from pathlib import Path

import pytest

from langfuse_synth_core.authoring.cli import main

NAME = "authoring-a-demo-kit"


@pytest.fixture(autouse=True)
def isolated_agent_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.mark.parametrize("agent,folder", [("claude", ".claude"), ("codex", ".agents")])
def test_install_for_selected_agent(agent, folder, capsys):
    assert main(["skills", "--install", "--agent", agent]) == 0
    assert (Path(folder) / "skills" / NAME / "SKILL.md").is_file()
    assert main(["skills", "--status", "--agent", agent]) == 0
    assert "current" in capsys.readouterr().out


def test_update_preserves_edited_reference_and_custom_destination(tmp_path, capsys):
    dest = tmp_path / "custom"
    args = ["skills", "--agent", "codex", "--dest", str(dest)]
    assert main([*args, "--install"]) == 0
    reference = dest / NAME / "references" / "langfuse-craft.md"
    reference.write_text("My local guidance")
    capsys.readouterr()
    assert main([*args, "--status"]) == 0
    assert "locally-modified" in capsys.readouterr().out
    assert main([*args, "--update"]) == 2
    assert reference.read_text() == "My local guidance"
    assert main([*args, "--update", "--force"]) == 0
    assert reference.read_text() != "My local guidance"
    backups = list(dest.parent.glob(".synth-skill-backups/*/references/langfuse-craft.md"))
    assert len(backups) == 1
    assert backups[0].read_text() == "My local guidance"
    assert "backup" in capsys.readouterr().out
    assert main([*args, "--status"]) == 0
    assert "current" in capsys.readouterr().out


@pytest.mark.parametrize("agent,folder", [("claude", ".claude"), ("codex", ".agents")])
def test_langfuse_prerequisite_in_personal_discovery_directory(agent, folder, capsys):
    assert main(["skills", "--install", "--agent", agent]) == 0
    output = capsys.readouterr().out
    assert "Langfuse skill: not found" in output
    assert "langfuse/SKILL.md" in output
    skill = Path.home() / folder / "skills" / "langfuse" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: langfuse\ndescription: Langfuse craft\n---\nGuidance\n")
    assert main(["skills", "--status", "--agent", agent]) == 0
    output = capsys.readouterr().out
    assert "Langfuse skill: found" in output
    assert str(skill) in output


def test_status_and_update_old_version_receipt(tmp_path, capsys):
    import json

    args = ["skills", "--dest", str(tmp_path / "skills")]
    assert main([*args, "--status"]) == 0
    assert "missing" in capsys.readouterr().out
    assert not (tmp_path / "skills").exists()
    assert main([*args, "--install"]) == 0
    receipt = tmp_path / "skills" / NAME / ".synth-authoring.json"
    stamp = json.loads(receipt.read_text())
    assert stamp["core_version"]
    assert "SKILL.md" in stamp["files"]
    stamp["core_version"] = "0.0.1"
    receipt.write_text(json.dumps(stamp))
    assert main([*args, "--status"]) == 0
    output = capsys.readouterr().out
    assert "stale" in output and "0.0.1" in output
    assert main([*args, "--update"]) == 0
    assert main([*args, "--status"]) == 0
    assert "current" in capsys.readouterr().out
    assert main([*args, "--update"]) == 0
    assert "0 skill(s) written" in capsys.readouterr().out


def test_status_detects_content_drift_with_same_version(tmp_path, capsys):
    import hashlib
    import json

    args = ["skills", "--dest", str(tmp_path / "skills")]
    assert main([*args, "--install"]) == 0
    target = tmp_path / "skills" / NAME
    receipt = target / ".synth-authoring.json"
    stamp = json.loads(receipt.read_text())
    # A receipt and matching content from an earlier build with the same version.
    old_body = b"An earlier bundled skill"
    (target / "SKILL.md").write_bytes(old_body)
    stamp["files"]["SKILL.md"] = hashlib.sha256(old_body).hexdigest()
    receipt.write_text(json.dumps(stamp))
    assert main([*args, "--status"]) == 0
    assert "stale" in capsys.readouterr().out
    assert main([*args, "--update"]) == 0
    assert (target / "SKILL.md").read_bytes() != old_body


@pytest.mark.parametrize("change", ["added", "deleted", "invalid-receipt"])
def test_update_protects_local_tree_changes(tmp_path, capsys, change):
    args = ["skills", "--dest", str(tmp_path / "skills")]
    assert main([*args, "--install"]) == 0
    target = tmp_path / "skills" / NAME
    if change == "added":
        (target / "notes.md").write_text("personal guidance")
    elif change == "deleted":
        (target / "SKILL.md").unlink()
    else:
        (target / ".synth-authoring.json").write_text("broken json")
    assert main([*args, "--update"]) == 2
    assert "inspect your copy" in capsys.readouterr().err


def test_default_install_still_targets_claude():
    assert main(["skills", "--install"]) == 0
    assert (Path(".claude/skills") / NAME / "SKILL.md").is_file()


def test_langfuse_prerequisite_in_repo_parent(tmp_path, monkeypatch, capsys):
    (tmp_path / ".git").mkdir()
    skill = tmp_path / ".agents/skills/langfuse/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Langfuse guidance")
    nested = tmp_path / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert main(["skills", "--status", "--agent", "codex"]) == 0
    assert str(skill) in capsys.readouterr().out


def test_force_does_not_replace_a_symlinked_installation(tmp_path, capsys):
    original = tmp_path / "original"
    assert main(["skills", "--install", "--dest", str(original)]) == 0
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / NAME).symlink_to(original / NAME, target_is_directory=True)
    assert main(["skills", "--install", "--force", "--dest", str(linked)]) == 2
    assert "symlink" in capsys.readouterr().err
    assert (linked / NAME).is_symlink()
    assert (original / NAME / "SKILL.md").is_file()
