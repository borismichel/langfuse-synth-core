"""Resumable released-candidate handoff using the depot's existing admission API.

The portal owns release resolution, validation, scratch deployment and verdicts.
This client owns only bounded polling and a nonsecret receipt; it never edits a
registry, creates a release or publishes a catalog entry.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests
import yaml

_ENDPOINT = "/api/v1/admin/admission"
_RELEASE = re.compile(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")
_LADDER = ("register", "build", "spawn", "seed", "verify")
_SHA = re.compile(r"[0-9a-f]{40}")
_IMAGE = re.compile(r"ghcr\.io/[A-Za-z0-9_./-]+@sha256:[0-9a-f]{64}")
_RUNG_ACTION = {
    "register": "Fix the candidate Manifest; release code changes under a new tag.",
    "build": "Check the released tag and signed GHCR image.",
    "spawn": "Check scratch-target configuration and deployment capacity.",
    "seed": "Inspect the seed job and reset its disposable data before retrying ingestion.",
    "verify": "Inspect the expected current-run records and the verification job.",
    "companion_smoke": "Check the Companion health route and Adapter readiness.",
}
_REPO = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?")
_ACTION = {
    401: "Refresh the admin session token and rerun with the same state file.",
    403: "Use an authenticated depot admin account.",
    404: "Check the portal address and saved run ID; do not discard progress blindly.",
    422: "Fix the candidate Manifest/register findings in the portal, then release a new version.",
    429: "Wait for scratch deployment capacity, then rerun with the same state file.",
    502: "Check the release tag and its published, signed GHCR image; rerun when available.",
    503: "Ask the depot admin to configure the disposable scratch admission target.",
}


class AdmissionError(ValueError):
    """An actionable handoff error safe for the terminal."""


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("admit", help="resume admission of a released candidate")
    parser.add_argument("--repo", required=True, help="credential-free GitHub HTTPS repo URL")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--ref", required=True, help="immutable published vX.Y.Z release tag")
    parser.add_argument("--portal", required=True, help="depot API origin (HTTPS)")
    parser.add_argument(
        "--token-env", default="DEMO_DEPOT_TOKEN", help="admin bearer-token env name"
    )
    parser.add_argument("--state", required=True, help="nonsecret progress JSON path to reuse")
    parser.add_argument("--timeout", type=float, default=60, help="polling seconds; 0 checks once")
    parser.add_argument("--poll-interval", type=float, default=2)
    parser.add_argument("--retry-failed", action="store_true", help="explicitly retry a failed run")
    parser.set_defaults(func=execute)


def _candidate(args) -> dict:
    portal = args.portal.rstrip("/")
    try:
        url = urlsplit(portal)
    except ValueError as exc:
        raise AdmissionError("Use a valid credential-free HTTPS portal origin.") from exc
    local = url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        (url.scheme != "https" and not local)
        or not url.netloc
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path
    ):
        raise AdmissionError("Use a credential-free HTTPS portal origin (HTTP only for loopback).")
    if not _REPO.fullmatch(args.repo):
        raise AdmissionError("Use a credential-free https://github.com/owner/repo URL.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug):
        raise AdmissionError("Use the Manifest's lowercase hyphenated slug.")
    if not _RELEASE.fullmatch(args.ref):
        raise AdmissionError(
            "Use an immutable released version tag such as v1.2.3, never a branch."
        )
    if (
        not math.isfinite(args.timeout)
        or args.timeout < 0
        or not math.isfinite(args.poll_interval)
        or args.poll_interval <= 0
    ):
        raise AdmissionError(
            "Timeout must be finite and nonnegative; poll interval must be positive."
        )
    return {"portal": portal, "repo_url": args.repo, "slug": args.slug, "ref": args.ref}


def _save(path: Path, state: dict, token: str) -> None:
    if token in json.dumps(state):
        raise AdmissionError("Credential material appeared in candidate evidence; refusing it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            json.dump(state, stream, indent=2)
            stream.write("\n")
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _read(path: Path, candidate: dict) -> dict:
    if not path.exists():
        return {"schema_version": 1, "candidate": candidate}
    try:
        state = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise AdmissionError(
            "Cannot read progress; repair it or select a separate --state path."
        ) from exc
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != 1
        or state.get("candidate") != candidate
    ):
        raise AdmissionError(
            "Saved candidate differs; use its original arguments or a new --state path."
        )
    initial_fields = {"schema_version", "candidate"}
    run_fields = {"run_id", "git_sha", "image_ref", "status"}
    try:
        if type(state["schema_version"]) is not int:
            raise ValueError
        if set(state) not in (initial_fields, initial_fields | run_fields):
            raise ValueError
        if "run_id" in state:
            state["run_id"] = str(uuid.UUID(state["run_id"]))
            if (
                not _SHA.fullmatch(state["git_sha"])
                or not _IMAGE.fullmatch(state["image_ref"])
                or state["status"] not in {"passed", "failed", "pending"}
            ):
                raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise AdmissionError("Saved progress is invalid; repair it before resuming.") from exc
    return state


def _request(client, method: str, candidate: dict, token: str, run_id=None) -> dict:
    url = candidate["portal"] + _ENDPOINT + (f"/{run_id}" if run_id else "")
    payload = {k: candidate[k] for k in ("repo_url", "slug", "ref")} if method == "POST" else None
    try:
        resp = client.request(
            method,
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        # Never echo request objects, URLs from exceptions, response bodies or headers.
        raise AdmissionError(
            "Portal request failed or timed out; rerun with the same state file."
        ) from exc
    if resp.status_code not in {200, 201}:
        action = _ACTION.get(
            resp.status_code, "Inspect the portal; rerun with the same state file."
        )
        raise AdmissionError(f"Portal HTTP {resp.status_code}: {action}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AdmissionError("Portal returned invalid JSON; no handoff is available.") from exc


def _checked_run(run: dict, state: dict, *, retry_failed: bool = False) -> dict:
    try:
        run_id = str(uuid.UUID(run["id"]))
        sha = run["git_sha"]
        image = run["image_ref"]
        verdict = run["verdict"]
        if (
            run["candidate_slug"] != state["candidate"]["slug"]
            or not _SHA.fullmatch(sha)
            or not _IMAGE.fullmatch(image)
        ):
            raise ValueError
        if state.get("run_id") and (
            (state["run_id"] != run_id and not retry_failed)
            or state["git_sha"] != sha
            or state["image_ref"] != image
        ):
            raise ValueError
        if not isinstance(verdict["passed"], bool) or not isinstance(
            verdict["eligible_to_pin"], bool
        ):
            raise ValueError
        steps = verdict["steps"]
        if not isinstance(steps, list):
            raise ValueError
        names = tuple(step["step"] for step in steps)
        if names not in (_LADDER, (*_LADDER, "companion_smoke")):
            raise ValueError
        statuses = [step["status"] for step in steps]
        if any(status not in {"passed", "failed", "pending"} for status in statuses):
            raise ValueError
        failed = [step["step"] for step in steps if step["status"] == "failed"]
        if (
            len(failed) > 1
            or verdict.get("failed_step") != (failed[0] if failed else None)
            or verdict["passed"] != all(status == "passed" for status in statuses)
            or verdict["eligible_to_pin"] != verdict["passed"]
        ):
            raise ValueError
        # Rungs advance in order: after a failed or pending rung, only pending follows.
        stopped = False
        for step in steps:
            if stopped and step["status"] != "pending":
                raise ValueError
            stopped = stopped or step["status"] != "passed"
            if step.get("exit_code") is not None and type(step["exit_code"]) is not int:
                raise ValueError
        if verdict["passed"] and verdict.get("failed_step"):
            raise ValueError
        return {
            "schema_version": 1,
            "candidate": state["candidate"],
            "run_id": run_id,
            "git_sha": sha,
            "image_ref": image,
            "status": "passed"
            if verdict["passed"]
            else ("failed" if verdict.get("failed_step") else "pending"),
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AdmissionError(
            "Portal result is inconsistent with the candidate or saved evidence."
        ) from exc


def _print_step(step: dict) -> None:
    # Free-form job errors can contain arbitrary credentials. Show validated protocol
    # fields and a local next action; inspect raw evidence in the authenticated portal.
    detail = ""
    if step.get("exit_code") is not None:
        detail = f" (exit {step['exit_code']})"
    if step["status"] == "failed":
        detail += " " + _RUNG_ACTION[step["step"]]
    elif step["status"] == "pending":
        detail += " Waiting for this rung; resume with the same state file."
    print(f"  {step['step']}: {step['status']}{detail}")


def execute(args: argparse.Namespace) -> int:
    try:
        candidate = _candidate(args)
        token = os.environ.get(args.token_env, "")
        if not token or any(c in token for c in "\r\n"):
            raise AdmissionError("Set the admin bearer token in the selected environment variable.")
        path = Path(args.state).resolve()
        state = _read(path, candidate)
        # Record intent before POST; the server handles uncertain retries idempotently.
        _save(path, state, token)
        deadline = time.monotonic() + args.timeout
        with requests.Session() as client:
            client.trust_env = False  # no inherited proxy credentials or .netrc authentication
            retry = args.retry_failed and state.get("status") == "failed"
            method = "GET" if state.get("run_id") and not retry else "POST"
            while True:
                run = _request(
                    client,
                    method,
                    candidate,
                    token,
                    state.get("run_id") if method == "GET" else None,
                )
                state = _checked_run(run, state, retry_failed=retry)
                retry = False
                _save(path, state, token)
                verdict = run["verdict"]
                print(f"Admission {state['run_id']}: {state['status']}")
                for step in verdict["steps"]:
                    _print_step(step)
                if state["status"] == "passed":
                    print(f"Verified commit {state['git_sha']}; image {state['image_ref']}")
                    print("Registry handoff (no file changed):")
                    print(
                        yaml.safe_dump(
                            {"use_cases": [{k: candidate[k] for k in ("slug", "repo_url", "ref")}]},
                            sort_keys=False,
                        ).rstrip()
                    )
                    print(
                        "After reviewed registry merge and sync, a new kit enters staging. "
                        "Rehearse as admin, then explicitly publish. "
                        "Existing visibility is retained."
                    )
                    return 0
                if state["status"] == "failed":
                    print(
                        f"Failed {verdict['failed_step']}; inspect raw job evidence "
                        "in the authenticated portal (not copied into this receipt)."
                    )
                    print(
                        "Inspect that rung in the portal. Release code fixes under a new tag; "
                        "use --retry-failed only for a repaired transient failure."
                    )
                    return 1
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    print(
                        "Pending; rerun with the same arguments and state file to resume. "
                        "No registry handoff is available yet."
                    )
                    return 2
                time.sleep(min(args.poll_interval, remaining))
                method = "GET"
    except AdmissionError as exc:
        print(f"admit: {exc}")
        return 1
    except OSError:
        print("admit: Cannot save/read progress; check the state directory permissions.")
        return 1
