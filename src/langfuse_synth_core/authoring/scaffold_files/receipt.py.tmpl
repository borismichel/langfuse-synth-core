"""Verification receipt on the spool volume, separate from optional kit RunState.

Only a successful live seed writes this bounded evidence. Core owns the IO/location;
this kit owns the representative score/trace assertions. Grow them with the story.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from langfuse_synth_core.anchors import AnchorsIO

SAMPLE_LIMIT = 5


@dataclass
class RunReceipt(AnchorsIO):
    FALLBACK_STATE_DIR: ClassVar[Path] = Path(__file__).resolve().parents[2] / ".synth_spool"

    base_url: str
    public_key: str
    seed: int
    target_traces: int
    as_of_date: str
    examples: list[dict]

    @classmethod
    def state_path(cls) -> str:
        # Share the core spool mount without overwriting --anchors' .synth_state.json.
        return str(cls.state_dir() / ".synth_verify.json")
