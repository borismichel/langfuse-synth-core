"""Local benchmark recording; offline timings never stand in for authoring outcomes."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from importlib import metadata, resources
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from jsonschema import Draft202012Validator, FormatChecker

BRIEFS = ("adapt-kit", "new-spool", "interactive-companion")


def _git(source: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def _new_record(args: argparse.Namespace) -> dict:
    source = Path(args.source).resolve()
    if not (source / "src/langfuse_synth_core/authoring/cli.py").is_file():
        raise ValueError("--source must be a core checkout with the authoring CLI")
    outcome = {"status": "pending", "evidence": []}
    return {
        "schema_version": 1,
        "protocol_version": 1,
        "run_id": str(uuid.uuid4()),
        "brief": args.brief,
        "mode": "authoring",
        "source_revision": _git(source, "rev-parse", "HEAD"),
        "source_dirty": bool(_git(source, "status", "--porcelain")),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "platform": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(),
            "dependencies": dict(sorted(
                (d.metadata["Name"], d.version) for d in metadata.distributions()
                if d.metadata["Name"].lower() != "langfuse-synth-core"
            )),
            "context": "Record hardware, tool access, network and deployment versions here.",
        },
        "agent": {"name": args.agent, "model": args.model, "settings": json.loads(args.settings)},
        "inputs": {},
        "status": "pending",
        "first_walkthrough": {**outcome, "seconds": None},
        "human_interventions": None,
        "intervention_evidence": [],
        "admission": dict(outcome),
        "rehearsal": dict(outcome),
        "offline_checks": [],
        "notes": "Authoring and live outcomes await observation.",
    }


def _validate(record: dict) -> None:
    json.dumps(record, allow_nan=False)
    schema = json.loads(resources.files(__package__).joinpath("benchmark.schema.json").read_text())
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(record),
        key=lambda e: str(list(e.path)),
    )
    if errors:
        raise ValueError("; ".join(f"{list(e.path)}: {e.message}" for e in errors))
    if record["mode"] == "offline":
        if record["agent"] is not None or record["human_interventions"] is not None:
            raise ValueError("offline runs cannot measure agent or human interventions")
        if any(record[k]["status"] != "pending" for k in
               ("first_walkthrough", "admission", "rehearsal")):
            raise ValueError("offline runs must leave walkthrough/admission/rehearsal pending")
        if record["status"] == "completed" and (
            not record["offline_checks"] or
            any(c["exit_code"] != 0 for c in record["offline_checks"])
        ):
            raise ValueError("completed offline runs require successful recorded checks")
    else:
        if record["agent"] is None or record["offline_checks"]:
            raise ValueError("authoring runs require an agent and separate offline records")
        if record["status"] == "completed":
            if record["first_walkthrough"]["status"] != "passed":
                raise ValueError("completed authoring requires an evidenced coherent walkthrough")
            if record["human_interventions"] is None or not record["intervention_evidence"]:
                raise ValueError(
                    "completed authoring requires intervention count and session evidence"
                )
            if any(record[k]["status"] == "pending" for k in ("admission", "rehearsal")):
                raise ValueError("completed authoring requires observed admission and rehearsal")
            if record["brief"] == "adapt-kit" and not record["inputs"]:
                raise ValueError("adapt-kit requires pinned starting-kit inputs")
    walk = record["first_walkthrough"]
    if (walk["status"] == "passed") != (walk["seconds"] is not None):
        raise ValueError("walkthrough seconds exist exactly when a walkthrough passed")
    for key in ("first_walkthrough", "admission", "rehearsal"):
        outcome = record[key]
        if outcome["status"] != "pending" and not outcome["evidence"]:
            raise ValueError(f"{key} requires evidence for an observed outcome")


def _write(path: Path, record: dict) -> None:
    _validate(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(json.dumps(record, indent=2, allow_nan=False) + "\n")


def execute(args: argparse.Namespace) -> int:
    try:
        if args.action == "brief":
            print(resources.files(__package__).joinpath(
                "benchmark_briefs", f"{args.brief}.md"
            ).read_text())
        elif args.action == "init":
            _write(Path(args.out), _new_record(args))
            print(f"Pending benchmark record: {args.out}")
        elif args.action == "validate":
            for path in args.records:
                _validate(json.loads(Path(path).read_text()))
            print(f"Validated {len(args.records)} benchmark record(s).")
        elif args.action == "summary":
            print(json.dumps(summarize(args.records), indent=2, allow_nan=False))
        elif args.action == "offline":
            return _offline(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"benchmark: {exc}", file=sys.stderr)
        return 2
    return 0


def summarize(paths: list[str]) -> dict:
    """Partition by fixed inputs/environment, with revisions as comparison columns."""
    groups: dict[str, dict] = {}
    excluded: Counter = Counter()
    seen: set[str] = set()
    for path in paths:
        record = json.loads(Path(path).read_text())
        _validate(record)
        if record["run_id"] in seen:
            raise ValueError(f"duplicate run_id: {record['run_id']}")
        seen.add(record["run_id"])
        if record["status"] != "completed" or record["source_dirty"]:
            excluded["dirty" if record["source_dirty"] else record["status"]] += 1
            continue
        cohort = {k: record[k] for k in
                  ("protocol_version", "brief", "mode", "environment", "agent", "inputs")}
        if record["mode"] == "offline":
            cohort["checks"] = [c["name"] for c in record["offline_checks"]]
        key = json.dumps(cohort, sort_keys=True)
        group = groups.setdefault(key, {"cohort": cohort, "revisions": {}})
        samples = group["revisions"].setdefault(record["source_revision"], [])
        samples.append(record)
    for group in groups.values():
        for revision, records in group["revisions"].items():
            metrics = {"runs": len(records), "run_ids": [r["run_id"] for r in records]}
            if group["cohort"]["mode"] == "offline":
                metrics["offline_seconds_mean"] = {
                    name: sum(next(c["seconds"] for c in r["offline_checks"]
                                   if c["name"] == name) for r in records) / len(records)
                    for name in group["cohort"]["checks"]
                }
                metrics["authoring_and_live"] = "pending (not measured by offline checks)"
            else:
                metrics["walkthrough_seconds_mean"] = sum(
                    r["first_walkthrough"]["seconds"] for r in records
                ) / len(records)
                metrics["human_interventions_mean"] = sum(
                    r["human_interventions"] for r in records
                ) / len(records)
                for outcome in ("admission", "rehearsal"):
                    metrics[f"{outcome}_passed"] = sum(
                        r[outcome]["status"] == "passed" for r in records
                    )
            group["revisions"][revision] = metrics
    return {"groups": list(groups.values()), "excluded": dict(excluded)}


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("benchmark", help="record and compare time-to-demo evidence")
    actions = parser.add_subparsers(dest="action", required=True)
    brief = actions.add_parser("brief", help="print a fixed benchmark brief")
    brief.add_argument("brief", choices=BRIEFS)
    init = actions.add_parser("init", help="start an authoring record with pending outcomes")
    init.add_argument("brief", choices=BRIEFS)
    init.add_argument("--agent", required=True)
    init.add_argument("--model", required=True)
    init.add_argument("--settings", required=True, help="JSON object of exact agent settings")
    offline = actions.add_parser("offline", help="measure scaffold and checks without a live host")
    offline.add_argument("--companion", action="store_true")
    for command in (init, offline):
        command.add_argument("--source", required=True, help="core Git checkout under measurement")
        command.add_argument(
            "--out", required=True, help="new JSON record path (never overwritten)"
        )
    for name in ("validate", "summary"):
        action = actions.add_parser(name)
        action.add_argument("records", nargs="+")
    parser.set_defaults(func=execute)


def _offline(args: argparse.Namespace) -> int:
    """Measure the selected checkout via public subprocess commands, without installing it."""
    args.brief = "interactive-companion" if args.companion else "new-spool"
    args.agent, args.model, args.settings = "offline", "none", "{}"
    record = _new_record(args)
    record.update(mode="offline", agent=None)
    record["environment"]["context"] = (
        "Local subprocesses; existing Python environment; no install, image build or deployment. "
        "System load and filesystem caches uncontrolled."
    )
    record["inputs"] = {"scaffold": "companion" if args.companion else "pure-spool"}
    record["notes"] = (
        "Observed scaffold/check timings only. No agent authored the brief; "
        "first coherent walkthrough, interventions, admission and rehearsal remain pending."
    )
    source, output = Path(args.source).resolve(), Path(args.out).resolve()
    evidence = output.parent / (output.stem + "-evidence")
    if output.exists() or evidence.exists():
        raise FileExistsError(f"record or evidence already exists: {output}")
    evidence.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="synth-benchmark-") as temp:
        kit = Path(temp) / "benchmark-kit"
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(source / "src"), str(kit / "src")])
        # No inherited pytest selection/plugins or caller project on the Python path.
        env.pop("PYTEST_ADDOPTS", None)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        base = [sys.executable, "-m", "langfuse_synth_core.authoring.cli"]
        new = [*base, "new", "benchmark-kit", "--dir", temp,
               "--core-ref", record["source_revision"]]
        if args.companion:
            new.append("--companion")
        commands = [
            ("scaffold", new, Path(temp)),
            ("validate", [*base, "validate", str(kit / "usecase.yaml")], kit),
            ("conformance", [*base, "conformance", str(kit)], kit),
            ("kit-tests", [sys.executable, "-m", "pytest", "-q"], kit),
        ]
        for name, command, cwd in commands:
            started = time.perf_counter()
            try:
                result = subprocess.run(command, cwd=cwd, env=env, text=True,
                                        capture_output=True, timeout=120)
                code, log = result.returncode, result.stdout + result.stderr
            except subprocess.TimeoutExpired:
                code, log = 124, "Timed out after 120 seconds.\n"
            elapsed = time.perf_counter() - started
            log_path = evidence / f"{name}.txt"
            log_path.write_text(json.dumps(command) + "\n" + log)
            record["offline_checks"].append({
                "name": name, "seconds": elapsed, "exit_code": code,
                "evidence": [str(log_path.relative_to(output.parent))],
            })
            if code:
                break
    record["status"] = "completed" if all(
        c["exit_code"] == 0 for c in record["offline_checks"]
    ) else "failed"
    _write(output, record)
    print(f"Recorded {record['status']} offline measurements: {output}")
    return 0 if record["status"] == "completed" else 1
