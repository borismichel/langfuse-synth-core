"""``synth-authoring repin`` — one-command core re-pin for a kit checkout (portal #197).

Cutting a core release used to end in a manual dance per kit: bump every
``langfuse-synth-core`` pin in ``pyproject.toml`` (three in EV/Lender, two in support),
bump the ``kit-publish.yml`` workflow ref, grep for stragglers, then hand-write the
portal registry entry. Each step is trivial; the failure mode is skipping one — a kit
that runs the new core but serves through the old one, or a registry PR pinned to a tag
whose image never published. This module does the whole step atomically.

Shape: pure text rewrites (:func:`rewrite_core_pins`, :func:`rewrite_workflow_pin`) plus
a straggler scan (:func:`stale_core_refs`) that mechanizes RELEASING.md step 3's "grep
and check every hit moved" — a core pin the rewrite could not reach fails the repin
before anything is written, never half-lands. The GHCR digest lookup is the same
anonymous-token + ``Docker-Content-Digest`` flow the portal's own sync resolver uses
(``tools/sync_ops.py``), behind an injectable ``resolver`` seam so the command is
testable offline. A digest that is not published yet is REPORTED, not fatal: at repin
time the kit's next tag usually does not exist — the registry snippet is still the
deliverable, and portal sync re-resolves the digest at sync time anyway.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

# The repo whose pins this command moves. The regexes anchor on the full repo name so a
# kit's OTHER git dependencies can never be rewritten by accident.
CORE_REPO = "langfuse-synth-core"

# Snippet placeholder when the kit's next release tag is not decided yet.
KIT_TAG_PLACEHOLDER = "vX.Y.Z"

_GHCR_API = "https://ghcr.io"

# A dependency pin: `langfuse-synth-core[extras] @ git+https://github.com/<org>/langfuse-synth-core@<ref>`
_DEP_PIN_RE = re.compile(
    rf"({re.escape(CORE_REPO)}(?:\[[^\]]+\])?\s*@\s*git\+https://github\.com/[^@\"'\s]+/{re.escape(CORE_REPO)})@([^\"'\s]+)"
)

# The reusable-workflow pin:
# `uses: <org>/langfuse-synth-core/.github/workflows/kit-publish.yml@<ref>`
_WORKFLOW_PIN_RE = re.compile(
    rf"(uses:\s*[^\s@]*/{re.escape(CORE_REPO)}/\.github/workflows/kit-publish\.yml)@(\S+)"
)

# The straggler net, two layers wider than the rewrite regexes on purpose:
# any version-tag/SHA-looking token on a core-mentioning line, PLUS whatever ref sits
# directly after `langfuse-synth-core@` or `kit-publish.yml@` — the latter is what
# catches a branch pin (`@main`, `@v1.7`) that the version/SHA shape alone would miss.
_REF_TOKEN_RE = re.compile(r"@(v\d+\.\d+\.\d+|[0-9a-f]{7,40})\b")
_CORE_AT_RE = re.compile(rf"(?:{re.escape(CORE_REPO)}|kit-publish\.yml)@([^\s\"']+)")

_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class RepinError(Exception):
    """The repin cannot proceed (bad ref, missing kit file, or a stale pin left behind)."""


class DigestUnavailable(Exception):
    """GHCR serves no image for the requested kit tag (CI has not published it yet)."""


def validate_core_ref(ref: str) -> None:
    """Enforce RELEASING.md's pin rule: a ``vX.Y.Z`` tag or a full 40-hex SHA — never a
    branch, so a kit's vendor-approved output can never be silently rewritten later."""
    if not (_TAG_RE.match(ref) or _SHA_RE.match(ref)):
        raise RepinError(
            f"core ref {ref!r} is not a vX.Y.Z tag or a full 40-hex SHA — kits pin to a "
            "tag or full SHA, never a branch (RELEASING.md)."
        )


def rewrite_core_pins(text: str, core_ref: str) -> tuple[str, int]:
    """Move every ``langfuse-synth-core...@<ref>`` dependency pin in ``text`` to
    ``core_ref``. Returns the rewritten text and how many pins moved (a pin already on
    ``core_ref`` still counts — the point is how many the command now vouches for)."""
    new_text, moved = _DEP_PIN_RE.subn(rf"\1@{core_ref}", text)
    return new_text, moved


def rewrite_workflow_pin(text: str, core_ref: str) -> tuple[str, int]:
    """Move the ``kit-publish.yml@<ref>`` reusable-workflow pin to ``core_ref``."""
    new_text, moved = _WORKFLOW_PIN_RE.subn(rf"\1@{core_ref}", text)
    return new_text, moved


