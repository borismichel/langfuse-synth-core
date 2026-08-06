"""The shared kit publish workflow must publish a MULTI-ARCH image (portal #185).

Kit images are consumed by the portal worker on whatever architecture the depot host
happens to be — the dedicated host is amd64, but a presenter's laptop running the local
demo stack is arm64. A single-arch manifest makes `POST /images/create` 404 there with
``no matching manifest for linux/arm64/v8``, which is exactly the failure #185 reports.

There is no unit-testable seam inside a GitHub Actions workflow, so this suite does the
next best thing: it parses the YAML and pins the properties that, if silently dropped,
recreate the outage. It is a drift guard, not a substitute for a real tag push.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/kit-publish.yml"

# The two architectures the depot actually runs on: the amd64 depot host and arm64
# Apple-silicon laptops running the local demo stack.
REQUIRED_PLATFORMS = {"linux/amd64", "linux/arm64"}


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: dict) -> list[dict]:
    return list(job.get("steps") or [])


def _text(node: object) -> str:
    """Every scalar in a job/step subtree, flattened — for substring assertions."""
    return json.dumps(node)


def test_workflow_is_callable_by_kits(workflow: dict) -> None:
    # `on` parses as the YAML 1.1 boolean True unless quoted; accept either spelling so
    # the guard survives a cosmetic edit to the workflow header.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict) and "workflow_call" in triggers


def test_every_required_platform_is_built(workflow: dict) -> None:
    built = _text(workflow["jobs"])
    missing = {p for p in REQUIRED_PLATFORMS if p not in built}
    assert not missing, f"kit images would not run on: {sorted(missing)}"


def test_arm64_builds_on_a_native_arm_runner(workflow: dict) -> None:
    """Native arm64 runners, not QEMU emulation.

    GitHub-hosted arm runners are free on public repos (which every kit is), and an
    emulated arm64 wheel build is slow enough to make releases painful. If this ever
    flips to QEMU deliberately, this test is the place to record that decision.
    """
    matrix_entries = [
        entry
        for job in workflow["jobs"].values()
        for entry in (job.get("strategy", {}).get("matrix", {}).get("include") or [])
    ]
    arm = [e for e in matrix_entries if e.get("platform") == "linux/arm64"]
    assert arm, "no matrix entry builds linux/arm64"
    assert all("arm" in e.get("runner", "") for e in arm), (
        "linux/arm64 must build on an arm runner, not under emulation"
    )
    assert "setup-qemu-action" not in _text(workflow["jobs"])


def test_per_arch_builds_push_by_digest_and_do_not_claim_the_tag(
    workflow: dict,
) -> None:
    """Only the merged index may carry the release tag.

    A per-arch build that pushes `:vX.Y.Z` directly would have the two architectures
    race, and the last writer would leave a single-arch tag behind — the #185 bug with
    extra steps.
    """
    build_jobs = [
        job
        for job in workflow["jobs"].values()
        if job.get("strategy", {}).get("matrix", {}).get("include")
    ]
    assert build_jobs, "expected a per-architecture build matrix"
    for job in build_jobs:
        push_steps = [
            s for s in _steps(job) if "build-push-action" in str(s.get("uses", ""))
        ]
        assert push_steps, "expected a build-push step in each per-arch build"
        for step in push_steps:
            with_ = step.get("with", {})
            assert "push-by-digest=true" in str(with_.get("outputs", ""))
            # `tags:` is how build-push-action claims a tag. The version LABEL may
            # still name the release; only the tag is contended.
            assert "tags" not in with_, (
                "a per-arch build must not push the release tag; the merge job owns it"
            )


def test_the_release_tag_and_the_signature_cover_the_merged_index(
    workflow: dict,
) -> None:
    """The tag the portal resolves and the digest cosign signs must be the index.

    The portal pins `ghcr.io/<org>/<repo>@sha256:<index digest>`; its E2 cosign gate
    verifies that same ref. Signing a per-arch child manifest instead would leave the
    ref the portal actually pulls unsigned.
    """
    merge_jobs = [
        job
        for job in workflow["jobs"].values()
        if "imagetools create" in _text(job).replace("\\n", " ")
    ]
    assert len(merge_jobs) == 1, "expected exactly one manifest-merge job"
    merge = merge_jobs[0]
    assert merge.get("needs"), "the merge job must wait for the per-arch builds"

    merge_steps = [s for s in _steps(merge) if "imagetools create" in _text(s)]
    assert len(merge_steps) == 1
    # The tag must be applied by `imagetools create` itself — a `ref_name` that only
    # reaches the step summary would leave the release tag unpublished.
    step = merge_steps[0]
    tag_var = next(
        (k for k, v in (step.get("env") or {}).items() if "github.ref_name" in str(v)),
        None,
    )
    assert tag_var, "the merge step must bind the release tag into its env"
    run = str(step.get("run", ""))
    assert "--tag" in run and tag_var in run.split("--tag", 1)[1].split("\n", 1)[0], (
        "the merge job must pass the release tag to `imagetools create`"
    )

    sign_steps = [s for s in _steps(merge) if "cosign sign" in _text(s)]
    assert len(sign_steps) == 1, "expected exactly one cosign signing step"
    # The signature must cover the digest the MERGE step resolved (the index), not a
    # per-arch child from the build matrix — and never the mutable tag.
    signed = _text(sign_steps[0])
    assert "@${" in signed and f"steps.{merge_steps[0]['id']}.outputs" in signed, (
        "cosign must sign the merged index digest"
    )


def test_the_tag_is_checked_against_pyproject_before_any_build(workflow: dict) -> None:
    """A tag whose pyproject `version` disagrees must fail before anything is pushed.

    EV/Lender v0.4.0 and support v0.1.4 all published carrying the previous release's
    `version` field — `pip show` inside those images reports the wrong version. The
    guard only helps if every build waits for it, so pin the `needs` edge too.
    """
    verify = [
        name
        for name, job in workflow["jobs"].items()
        if "pyproject.toml" in _text(job) and "GITHUB_REF_NAME" in _text(job)
    ]
    assert len(verify) == 1, "expected exactly one tag/pyproject version-check job"
    build_jobs = [
        job
        for job in workflow["jobs"].values()
        if job.get("strategy", {}).get("matrix", {}).get("include")
    ]
    assert build_jobs, "expected a per-architecture build matrix"
    for job in build_jobs:
        needs = job.get("needs")
        needs = [needs] if isinstance(needs, str) else list(needs or [])
        assert verify[0] in needs, (
            "per-arch builds must wait for the version check, or a mismatched tag "
            "still pushes per-arch manifests by digest"
        )


def test_signing_identity_stays_kit_publish_yml(workflow: dict) -> None:
    """#104's verification identity is `kit-publish.yml@refs/tags/v*`.

    That identity comes from the OIDC `job_workflow_ref` claim, which names THIS file
    for every job in it — splitting the build across jobs does not move it. What would
    break the contract is signing from somewhere else (a composite action, another
    reusable workflow), so pin that the signing job is defined inline here.
    """
    for job in workflow["jobs"].values():
        assert "uses" not in job, (
            "a job delegating to another reusable workflow would change the cosign "
            "identity #104 verifies"
        )
    assert workflow["permissions"]["id-token"] == "write"
