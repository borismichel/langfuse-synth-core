"""Fresh per-operation authorization over Depot's fixed private bridge.

This is point-in-time hourly-estimate governance, not a billing reservation.
The capability has a distinct credential purpose from live ingress authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import requests

OPERATION_HEADER = "X-Depot-Operation-Token"
_DENIALS = frozenset({"cap_exhausted", "instance_inactive", "provider_unbound", "unauthorized"})


def operation_token(secret: str, instance_id: str) -> str:
    """Host-side, purpose-separated proof; never pass host signing material to kits."""
    if len(secret) < 32 or str(uuid.UUID(instance_id)) != instance_id:
        raise ValueError("invalid operation signing configuration")
    return hmac.new(
        secret.encode(), ("depot-live-operation-v1:" + instance_id).encode(), hashlib.sha256
    ).hexdigest()


def operation_url(instance_id: str) -> str:
    if str(uuid.UUID(instance_id)) != instance_id:
        raise ValueError("invalid operation instance")
    return f"http://caddy:8081/api/v1/live-assets/{instance_id}/operation-permit"


class SpendingDenied(RuntimeError):
    """Stop scheduling paid operations; the safe reason may be displayed."""

    def __init__(self, reason: str):
        self.reason = reason if reason in _DENIALS else "unauthorized"
        super().__init__(self.reason)


class SpendingUnavailable(RuntimeError):
    """Authorization could not be established. Fail closed; do not dispatch."""

    def __init__(self, reason: str = "check_unavailable"):
        self.reason = "check_unavailable"
        super().__init__(self.reason)


@dataclass(frozen=True)
class SpendingDecision:
    allowed: bool
    provider: str
    source: str
    checked_at: str
    accounting: str
    scope: str


class OperationSpending:
    """Private transport owned by the Adapter; captures env without network I/O."""

    def __init__(self):
        self._url = os.environ.get("DEPOT_OPERATION_CHECK_URL", "")
        self._token = os.environ.get("DEPOT_OPERATION_TOKEN", "")
        self._instance = os.environ.get("PORTAL_LIVE_INSTANCE_ID", "")

    def check(self, provider: str) -> SpendingDecision:
        try:
            valid = (
                self._url == operation_url(self._instance)
                and re.fullmatch("[a-f0-9]{64}", self._token)
                and provider in {"anthropic", "openai"}
            )
        except (ValueError, AttributeError):
            valid = False
        if not valid:
            raise SpendingUnavailable()
        # Ignore process proxy/netrc settings: the only destination is the private bridge.
        try:
            with requests.Session() as transport:
                transport.trust_env = False
                deadline = time.monotonic() + 5
                with transport.post(
                    self._url,
                    json={"provider": provider},
                    headers={OPERATION_HEADER: self._token},
                    timeout=(2, 2),
                    allow_redirects=False,
                    stream=True,
                ) as response:
                    data = bytearray()
                    for chunk in response.iter_content(chunk_size=1):
                        data.extend(chunk)
                        if len(data) > 4096 or time.monotonic() > deadline:
                            raise SpendingUnavailable()
                    body = json.loads(data)
                    if response.status_code in (401, 403, 409) and isinstance(body, dict):
                        raise SpendingDenied(body.get("reason"))
                    if response.status_code != 200 or not isinstance(body, dict):
                        raise SpendingUnavailable()
                    if (
                        set(body)
                        != {"allowed", "provider", "source", "checked_at", "accounting", "scope"}
                        or body["allowed"] is not True
                        or body["provider"] != provider
                        or body["source"] not in {"shared", "environment"}
                        or body["accounting"] != "hourly-estimate"
                        or body["scope"]
                        != ("shared-company" if body["source"] == "shared" else "external-provider")
                    ):
                        raise SpendingUnavailable()
                    if datetime.fromisoformat(body["checked_at"]).tzinfo is None:
                        raise SpendingUnavailable()
                    return SpendingDecision(**body)
        except SpendingDenied:
            raise
        except Exception:
            # Neither transport errors nor remote bodies may echo capability material.
            raise SpendingUnavailable() from None
