"""The scaffolded kit's BUILT image runs as the portal runs it — uid 10001, writing (#189).

Every other authoring gate — the kit suite, the determinism golden, ``synth-authoring
validate`` — runs the kit as the author's own user from a source checkout. None of them
builds the Dockerfile, let alone runs the result as the job runner's uid. That gap shipped
a scaffold whose ``COPY . .`` left /app root-owned: the container drops to uid 10001
(JOB_RUN_USER) and the kit dies on ``open_spool()`` at its first real deployment, with
/app/out — where the worker collects declared artifacts — as the second casualty.

This is the honest gate: build the emitted Dockerfile and run ``synth seed --dry-run`` in
it as uid 10001 (offline at runtime — dry-run skips the guardrail and the import), proving
both runtime write paths take writes. A grep of the Dockerfile cannot prove this
(``test_reference_dockerfile_makes_the_runtime_write_paths_writable`` guards the shape
only); running the image is the point.

Cost and where it runs: the build needs a Docker daemon plus network (the base image and a
git-pinned core release — the subject under test is the emitted Dockerfile, not core's
Python). The module skips wherever there is no daemon; core CI's ``authoring-suite`` job
(GitHub-hosted ubuntu, daemon preinstalled) has one, so the gate is load-bearing on every
core PR. It deliberately does NOT ship in every scaffolded kit's suite — kit CI would pay
the build on every push to re-prove a file only the scaffolder changes.

Which core ref the image installs: the **latest tag that actually exists on origin**, not
``DEFAULT_CORE_REF``. On a release-bump PR the default names the tag being cut — it cannot
exist until after the PR merges, so scaffolding with it makes ``pip install`` fail inside
the build on every release PR (found cutting v1.7.0). Resolving the newest published
``v*`` tag keeps the gate green through a release while still building against a real,
installable core.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import uuid

import pytest


def _docker_daemon_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


pytestmark = [
    pytest.mark.skipif(
        importlib.util.find_spec("jsonschema") is None,
        reason="the scaffolder ships in the [authoring] extra; not on a runtime-only job",
    ),
    pytest.mark.skipif(
        not _docker_daemon_available(),
        reason="needs a Docker daemon to build and run the scaffolded image",
    ),
]

# The uid:gid the portal's worker passes as JOB_RUN_USER — matched explicitly rather than
# trusting the image's own USER, because that is how job containers actually start.
JOB_RUN_USER = "10001:10001"

BUILD_TIMEOUT = 900  # base-image pull + apt git + pip install of the pinned core
RUN_TIMEOUT = 300

CORE_REPO_URL = "https://github.com/borismichel/langfuse-synth-core"


def _latest_released_core_ref() -> str:
    """The newest ``vX.Y.Z`` tag on origin — a ref ``pip install`` can always resolve.

    ``DEFAULT_CORE_REF`` is deliberately not used: on a release-bump PR it names the tag
    being cut, which does not exist yet (CI checkouts are shallow and tagless anyway, so
    this asks the remote). Falls back to ``DEFAULT_CORE_REF`` only if the remote cannot be
    queried — in which case the build was doomed without network regardless.
    """
    from langfuse_synth_core.authoring.scaffold import DEFAULT_CORE_REF

    try:
        out = subprocess.run(
            ["git", "ls-remote", "--tags", CORE_REPO_URL, "v*"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return DEFAULT_CORE_REF
    versions = []
    for line in out.splitlines():
        _, _, ref = line.partition("\t")
        tag = ref.removeprefix("refs/tags/").removesuffix("^{}")
        m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
        if m:
            versions.append((tuple(int(g) for g in m.groups()), tag))
    return max(versions)[1] if versions else DEFAULT_CORE_REF


@pytest.fixture(scope="module")
def image(tmp_path_factory):
    """Scaffold one kit, build its emitted Dockerfile, yield the tag, clean up the image."""
    from langfuse_synth_core.authoring.scaffold import scaffold_kit

    dest = tmp_path_factory.mktemp("kits") / "image-gate"
    scaffold_kit("image-gate", dest, core_ref=_latest_released_core_ref())

    tag = f"synth-scaffold-gate:{uuid.uuid4().hex[:12]}"
    build = subprocess.run(
        ["docker", "build", "-t", tag, "."],
        cwd=dest,
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
    )
    if build.returncode != 0:
        pytest.fail(f"docker build of the scaffolded kit failed:\n{build.stderr[-4000:]}")
    yield tag
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, timeout=60)


def _run_as_job_user(image_tag: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "run", "--rm", "--user", JOB_RUN_USER, image_tag, *argv],
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT,
    )


def test_seed_dry_run_writes_the_spool_as_uid_10001(image):
    """The exact first casualty of #189: `seed` must get through `open_spool()` and write
    /app/.synth_spool as the job uid. Dry-run keeps it offline (no guardrail, no import)."""
    result = _run_as_job_user(image, "synth", "seed", "--config", "config/demo.yaml", "--dry-run")
    assert result.returncode == 0, (
        f"seed --dry-run failed as {JOB_RUN_USER}:\n{result.stdout}\n{result.stderr}"
    )
    assert "spooled" in result.stdout, "seed exited 0 but never reported spooling events"


def test_artifact_dir_takes_writes_as_uid_10001(image):
    """The second casualty: the worker collects declared artifacts from /app/out after a
    step exits, so a kit that spooled fine still could not publish its Presenter Runbook."""
    result = _run_as_job_user(image, "sh", "-c", "touch /app/out/probe")
    assert result.returncode == 0, (
        f"/app/out is not writable as {JOB_RUN_USER}:\n{result.stderr}"
    )
