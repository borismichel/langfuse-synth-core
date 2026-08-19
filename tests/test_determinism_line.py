"""The determinism line, as an executable check (portal #208, spec H #204).

`CONTRACT.md` draws one line through every Langfuse conversation the depot has: the Spool
is deterministic, backdated and byte-compared against a golden; live surfaces are
wall-clock, may call a model, and are outside the golden-gate. The v4 migration gives each
side its own seam, and this file is what keeps them from leaking into one another.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "langfuse_synth_core"


def _imported_modules(path: Path, *, top_level_only: bool = False) -> set[str]:
    """Every module name the file imports, absolute and relative alike.

    ``top_level_only`` looks at import statements that run at *import time* — the ones that
    decide whether the module loads on a bare runtime install — and ignores the lazy ones
    inside functions.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = tree.body if top_level_only else list(ast.walk(tree))
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return names


def test_the_live_seam_never_reaches_for_the_spool():
    """A live surface that borrowed the Spool's builders would be emitting through machinery
    whose entire purpose is backdating — the coupling this seam exists to cut (#204)."""
    imports = _imported_modules(SRC / "live" / "emit.py")

    assert not [i for i in imports if "seed" in i], \
        f"the live seam imported the Spool's write path: {sorted(imports)}"


def test_the_live_seam_holds_no_spool_vocabulary():
    """Not just the imports: no ingestor, no spool file, no event envelopes."""
    source = (SRC / "live" / "emit.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
    body = code.split('"""', 2)[-1]  # drop the module docstring, which discusses the line

    for forbidden in ("Ingestor", "trace_event(", "span_event(", "generation_event(",
                      "score_event(", "spool_path", "ndjson"):
        assert forbidden not in body, f"live seam reaches for the Spool: {forbidden}"


def test_the_spool_write_path_never_reaches_for_the_sdk():
    """The mirror image: the SDK stamps wall-clock and cannot backdate, so the Spool's own
    modules may not use it however convenient it looks."""
    for module in ("seed/events.py", "seed/otlp.py", "seed/ingest.py"):
        source = (SRC / module).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        body = code.split('"""', 2)[-1]
        assert "from langfuse import" not in body, f"{module} imports the Langfuse SDK"


@pytest.mark.parametrize("module", ["read.py", "live/emit.py"])
def test_the_new_seams_import_on_a_bare_runtime_install(module):
    """Both seams are imported by kit code that may run without the optional extras, so the
    heavy dependencies stay lazy — the same rule the adapter and `lfclient` follow."""
    imports = _imported_modules(SRC / module, top_level_only=True)

    assert "langfuse" not in imports, "the SDK must be imported lazily, inside the call"
    assert not [i for i in imports if i.startswith("fastapi") or i.startswith("uvicorn")]
