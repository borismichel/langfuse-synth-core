"""`synth-authoring repin` — one-command core re-pin for kits (portal #197).

RELEASING.md's manual dance was three pyproject pins + the publish-workflow pin, per kit,
with a grep to catch stragglers. These tests pin the command that replaces it: every
core pin in the kit checkout moves in one step, a straggler the rewrite could not reach
fails loudly instead of half-landing, dry-run shows the diff without writing, and the
portal registry snippet comes out ready to paste (with the GHCR digest resolved when the
kit tag is already published, and a plain statement when it is not).
"""

from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="the repin command ships in langfuse-synth-core[authoring]",
)

CORE_GIT = "git+https://github.com/borismichel/langfuse-synth-core"

# The EV/Lender shape: runtime + [companion] (the playground extra) + [authoring] dev pin,
# plus an unrelated git dependency that must NOT move.
EV_PYPROJECT = textwrap.dedent(
    f"""\
    [project]
    name = "langfuse-demo-synth"
    version = "0.2.0"
    dependencies = [
        "langfuse-synth-core @ {CORE_GIT}@v1.7.0",
        "somelib @ git+https://github.com/other/somelib@v9.9.9",
    ]

    [project.optional-dependencies]
    playground = [
        "langfuse-synth-core[companion] @ {CORE_GIT}@v1.7.0",
    ]
    dev = [
        "langfuse-synth-core[authoring] @ {CORE_GIT}@v1.7.0",
    ]
    """
)

# The support-kit shape: the runtime pin IS the [companion] extra (two pins total).
SUPPORT_PYPROJECT = textwrap.dedent(
    f"""\
    [project]
    name = "langfuse-synth-support"
    version = "0.1.0"
    dependencies = [
        "langfuse-synth-core[companion] @ {CORE_GIT}@v1.7.0",
    ]

    [project.optional-dependencies]
    dev = [
        "langfuse-synth-core[authoring] @ {CORE_GIT}@v1.7.0",
    ]
    """
)

PUBLISH_YML = textwrap.dedent(
    """\
    name: Publish
    on:
      push:
        tags:
          - "v*.*.*"
    jobs:
      publish:
        uses: borismichel/langfuse-synth-core/.github/workflows/kit-publish.yml@v1.7.0
        permissions:
          contents: read
    """
)

USECASE_YAML = 'schema_version: 1\nslug: ev-subsidy-regression\nname: "EV"\n'

REPO_URL = "https://github.com/borismichel/langfuse-synth-ev"


def make_kit(tmp_path: Path, pyproject: str = EV_PYPROJECT) -> Path:
    kit = tmp_path / "kit"
    (kit / ".github" / "workflows").mkdir(parents=True)
    (kit / "pyproject.toml").write_text(pyproject)
    (kit / ".github" / "workflows" / "publish.yml").write_text(PUBLISH_YML)
    (kit / "usecase.yaml").write_text(USECASE_YAML)
    return kit


# --- the pure rewrites --------------------------------------------------------------------
def test_rewrite_core_pins_moves_all_three_ev_pins_and_nothing_else():
    from langfuse_synth_core.authoring.repin import rewrite_core_pins

    new_text, moved = rewrite_core_pins(EV_PYPROJECT, "v1.8.0")
    assert moved == 3
    assert new_text.count(f"{CORE_GIT}@v1.8.0") == 3
    assert f"{CORE_GIT}@v1.7.0" not in new_text
    # The unrelated git pin is untouched.
    assert "somelib @ git+https://github.com/other/somelib@v9.9.9" in new_text


def test_rewrite_core_pins_moves_both_support_shape_pins():
    from langfuse_synth_core.authoring.repin import rewrite_core_pins

    new_text, moved = rewrite_core_pins(SUPPORT_PYPROJECT, "v1.8.0")
    assert moved == 2
    assert f"{CORE_GIT}@v1.7.0" not in new_text


def test_rewrite_workflow_pin_moves_the_kit_publish_ref():
    from langfuse_synth_core.authoring.repin import rewrite_workflow_pin

    new_text, moved = rewrite_workflow_pin(PUBLISH_YML, "v1.8.0")
    assert moved == 1
    assert "kit-publish.yml@v1.8.0" in new_text
    assert "kit-publish.yml@v1.7.0" not in new_text


def test_stale_scan_catches_a_pin_shape_the_rewrite_could_not_reach():
    """RELEASING.md step 3's grep, mechanized: a core pin in a form the rewrite regex
    does not recognize (here an ssh remote) must fail the repin, not half-land."""
    from langfuse_synth_core.authoring.repin import stale_core_refs

    sneaky = EV_PYPROJECT + (
        '\nextra = ["langfuse-synth-core @ git+ssh://git@github.com/borismichel/langfuse-synth-core@v1.5.0"]\n'
    )
    assert stale_core_refs(sneaky, "v1.8.0") == ["v1.5.0", "v1.7.0", "v1.7.0", "v1.7.0"]
    from langfuse_synth_core.authoring.repin import rewrite_core_pins

    rewritten, _ = rewrite_core_pins(sneaky, "v1.8.0")
    assert stale_core_refs(rewritten, "v1.8.0") == ["v1.5.0"]


