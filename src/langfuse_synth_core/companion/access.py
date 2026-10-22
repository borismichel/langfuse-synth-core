"""Portal-only transport for components with protected routes.

The portal authorizes each route before forwarding. A deployment-specific proof
closes both wildcard-proxy and container-network bypasses. It is consumed before
kit code, never a browser credential or an upstream-service credential.
"""

from __future__ import annotations

import hashlib
import hmac

PROOF_HEADER = "x-depot-live-proof"


def instance_proof(signing_secret: str, instance_id: str) -> str:
    if len(signing_secret) < 32:
        raise ValueError("LIVE_PROXY_SIGNING_SECRET must contain at least 32 characters")
    return hmac.new(
        signing_secret.encode(), ("depot-live-v1:" + instance_id).encode(), hashlib.sha256
    ).hexdigest()


class LiveAccessMiddleware:
    """Require the portal transport proof; allow only the exact GET health route."""

    def __init__(self, app, *, proof: str, health_path: str):
        if len(proof) != 64 or any(c not in "0123456789abcdef" for c in proof):
            raise ValueError("Invalid live transport proof")
        self.app, self.proof, self.health_path = app, proof, health_path

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        try:
            if b"%" in scope.get("raw_path", b"").split(b"?")[0]:
                raise ValueError("Encoded live path")
            validate_request_path(scope["path"])
        except ValueError:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send({"type": "http.response.start", "status": 400, "headers": []})
                await send({"type": "http.response.body", "body": b"Invalid path"})
            return
        headers = scope.get("headers", [])
        proofs = [v for k, v in headers if k.lower() == PROOF_HEADER.encode()]
        permitted = len(proofs) == 1 and hmac.compare_digest(proofs[0], self.proof.encode())
        health = (
            scope["type"] == "http"
            and scope.get("method") in {"GET", "HEAD"}
            and scope["path"] == self.health_path
        )
        if not (permitted or health):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send({"type": "http.response.start", "status": 404, "headers": []})
                await send({"type": "http.response.body", "body": b"Not found"})
            return
        scope = dict(
            scope, headers=[(k, v) for k, v in headers if k.lower() != PROOF_HEADER.encode()]
        )
        await self.app(scope, receive, send)


def validate_request_path(path: str) -> str:
    """Use one unambiguous path spelling across authorization and the upstream.

    Query strings are separate. Percent-encoding in the *decoded* ASGI path is
    forbidden to prevent a later proxy or service from decoding it a second time.
    """
    if (
        not path.startswith("/")
        or "//" in path
        or any(c in path for c in "%\\;?#")
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise ValueError("Ambiguous live component path")
    return path


def validate_protected_paths(entries: list[dict]) -> list[dict[str, str]]:
    """Validate a small route-prefix policy; the most restrictive match wins."""
    if not isinstance(entries, list) or len(entries) > 32:
        raise ValueError("protected_paths must be a list with at most 32 entries")
    out = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "access"}:
            raise ValueError("Protected paths require path and access")
        path = validate_request_path(entry["path"])
        if path == "/" or path.endswith("/") or entry["access"] not in {"presenter", "manage"}:
            raise ValueError("Invalid protected path or access scope")
        out.append({"path": path, "access": entry["access"]})
    return out


def route_access(path: str, policy: list[dict[str, str]]) -> str | None:
    validate_request_path(path)
    matches = {
        item["access"]
        for item in policy
        if path == item["path"] or path.startswith(item["path"] + "/")
    }
    return "manage" if "manage" in matches else "presenter" if matches else None


def guarded_app(app, *, health_path: str):
    """Install the immutable worker policy; absent configuration preserves legacy serving."""
    import json
    import os

    policy = validate_protected_paths(
        json.loads(os.environ.get("PORTAL_LIVE_PROTECTED_PATHS", "[]"))
    )
    if not policy:
        return app
    if route_access(health_path, policy):
        raise ValueError("Health route cannot be a protected route")
    return LiveAccessMiddleware(
        app, proof=os.environ.get("PORTAL_LIVE_PROOF", ""), health_path=health_path
    )
