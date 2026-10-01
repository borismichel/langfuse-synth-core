# Recipe, volume and transport boundaries

Read when changing trace generation, retargeting or a transport-sensitive integration.
Use the `langfuse` skill for observation/evaluator semantics; see
[langfuse-craft.md](langfuse-craft.md). The kit owns scenario choices and core owns the wire.

## Generate deterministic story data

Compose events through `langfuse_synth_core.seed.events`: `trace_event`, `span_event`,
`generation_event`, `event_event`, `observation_event` and `score_event`. Use the library's
`Rng` substreams (`rng.sub("namespace", i)`) and ID helpers (`trace_id`, `obs_id`, `score_id`).
Explicitly order collections before generation or serialization. Keep model calls out of
seed runtime; richer content belongs in committed author-time fixtures.

Every story timestamp is an offset from the `run_date` passed by `synth.seed`, resolved
from `generation.as_of_date` or the clock when none was supplied. Use core's time helpers
for backdating. A future as-of date is valid. Keep pinned dates in the golden adapter;
production generation reads neither a date constant nor the clock independently.

## Preserve the operator knobs

The volume knob is `generation.target_traces`. The kit's deterministic `DERIVATION_HOOK`
can map it into cohort sizes or experiment counts; retain identity derivation when the
volume is already a direct trace count. Keep fixed evaluation assets unscaled and preserve
each beat's representative examples when increasing background volume. All declared
parameters must shape the real runtime configuration and golden adapter consistently.

Retargeting uses the committed `Target.host` as fallback and lets `LANGFUSE_BASE_URL` win
through `Target.base_url`. Preserve both branches of the generated retargeting test. A
flattened base URL can silently send a deployed kit to its author's localhost.

## Observation vocabulary

The recognised wire types are `span`, `generation`, `event`, `agent`, `tool`, `chain`,
`retriever`, `embedding`, `evaluator` and `guardrail`. They are lowercase and case-sensitive.
An unknown OTLP type can be silently represented as a span or, when it carries a model,
a generation; the resulting cost and usage story is misleading.

Core's `observation_event` validates and normalises its `obs_type`. The live seam validates
`as_type` strictly, so use lowercase there. `synth-authoring conformance` checks literals;
the runtime guards cover dynamically constructed values. Choosing the appropriate type
still belongs to the `langfuse` skill.

## Transport and migration detail

Every observation is written as an OTLP span. A trace is represented by its root
observation; account for that root when reasoning about billable volume. Scores retain
`score-create` envelopes on the ingestion API. Build with core primitives instead of
constructing transport payloads or maintaining a second read client in the Recipe.

OTLP appends rather than upserting. Deterministic Spool bytes do not make ingestion
idempotent. `import-spool` is non-resumable; use the established authorised reset or a
fresh target before importing after a partial failure. The main workflow explains when
a live run is needed so ordinary edits do not trigger repeated ingestion.

Core records the platform's v4-only migration date as 2026-11-16. For a migration, use the
version of these documents matching the kit's core pin and confirm current Langfuse
semantics through the Langfuse skill:

- [Contract](https://github.com/borismichel/langfuse-synth-core/blob/main/CONTRACT.md)
- [Library seam](https://github.com/borismichel/langfuse-synth-core/blob/main/docs/SEAM.md)
- [Write-path mapping](https://github.com/borismichel/langfuse-synth-core/blob/main/docs/WRITE_PATHS.md)

These are external repository references; all instructions needed for the normal authoring
loop remain in the installed skill and its local references.
