"""The golden gate over the v4 write model (portal #206, #213).

The golden captures are this migration's primary regression gate, so the Spool has to be
provably byte-deterministic on the wire the fleet actually writes. These run the gate over
a fixture kit that materializes its Spool through the real event builders and the real
``Ingestor``, in a fresh subprocess with a pinned hash seed and egress blocked — the same
machine a kit's own golden test uses.

There was a write-path flag here, and three of these tests measured its two sides against
each other. #213 removed the batch path; what survives is the half that still means
something — the Spool is deterministic, and it carries the whole story.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="golden gate ships in the [authoring] extra; not installed on a runtime-only job",
)

FIXTURES = str(Path(__file__).resolve().parent / "fixtures")


def _spec(golden_path: Path):
    from langfuse_synth_core.authoring.golden import GoldenSpec

    return GoldenSpec(seed_ref="wire_kit:seed", target_traces=4, golden_path=golden_path,
                      params={"seed": 7}, search_paths=(FIXTURES,))


def test_the_spool_is_byte_deterministic(tmp_path):
    from langfuse_synth_core.authoring.golden import assert_golden, freeze

    spec = _spec(tmp_path / "wire-otlp.golden")
    freeze(spec)
    assert_golden(spec)   # a second, independent materialization is byte-identical


def test_the_spool_still_carries_the_whole_story(tmp_path):
    from langfuse_synth_core.authoring.golden import materialize_spool

    blob = materialize_spool(_spec(tmp_path / "unused.golden"))
    assert b'"langfuse.observation.usage_details"' in blob
    assert b'"langfuse.session.id"' in blob
    assert b'"score-create"' in blob          # the supported v4 write path for scores
    assert b'"parentObservationId"' not in blob   # nesting is span context now


def test_no_deprecated_ingestion_envelope_reaches_the_spool(tmp_path):
    """The contract half of expand–contract, asserted on the bytes (portal #213). The
    ingestion deprecation is per event type, so the Spool may carry `score-create` and
    nothing else that goes to that endpoint."""
    from langfuse_synth_core.authoring.golden import materialize_spool

    blob = materialize_spool(_spec(tmp_path / "unused.golden"))
    for retired in (b'"trace-create"', b'"span-create"', b'"generation-create"',
                    b'"event-create"', b'"observation-create"'):
        assert retired not in blob, retired
