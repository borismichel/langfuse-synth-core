"""The golden gate under the write-path flag (portal #206).

The golden captures are this migration's primary regression gate, so the flag has to be
provably invisible while it is off and provably deterministic once it is on. These run the
gate over a fixture kit that materializes its Spool through the real event builders and the
real ``Ingestor``, in a fresh subprocess with a pinned hash seed and egress blocked — the
same machine a kit's own golden test uses.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from langfuse_synth_core.seed import writepath

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="golden gate ships in the [authoring] extra; not installed on a runtime-only job",
)

FIXTURES = str(Path(__file__).resolve().parent / "fixtures")


def _spec(golden_path: Path):
    from langfuse_synth_core.authoring.golden import GoldenSpec

    return GoldenSpec(seed_ref="wire_kit:seed", target_traces=4, golden_path=golden_path,
                      params={"seed": 7}, search_paths=(FIXTURES,))


def test_a_blessed_golden_stays_green_while_the_flag_is_off(tmp_path, monkeypatch):
    from langfuse_synth_core.authoring.golden import assert_golden, freeze

    monkeypatch.delenv(writepath.WRITE_PATH_ENV, raising=False)
    spec = _spec(tmp_path / "wire.golden")
    freeze(spec)
    assert_golden(spec)
    assert b'"span-create"' in spec.golden_path.read_bytes()


def test_the_otlp_spool_is_byte_deterministic_too(tmp_path, monkeypatch):
    from langfuse_synth_core.authoring.golden import assert_golden, freeze

    monkeypatch.setenv(writepath.WRITE_PATH_ENV, writepath.OTLP)
    spec = _spec(tmp_path / "wire-otlp.golden")
    freeze(spec)
    assert_golden(spec)   # a second, independent materialization is byte-identical


def test_flipping_the_flag_is_what_moves_the_golden(tmp_path, monkeypatch):
    """A blessed batch golden fails the gate once the kit cuts over — which is exactly why
    #210 re-blesses one kit at a time and reviews the diff as data."""
    from langfuse_synth_core.authoring.golden import GoldenMismatch, assert_golden, freeze

    monkeypatch.delenv(writepath.WRITE_PATH_ENV, raising=False)
    spec = _spec(tmp_path / "wire.golden")
    freeze(spec)

    monkeypatch.setenv(writepath.WRITE_PATH_ENV, writepath.OTLP)
    with pytest.raises(GoldenMismatch):
        assert_golden(spec)


def test_the_otlp_spool_still_carries_the_whole_story(tmp_path, monkeypatch):
    from langfuse_synth_core.authoring.golden import materialize_spool

    monkeypatch.setenv(writepath.WRITE_PATH_ENV, writepath.OTLP)
    blob = materialize_spool(_spec(tmp_path / "unused.golden"))
    assert b'"langfuse.observation.usage_details"' in blob
    assert b'"langfuse.session.id"' in blob
    assert b'"score-create"' in blob          # scores keep the ingestion endpoint
    assert b'"parentObservationId"' not in blob   # nesting is span context now
