"""Deny-LLM egress block for the determinism golden gate (#28).

The determinism law is ``seed + target_traces + declared params -> byte-identical
Spool``, with **no LLM emitting observations at seed runtime**. A static provider-scan
is theatre — a dynamic import evades it. The binding enforcement is a REAL runtime
egress block: ``seed`` runs in a subprocess whose outbound network is denied, mirroring
the production egress posture (the portal's default-deny egress-proxy, tightened here to
deny *all* non-loopback egress because the offline pre-ingestion seed talks to nothing).

Two layers, both real:

1. **Socket-level guard** (binding). ``install_guard`` monkeypatches the ``socket``
   entry points so any attempt to reach a non-loopback host raises
   :class:`EgressBlockedError` *before* DNS or a connection is made. This guards the
   agent's kit-owned generation code, not just the library's model-free-by-construction
   write machinery: a planted LLM call anywhere under ``seed`` trips it.
2. **Proxy/base-url env pointed at an unroutable sink** (belt-and-suspenders, and the
   faithful mirror of production, where containers egress through ``EGRESS_PROXY_URL``).
   An LLM SDK that honours ``HTTPS_PROXY`` / ``ANTHROPIC_BASE_URL`` is steered at an
   unroutable address, which the socket guard then denies.

Trust boundary (accepted by design): the guard covers the TCP/DNS entry points every
real LLM SDK uses — ``getaddrinfo`` / ``create_connection`` / ``socket.connect[_ex]``. It
does not chase UDP datagrams, a subprocess that shells out (e.g. ``curl``), or code that
reaches the raw C ``_socket`` object directly. Those still FAIL the gate — the
proxy/base-url env points every known lever at an unroutable sink, so such a call errors
— but as a generic failure rather than a typed :class:`EgressBlockedError`. The goal is a
binding, deterministic block on the model-call path, not a sandbox; defeating it takes
deliberate effort that a kit's generation code has no reason to make.

This module holds the guard and the env posture; :mod:`langfuse_synth_core.authoring.golden`
drives the subprocess and compares the materialized Spool against the blessed golden.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping

# RFC 5737 TEST-NET-1 — guaranteed non-routable on the public internet. Used as the
# egress sink the proxy/base-url env points at, so an SDK that honours those vars is
# steered somewhere the socket guard denies (rather than at a real endpoint).
_SINK_HOST = "192.0.2.1"
_SINK_PORT = 9
_SINK_URL = f"http://{_SINK_HOST}:{_SINK_PORT}"

# Loopback is permitted: the offline seed makes no outbound calls at all, but legitimate
# in-process/local IPC (a spawned helper, a local temp server) must not be collateral.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"})


class EgressBlockedError(RuntimeError):
    """Raised when code under the seed egress block attempts a non-loopback connection.

    A clean, model-free seed never triggers this. A planted LLM call (or any other
    outbound network access) at seed runtime does — which is exactly what the golden
    gate must fail on.
    """


def egress_block_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment dict with LLM/proxy egress steered at an unroutable sink.

    Mirrors the production posture of injecting a proxy URL into the container env, but
    points every known LLM egress lever at :data:`_SINK_URL` so an SDK honouring them
    cannot reach a live provider. This is the belt to the socket guard's suspenders.
    """
    env = dict(base) if base is not None else {}
    for var in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        # Common LLM SDK base-url overrides — a call that honours these is steered
        # at the sink and then denied by the guard.
        "ANTHROPIC_BASE_URL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    ):
        env[var] = _SINK_URL
    # Ensure nothing on the host slips a bypass through NO_PROXY.
    env["NO_PROXY"] = ""
    env["no_proxy"] = ""
    return env


def _is_loopback(host: object) -> bool:
    if not isinstance(host, str):
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _addr_host(address: object) -> object:
    """The host portion of a socket address (a ``(host, port, ...)`` tuple or bare host)."""
    if isinstance(address, tuple) and address:
        return address[0]
    return address


def _deny_non_loopback(host: object, action: str) -> None:
    """Raise :class:`EgressBlockedError` unless ``host`` is loopback.

    The single deny path shared by every socket guard, so the loopback rule and the error
    shape live in one place instead of being repeated per entry point.
    """
    if not _is_loopback(host):
        raise EgressBlockedError(
            f"seed egress blocked: {action} for {host!r} denied under the deny-LLM "
            "egress block (seed runtime must be model-free and offline)"
        )


def install_guard() -> None:
    """Install the socket-level egress guard in the current process (idempotent).

    Patches ``socket.getaddrinfo``, ``socket.create_connection`` and
    ``socket.socket.connect``/``connect_ex`` so any non-loopback target raises
    :class:`EgressBlockedError` before a real connection (or even DNS) happens. Call
    this at the very top of the seed subprocess, before importing kit code.
    """
    if getattr(socket, "_synth_egress_guard_installed", False):
        return

    _real_getaddrinfo = socket.getaddrinfo
    _real_create_connection = socket.create_connection
    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex

    def _guarded_getaddrinfo(host, *args, **kwargs):
        # Block name resolution up front, so a planted call fails fast and
        # deterministically rather than hanging on DNS or depending on connectivity.
        _deny_non_loopback(host, "DNS lookup")
        return _real_getaddrinfo(host, *args, **kwargs)

    def _guarded_create_connection(address, *args, **kwargs):
        _deny_non_loopback(_addr_host(address), "connection")
        return _real_create_connection(address, *args, **kwargs)

    def _guarded_connect(self, address):
        _deny_non_loopback(_addr_host(address), "socket connect")
        return _real_connect(self, address)

    def _guarded_connect_ex(self, address):
        _deny_non_loopback(_addr_host(address), "socket connect_ex")
        return _real_connect_ex(self, address)

    socket.getaddrinfo = _guarded_getaddrinfo
    socket.create_connection = _guarded_create_connection
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    socket._synth_egress_guard_installed = True
