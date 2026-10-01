"""Kit-dev skills — the agent pack shipped and versioned with the library (#37).

The Authoring SDK is **agent-first**: a coding agent authors ~99% of new demos, so the
center of gravity is the library CLI plus shipped kit-dev skills (Spec A / #19). The
orchestrator skill ``authoring-a-demo-kit`` walks an agent through scaffold → model the
trace tree → wire the ``target_traces`` derivation → runbook → run the gates, and
delegates Langfuse craft (which observation type, which evaluator type — judgment the
validator can't check) to the existing ``langfuse`` skill.

The skills ride here as **package data** under ``langfuse_synth_core.authoring`` — one
version, one repo — so the Contract, its validator, and the skills that teach both can
travel together; installation receipts detect stale copies. They live behind the
``[authoring]`` extra: a deployed kit never authors, so the runtime image carries none of
this.

This module is the locate/install surface. ``list_skills`` / ``read_skill`` /
``skill_frontmatter`` resolve the shipped files via :mod:`importlib.resources` on any
install shape (source, wheel, editable); ``install_skills`` copies them into a coding
agent's skills directory (``.claude/skills`` by default) so the shipped skill becomes
discoverable where the agent looks for it.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TypedDict

import yaml

from langfuse_synth_core import __version__


def _copy_tree(src: Traversable, dest: Path) -> None:
    """Recursively copy a resource tree ``src`` to filesystem path ``dest``.

    Walks the :class:`Traversable` with ``read_bytes`` rather than
    ``importlib.resources.as_file`` on the directory — ``as_file`` only gained
    directory support in Python 3.12, and this library targets 3.11, so a zipped-wheel
    install must not depend on it. Works identically for source, editable, and wheel
    installs.
    """
    if src.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _copy_tree(child, dest / child.name)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())

# The subdirectory (relative to the authoring package) the skills ship under, mirrored in
# pyproject's ``[tool.setuptools.package-data]`` glob. Each child dir that holds a
# ``SKILL.md`` is one skill.
_SKILLS_SUBDIR = "skills"
_SKILL_FILE = "SKILL.md"
_RECEIPT = ".synth-authoring.json"
AGENT_DESTS = {"claude": ".claude/skills", "codex": ".agents/skills"}


class SkillNotFoundError(LookupError):
    """No skill with the requested name ships in this build of the library."""


def _skills_root() -> Traversable:
    return resources.files(__package__).joinpath(_SKILLS_SUBDIR)


def list_skills() -> list[str]:
    """Return the names (dir slugs) of every shipped skill, sorted.

    A directory counts as a skill only if it holds a ``SKILL.md`` — so a stray
    ``references`` dir or an ``__pycache__`` never masquerades as one.
    """
    root = _skills_root()
    if not root.is_dir():
        return []
    names = [
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.joinpath(_SKILL_FILE).is_file()
    ]
    return sorted(names)


def _skill_dir(name: str) -> Traversable:
    skill = _skills_root().joinpath(name)
    if not skill.is_dir() or not skill.joinpath(_SKILL_FILE).is_file():
        raise SkillNotFoundError(
            f"no kit-dev skill named {name!r} ships in this library "
            f"(available: {', '.join(list_skills()) or 'none'})"
        )
    return skill


def read_skill(name: str) -> str:
    """Return the full ``SKILL.md`` text of the named skill."""
    return _skill_dir(name).joinpath(_SKILL_FILE).read_text(encoding="utf-8")


def skill_frontmatter(name: str) -> dict:
    """Parse the YAML frontmatter block (between the leading ``---`` fences) of a skill.

    Returns ``{}`` for a skill with no frontmatter rather than raising — the caller decides
    whether a missing ``name``/``description`` matters.
    """
    text = read_skill(name)
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    meta = yaml.safe_load(parts[1])
    return meta if isinstance(meta, dict) else {}


def install_skills(
    dest: str | Path, *, force: bool = False, update: bool = False,
) -> list[Path]:
    """Install the shipped pack, or update unedited copies with ``update=True``.

    Every replacement preserves the previous directory in a sibling backup directory.
    ``force`` permits replacing modified/unmanaged copies; it never discards their files.
    All conflicts are checked before writing any skill.
    """
    dest = Path(dest)
    statuses = skill_status(dest)
    for item in statuses:
        target = dest / item["name"]
        state = item["state"]
        if target.is_symlink():
            raise FileExistsError(f"{target} is a symlink; choose a regular destination")
        if state != "missing" and not force:
            if not update or state in ("locally-modified", "unmanaged"):
                raise FileExistsError(
                    f"{target}: {state}; inspect your copy, then use --update --force "
                    "to replace it with a preserved backup"
                )
    written = []
    dest.mkdir(parents=True, exist_ok=True)
    for item in statuses:
        name = item["name"]
        target = dest / name
        if update and item["state"] == "current":
            continue
        # Prepare a complete copy before moving the installed one out of the way.
        with tempfile.TemporaryDirectory(prefix=".synth-skill-", dir=dest.parent) as temp:
            staged = Path(temp) / name
            _copy_tree(_skill_dir(name), staged)
            (staged / _RECEIPT).write_text(json.dumps({
                "core_version": __version__, "files": _hashes(staged),
            }, indent=2) + "\n", encoding="utf-8")
            backup = None
            if target.exists():
                backup = dest.parent / ".synth-skill-backups" / f"{name}-{uuid.uuid4().hex}"
                backup.parent.mkdir(parents=True, exist_ok=True)
                target.rename(backup)
            try:
                staged.rename(target)
            except OSError:
                if backup is not None:
                    backup.rename(target)
                raise
        written.append(target)
    return written


def _hashes(root: Traversable, prefix: str = "") -> dict[str, str]:
    files = {}
    for child in root.iterdir():
        if not prefix and child.name == _RECEIPT:
            continue
        name = prefix + child.name
        if isinstance(child, Path) and child.is_symlink():
            files[name] = "symlink:" + str(child.readlink())
        elif child.is_dir():
            files.update(_hashes(child, name + "/"))
        else:
            files[name] = hashlib.sha256(child.read_bytes()).hexdigest()
    return files


class SkillStatus(TypedDict):
    name: str
    state: str
    core_version: str | None


def skill_status(dest: str | Path) -> list[SkillStatus]:
    """Compare installed files with their receipt and the currently running core."""
    result: list[SkillStatus] = []
    for name in list_skills():
        target = Path(dest) / name
        version = None
        if not target.exists():
            state = "missing"
        else:
            try:
                receipt = json.loads((target / _RECEIPT).read_text(encoding="utf-8"))
                version = receipt["core_version"]
                recorded = receipt["files"]
                if not isinstance(version, str) or not isinstance(recorded, dict):
                    raise ValueError("invalid receipt")
            except (OSError, ValueError, KeyError, TypeError):
                version = None
                state = "unmanaged"
            else:
                actual = _hashes(target)
                if actual != recorded:
                    state = "locally-modified"
                elif version != __version__ or actual != _hashes(_skill_dir(name)):
                    state = "stale"
                else:
                    state = "current"
        result.append({"name": name, "state": state, "core_version": version})
    return result


def find_langfuse_skill(dest: str | Path, *, agent: str) -> Path | None:
    """Find the prerequisite in conventional project/user filesystem locations.

    A custom destination is checked first. Plugin registration and agent enable/disable
    settings require confirmation in the agent itself, outside this filesystem check.
    """
    relative = AGENT_DESTS[agent]
    directories = [Path(dest).expanduser()]
    cwd = Path.cwd()
    parents = [cwd, *cwd.parents]
    repo = next((parent for parent in parents if (parent / ".git").exists()), cwd)
    for parent in parents:
        directories.append(parent / relative)
        if parent == repo:
            break
    directories.append(Path.home() / relative)
    if agent == "codex":
        directories.append(Path("/etc/codex/skills"))
    for directory in directories:
        skill = directory / "langfuse" / "SKILL.md"
        if skill.is_file():
            return skill
    return None
