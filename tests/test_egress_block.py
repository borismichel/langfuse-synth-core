"""The deny-LLM egress block is a REAL runtime block, not a static scan (#28).

These prove the socket guard actually denies non-loopback egress at runtime while
leaving loopback IO intact, and that the env posture steers LLM levers at an unroutable
sink. The end-to-end proof that the block guards kit-owned generation code lives in
``test_golden_gate.py`` (the planted-LLM negative test); here we pin the mechanism.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="egress block ships in the [authoring] extra; not installed on a runtime-only job",
)


def _run_guarded(body: str) -> subprocess.CompletedProcess:
    """Run a snippet in a fresh interpreter with the egress guard installed first."""
    script = (
        "from langfuse_synth_core.authoring.egress import install_guard, EgressBlockedError\n"
        "install_guard()\n" + body
    )
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )


def test_guard_blocks_a_non_loopback_connection():
    """A direct connection to a public host is denied before it is made."""
    result = _run_guarded(
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('api.anthropic.com', 443), timeout=2)\n"
        "except EgressBlockedError:\n"
        "    print('BLOCKED')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "BLOCKED" in result.stdout


def test_guard_blocks_dns_resolution_of_a_public_host():
    """DNS is denied up front, so a planted call fails fast and deterministically."""
    result = _run_guarded(
        "import socket\n"
        "try:\n"
        "    socket.getaddrinfo('api.openai.com', 443)\n"
        "except EgressBlockedError:\n"
        "    print('BLOCKED')\n"
    )
    assert result.returncode == 0, result.stderr
    assert "BLOCKED" in result.stdout


def test_guard_allows_loopback():
    """Legitimate local IO is not collateral: a loopback listener/connect still works."""
    result = _run_guarded(
        "import socket\n"
        "srv = socket.socket()\n"
        "srv.bind(('127.0.0.1', 0))\n"
        "srv.listen(1)\n"
        "port = srv.getsockname()[1]\n"
        "c = socket.create_connection(('127.0.0.1', port), timeout=2)\n"
        "print('LOOPBACK_OK')\n"
        "c.close(); srv.close()\n"
    )
    assert result.returncode == 0, result.stderr
    assert "LOOPBACK_OK" in result.stdout


def test_env_posture_points_llm_levers_at_an_unroutable_sink():
    """The proxy/base-url env mirrors production egress, steered at an unroutable sink."""
    from langfuse_synth_core.authoring.egress import egress_block_env

    env = egress_block_env({"PATH": "/usr/bin"})
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
        assert env[var] == "http://192.0.2.1:9"  # RFC 5737 TEST-NET-1, non-routable
    assert env["NO_PROXY"] == "" and env["no_proxy"] == ""
    assert env["PATH"] == "/usr/bin"  # base env is preserved
