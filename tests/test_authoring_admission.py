"""Released-candidate handoff through a controlled portal HTTP interface."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None, reason="requires authoring extra"
)

TOKEN = "test-admin-secret-sentinel"
RUN_ID = "12345678-1234-5678-1234-567812345678"
SHA = "a" * 40


def response(passed=False, failed=False):
    return {
        "id": RUN_ID,
        "candidate_slug": "demo",
        "git_sha": SHA,
        "image_ref": "ghcr.io/example/demo@sha256:" + "b" * 64,
        "verdict": {
            "passed": passed,
            "eligible_to_pin": passed,
            "failed_step": "verify" if failed else None,
            "reason": f"verify failed: {TOKEN}" if failed else None,
            "steps": [
                {
                    "step": step,
                    "status": (
                        "failed"
                        if failed and step == "verify"
                        else "passed"
                        if passed or failed or step in {"register", "build", "spawn"}
                        else "pending"
                    ),
                }
                for step in ("register", "build", "spawn", "seed", "verify")
            ],
        },
    }


@pytest.fixture
def portal(monkeypatch):
    state = {"calls": [], "response": response(), "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            state["calls"].append(
                (
                    "POST",
                    self.path,
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            self.reply()

        def do_GET(self):
            state["calls"].append(("GET", self.path))
            self.reply()

        def reply(self):
            assert self.headers["Authorization"] == f"Bearer {TOKEN}"
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = state["responses"].pop(0) if state.get("responses") else state["response"]
            self.wfile.write(state.get("raw_body", json.dumps(payload).encode()))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DEMO_DEPOT_TOKEN", TOKEN)
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def args(portal, tmp_path):
    return [
        "admit",
        "--repo",
        "https://github.com/example/demo",
        "--slug",
        "demo",
        "--ref",
        "v1.2.3",
        "--portal",
        portal[0],
        "--state",
        str(tmp_path / "run.json"),
        "--timeout",
        "0",
    ]


def test_pending_restart_reuses_run_and_only_pass_produces_snippet(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    command = args(portal, tmp_path)
    assert main(command) == 2
    assert "use_cases:" not in capsys.readouterr().out
    assert len(portal[1]["calls"]) == 1
    portal[1]["response"] = response(passed=True)
    assert main(command) == 0
    out = capsys.readouterr().out
    assert "repo_url: https://github.com/example/demo" in out
    assert "ref: v1.2.3" in out and "staging" in out
    assert [c[0] for c in portal[1]["calls"]] == ["POST", "GET"]
    assert TOKEN not in (tmp_path / "run.json").read_text()
    assert main(command) == 0
    assert portal[1]["calls"][-1][0] == "GET"


def test_failed_run_has_actionable_reason_without_token(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    portal[1]["response"] = response(failed=True)
    assert main(args(portal, tmp_path)) == 1
    out = capsys.readouterr().out
    assert "verify" in out and "use_cases:" not in out and TOKEN not in out
    assert TOKEN not in (tmp_path / "run.json").read_text()
    assert main(args(portal, tmp_path)) == 1
    assert [c[0] for c in portal[1]["calls"]] == ["POST", "GET"]


def test_release_failure_and_credentials_are_not_saved_or_echoed(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    portal[1].update(status=502, response={"detail": TOKEN})
    assert main(args(portal, tmp_path)) == 1
    out = capsys.readouterr().out
    assert "release" in out.lower() and TOKEN not in out
    assert "run_id" not in json.loads((tmp_path / "run.json").read_text())


@pytest.mark.parametrize("ref", ["main", "latest", "refs/heads/main", "v1.2"])
def test_requires_released_version_ref(portal, tmp_path, capsys, ref):
    from langfuse_synth_core.authoring.cli import main

    command = args(portal, tmp_path)
    command[command.index("--ref") + 1] = ref
    assert main(command) == 1
    assert not portal[1]["calls"]


def test_candidate_mismatch_preserves_existing_progress(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    command = args(portal, tmp_path)
    assert main(command) == 2
    before = (tmp_path / "run.json").read_bytes()
    command[command.index("--ref") + 1] = "v2.0.0"
    assert main(command) == 1
    assert (tmp_path / "run.json").read_bytes() == before
    assert len(portal[1]["calls"]) == 1


def test_inconsistent_success_cannot_produce_handoff(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    portal[1]["response"] = response(passed=True)
    portal[1]["response"]["verdict"]["steps"][-1]["status"] = "pending"
    assert main(args(portal, tmp_path)) == 1
    assert "use_cases:" not in capsys.readouterr().out


def test_explicit_failed_retry_accepts_new_run_without_changing_release(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    command = args(portal, tmp_path)
    portal[1]["response"] = response(failed=True)
    assert main(command) == 1
    retried = response(passed=True)
    retried["id"] = "99999999-1234-5678-1234-567812345678"
    portal[1]["response"] = retried
    assert main([*command, "--retry-failed"]) == 0
    assert [call[0] for call in portal[1]["calls"]] == ["POST", "POST"]
    saved = json.loads((tmp_path / "run.json").read_text())
    assert saved["run_id"] == retried["id"] and saved["git_sha"] == SHA


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown-rung",
        "duplicate-rung",
        "missing-rung",
        "failed-without-rung",
        "failed-rung-still-pending",
        "out-of-order",
        "success-flags-disagree",
    ],
)
def test_malformed_ladder_never_updates_progress_or_produces_handoff(
    portal,
    tmp_path,
    capsys,
    mutation,
):
    from langfuse_synth_core.authoring.cli import main

    run = response(passed=True)
    verdict = run["verdict"]
    if mutation == "unknown-rung":
        verdict["steps"].append({"step": "not-a-rung", "status": "passed"})
    elif mutation == "duplicate-rung":
        verdict["steps"].append(dict(verdict["steps"][-1]))
    elif mutation == "missing-rung":
        verdict["steps"].pop()
    elif mutation == "failed-without-rung":
        verdict.update(passed=False, eligible_to_pin=False)
        verdict["steps"][-1]["status"] = "failed"
    elif mutation == "failed-rung-still-pending":
        run = response()
        run["verdict"]["failed_step"] = "verify"
    elif mutation == "out-of-order":
        verdict["steps"].reverse()
    else:
        verdict["eligible_to_pin"] = False
    portal[1]["response"] = run
    assert main(args(portal, tmp_path)) == 1
    assert "inconsistent" in capsys.readouterr().out
    assert "run_id" not in json.loads((tmp_path / "run.json").read_text())


def test_arbitrary_portal_error_text_is_neither_echoed_nor_saved(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    portal[1]["response"] = response(failed=True)
    reason = "provider password=unrecognised-private-value authorization=Bearer other-session"
    portal[1]["response"]["verdict"]["reason"] = reason
    portal[1]["response"]["verdict"]["steps"][-1].update(reason=reason, exit_code=3)
    assert main(args(portal, tmp_path)) == 1
    output = capsys.readouterr().out
    assert "unrecognised-private-value" not in output
    assert "other-session" not in output
    assert "verify" in output and "exit 3" in output
    assert reason not in (tmp_path / "run.json").read_text()


def test_saved_state_with_unknown_fields_is_rejected_before_any_request(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    command = args(portal, tmp_path)
    assert main(command) == 2
    path = tmp_path / "run.json"
    receipt = json.loads(path.read_text())
    receipt["credentials"] = TOKEN
    path.write_text(json.dumps(receipt))
    before = path.read_bytes()
    assert main(command) == 1
    assert len(portal[1]["calls"]) == 1
    assert path.read_bytes() == before
    assert TOKEN not in capsys.readouterr().out


def test_polls_a_pending_run_to_completion_within_one_invocation(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    command = args(portal, tmp_path)
    command[-1] = "1"
    portal[1]["responses"] = [response(), response(), response(passed=True)]
    assert main([*command, "--poll-interval", "0.01"]) == 0
    assert [call[0] for call in portal[1]["calls"]] == ["POST", "GET", "GET"]
    assert "Registry handoff" in capsys.readouterr().out


def test_pending_timeout_is_bounded_and_leaves_resumable_progress(portal, tmp_path, capsys):
    import time
    from langfuse_synth_core.authoring.cli import main

    command = args(portal, tmp_path)
    command[-1] = "0.06"
    start = time.monotonic()
    assert main([*command, "--poll-interval", "0.02"]) == 2
    assert time.monotonic() - start < 1
    assert len(portal[1]["calls"]) >= 2
    assert all(call[0] == "GET" for call in portal[1]["calls"][1:])
    assert json.loads((tmp_path / "run.json").read_text())["status"] == "pending"
    assert "Registry handoff" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "field,changed",
    [
        ("git_sha", "c" * 40),
        ("image_ref", "ghcr.io/example/demo@sha256:" + "c" * 64),
        ("id", "99999999-1234-5678-1234-567812345678"),
    ],
)
def test_resume_rejects_changed_run_evidence_without_overwriting_receipt(
    portal,
    tmp_path,
    capsys,
    field,
    changed,
):
    from langfuse_synth_core.authoring.cli import main

    command = args(portal, tmp_path)
    assert main(command) == 2
    before = (tmp_path / "run.json").read_bytes()
    portal[1]["response"] = response(passed=True)
    portal[1]["response"][field] = changed
    assert main(command) == 1
    assert "Registry handoff" not in capsys.readouterr().out
    assert (tmp_path / "run.json").read_bytes() == before


def test_retry_cannot_change_the_release_digest(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    command = args(portal, tmp_path)
    portal[1]["response"] = response(failed=True)
    assert main(command) == 1
    before = (tmp_path / "run.json").read_bytes()
    portal[1]["response"] = response(passed=True)
    portal[1]["response"]["id"] = "99999999-1234-5678-1234-567812345678"
    portal[1]["response"]["image_ref"] = "ghcr.io/example/demo@sha256:" + "c" * 64
    assert main([*command, "--retry-failed"]) == 1
    assert (tmp_path / "run.json").read_bytes() == before
    assert "Registry handoff" not in capsys.readouterr().out


def test_missing_token_does_not_start_or_save_an_admission(portal, tmp_path, monkeypatch, capsys):
    from langfuse_synth_core.authoring.cli import main

    monkeypatch.delenv("DEMO_DEPOT_TOKEN")
    assert main(args(portal, tmp_path)) == 1
    assert not portal[1]["calls"] and not (tmp_path / "run.json").exists()
    assert "environment variable" in capsys.readouterr().out


@pytest.mark.parametrize("payload", [None, [], "not an object", {"id": RUN_ID}])
def test_malformed_portal_object_is_an_actionable_error(portal, tmp_path, capsys, payload):
    from langfuse_synth_core.authoring.cli import main

    portal[1]["response"] = payload
    assert main(args(portal, tmp_path)) == 1
    assert "inconsistent" in capsys.readouterr().out


def test_companion_smoke_must_pass_before_handoff(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    run = response(passed=True)
    run["verdict"]["steps"].append({"step": "companion_smoke", "status": "pending"})
    run["verdict"].update(passed=False, eligible_to_pin=False)
    portal[1]["response"] = run
    assert main(args(portal, tmp_path)) == 2
    assert "Registry handoff" not in capsys.readouterr().out
    run["verdict"]["steps"][-1]["status"] = "passed"
    run["verdict"].update(passed=True, eligible_to_pin=True)
    assert main(args(portal, tmp_path)) == 0


def test_token_hidden_in_otherwise_valid_image_evidence_is_rejected(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    portal[1]["response"] = response(passed=True)
    portal[1]["response"]["image_ref"] = f"ghcr.io/example/{TOKEN}@sha256:" + "b" * 64
    assert main(args(portal, tmp_path)) == 1
    assert TOKEN not in capsys.readouterr().out
    assert TOKEN not in (tmp_path / "run.json").read_text()


def test_invalid_json_response_never_produces_handoff(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    portal[1]["raw_body"] = ("invalid json " + TOKEN).encode()
    assert main(args(portal, tmp_path)) == 1
    output = capsys.readouterr().out
    assert "invalid JSON" in output and TOKEN not in output
    assert "run_id" not in json.loads((tmp_path / "run.json").read_text())


def test_uncertain_post_saves_only_intent_and_can_be_repeated(
    portal,
    tmp_path,
    monkeypatch,
    capsys,
):
    import requests
    from langfuse_synth_core.authoring.cli import main

    with monkeypatch.context() as patch:

        def interrupted(self, method, url, **kwargs):
            assert kwargs["timeout"] == 10 and not kwargs["allow_redirects"]
            raise requests.Timeout("request exposed " + TOKEN)

        patch.setattr(requests.Session, "request", interrupted)
        assert main(args(portal, tmp_path)) == 1
    assert TOKEN not in capsys.readouterr().out
    receipt = json.loads((tmp_path / "run.json").read_text())
    assert set(receipt) == {"schema_version", "candidate"}
    assert main(args(portal, tmp_path)) == 2
    assert portal[1]["calls"][0][0] == "POST"


def test_manifest_slug_rejection_never_produces_registry_handoff(portal, tmp_path, capsys):
    from langfuse_synth_core.authoring.cli import main

    portal[1].update(status=422, response={"detail": "candidate slug does not match usecase.yaml"})
    assert main(args(portal, tmp_path)) == 1
    output = capsys.readouterr().out
    assert "Manifest" in output
    assert "use_cases:" not in output
    assert "run_id" not in json.loads((tmp_path / "run.json").read_text())
