# The Spool's two write paths

Langfuse Cloud becomes **v4-only on 2026-11-16** and removes the legacy batch-ingestion API
the Spool has always used. Core therefore speaks two wire formats, selected by one flag, so
that kits cut over one at a time instead of all at once (portal #206, spec H #204).

| | `batch` (default) | `otlp` |
| --- | --- | --- |
| Endpoint | `/api/public/ingestion` | `/api/public/otel/v1/traces` |
| Spool line | `{id, type, timestamp, body}` envelope | one OTLP span |
| Trace | a `trace-create` envelope | no trace entity — a minted **root observation** |
| Nesting | `parentObservationId` | OTLP parent span context |
| Re-import | idempotent upsert | **appends — non-resumable** |

Scores are on **neither** side of that line: score creation survives the cutover on the
legacy ingestion endpoint and is the only envelope type that does, so `score_event` emits
the same envelope on both paths. That is a decision, not an oversight.

## Selecting a path

```python
from langfuse_synth_core.seed import writepath

writepath.set_spool_write_path(writepath.OTLP)   # one line in a kit's `seed` entrypoint
```

`SYNTH_SPOOL_WRITE_PATH=batch|otlp` supplies the default when a kit sets nothing — that is
how the golden subprocess, the conformance suite and an operator select a path without
touching kit code. A kit-set pin wins over the env; an unrecognised value raises rather
than silently writing the old format.

## Why raw OTLP and not the Langfuse SDK

The SDK stamps wall-clock and exposes no start-time parameter. A Spool is *by definition*
weeks of backdated history, so the SDK would collapse every demo onto the deploy date.
Raw OTLP takes producer-supplied nanosecond timestamps **and** producer-minted ids — core's
BLAKE2b ids are already exactly the 32-hex trace / 16-hex span widths OTLP accepts.

Langfuse's own migration guidance points Python projects at the SDK. That guidance is wrong
for this seam and the deviation is deliberate: **do not "correct" it later.** Live,
wall-clock surfaces (companion apps, playground, workbench) are a different seam and may
use the SDK.

Verified against a real Langfuse v4 Cloud project on 2026-08-19: a 21-day-backdated span
read back with its timestamp intact to the millisecond, producer ids verbatim, hierarchy as
parent span context, and model / usage / cost / prompt-link on the generation.

## The observation-type vocabulary

`langfuse.observation.type` carries one of ten values — `span`, `generation`, `event`,
`agent`, `tool`, `chain`, `retriever`, `embedding`, `evaluator`, `guardrail`. Langfuse's own
docs disagreed about this set (the observation-types page listed ten, the OTEL mapping table
three), so it was settled by posting to a real Langfuse Cloud project on 2026-08-19:

| Sent as | Lands as |
| --- | --- |
| `agent` | `AGENT` |
| `AGENT` — uppercase | `SPAN` |
| `genration` — a typo | `SPAN` |
| an unknown value, on a span carrying a model | `GENERATION` |

Values are lowercase and case-sensitive, and **nothing is rejected**: the last two rows are
silent, and the last one is damaging — a mistyped step that names a model is ingested as a
generation, so it lands in cost and usage views and changes the story the demo tells.

Batch ingestion accepted only `SPAN | GENERATION | EVENT` and answered `400` on anything
else. That rejection was a safety net the OTLP wire removes, so core replaces it rather than
inheriting the gap (portal #217): `otlp.checked_observation_type` guards the wire boundary
and every event builder runs it, and `synth-authoring conformance` **blocks** on a type named
in a kit's sources that is not one of the ten. `CONTRACT.md` §"The spool" is the rule; this
is what it was measured against.

## What flipping a kit costs

The OTLP path mints one root observation per trace, so `count_spool`'s `observations` term —
and a deployment's measured billable volume — rises by the trace count. The *shape* of the
count is unchanged, so the plan-time estimate, the cap gate and the over-cap halt need no
code change; their numbers move on the commit that flips a kit.

Each kit also re-blesses its golden exactly once, and the diff is reviewed as data.

## Where the fleet stands

`synth-authoring new` emits a kit **on the OTLP path** (portal #207): its `seed` pins it in
one line, its `verify` reads the v4 APIs, and its blessed golden is a Spool of spans. The
three gold kits stay on `batch` until each is deliberately cut over (portal #210).

`synth-authoring conformance` reports the difference rather than leaving it to memory: a kit
whose sources still name a deprecated endpoint, or that carries no OTLP pin, gets an
**advisory** per site — reported in every mode, blocking in none, because every kit in the
fleet has some of this debt while the migration is in flight.

## Non-resumable imports, and how to recover

OTLP has no idempotent upsert. Three identical posts produced three copies of every
observation on a live project — where three identical batch posts produced one row. So an
OTLP `import-spool` **records that it ran** (a `.imported` file beside the Spool, on the
spool volume so it survives between job containers) and refuses a second attempt with
`NonResumableImportError` rather than silently doubling a demo's volume.

Recovery, when an import failed part-way:

1. **Clear that deployment's Langfuse data.** There is no partial repair — the project must
   go back to empty for this deployment.
2. Re-import from the top with `confirm_cleared=True`, or `SYNTH_IMPORT_CONFIRM_CLEARED=1`.

Re-running `generate-spool` is also a clean slate: a fresh Spool clears the record.

The record is written **before** the first POST, not after, and that is deliberate. A
request that fails after Langfuse accepted it is indistinguishable from one that never
arrived, so recording afterwards would let exactly that case retry and duplicate. The cost
is that an import which posted nothing is locked too — which is cheap, because
`generate-spool` is the step immediately before and re-running it clears the record.

Spool-side checkpoints and a read-back probe were both considered and declined — they add
machinery to preserve a property the platform no longer offers.
