"""Development-only fixture preview; run in its own process, never a deployment.

Uses the installed core's existing authoring guard, so the default released core pin
works. Importing/building this preview requires the kit's [dev] dependencies.
"""
from __future__ import annotations

import argparse
import os
import socket
from typing import Any

from langfuse_synth_core.authoring.egress import EgressBlockedError, install_guard
from langfuse_synth_core.companion import ReadinessReport
from langfuse_synth_core.read import Trace


class FixtureReader:
    """The starter's trace-read interface backed solely by explicit sample data."""

    def traces(self, *, limit: int = 100, **filters: Any) -> list[Trace]:
        if filters:
            raise NotImplementedError("Add an explicit preview fixture for these filters")
        return [Trace(
            id="preview-trace-001", name="Fixture support request",
            input="Can you help with my sample request?",
            output="Your sample request has been resolved.",
        )][:max(0, limit)]


class FixtureAdapter:
    """Ready fixture clients only; unsupported surfaces fail without binding real clients."""

    # Structurally implements the Adapter seam without inheriting any live client builders.
    health_path = "/healthz"

    def reader(self, **kw: Any) -> FixtureReader:
        if kw:
            raise NotImplementedError("Add explicit preview reader options")
        return FixtureReader()

    def langfuse(self) -> Any:
        raise NotImplementedError("Langfuse SDK access has no preview fixture")

    def emitter(self, **kw: Any) -> Any:
        raise NotImplementedError("Live writes are unavailable in preview")

    def read_json(self, path: str, params: dict | None = None, *, throttle: float = 0) -> dict:
        raise NotImplementedError("Raw API reads have no preview fixture")

    def llm(self, model: str | None = None) -> Any:
        raise NotImplementedError("LLM calls have no preview fixture")

    def readiness(self) -> ReadinessReport:
        return ReadinessReport(False, False, {"mode": "preview", "admission": False})

    def mount_health(self, app: Any) -> None:
        from starlette.responses import JSONResponse

        async def health(_request: Any) -> Any:
            return JSONResponse(self.readiness().as_dict(), status_code=503)

        app.add_route(self.health_path, health, methods=["GET"])

    def serve(self, app: Any, *, host: str, port: int) -> None:
        if host != "127.0.0.1":
            raise ValueError("Development preview binds only to 127.0.0.1")
        import uvicorn

        uvicorn.run(app, host=host, port=port, log_level="warning")


def _deny_connection(*args: Any, **kwargs: Any) -> Any:
    raise EgressBlockedError("Preview permits no outgoing connections, including loopback")


def create_preview_app() -> Any:
    """Build the Surface with fixture clients after isolating this development process.

    Clears inherited credentials/configuration and permanently blocks outgoing TCP in
    this process. Use a fresh process for live checks or other tools afterward.
    """
    keep = {key: value for key, value in os.environ.items()
            if key in ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL")}
    os.environ.clear()
    os.environ.update(keep)
    install_guard()
    # The common guard permits loopback IPC. Preview also denies local service writes;
    # accepting inbound browser connections still works, without connect()/connect_ex().
    socket.create_connection = _deny_connection
    socket.socket.connect = _deny_connection
    socket.socket.connect_ex = _deny_connection

    from .app import create_app

    adapter = FixtureAdapter()
    app = create_app(adapter, preview=True)
    adapter.mount_health(app)

    @app.middleware("http")
    async def offline_browser(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'"
        )
        response.headers["X-Demo-Mode"] = "preview"
        return response

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline development preview; not live readiness")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    app = create_preview_app()
    print(f"Development preview: http://127.0.0.1:{args.port} — fixture data, not admission")
    FixtureAdapter().serve(app, host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