def stale_core_refs(text: str, core_ref: str) -> list[str]:
    """Every ref token pinned on a line mentioning the core repo that is NOT ``core_ref``
    — RELEASING.md step 3's "grep the kit's pyproject and check every hit moved",
    mechanized. Deliberately wider than the rewrite regexes: an ssh remote, a branch pin
    right after the repo name, a tag in an exotic position — each still trips this net
    and fails the repin loudly. (Not literally every conceivable shape: a ref that
    neither looks like a tag/SHA nor follows ``langfuse-synth-core@`` /
    ``kit-publish.yml@`` slips through — the net is belt-and-braces, not proof.)"""
    stale: list[str] = []
    for line in text.splitlines():
        if CORE_REPO not in line:
            continue
        tokens = set(_REF_TOKEN_RE.findall(line)) | set(_CORE_AT_RE.findall(line))
        stale.extend(ref for ref in tokens if ref != core_ref)
    return sorted(stale)


@dataclass
class RepinResult:
    kit_dir: Path
    core_ref: str
    slug: str
    pyproject_moves: int
    workflow_moves: int
    snippet: str
    image_ref: str | None = None
    image_note: str = ""
    dry_run: bool = False
    diffs: dict[str, str] = field(default_factory=dict)


def _unified_diff(rel_path: str, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
    )


