"""langfuse-synth-core — shared synthesis library + Authoring SDK (Spec A, #19).

The seam (hand-off rule):
  * Library = the machine that speaks the Langfuse data model, bidirectionally.
  * Kit     = everything that speaks the scenario.
  * The orchestration skeleton is a kit-owned scaffold; edge cases break to the kit.

This is a toolbox the kit calls, deliberately NOT an inversion-of-control framework
(T2 verdict: flexibility > deduplication). See docs/SEAM.md.

Scaffold stage (#26): this package establishes the distribution spine, the
runtime-vs-[authoring] boundary, and the runtime seams (companion adapter shell,
target_traces derivation hook). The synthesis machinery itself is extracted from the
two gold-standard kits in the Ring 1 / Ring 2 migration (#31–#34).
"""

from importlib.metadata import version as _distribution_version

from langfuse_synth_core import anchors, companion, derivation

# Derived from the installed distribution so it tracks `pyproject.toml` by construction
# (#145 — the hardcoded pre-Ring-1 "0.0.0" drifted for five releases unnoticed).
# `tests/test_runtime_import.py::test_version_matches_packaging_version` is the guard.
__version__ = _distribution_version("langfuse-synth-core")

__all__ = ["anchors", "companion", "derivation", "__version__"]
