"""Kit-dev skills — the agent pack shipped and versioned with the library (#37).

The Authoring SDK is **agent-first**: a coding agent authors ~99% of new demos, so the
center of gravity is the library CLI plus shipped kit-dev skills (Spec A / #19). The
orchestrator skill ``authoring-a-demo-kit`` walks an agent through scaffold → model the
trace tree → wire the ``target_traces`` derivation → runbook → run the gates, and
delegates Langfuse craft (which observation type, which evaluator type — judgment the
validator can't check) to the existing ``langfuse`` skill.

The skills ride here as **package data** under ``langfuse_synth_core.authoring`` — one
version, one repo — so the Contract, its validator, and the skills that teach both can
never drift (the drift that killed the pure template-repo option). They live behind the
``[authoring]`` extra: a deployed kit never authors, so the runtime image carries none of
this.

This module is the locate/install surface. ``list_skills`` / ``read_skill`` /
``skill_frontmatter`` resolve the shipped files via :mod:`importlib.resources` on any
install shape (source, wheel, editable); ``install_skills`` copies them into a coding
agent's skills directory (``.claude/skills`` by default) so the shipped skill becomes
discoverable where the agent looks for it.
"""

from __future__ import annotations

import shutil
from importlib import resources
from importlib.abc import Traversable
from pathlib import Path

import yaml


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


def install_skills(dest: str | Path, *, force: bool = False) -> list[Path]:
    """Copy every shipped skill (SKILL.md + references) into ``dest``; return the dirs written.

    ``dest`` is the skills directory a coding agent reads (``.claude/skills`` by default via
    the CLI). Each skill lands at ``dest/<name>/``. Refuses to overwrite an existing skill
    dir unless ``force`` — an author may have edited their installed copy, and a silent
    clobber would lose that.
    """
    dest = Path(dest)
    written: list[Path] = []
    for name in list_skills():
        target = dest / name
        if target.exists() and not force:
            raise FileExistsError(
                f"{target} already exists — pass force=True to overwrite (this would "
                "replace an edited copy of the skill)"
            )
        if target.exists():
            shutil.rmtree(target)
        _copy_tree(_skill_dir(name), target)
        written.append(target)
    return written
