"""Backdated batch ingestion — the core architectural decision (Ring 2 middle field, #33).

We build event objects directly and POST them to ``/api/public/ingestion`` with explicit
``timestamp`` / ``startTime`` / ``endTime``, bypassing the high-level OTel SDK (which pins
everything to "now"). HTTP Basic auth with the project keys; the
``x-langfuse-ingestion-version: 4`` header makes the data visible in real time on the v2
query/metrics endpoints.

Idempotent on re-run: every object carries a deterministic id, so re-seeding upserts within
Langfuse's 30-day merge window rather than duplicating.

Two-phase by design (hardened): generation **spools every event to an NDJSON file on disk
first**, then a separate pass **batch-imports** that file in ``chunk_size`` POSTs. Network
never runs interleaved with generation, so a wedged or slow upload can't lose the
(expensive, deterministic) generated data — re-run ``import_spool`` against the same file to
resume. Never one-request-per-event.

This is data-model-facing plumbing, not scenario substance: it speaks the Langfuse
ingestion API in the abstract. The per-kit deltas are *values* (base_url, keys, chunk_size,
the ``project_hint`` guardrail, the score-config bodies), so it moved into the shared core
parametrized by those. The event *bodies* it ships are composed by the kit from
:mod:`langfuse_synth_core.seed.events` primitives — the kit still owns the scenario tree.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from ..http import request_retry
from . import otlp
from .writepath import on_otlp


class IngestError(RuntimeError):
    pass


class NonResumableImportError(IngestError):
    """An OTLP Spool import was attempted over a project it has already been posted to.

    OTLP has no idempotent upsert: identical re-posts create duplicate observations where
    batch ingestion produced one row. So there is no safe resume, and re-running an import
    silently doubles a demo's volume. The recovery is to clear that deployment's Langfuse
    data and import from the top; the message says so, and ``confirm_cleared=True`` is how
    the caller states it has done that.
    """


@dataclass
class Ingestor:
    base_url: str
    public_key: str
    secret_key: str
    chunk_size: int = 100
    ingestion_version: str = "4"
    dry_run: bool = False
    timeout: int = 30
    max_retries: int = 5
    spool_path: Path | None = None
    _events: list[dict] = field(default_factory=list)
    _spool_fh: object = field(default=None, repr=False)
    spooled: int = 0
    sent: int = 0

    @classmethod
    def from_env(cls, base_url: str, **kw) -> "Ingestor":
        pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sec = os.environ.get("LANGFUSE_SECRET_KEY")
        if not (pub and sec) and not kw.get("dry_run"):
            raise IngestError(
                "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY must be set (see .env.example)."
            )
        return cls(base_url=base_url.rstrip("/"), public_key=pub or "", secret_key=sec or "", **kw)

    # -- phase 1: accumulate / spool to disk ------------------------------
    def open_spool(self) -> None:
        """Begin streaming events to ``spool_path`` as NDJSON (one event per line).

        Writing straight to disk keeps memory flat across the full run and means the
        generated data survives a wedged or failed upload."""
        if self.spool_path is None:
            raise IngestError("open_spool: no spool_path set")
        self.spool_path.parent.mkdir(parents=True, exist_ok=True)
        # A fresh Spool is a clean slate: whatever a previous Spool was imported into is no
        # longer what these bytes describe, so the non-resumable record goes with it.
        _import_marker(self.spool_path).unlink(missing_ok=True)
        self._spool_fh = self.spool_path.open("w", encoding="utf-8")
        self.spooled = 0

    def close_spool(self) -> None:
        """Close the spool and, on the OTLP path, finalise it in place.

        Finalisation is the second half of writing an OTLP Spool (see
        :mod:`langfuse_synth_core.seed.otlp`): a builder cannot know its trace's whole story
        at call time, so the closed file is walked twice — once to learn each trace's shell
        attributes and last end time, once to rewrite every span with those applied. It is a
        pure function of the lines, so the Spool stays byte-deterministic and the golden gate
        still binds. Memory stays flat: only one small record per trace is held, never the
        file.
        """
        if self._spool_fh is not None:
            self._spool_fh.flush()
            self._spool_fh.close()
            self._spool_fh = None
        if self.spool_path is not None and on_otlp():
            self._finalize_spool(self.spool_path)

    @staticmethod
    def _finalize_spool(path: Path) -> None:
        if not path.exists():
            return
        state: dict = {}
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    otlp.scan(json.loads(line), state)
        tmp = path.with_name(path.name + ".finalizing")
        with path.open("r", encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
            for line in src:
                if not line.strip():
                    continue
                final = otlp.apply(json.loads(line), state)
                dst.write(json.dumps(final, separators=(",", ":")) + "\n")
        tmp.replace(path)

    def add(self, event: dict) -> None:
        if self._spool_fh is not None:
            self._spool_fh.write(json.dumps(event, separators=(",", ":")) + "\n")
            self.spooled += 1
        else:
            self._events.append(event)

    def extend(self, events) -> None:
        for event in events:
            self.add(event)

    @property
    def pending(self) -> int:
        return len(self._events)

    # -- phase 2: batch-import --------------------------------------------
    def import_spool(self, path: Path | None = None,
                     log: Callable[[str], None] = lambda _m: None,
                     confirm_cleared: bool | None = None) -> int:
        """Read a spooled NDJSON file and POST it in ``chunk_size`` batches.

        Routing is decided by the **content of the Spool**, not by the ambient flag, so a
        Spool always imports the way it was written: OTLP spans go to the OTLP traces
        endpoint, ingestion envelopes (which is where scores stay) go to batch ingestion.

        **Re-runnability differs by path.** A batch Spool carries idempotent ids, so a
        re-import upserts rather than duplicating and is the recovery path after an
        interrupted upload. An OTLP Spool has no such property: re-posting appends. So an
        OTLP import is **non-resumable** — it records that it ran beside the Spool and
        refuses a second attempt with :class:`NonResumableImportError` rather than silently
        doubling every observation. Recovery is to clear that deployment's Langfuse data and
        import from the top, stated with ``confirm_cleared=True`` (or
        ``SYNTH_IMPORT_CONFIRM_CLEARED=1``). Re-running ``generate-spool`` is also a clean
        slate, because a fresh Spool clears the record.
        """
        path = path or self.spool_path
        if path is None:
            raise IngestError("import_spool: no spool path")
        if not path.exists():
            raise IngestError(f"import_spool: spool file not found: {path}")

        marker = _import_marker(path)
        otlp_spool = _spool_has_otlp_spans(path)
        if otlp_spool:
            if confirm_cleared is None:
                confirm_cleared = os.environ.get("SYNTH_IMPORT_CONFIRM_CLEARED") == "1"
            if marker.exists() and not confirm_cleared:
                raise NonResumableImportError(_non_resumable_message(path, marker))
            marker.write_text(f"import started for {path.name}\n", encoding="utf-8")

        chunk: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                chunk.append(json.loads(line))
                if len(chunk) >= self.chunk_size:
                    self._flush_chunk(chunk, log)
                    chunk = []
        if chunk:
            self._flush_chunk(chunk, log)
        return self.sent

    def _flush_chunk(self, chunk: list[dict], log: Callable[[str], None]) -> None:
        """Post one chunk, splitting it by the endpoint each line belongs to."""
        spans = [line for line in chunk if otlp.is_span(line)]
        envelopes = [line for line in chunk if not otlp.is_span(line)]
        if spans:
            self._post_spans(spans)
        if envelopes:
            self._post_chunk(envelopes)
        self.sent += len(chunk)
        log(f"  · imported {self.sent} events")

    # -- write-path liveness probe ----------------------------------------
    def write_ping(self) -> None:
        """Exercise the write path with an EMPTY batch — proves auth + endpoint
        reachability without emitting a single event, so the seeded pool is untouched.

        The Companion Adapter's readiness surface (Spec G · G2, #140) uses this as its
        "Langfuse write path ok" probe: a POST of ``{"batch": []}`` round-trips the real
        ingestion endpoint with the deployment's project keys and raises on any non-2xx, so
        a missing/wrong key or an unreachable host fails loudly — but nothing is written.
        A no-op under ``dry_run`` (the network is intentionally not touched). On the OTLP
        path the same idea applies to the OTLP traces endpoint: an export with no spans."""
        if on_otlp():
            self._post_spans([])
        else:
            self._post_chunk([])

    # -- in-memory send (back-compat; the seed path uses spool/import) ----
    def flush(self) -> None:
        """Send all accumulated events in chunks; clears the buffer.

        Finalises first on the OTLP path, exactly as ``close_spool`` does for a spooled run —
        this is the path the backdate probe takes, so it must produce the same wire objects
        an imported Spool would.
        """
        events, self._events = self._events, []
        if on_otlp():
            state: dict = {}
            for event in events:
                otlp.scan(event, state)
            events = [otlp.apply(event, state) for event in events]
        for i in range(0, len(events), self.chunk_size):
            chunk = events[i : i + self.chunk_size]
            spans = [line for line in chunk if otlp.is_span(line)]
            envelopes = [line for line in chunk if not otlp.is_span(line)]
            if spans:
                self._post_spans(spans)
            if envelopes:
                self._post_chunk(envelopes)
            self.sent += len(chunk)

    def _post_spans(self, spans: list[dict]) -> None:
        """Export finalised OTLP spans to Langfuse's OTLP/HTTP traces endpoint.

        Same auth and same real-time-ingestion header as batch ingestion; what differs is
        the failure contract. An OTLP export can return 200 and still have rejected spans,
        so a clean status is not proof of delivery — :func:`otlp.partial_failure` reads the
        ``partialSuccess`` report and we raise on it, the way the batch path raised on a
        207's per-event errors.
        """
        self._post(
            f"{self.base_url}{otlp.OTEL_TRACES_PATH}",
            otlp.payload(spans),
            self._check_rejected_spans,
            "otlp export",
        )

    def _post_chunk(self, chunk: list[dict]) -> None:
        self._post(
            f"{self.base_url}/api/public/ingestion",
            {"batch": chunk},
            self._check_partial,
            "ingestion",
        )

    def _post(self, url: str, body: dict, check, what: str) -> None:
        if self.dry_run:
            return
        headers = {"x-langfuse-ingestion-version": self.ingestion_version}
        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    url,
                    json=body,
                    auth=(self.public_key, self.secret_key),
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt == self.max_retries:
                    raise IngestError(f"{what} request failed: {exc}") from exc
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue

            if resp.status_code in (200, 201, 207):
                # 207 = partial success; surface per-event errors loudly. The OTLP path has
                # no 207 — its partial failures ride a 200 body — hence the injected check.
                check(resp)
                return
            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt == self.max_retries:
                    raise IngestError(f"{what} failed {resp.status_code}: {resp.text[:500]}")
                wait = backoff
                if resp.status_code == 429:  # Cloud sets Retry-After; honour it
                    try:
                        wait = max(wait, float(resp.headers.get("Retry-After", 0)))
                    except (TypeError, ValueError):
                        pass
                time.sleep(min(wait, 30))
                backoff = min(backoff * 2, 30)
                continue
            raise IngestError(f"{what} rejected {resp.status_code}: {resp.text[:500]}")

    @staticmethod
    def _check_rejected_spans(resp: requests.Response) -> None:
        try:
            body = resp.json()
        except ValueError:
            return
        problem = otlp.partial_failure(body)
        if problem:
            raise IngestError(problem)

    @staticmethod
    def _check_partial(resp: requests.Response) -> None:
        try:
            body = resp.json()
        except ValueError:
            return
        errors = body.get("errors") or []
        if errors:
            sample = errors[:3]
            raise IngestError(f"{len(errors)} events rejected by ingestion; sample: {sample}")


# ---------------------------------------------------------------------------
# Non-resumability bookkeeping (OTLP path only)
# ---------------------------------------------------------------------------
def _import_marker(spool_path: Path) -> Path:
    """The record that an OTLP Spool has been posted, written beside the Spool.

    It lives on the spool volume so it survives the gap between the ``generate-spool`` and
    ``import-spool`` job containers, which is exactly the window a retry would land in.
    """
    return spool_path.with_name(spool_path.name + ".imported")


def _spool_has_otlp_spans(path: Path) -> bool:
    """Whether these bytes are an OTLP Spool. Read from the Spool rather than the ambient
    flag so a Spool always imports the way it was written.

    Scans until it finds a span rather than judging by the first line: scores stay ingestion
    envelopes on both paths, so a Spool that happens to open with one is still an OTLP Spool
    — and getting that wrong would quietly disable the non-resumable guard.
    """
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and otlp.is_span(json.loads(line)):
                return True
    return False


def _non_resumable_message(path: Path, marker: Path) -> str:
    return (
        f"import-spool is NON-RESUMABLE on the OTLP write path, and {path.name} has already "
        f"been imported (record: {marker.name}). OTLP has no idempotent upsert — re-posting "
        "the same spans appends duplicate observations where batch ingestion produced one "
        "row, so re-running would silently double this deployment's volume.\n"
        "Recovery: CLEAR this deployment's Langfuse data, then re-import from the top with "
        "confirm_cleared=True (or SYNTH_IMPORT_CONFIRM_CLEARED=1). Re-running generate-spool "
        "is also a clean slate."
    )


# ---------------------------------------------------------------------------
# Project guardrail + score-config REST helpers (share the same auth)
# ---------------------------------------------------------------------------
def assert_demo_project(base_url: str, project_hint: str) -> tuple[str, str]:
    """Refuse to run unless the key's project name contains ``project_hint``.

    Returns ``(project_id, project_name)``. Loud failure if it doesn't match — this is
    the guardrail that stops a seed ever hitting a production project.
    """
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "")
    resp = request_retry(
        "GET", f"{base_url.rstrip('/')}/api/public/projects",
        auth=(pub, sec), timeout=15,
    )
    resp.raise_for_status()
    projects = resp.json().get("data", [])
    names = [p.get("name", "") for p in projects]
    matched = [p for p in projects if project_hint.lower() in p.get("name", "").lower()]
    if not matched:
        raise IngestError(
            f"GUARDRAIL: no project matching project_hint={project_hint!r} for these keys "
            f"(saw {names!r}). Point at a demo/sandbox project or fix project_hint."
        )
    p = matched[0]
    return p.get("id", ""), p.get("name", "")


def ensure_score_config(base_url: str, body: dict) -> None:
    """Create a score config (POST /api/public/score-configs). Idempotent-ish: ignores 409."""
    pub = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sec = os.environ.get("LANGFUSE_SECRET_KEY", "")
    resp = request_retry(
        "POST", f"{base_url.rstrip('/')}/api/public/score-configs",
        json=body, auth=(pub, sec), timeout=15,
    )
    if resp.status_code in (200, 201):
        return
    if resp.status_code == 409:
        return  # already exists
    # Some deployments 400 on duplicate name; treat as benign if the name already exists.
    if resp.status_code == 400 and "exist" in resp.text.lower():
        return
    raise IngestError(f"score-config create failed {resp.status_code}: {resp.text[:300]}")
