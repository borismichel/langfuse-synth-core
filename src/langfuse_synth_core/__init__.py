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

from langfuse_synth_core import companion, derivation

# 0.0.0 = pre-Ring-1 scaffold. v0.1.0 is tagged only when the byte-identical core is
# extracted and BOTH kits are golden-green (Ring 1b, #32); v1.0.0 after Ring 2 (#34).
__version__ = "0.0.0"

__all__ = ["companion", "derivation", "__version__"]
