"""Per-run anchors IO — the shared read/write mechanism (portal #199).

A seed run makes choices no other party can reconstruct — the run date, the resolved
Langfuse project id, example trace ids, prompt versions, headline figures. Anchors are
those facts, written once by ``synth seed`` so every later reader (``verify``, ``script``,
the kit's live pages, the Presenter Runbook) agrees with the data actually in Langfuse.
The rules — the file, its location, the read-only-spool transport, opt-in per kit — are
stated once in ``CONTRACT.md`` §"Per-run anchors (opt-in)"; this module is that section's
mechanism, shipped once here so kits stop carrying diverged twins.

The split (epic portal #195, decision 2): **core owns the IO** — the canonical filename,
the location resolved from ``SYNTH_STATE_DIR``, save/load/exists — while the **payload
stays kit territory**. A kit declares its anchors as a plain ``@dataclass`` and mixes in
:class:`AnchorsIO`; the fields, and any convenience accessors over them, never cross the
seam (the portal transports the file and never parses it, so neither does this library).

Anchors are opt-in: a stateless kit simply never imports this module (the support kit's
console companion derives its scene from config + adapter and reads no run state —
statelessness is a legitimate contract citizen, not a gap).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import ClassVar

# The canonical state file, beside `events.ndjson` on the spool volume — the only
# cross-container surface (the artifact dir is container-local and would strand it).
STATE_FILENAME = ".synth_state.json"

# The env var naming the state dir. The portal injects it in every container; a shell
# export serves dev runs. Resolved at CALL time, never import time, so a container `ENV`
# and a test monkeypatch both work.
STATE_DIR_ENV = "SYNTH_STATE_DIR"


def state_dir(fallback: str | Path) -> Path:
    """Where the state file lives: ``SYNTH_STATE_DIR`` if set, else ``fallback``.

    ``fallback`` is the kit's dev-checkout spool dir (conventionally
    ``<repo root>/.synth_spool``) — deployed containers always get the env var.
    """
    env = os.environ.get(STATE_DIR_ENV)
    return Path(env) if env else Path(fallback)


def state_path(fallback: str | Path) -> str:
    """The full state-file path under :func:`state_dir`."""
    return str(state_dir(fallback) / STATE_FILENAME)


class AnchorsIO:
    """Save/load/exists for a kit's anchors payload.

    The kit subclasses this with a plain ``@dataclass`` holding its anchor fields and sets
    ``FALLBACK_STATE_DIR`` (a ``ClassVar``, so the dataclass machinery ignores it) to its
    dev-checkout spool dir::

        @dataclass
        class RunState(AnchorsIO):
            FALLBACK_STATE_DIR: ClassVar[Path] = REPO_ROOT / ".synth_spool"

            project_name: str
            ...

    ``save`` writes ``json.dumps(asdict(self), indent=2)`` — byte-identical to the format
    the pre-extraction kit-local twins wrote, so existing state files and golden spools
    survive the migration unchanged. ``load`` tolerates unknown keys (a file written by an
    older or newer payload schema loads with those keys dropped — the Lender twin's
    behavior, adopted for every kit).
    """

    FALLBACK_STATE_DIR: ClassVar[Path]

    @classmethod
    def state_dir(cls) -> Path:
        return state_dir(cls.FALLBACK_STATE_DIR)

    @classmethod
    def state_path(cls) -> str:
        return state_path(cls.FALLBACK_STATE_DIR)

    def save(self, path: str | None = None) -> None:
        p = Path(path or self.state_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: str | None = None):
        data = json.loads(Path(path or cls.state_path()).read_text())
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def exists(cls, path: str | None = None) -> bool:
        return Path(path or cls.state_path()).exists()
