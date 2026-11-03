"""Publish a local skill folder through the Langfuse Python SDK v5 skills API.

This is not on the seed path and it does not call a model. Seed only records skill
versions that already exist in the project; a kit's setup step calls
:func:`publish_skill_bundle` before those traces are written.

Verified against the installed Langfuse Python SDK **5.1.0** (``langfuse.api.skills``,
generated from the public API). The methods used are:

* ``get(name, label="latest")`` — ``GET /api/public/skills/{name}?label=latest``
* ``create_version(files=...)`` — ``POST /api/public/skills``
* ``set_labels(name, version, labels=...)`` —
  ``PATCH /api/public/skills/{name}/versions/{version}``

``SkillFile.sha256Hash`` is the canonical base64 encoding of the SHA-256 digest of the
file's UTF-8 bytes. A matching hash set skips ``create_version`` and only repairs labels.
Langfuse assigns the ``latest`` label itself, so that label is ignored when comparing.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any, Sequence

#: Langfuse manages this label. It is not a user-managed label ``set_labels`` must resend.
_AUTO_LABEL = "latest"


def publish_skill_bundle(client: Any, bundle_dir: str | Path,
                         labels: Sequence[str]) -> dict:
    """Create a skill version from ``bundle_dir`` and assign ``labels``.

    The folder must contain a root ``SKILL.md`` whose frontmatter ``name`` is the skill
    name, plus any supporting files. Returns ``{"name", "version"}``.

    Idempotent: when the latest version's file hashes match this folder, no new version
    is created. ``set_labels`` replaces that version's user-managed labels and is called
    only when they differ from ``labels``. Langfuse keeps the ``latest`` label itself.
    """
    files = _read_bundle(Path(bundle_dir))
    name = _skill_name(files)
    requested = _requested_labels(labels)
    skills = _skills_api(client)
    latest = _latest_version(skills, name)
    if latest is not None and _same_files(files, latest):
        version = _ensure_labels(skills, latest, requested)
        return {"name": version.name, "version": int(version.version)}

    from langfuse.api.skills import SkillVersionFileInput

    created = skills.create_version(
        files=[SkillVersionFileInput(path=path, content=content) for path, content in files]
    )
    version = _ensure_labels(skills, created, requested)
    return {"name": version.name, "version": int(version.version)}


def _skills_api(client: Any) -> Any:
    skills = getattr(getattr(client, "api", None), "skills", None)
    required = ("get", "create_version", "set_labels")
    if skills is None or any(not callable(getattr(skills, name, None)) for name in required):
        raise RuntimeError(
            "publish_skill_bundle needs the Langfuse Python SDK v5 skills API "
            "(client.api.skills.get, create_version, set_labels), present in "
            "langfuse 5.1.0. This client does not expose it."
        )
    return skills


def _latest_version(skills: Any, name: str) -> Any | None:
    try:
        return skills.get(name, label="latest")
    except Exception as exc:  # noqa: BLE001 — only a missing skill is "create"
        if getattr(exc, "status_code", None) == 404:
            return None
        raise


def _ensure_labels(skills: Any, version: Any, labels: list[str]) -> Any:
    if _managed_labels(version) == set(labels):
        return version
    return skills.set_labels(version.name, int(version.version), labels=labels)


def _managed_labels(version: Any) -> set[str]:
    raw = getattr(version, "labels", None) or []
    return {str(label) for label in raw if label != _AUTO_LABEL}


def _requested_labels(labels: Sequence[str]) -> list[str]:
    if isinstance(labels, (str, bytes)) or not isinstance(labels, Sequence):
        raise ValueError("labels must be a sequence of strings")
    requested: list[str] = []
    for label in labels:
        if not isinstance(label, str) or label == "":
            raise ValueError("labels must be a sequence of non-empty strings")
        if label == _AUTO_LABEL or label in requested:
            continue
        requested.append(label)
    return requested


def _read_bundle(root: Path) -> list[tuple[str, str]]:
    if not root.is_dir():
        raise ValueError(f"skill bundle {root} is not a directory")
    files: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"skill file {rel} is not UTF-8 text") from exc
        files.append((rel, text))
    if not any(rel == "SKILL.md" for rel, _text in files):
        raise ValueError(f"skill bundle {root} has no SKILL.md")
    return files


def _skill_name(files: list[tuple[str, str]]) -> str:
    text = next(content for path, content in files if path == "SKILL.md")
    body = text.lstrip("\ufeff")
    if not body.startswith("---"):
        raise ValueError("SKILL.md must start with YAML frontmatter that includes name")
    rest = body[3:]
    if rest.startswith("\r\n"):
        rest = rest[2:]
    elif rest.startswith("\n"):
        rest = rest[1:]
    else:
        raise ValueError("SKILL.md frontmatter must start on its own line")
    end = rest.find("\n---")
    if end < 0:
        raise ValueError("SKILL.md frontmatter is not closed")
    import yaml

    data = yaml.safe_load(rest[:end])
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or name == "":
        raise ValueError("SKILL.md frontmatter must include a name")
    return name


def _same_files(files: list[tuple[str, str]], version: Any) -> bool:
    remote: dict[str, str] = {}
    for record in getattr(version, "files", None) or []:
        path, digest = _remote_file(record)
        remote[path] = digest
    local = {path: _content_hash(content) for path, content in files}
    return local == remote


def _remote_file(record: Any) -> tuple[str, str]:
    if isinstance(record, dict):
        path = record.get("path")
        digest = record.get("sha256Hash", record.get("sha256hash"))
    else:
        path = getattr(record, "path", None)
        digest = getattr(record, "sha256hash", None)
        if digest is None:
            digest = getattr(record, "sha256Hash", None)
    return str(path), "" if digest is None else str(digest)


def _content_hash(content: str) -> str:
    """Canonical base64 SHA-256 of the UTF-8 bytes, matching ``sha256Hash``."""
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")
