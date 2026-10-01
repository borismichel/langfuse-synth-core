"""The emitted development preview through its HTTP and process boundaries."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from langfuse_synth_core.authoring.scaffold import scaffold_kit


def _run_preview(kit, script):
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join([str(kit.dest / "src"),
                                         str(Path(__file__).resolve().parents[1] / "src")]),
           "LANGFUSE_SECRET_KEY": "inherited-secret",
           "OPENAI_API_KEY": "inherited-secret",
           "LANGFUSE_BASE_URL": "https://example.com",
           "LIVE_BASE_PATH": "/live/unrelated"}
    return subprocess.run([sys.executable, "-c", script], cwd=kit.dest, env=env,
                          capture_output=True, text=True)


def test_preview_renders_fixture_interaction_without_live_readiness(tmp_path):
    kit = scaffold_kit("preview-demo", tmp_path / "kit", with_companion=True)
    run = _run_preview(kit, '''
from starlette.testclient import TestClient
from synth.companion.preview import create_preview_app
with TestClient(create_preview_app()) as client:
    page = client.get("/")
    assert page.status_code == 200
    assert "Development preview" in page.text
    assert "View sample trace" in page.text
    result = client.get("/sample")
    assert result.status_code == 200
    assert "Fixture support request" in result.text
    assert "Your sample request has been resolved." in result.text
    assert result.text == client.get("/sample").text
    assert "Development preview" in result.text
    health = client.get("/healthz")
    assert health.status_code == 503
    assert health.json()["ready"] is False
    assert health.json()["langfuse_write_ok"] is False
    assert health.json()["llm_bound"] is False
    assert health.json()["detail"]["mode"] == "preview"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert "inherited-secret" not in page.text + result.text + health.text
''')
    assert run.returncode == 0, run.stdout + run.stderr


def test_preview_drops_inherited_credentials_and_blocks_outgoing_connections(tmp_path):
    kit = scaffold_kit("preview-demo", tmp_path / "kit", with_companion=True, with_anchors=True)
    run = _run_preview(kit, '''
import os, socket
from starlette.testclient import TestClient
from synth.companion.preview import create_preview_app, FixtureAdapter
from langfuse_synth_core.authoring.egress import EgressBlockedError
app = create_preview_app()
assert "LANGFUSE_SECRET_KEY" not in os.environ
assert "OPENAI_API_KEY" not in os.environ
assert "LANGFUSE_BASE_URL" not in os.environ
assert "LIVE_BASE_PATH" not in os.environ
for host in ("example.com", "127.0.0.1"):
    try:
        socket.create_connection((host, 443))
    except EgressBlockedError:
        pass
    else:
        raise AssertionError("preview allowed an outgoing connection")
adapter = FixtureAdapter()
from langfuse_synth_core.companion import CompanionAdapterContract
assert isinstance(adapter, CompanionAdapterContract)
for call in (adapter.langfuse, adapter.emitter, adapter.llm,
             lambda: adapter.read_json("/api/public/projects")):
    try:
        call()
    except NotImplementedError:
        pass
    else:
        raise AssertionError("unsupported client was allowed")
with TestClient(app) as client:
    page = client.get("/")
    assert "Anchored to the seeded run" not in page.text
    assert "No run anchors yet" not in page.text
    assert "href='/sample'" in page.text
    assert client.get("/sample").status_code == 200
''')
    assert run.returncode == 0, run.stdout + run.stderr


def test_preview_command_serves_only_local_fixture_app(tmp_path):
    import socket
    import time
    import urllib.error
    import urllib.request

    kit = scaffold_kit("preview-demo", tmp_path / "kit", with_companion=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([
        str(kit.dest / "src"), str(Path(__file__).resolve().parents[1] / "src")]),
        "LANGFUSE_SECRET_KEY": "inherited-secret"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "synth.companion.preview", "--port", str(port)],
        cwd=kit.dest, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(50):
            if proc.poll() is not None:
                raise AssertionError(proc.communicate())
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as page:
                    assert b"Development preview" in page.read()
                break
            except urllib.error.URLError:
                time.sleep(0.1)
        else:
            raise AssertionError("preview did not start")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/sample", timeout=1) as page:
            assert b"Your sample request has been resolved." in page.read()
    finally:
        proc.terminate()
        proc.communicate(timeout=10)
