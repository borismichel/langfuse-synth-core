"""**Target detection** — the facts about the Langfuse a kit is pointed at (portal #211).

Every kit is cloned per scenario and pointed at a different Langfuse through
``LANGFUSE_BASE_URL``, and two facts about that target change what the kit does:

1. **Is it Langfuse Cloud?** URL-derived, and free. Cloud rate-limits the one-at-a-time
   REST reads and writes a `verify` sweep fires, so those calls are spaced out and lean on
   the Retry-After-aware backoff in :mod:`langfuse_synth_core.http`. Self-hosted has no
   such limit. The batch/OTLP write path is unaffected — it already retries and is not one
   request per event — so the seed itself needs no throttle.
2. **Which read API generation does it serve?** *Probed*, not URL-derived, and therefore
   not free: it costs one HTTP round trip. Cloud goes v4-only on 2026-11-16 and a
   self-hosted host cuts over whenever its operator upgrades it, so the host name answers
   nothing here. :meth:`TargetProfile.resolved` asks the target and hands back a profile
   that knows — which is what makes a v4 host something a kit *recognises* rather than
   something it is configured for.

Both kits carried a byte-identical copy of the URL-derived half; it lives here now so a
third kit inherits it rather than growing its own ``"cloud.langfuse.com" in url`` check.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from . import read

#: Both EU (``cloud.langfuse.com``) and US (``us.cloud.langfuse.com``) contain this.
CLOUD_HOST_MARKER = "cloud.langfuse.com"

#: Per-request spacing on the one-at-a-time REST reads/writes, Cloud only.
CLOUD_POST_THROTTLE_S = 0.35


@dataclass(frozen=True)
class TargetProfile:
    """What a kit knows about its target. Build once with :meth:`detect`, pass it around.

    ``read_api`` is ``None`` until the target has been asked (:meth:`resolved`). That is a
    third state, not a missing value: "nobody has looked yet" is different from "legacy"
    and from "v4", and a kit that only writes never needs to pay for the answer.
    """

    base_url: str
    is_cloud: bool
    post_throttle_s: float
    read_api: str | None = None

    @classmethod
    def detect(cls, base_url: str) -> "TargetProfile":
        """The URL-derived facts. Pure, and makes no request."""
        url = (base_url or "").rstrip("/")
        is_cloud = CLOUD_HOST_MARKER in url
        return cls(base_url=url, is_cloud=is_cloud,
                   post_throttle_s=CLOUD_POST_THROTTLE_S if is_cloud else 0.0)

    # -- which generation the target serves ---------------------------------
    def resolved(self, reader: Any = None) -> "TargetProfile":
        """This profile with :attr:`read_api` filled in — probing the target if needed.

        Takes the reader it should ask, so a caller that already built one does not pay for
        a second probe, and a test can hand in a stub. Already-resolved profiles are
        returned unchanged, so this is safe to call on the way into any read.
        """
        if self.read_api is not None:
            return self
        return replace(self, read_api=(reader or self.reader()).read_api)

    @property
    def is_v4(self) -> bool:
        """True once the target has been asked *and* answered v4. Unresolved reads False —
        ask :meth:`resolved` first; guessing "not v4" is the safe way to be wrong here,
        because the legacy arm is the one that is preferred while it lives."""
        return self.read_api == read.V4

    def reader(self, **kw: Any) -> read.LangfuseReader:
        """A reader for this target: its URL, its throttle, its resolved generation.

        The generation is passed through when this profile already knows it, so a reader
        built off a resolved profile makes no probe of its own.
        """
        kw.setdefault("throttle", self.post_throttle_s)
        if self.read_api is not None:
            kw.setdefault("read_api", self.read_api)
        return read.LangfuseReader.from_env(self.base_url, **kw)

    @property
    def label(self) -> str:
        """A one-line description for a `verify` log — including the generation when the
        target has been asked, because "which API answered" is the first thing to know when
        a check that passed yesterday fails today."""
        host = "Langfuse Cloud" if self.is_cloud else "self-hosted Langfuse"
        return host if self.read_api is None else f"{host}, {self.read_api} read APIs"


def post_throttle_seconds(base_url: str) -> float:
    """Convenience: per-object REST call spacing for this target (0 off-Cloud)."""
    return TargetProfile.detect(base_url).post_throttle_s
