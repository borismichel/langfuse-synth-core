"""Backdated batch ingestion — data-model middle field (Ring 2, #33).

The ``Ingestor`` is scenario-agnostic plumbing: spool events to NDJSON, batch-POST to
``/api/public/ingestion``. These lock the offline behaviour (spool/import phases, dry-run,
env-key guard) without any network — the golden gate and the kit's own seed path exercise
the composed event bodies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from langfuse_synth_core.seed.ingest import Ingestor, IngestError


def test_from_env_requires_keys_unless_dry_run(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with pytest.raises(IngestError):
        Ingestor.from_env("http://localhost:3000")
    # dry_run bypasses the key requirement (offline generation).
    ing = Ingestor.from_env("http://localhost:3000/", dry_run=True)
    assert ing.base_url == "http://localhost:3000"  # trailing slash trimmed


def test_spool_writes_ndjson_one_event_per_line(tmp_path: Path):
    spool = tmp_path / "events.ndjson"
    ing = Ingestor(base_url="http://x", public_key="p", secret_key="s", spool_path=spool)
    ing.open_spool()
    ing.extend([{"type": "trace-create", "id": "a"}, {"type": "score-create", "id": "b"}])
    ing.close_spool()
    lines = spool.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and ing.spooled == 2
    # Compact separators — no spaces — so the spool is byte-stable.
    assert lines[0] == '{"type":"trace-create","id":"a"}'


def test_import_spool_missing_file_raises(tmp_path: Path):
    ing = Ingestor(base_url="http://x", public_key="p", secret_key="s",
                   spool_path=tmp_path / "nope.ndjson")
    with pytest.raises(IngestError):
        ing.import_spool()


def test_dry_run_import_sends_nothing_but_counts(tmp_path: Path):
    spool = tmp_path / "events.ndjson"
    ing = Ingestor(base_url="http://x", public_key="p", secret_key="s",
                   spool_path=spool, dry_run=True, chunk_size=2)
    ing.open_spool()
    ing.extend([{"i": n} for n in range(5)])
    ing.close_spool()
    # dry_run: _post_chunk is a no-op, but the import loop still walks the file.
    assert ing.import_spool() == 5