def test_stale_scan_catches_a_branch_pin_after_the_repo_name():
    """A branch ref isn't a tag/SHA-shaped token, so the version net alone would miss it;
    the `langfuse-synth-core@<ref>` anchor is what trips on `@main`."""
    from langfuse_synth_core.authoring.repin import stale_core_refs

    branchy = 'x = "langfuse-synth-core @ git+ssh://git@github.com/borismichel/langfuse-synth-core@main"'
    assert stale_core_refs(branchy, "v1.8.0") == ["main"]


# --- ref validation: pin to a tag or full SHA, never a branch -----------------------------
def test_core_ref_must_be_a_version_tag_or_full_sha():
    from langfuse_synth_core.authoring.repin import RepinError, validate_core_ref

    validate_core_ref("v1.8.0")
    validate_core_ref("a" * 40)
    for bad in ("main", "1.8.0", "v1.8", "refs/heads/main", "abc123"):
        with pytest.raises(RepinError, match="tag.*full SHA|never a.*branch"):
            validate_core_ref(bad)


# --- repin_kit: the whole step ------------------------------------------------------------
def test_repin_kit_rewrites_both_files_and_emits_the_registry_snippet(tmp_path):
    from langfuse_synth_core.authoring.repin import repin_kit

    kit = make_kit(tmp_path)
    fake_image = f"ghcr.io/borismichel/langfuse-synth-ev@sha256:{'0' * 64}"
    result = repin_kit(kit, "v1.8.0", kit_tag="v0.4.0", repo_url=REPO_URL,
                       resolver=lambda repo_url, tag: fake_image)

    assert result.pyproject_moves == 3
    assert result.workflow_moves == 1
    text = (kit / "pyproject.toml").read_text()
    assert text.count(f"{CORE_GIT}@v1.8.0") == 3
    assert "kit-publish.yml@v1.8.0" in (kit / ".github" / "workflows" / "publish.yml").read_text()

    # The snippet matches the portal registry.yaml entry shape, ready to paste.
    assert "- slug: ev-subsidy-regression" in result.snippet
    assert f"repo_url: {REPO_URL}" in result.snippet
    assert "ref: v0.4.0" in result.snippet
    assert "core v1.8.0" in result.snippet  # the why-comment on the ref line
    assert result.image_ref == f"ghcr.io/borismichel/langfuse-synth-ev@sha256:{'0' * 64}"


def test_repin_kit_dry_run_shows_diffs_but_writes_nothing(tmp_path):
    from langfuse_synth_core.authoring.repin import repin_kit

    kit = make_kit(tmp_path)
    before_py = (kit / "pyproject.toml").read_text()
    before_wf = (kit / ".github" / "workflows" / "publish.yml").read_text()

    result = repin_kit(kit, "v1.8.0", repo_url=REPO_URL, dry_run=True)

    assert (kit / "pyproject.toml").read_text() == before_py
    assert (kit / ".github" / "workflows" / "publish.yml").read_text() == before_wf
    assert "pyproject.toml" in result.diffs
    joined = result.diffs["pyproject.toml"]
    assert f"-    \"langfuse-synth-core @ {CORE_GIT}@v1.7.0\"," in joined
    assert f"+    \"langfuse-synth-core @ {CORE_GIT}@v1.8.0\"," in joined
    assert ".github/workflows/publish.yml" in result.diffs


def test_repin_kit_without_kit_tag_emits_placeholder_and_skips_digest(tmp_path):
    from langfuse_synth_core.authoring.repin import repin_kit

    kit = make_kit(tmp_path)
    result = repin_kit(kit, "v1.8.0", repo_url=REPO_URL)
    assert "ref: vX.Y.Z" in result.snippet
    assert result.image_ref is None
    assert "--kit-tag" in result.image_note


def test_repin_kit_says_plainly_when_the_digest_is_not_yet_published(tmp_path):
    from langfuse_synth_core.authoring.repin import DigestUnavailable, repin_kit

    def not_published(repo_url: str, tag: str) -> str:
        raise DigestUnavailable(f"no GHCR image found for {tag} — has CI published it yet?")

    kit = make_kit(tmp_path)
    result = repin_kit(kit, "v1.8.0", kit_tag="v0.4.0", repo_url=REPO_URL, resolver=not_published)
    assert result.image_ref is None
    assert "no GHCR image found" in result.image_note
    # A missing digest is not a failed repin: the files still moved.
    assert f"{CORE_GIT}@v1.8.0" in (kit / "pyproject.toml").read_text()


