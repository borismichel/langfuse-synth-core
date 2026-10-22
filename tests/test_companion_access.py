"""Generic protected-route contract at the delivered ASGI boundary."""

import asyncio

import httpx
from fastapi import FastAPI, Request

from langfuse_synth_core.companion.access import LiveAccessMiddleware, instance_proof


def test_private_route_requires_depot_proof_and_never_delivers_it_to_application():
    app = FastAPI()

    @app.get("/{path:path}")
    def echo(request: Request, path: str):
        return dict(request.headers)

    proof = instance_proof("test-signing-secret-long-enough-for-hmac", "instance-a")
    guarded = LiveAccessMiddleware(app, proof=proof, health_path="/healthz")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=guarded), base_url="http://kit"
        ) as client:
            assert (await client.get("/apm")).status_code == 404
            assert (
                await client.get("/apm", headers={"x-depot-live-proof": "spoof"})
            ).status_code == 404
            assert (
                await client.get(
                    "/apm",
                    headers={
                        "x-depot-live-proof": instance_proof(
                            "test-signing-secret-long-enough-for-hmac", "instance-b"
                        )
                    },
                )
            ).status_code == 404
            response = await client.get("/apm", headers={"x-depot-live-proof": proof})
            assert response.status_code == 200
            assert "x-depot-live-proof" not in response.json()
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.post("/healthz")).status_code == 404

    asyncio.run(exercise())


def test_route_policy_uses_segment_boundaries_and_rejects_ambiguous_encodings():
    import pytest
    from langfuse_synth_core.companion.access import route_access, validate_protected_paths

    policy = validate_protected_paths(
        [{"path": "/apm", "access": "presenter"}, {"path": "/apm/admin", "access": "manage"}]
    )
    assert route_access("/apm", policy) == "presenter"
    assert route_access("/apm/api/traces", policy) == "presenter"
    assert route_access("/apm/admin/settings", policy) == "manage"
    assert route_access("/apm-lookalike", policy) is None
    assert route_access("/", policy) is None
    for path in [
        "/apm/../public",
        "/%61pm",
        "/%2561pm",
        "//apm",
        "/./apm",
        "/apm\\other",
        "/apm;extra",
        "/apm?x=1",
        "/apm#x",
        "/apm\x00",
    ]:
        with pytest.raises(ValueError):
            route_access(path, policy)
    for entry in [
        {"path": "/a/../b", "access": "presenter"},
        {"path": "/apm/", "access": "presenter"},
        {"path": "/apm", "access": "public"},
        {"path": "/", "access": "presenter"},
    ]:
        with pytest.raises(ValueError):
            validate_protected_paths([entry])


def test_adapter_installs_fail_closed_gate_for_manifest_protected_components(monkeypatch):
    import json
    import pytest
    from types import SimpleNamespace
    from langfuse_synth_core.companion import CompanionAdapter

    app = FastAPI()

    @app.get("/apm")
    def apm():
        return {"ok": True}

    adapter = CompanionAdapter(SimpleNamespace(), health_path="/healthz")
    monkeypatch.setenv(
        "PORTAL_LIVE_PROTECTED_PATHS", json.dumps([{"path": "/apm", "access": "presenter"}])
    )
    monkeypatch.delenv("PORTAL_LIVE_PROOF", raising=False)
    with pytest.raises(ValueError):
        adapter.make_server(app, port=8080)
    monkeypatch.setenv("PORTAL_LIVE_PROOF", "a" * 64)
    guarded = adapter.make_server(app, port=8080).config.app

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=guarded), base_url="http://kit"
        ) as client:
            assert (await client.get("/apm")).status_code == 404
            assert (
                await client.get("/apm", headers={"x-depot-live-proof": "a" * 64})
            ).status_code == 200
            assert (
                await client.get(
                    "/apm/%252e%252e/private", headers={"x-depot-live-proof": "a" * 64}
                )
            ).status_code == 400

    asyncio.run(exercise())


def test_manifest_accepts_protected_route_scopes_and_rejects_unbounded_paths():
    import json
    from pathlib import Path
    import jsonschema
    import pytest

    schema = json.loads(
        (
            Path(__file__).parents[1] / "src/langfuse_synth_core/authoring/usecase.schema.json"
        ).read_text()
    )
    component_schema = schema["properties"]["live_components"]["items"]
    component = {
        "name": "app",
        "command": "synth companion",
        "port": 8080,
        "requires_secrets": [],
        "protected_paths": [{"path": "/apm", "access": "presenter"}],
    }
    jsonschema.validate(component, component_schema)
    for path in ["/", "/apm/", "/%61pm", "/a/../b", "/apm?query", "/apm\\x"]:
        component["protected_paths"][0]["path"] = path
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(component, component_schema)
