"""Which wire the Spool is written on — the v4 migration's expand-half switch (portal #206).

Langfuse Cloud goes v4-only on **2026-11-16**, removing the legacy batch-ingestion API the
Spool has always used. The replacement is raw OTLP, and the migration is deliberately
*expand–contract*: core learns to speak OTLP **beside** the batch path, and this flag says
which one a given Spool is written on. It defaults to today's behaviour, so a kit that
repins core and changes nothing keeps emitting batch envelopes and keeps its blessed
golden byte-for-byte.

Deliberately raw OTLP and NOT the Langfuse SDK for this seam: the SDK stamps wall-clock and
has no start-time parameter, and a Spool is by definition weeks of *backdated* history.
Following Langfuse's own migration guidance (which points Python projects at the SDK) would
collapse every Spool onto the moment of the deploy. Live, wall-clock surfaces are a
different seam and may use the SDK; the Spool may not.

**How a cutover happens.** #210 flips one kit at a time by calling
:func:`set_spool_write_path` in that kit's ``seed`` entrypoint — a one-line commit in one
kit repo, reverted by reverting it. ``SYNTH_SPOOL_WRITE_PATH`` supplies the default when a
kit sets nothing, which is how the golden subprocess, the conformance suite and an operator
select a path without touching kit code.

**What flipping costs.** The OTLP path mints a root span per trace (there is no trace entity
under v4), so ``count_spool``'s ``observations`` term — and therefore a deployment's
measured billable volume — rises by one per trace. The *shape* of the count is unchanged, so
the plan-time estimate, the cap gate and the over-cap halt need no code change; their
numbers move on the commit that flips a kit.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

#: Today's behaviour: ``/api/public/ingestion`` envelopes, idempotent on re-import.
BATCH = "batch"
#: Raw OTLP spans posted to ``/api/public/otel/v1/traces``. Append-only: NOT idempotent.
OTLP = "otlp"

WRITE_PATH_ENV = "SYNTH_SPOOL_WRITE_PATH"

_PATHS = (BATCH, OTLP)

# A kit-set pin (``None`` = unset, fall through to the env default). Module-level because
# the event builders are module-level functions the kit calls directly — the same shape as
# the neighbouring ``RICH_OBSERVATION_TYPES`` switch in :mod:`.events`.
_override: str | None = None


def _validated(value: str) -> str:
    path = value.strip().lower()
    if path not in _PATHS:
        raise ValueError(
            f"unknown Spool write path {value!r} — expected one of {list(_PATHS)}. "
            f"(Set {WRITE_PATH_ENV} or call set_spool_write_path().)"
        )
    return path


def spool_write_path() -> str:
    """The selected path: a kit-set pin if there is one, else ``SYNTH_SPOOL_WRITE_PATH``,
    else :data:`BATCH`. Raises ``ValueError`` on an unrecognised value rather than falling
    back silently — a typo'd cutover must fail, not quietly write the old format."""
    if _override is not None:
        return _override
    return _validated(os.environ.get(WRITE_PATH_ENV) or BATCH)


def on_otlp() -> bool:
    """True when the Spool is being written as OTLP spans."""
    return spool_write_path() == OTLP


def set_spool_write_path(path: str | None) -> None:
    """Pin the write path from kit code (``None`` clears the pin). This is the one line a
    kit's ``seed`` entrypoint changes to cut over in #210."""
    global _override
    _override = None if path is None else _validated(path)


@contextmanager
def use_spool_write_path(path: str | None):
    """Pin the write path for a block, then restore. Used by tests and by any tool that
    has to materialize a Spool on a path other than the ambient one."""
    global _override
    previous = _override
    set_spool_write_path(path)
    try:
        yield
    finally:
        _override = previous