def _read_slug(kit_dir: Path) -> str:
    """The registry snippet's ``slug`` comes from the kit's own usecase.yaml — the same
    value the portal cross-checks at sync. A plain line scan, not a YAML load: the
    manifest is validated elsewhere (`synth-authoring validate`); here we only need the
    one scalar."""
    manifest = kit_dir / "usecase.yaml"
    if not manifest.is_file():
        raise RepinError(f"{manifest} not found — is {kit_dir} a kit checkout?")
    for line in manifest.read_text().splitlines():
        if line.startswith("slug:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    raise RepinError(f"{manifest} declares no top-level `slug:`")


def _git_remote_url(kit_dir: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(kit_dir), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    url = out.stdout.strip()
    # Normalize the ssh remote form to the https form the registry records.
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    return url.removesuffix(".git") or None


def _image_name_from_repo_url(repo_url: str) -> str:
    """``https://github.com/<org>/<repo>`` -> ``<org>/<repo>`` — GHCR images publish to
    ``ghcr.io/<github.repository>`` (kit-publish.yml), so the repo IS the image name."""
    path = repo_url.removesuffix(".git").rstrip("/")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break
    org, _, repo = path.partition("/")
    if not org or not repo or "/" in repo:
        raise RepinError(f"cannot derive a GHCR image name from repo URL {repo_url!r}")
    return f"{org}/{repo}".lower()


def resolve_image_digest(repo_url: str, tag: str) -> str:
    """Ask GHCR's v2 registry API what digest it serves for ``tag`` and return the
    immutable ``ghcr.io/<org>/<repo>@sha256:<digest>`` ref — the same anonymous-token +
    ``Docker-Content-Digest`` flow the portal's sync resolver trusts (``tools/sync_ops.py``).

    Raises :class:`DigestUnavailable` when no image exists for the tag (kit not tagged,
    or its CI publish has not finished), and :class:`RepinError` on transport failures.
    """
    image_name = _image_name_from_repo_url(repo_url)
    try:
        token_resp = requests.get(
            f"{_GHCR_API}/token",
            params={"service": "ghcr.io", "scope": f"repository:{image_name}:pull"},
            timeout=10,
        )
        token_resp.raise_for_status()
        token = token_resp.json()["token"]
        manifest_resp = requests.get(
            f"{_GHCR_API}/v2/{image_name}/manifests/{tag}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": ", ".join(
                    [
                        "application/vnd.oci.image.index.v1+json",
                        "application/vnd.docker.distribution.manifest.list.v2+json",
                        "application/vnd.oci.image.manifest.v1+json",
                        "application/vnd.docker.distribution.manifest.v2+json",
                    ]
                ),
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        raise RepinError(f"could not reach GHCR to resolve {image_name}:{tag}: {exc}") from exc

    if manifest_resp.status_code == 404:
        raise DigestUnavailable(
            f"no GHCR image found for {image_name}:{tag} — not yet published (tag the kit "
            "and let its CI publish workflow run, then re-run with --kit-tag; portal sync "
            "also re-resolves the digest at sync time)."
        )
    if manifest_resp.status_code >= 400:
        raise RepinError(
            f"GHCR manifest lookup failed for {image_name}:{tag}: HTTP {manifest_resp.status_code}"
        )
    digest = manifest_resp.headers.get("Docker-Content-Digest")
    if not digest:
        raise RepinError(
            f"GHCR response for {image_name}:{tag} carried no Docker-Content-Digest header"
        )
    return f"ghcr.io/{image_name}@{digest}"


def registry_snippet(slug: str, repo_url: str, kit_tag: str, core_ref: str) -> str:
    """The portal ``registry.yaml`` entry for this kit's next release, ready to paste."""
    ref_comment = f"# core {core_ref} re-pin"
    if kit_tag == KIT_TAG_PLACEHOLDER:
        ref_comment += " — replace with the kit's next release tag"
    return (
        f"  - slug: {slug}\n"
        f"    repo_url: {repo_url}\n"
        f"    ref: {kit_tag}          {ref_comment}\n"
    )


def repin_kit(
    kit_dir: str | Path,
    core_ref: str,
    *,
    kit_tag: str | None = None,
    repo_url: str | None = None,
    dry_run: bool = False,
    resolver: Callable[[str, str], str] = resolve_image_digest,
) -> RepinResult:
    """Perform the whole re-pin step for the kit checkout at ``kit_dir``.

    Moves every core dependency pin in ``pyproject.toml`` AND the ``kit-publish.yml``
    workflow pin to ``core_ref``, refuses to write if any core ref the rewrite could not
    reach would be left behind, and builds the portal registry snippet. With ``dry_run``
    nothing is written and per-file unified diffs land in ``result.diffs``.
    """
    validate_core_ref(core_ref)
    if kit_tag is not None and not _TAG_RE.match(kit_tag):
        raise RepinError(f"kit tag {kit_tag!r} is not a vX.Y.Z release tag")

    kit_dir = Path(kit_dir)
    pyproject = kit_dir / "pyproject.toml"
    workflow = kit_dir / ".github" / "workflows" / "publish.yml"
    for path in (pyproject, workflow):
        if not path.is_file():
            raise RepinError(f"{path} not found — is {kit_dir} a kit checkout?")
    slug = _read_slug(kit_dir)

    repo_url = repo_url or _git_remote_url(kit_dir)
    if not repo_url:
        raise RepinError(
            f"could not derive the kit's repo URL from `git -C {kit_dir} remote get-url "
            "origin` — pass --repo-url."
        )

    old_py = pyproject.read_text()
    new_py, py_moves = rewrite_core_pins(old_py, core_ref)
    if py_moves == 0:
        raise RepinError(f"{pyproject} carries no {CORE_REPO} git pin — nothing to repin")
    stale = stale_core_refs(new_py, core_ref)
    if stale:
        raise RepinError(
            f"stale {CORE_REPO} ref(s) {', '.join(stale)} would remain in {pyproject} "
            "after the rewrite — a pin shape this command does not recognize. Fix it by "
            "hand (or normalize it to the standard git+https pin), then re-run."
        )

    old_wf = workflow.read_text()
    new_wf, wf_moves = rewrite_workflow_pin(old_wf, core_ref)
    if wf_moves == 0:
        raise RepinError(f"{workflow} carries no kit-publish.yml@<ref> pin — nothing to repin")
    stale_wf = stale_core_refs(new_wf, core_ref)
    if stale_wf:
        raise RepinError(
            f"stale {CORE_REPO} ref(s) {', '.join(stale_wf)} would remain in {workflow} "
            "after the rewrite — a pin shape this command does not recognize. Fix it by "
            "hand, then re-run."
        )

    diffs: dict[str, str] = {}
    if dry_run:
        diffs["pyproject.toml"] = _unified_diff("pyproject.toml", old_py, new_py)
        diffs[".github/workflows/publish.yml"] = _unified_diff(
            ".github/workflows/publish.yml", old_wf, new_wf
        )
    else:
        pyproject.write_text(new_py)
        workflow.write_text(new_wf)

    image_ref: str | None = None
    if kit_tag is None:
        image_note = (
            "image digest not resolved: pass --kit-tag vA.B.C (the kit's next release "
            "tag) to resolve it from GHCR."
        )
    else:
        try:
            image_ref = resolver(repo_url, kit_tag)
            image_note = f"image: {image_ref}"
        except DigestUnavailable as exc:
            image_note = str(exc)
        except RepinError as exc:
            image_note = (
                f"{exc} (the snippet is still valid — portal sync resolves the digest "
                "at sync time)"
            )

    return RepinResult(
        kit_dir=kit_dir,
        core_ref=core_ref,
        slug=slug,
        pyproject_moves=py_moves,
        workflow_moves=wf_moves,
        snippet=registry_snippet(slug, repo_url, kit_tag or KIT_TAG_PLACEHOLDER, core_ref),
        image_ref=image_ref,
        image_note=image_note,
        dry_run=dry_run,
        diffs=diffs,
    )
