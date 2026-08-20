"""**Target detection** — the facts about the Langfuse a kit is pointed at (portal #211).

Every kit is cloned per scenario and pointed at a different Langfuse through
``LANGFUSE_BASE_URL``, and two facts about that target change what the kit does:

1. **Is it Langfuse Cloud?** URL-derived, and free. Cloud rate-limits the one-at-a-time
   REST reads and writes a `verify` sweep fires, so those calls are spaced out and lean on
   the Retry-After-aware backoff in :mod:`langfuse_synth_core.http`. Self-hosted has no
   such limit. The Spool's write path is unaffected — it already retries and is not one
   request per event — so the seed itself needs no throttle.
2. **Does it answer at all?** *Probed*, not URL-derived, and therefore not free: it costs
   one HTTP round trip. :meth:`TargetProfile.resolved` asks the target and hands back a
   profile that knows, so a `verify` can say "cannot read this project, and here is why"
   on its first line instead of failing every check with the same traceback.

   This was a *generation* probe until portal #213 — which read API the target served —
   and it asked on a deprecated endpoint, once per reader. The seam reads v4 and only v4
   now, so there is no generation to resolve and the question narrowed to reachability.

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

    ``reachable`` is ``None`` until the target has been asked (:meth:`resolved`). That is a
    third state, not a missing value: "nobody has looked yet" is different from "answers"
    and from "does not", and a kit that only writes never needs to pay for the answer.
    """

    base_url: str
    is_cloud: bool
    post_throttle_s: float
    reachable: bool | None = None

    @classmethod
    def detect(cls, base_url: str) -> "TargetProfile":
        """The URL-derived facts. Pure, and makes no request."""
        url = (base_url or "").rstrip("/")
        is_cloud = CLOUD_HOST_MARKER in url
        return cls(base_url=url, is_cloud=is_cloud,
                   post_throttle_s=CLOUD_POST_THROTTLE_S if is_cloud else 0.0)

    # -- does the target answer ---------------------------------------------
    def resolved(self, reader: Any = None) -> "TargetProfile":
        """This profile with :attr:`reachable` filled in — probing the target if needed.

        Takes the reader it should ask, so a caller that already built one does not pay for
        a second probe, and a test can hand in a stub. Already-resolved profiles are
        returned unchanged, so this is safe to call on the way into any read.

        Raises whatever the probe raises. A `verify` that wants to report an unreadable
        target as failing checks rather than as a traceback should use :meth:`try_resolve`.
        """
        if self.reachable is not None:
            return self
        (reader or self.reader()).ping()
        return replace(self, reachable=True)

    def try_resolve(self, reader: Any = None) -> tuple["TargetProfile", str]:
        """:meth:`resolved`, or this profile unchanged plus the reason it could not be.

        Bad keys, a wrong host, a server error — the probe cannot tell those apart from
        each other, so it raises. But a `verify` is a **report**: its job is to say which of
        the demo's anchors are missing, and a caller who typed the wrong key deserves that
        report with every check failed and the reason on each line, not a traceback in place
        of it. Unresolved is a fine state to carry forward — each read then fails inside its
        own check and reports there.
        """
        try:
            return self.resolved(reader), ""
        except Exception as exc:  # noqa: BLE001 — the reason travels to the report
            return self, f"{type(exc).__name__}: {exc}"

    def reader(self, **kw: Any) -> read.LangfuseReader:
        """A reader for this target: its URL and its throttle."""
        kw.setdefault("throttle", self.post_throttle_s)
        return read.LangfuseReader.from_env(self.base_url, **kw)

    @property
    def label(self) -> str:
        """A one-line description for a `verify` log. It names the v4 read APIs once the
        target has answered, because "what did we read it through" is the first thing to
        know when a check that passed yesterday fails today."""
        host = "Langfuse Cloud" if self.is_cloud else "self-hosted Langfuse"
        return host if self.reachable is None else f"{host}, v4 read APIs"


def post_throttle_seconds(base_url: str) -> float:
    """Convenience: per-object REST call spacing for this target (0 off-Cloud)."""
    return TargetProfile.detect(base_url).post_throttle_s
