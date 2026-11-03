"""``publish_skill_bundle`` skip logic, against a mocked skills client.

The real client is Langfuse Python SDK v5.1.0 ``client.api.skills``. These tests do not
call the network. They check that matching canonical base64 SHA-256 hashes skip
``create_version`` and only repair labels.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from langfuse_synth_core.skills import publish_skill_bundle

SKILL_MD = """---
name: returns-and-refunds
description: Decide a retail return.
---
Follow the checklist.
"""
POLICY = "Sale items are final.\n"


class _Missing(Exception):
    """Stands in for the SDK's ``NotFoundError``, which carries ``status_code == 404``."""

    status_code = 404


class _File:
    def __init__(self, path: str, content: str):
        self.path = path
        self.sha256hash = base64.b64encode(
            hashlib.sha256(content.encode("utf-8")).digest()
        ).decode("ascii")


class _Version:
    def __init__(self, name: str, version: int, files: list[_File], labels: list[str]):
        self.name = name
        self.version = version
        self.files = files
        self.labels = labels


class _Skills:
    def __init__(self, latest: _Version | None):
        self.latest = latest
        self.creates: list = []
        self.label_calls: list[tuple] = []

    def get(self, skill_name, *, label=None, version=None, include_content=None):
        assert label == "latest"
        if self.latest is None or self.latest.name != skill_name:
            raise _Missing("no such skill")
        return self.latest

    def create_version(self, *, files, commit_message=None):
        self.creates.append(files)
        created = _Version("returns-and-refunds", 3, [], ["latest"])
        self.latest = created
        return created

    def set_labels(self, skill_name, skill_version, *, labels):
        self.label_calls.append((skill_name, skill_version, list(labels)))
        self.latest.labels = ["latest", *labels]
        self.latest.version = skill_version
        self.latest.name = skill_name
        return self.latest


class _Client:
    def __init__(self, skills: _Skills):
        self.api = type("API", (), {"skills": skills})()


def _bundle(tmp_path, policy: str = POLICY) -> str:
    root = tmp_path / "returns-and-refunds"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (root / "references" / "returns-policy.md").write_text(policy, encoding="utf-8")
    return str(root)


def _matching(policy: str = POLICY) -> _Version:
    return _Version(
        "returns-and-refunds",
        2,
        [_File("SKILL.md", SKILL_MD), _File("references/returns-policy.md", policy)],
        ["latest", "candidate"],
    )


def test_matching_hashes_skip_creation_and_only_fix_labels(tmp_path):
    skills = _Skills(_matching())
    result = publish_skill_bundle(_Client(skills), _bundle(tmp_path), ["production", "latest"])

    assert skills.creates == []
    assert skills.label_calls == [("returns-and-refunds", 2, ["production"])]
    assert result == {"name": "returns-and-refunds", "version": 2}


def test_matching_hashes_and_labels_make_no_write(tmp_path):
    latest = _matching()
    latest.labels = ["production", "latest"]
    skills = _Skills(latest)

    result = publish_skill_bundle(_Client(skills), _bundle(tmp_path), ["production"])

    assert skills.creates == []
    assert skills.label_calls == []
    assert result == {"name": "returns-and-refunds", "version": 2}


def test_a_missing_skill_creates_a_version_and_sets_labels(tmp_path):
    skills = _Skills(None)

    result = publish_skill_bundle(_Client(skills), _bundle(tmp_path), ["production"])

    assert len(skills.creates) == 1
    written = {item.path: item.content for item in skills.creates[0]}
    assert written["SKILL.md"] == SKILL_MD
    assert written["references/returns-policy.md"] == POLICY
    assert skills.label_calls == [("returns-and-refunds", 3, ["production"])]
    assert result == {"name": "returns-and-refunds", "version": 3}


def test_a_changed_file_creates_a_new_version(tmp_path):
    skills = _Skills(_matching())

    result = publish_skill_bundle(
        _Client(skills), _bundle(tmp_path, policy="Sale items can be returned.\n"),
        ["candidate"],
    )

    assert len(skills.creates) == 1
    assert skills.label_calls == [("returns-and-refunds", 3, ["candidate"])]
    assert result["version"] == 3


def test_a_non_404_from_get_is_not_treated_as_missing(tmp_path):
    class _Broken(_Skills):
        def get(self, *args, **kwargs):
            raise RuntimeError("auth failed")

    with pytest.raises(RuntimeError, match="auth failed"):
        publish_skill_bundle(_Client(_Broken(None)), _bundle(tmp_path), ["production"])
