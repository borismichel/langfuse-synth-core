"""Seed-time synthesis primitives shared by every kit.

The machine that speaks the Langfuse v4 write model: the event builders (``events``), the
OTLP wire model (``otlp``), the spool/import ``Ingestor`` (``ingest``) and the billable
tally (``count``). The scenario-shaped seed subsystems (traces, scores, generator, run
orchestration) live in each kit.

Observations are OTLP spans and a trace is its root observation; scores stay `score-create`
envelopes, which is the supported v4 path for them. See ``docs/WRITE_PATHS.md``.
"""
