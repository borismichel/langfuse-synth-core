"""One bounded offline check over the existing validator, conformance and kit tests.

Each stage runs in the chosen Python environment and a fresh process. Kit imports
cannot contaminate the authoring process. This guards ordinary Python network calls;
it is not an operating-system sandbox for untrusted tests or native subprocesses.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_ACTIONS = {
    "validate": "Fix the manifest findings, then rerun check.",
    "conformance": "Fix the Contract findings or install missing Companion dependencies.",
    "tests": "Fix the reported kit tests; bless golden changes only when intentional.",
}


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("check", help="run all local authoring checks offline")
    parser.add_argument("kit", nargs="?", default=".")
    parser.add_argument("--json", action="store_true", help="emit schema-versioned JSON")
    parser.add_argument("--python", default=sys.executable, help="kit's Python environment")
    parser.add_argument("--timeout", type=float, default=120, help="seconds per stage")
    parser.set_defaults(func=execute)


def _result(name: str, status: str, code: int | None, output: str, action: str = "") -> dict:
    return {"name": name, "status": status, "exit_code": code,
            "output": output, "next_action": action}


def _run_stage(name: str, kit: Path, python: str, timeout: float, env: dict) -> dict:
    command = [python, "-m", __name__, name, str(kit)]
    try:
        proc = subprocess.run(command, cwd=kit, env=env, capture_output=True,
                              text=True, timeout=timeout)
    except OSError as exc:
        return _result(name, "unavailable", None, str(exc),
                       "Select an installed kit Python with --python; install .[dev].")
    except subprocess.TimeoutExpired:
        return _result(name, "failed", None, f"Stage exceeded {timeout:g} seconds.",
                       "Inspect slow tests/checks; increase --timeout only when justified.")
    try:
        result = json.loads(proc.stdout)
        if result["name"] == name and "status" in result:
            return result
    except (ValueError, KeyError, TypeError):
        pass
    return _result(name, "unavailable" if proc.returncode else "failed", proc.returncode,
                   (proc.stdout + proc.stderr).strip(),
                   "Install core[authoring] and kit .[dev] in the selected Python environment.")


def execute(args: argparse.Namespace) -> int:
    kit = Path(args.kit).resolve()
    stages = []
    if not kit.is_dir() or not math.isfinite(args.timeout) or args.timeout <= 0:
        stages = [_result(name, "unavailable", None, "Kit directory or timeout is invalid.",
                          "Select an existing kit directory and a positive --timeout.")
                  for name in _ACTIONS]
    else:
        # Pass only process necessities, never inherited provider/depot credentials.
        env = {k: v for k, v in os.environ.items() if k in {
            "PATH", "HOME", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "LANG", "LC_ALL",
        }}
        core_src = Path(__file__).resolve().parents[2]
        env["PYTHONPATH"] = os.pathsep.join(map(str, (core_src, kit / "src", kit / "tests")))
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        with tempfile.TemporaryDirectory(prefix="synth-check-") as tmp:
            env["SYNTH_STATE_DIR"] = tmp
            env["SYNTH_OUT_DIR"] = str(Path(tmp) / "out")
            for name in _ACTIONS:
                if name == "conformance" and stages[0]["status"] != "passed":
                    stages.append(_result(
                        name, "skipped", None, "Manifest validation did not pass.",
                        "Fix validation first, then rerun check.",
                    ))
                else:
                    stages.append(_run_stage(name, kit, args.python, args.timeout, env))
    ready = all(stage["status"] == "passed" for stage in stages)
    report = {"schema_version": 1, "kit": str(kit), "local_ready": ready,
              "live_verification": "not_run", "admission": "not_run", "stages": stages}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for stage in stages:
            print(f"{stage['name']}: {stage['status']}")
            if stage["output"]:
                print(stage["output"])
            if stage["next_action"]:
                print(f"Next: {stage['next_action']}")
        print(f"Local readiness: {'passed' if ready else 'not ready'}. "
              "Live verification and admission: not run.")
    return 0 if ready else 1


def _worker(name: str, kit: Path) -> dict:
    import socket

    from langfuse_synth_core.authoring.egress import egress_block_env, install_guard

    os.environ.update(egress_block_env(os.environ))
    install_guard()

    def deny(*args, **kwargs):
        raise RuntimeError("offline authoring check: network connections are disabled")

    # Local Langfuse endpoints must be just as unreachable as cloud endpoints.
    socket.create_connection = deny
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    output = io.StringIO()
    status = "passed"
    code = 0
    try:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            if name == "validate":
                from langfuse_synth_core.authoring.validate import run
                code = run([str(kit / "usecase.yaml")])
            elif name == "conformance":
                from langfuse_synth_core.authoring.conformance import run_conformance
                report = run_conformance(kit)
                print("\n".join(report.passed + report.all_findings + report.notes))
                code = 0 if report.ok else 1
                if report.ok and any("not installed" in note for note in report.notes):
                    status = "unavailable"
            else:
                import pytest
                class Outcomes:
                    skipped = 0

                    def pytest_runtest_logreport(self, report):
                        if report.skipped:
                            self.skipped += 1

                outcomes = Outcomes()
                code = int(pytest.main([str(kit / "tests"), "-q"], plugins=[outcomes]))
                if not code and outcomes.skipped:
                    status = "skipped"
                    print(f"{outcomes.skipped} test(s) skipped; local checks are incomplete.")
    except ImportError as exc:
        status, code = "unavailable", None
        output.write(f"Missing dependency: {exc}\n")
    except Exception as exc:
        status, code = "failed", 1
        output.write(f"{type(exc).__name__}: {exc}\n")
    if code and status == "passed":
        status = "failed"
    return _result(name, status, code, output.getvalue().strip(),
                   "" if status == "passed" else (
                       "Resolve skipped checks, then rerun; live gates remain separate."
                       if status == "skipped" else _ACTIONS[name]))


if __name__ == "__main__":
    # Internal worker, invoked only by execute with a resolved kit path.
    print(json.dumps(_worker(sys.argv[1], Path(sys.argv[2]))))