def test_repin_kit_fails_loudly_on_a_stale_unreachable_pin(tmp_path):
    from langfuse_synth_core.authoring.repin import RepinError, repin_kit

    kit = make_kit(tmp_path)
    py = kit / "pyproject.toml"
    py.write_text(
        py.read_text()
        + '\nextra = ["langfuse-synth-core @ git+ssh://git@github.com/borismichel/langfuse-synth-core@v1.5.0"]\n'
    )
    with pytest.raises(RepinError, match="stale.*v1.5.0"):
        repin_kit(kit, "v1.8.0", repo_url=REPO_URL)
    # Fail closed: nothing was written.
    assert f"{CORE_GIT}@v1.7.0" in py.read_text()


def test_repin_kit_fails_loudly_on_a_stale_pin_in_the_workflow_file(tmp_path):
    """The half-land guarantee covers publish.yml too, not just pyproject.toml."""
    from langfuse_synth_core.authoring.repin import RepinError, repin_kit

    kit = make_kit(tmp_path)
    wf = kit / ".github" / "workflows" / "publish.yml"
    wf.write_text(
        wf.read_text()
        + '      # unreachable second core ref\n'
        + '      run: pip install "langfuse-synth-core @ git+ssh://git@github.com/borismichel/langfuse-synth-core@v1.6.0"\n'
    )
    with pytest.raises(RepinError, match="stale.*v1.6.0"):
        repin_kit(kit, "v1.8.0", repo_url=REPO_URL)
    assert "kit-publish.yml@v1.7.0" in wf.read_text()


def test_repin_kit_requires_the_kit_files_it_rewrites(tmp_path):
    from langfuse_synth_core.authoring.repin import RepinError, repin_kit

    empty = tmp_path / "not-a-kit"
    empty.mkdir()
    with pytest.raises(RepinError, match="pyproject.toml"):
        repin_kit(empty, "v1.8.0", repo_url=REPO_URL)


# --- the CLI surface ----------------------------------------------------------------------
def test_cli_repin_end_to_end(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    kit = make_kit(tmp_path)
    rc = main([
        "repin", "v1.8.0", "--kit", str(kit), "--repo-url", REPO_URL, "--kit-tag", "vX.Y.Z",
    ])
    # vX.Y.Z placeholder is not a valid kit tag — reject it the same way as a bad core ref.
    assert rc == 2

    rc = main(["repin", "v1.8.0", "--kit", str(kit), "--repo-url", REPO_URL])
    assert rc == 0
    out = capsys.readouterr().out
    assert "- slug: ev-subsidy-regression" in out
    assert f"{CORE_GIT}@v1.8.0" in (kit / "pyproject.toml").read_text()


def test_cli_repin_dry_run_prints_a_diff_and_writes_nothing(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    kit = make_kit(tmp_path)
    rc = main(["repin", "v1.8.0", "--kit", str(kit), "--repo-url", REPO_URL, "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "-    \"langfuse-synth-core @ " in out
    assert f"{CORE_GIT}@v1.7.0" in (kit / "pyproject.toml").read_text()


def test_cli_repin_rejects_a_branch_ref(tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    kit = make_kit(tmp_path)
    rc = main(["repin", "main", "--kit", str(kit), "--repo-url", REPO_URL])
    assert rc == 2
    assert "never a" in capsys.readouterr().err


# --- the default GHCR resolver (network seam injected, no sockets) ------------------------
class FakeResp:
    def __init__(self, status_code=200, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


def test_resolve_image_digest_reads_docker_content_digest(monkeypatch):
    from langfuse_synth_core.authoring import repin

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.endswith("/token"):
            return FakeResp(payload={"token": "tok"})
        return FakeResp(headers={"Docker-Content-Digest": "sha256:" + "a" * 64})

    monkeypatch.setattr(repin.requests, "get", fake_get)
    ref = repin.resolve_image_digest(REPO_URL, "v0.4.0")
    assert ref == "ghcr.io/borismichel/langfuse-synth-ev@sha256:" + "a" * 64
    assert any("/manifests/v0.4.0" in url for url in calls)


def test_resolve_image_digest_maps_404_to_not_yet_published(monkeypatch):
    from langfuse_synth_core.authoring import repin

    def fake_get(url, **kwargs):
        if url.endswith("/token"):
            return FakeResp(payload={"token": "tok"})
        return FakeResp(status_code=404)

    monkeypatch.setattr(repin.requests, "get", fake_get)
    with pytest.raises(repin.DigestUnavailable, match="not.*published|no GHCR image"):
        repin.resolve_image_digest(REPO_URL, "v0.4.0")
