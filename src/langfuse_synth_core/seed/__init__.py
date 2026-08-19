"""Seed-time synthesis primitives shared by every kit.

The machine that speaks the Langfuse write model: the event builders (``events``), the
wire-path switch (``writepath``), the OTLP wire model (``otlp``), the spool/import
``Ingestor`` (``ingest``) and the billable tally (``count``). The scenario-shaped seed
subsystems (traces, scores, generator, run orchestration) live in each kit.

Two wire formats, one Python API: a kit calls the same builders whichever path a Spool is
written on. See :mod:`langfuse_synth_core.seed.writepath` and ``docs/WRITE_PATHS.md``.
"""
