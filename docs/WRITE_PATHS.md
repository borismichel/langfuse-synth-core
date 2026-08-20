# How the Spool is written

Langfuse platform v4 makes the **observation** the primary entity — there is no separately
ingested trace — and Langfuse Cloud removes the legacy batch-ingestion API on
**2026-11-16**. The Spool is written against that model, and there is nothing to select:

| | What the Spool writes |
| --- | --- |
| Observations | one **OTLP span** each, posted to `/api/public/otel/v1/traces` |
| Traces | no trace entity — core mints the trace's **root observation** |
| Nesting | OTLP parent span context |
| Scores | a `score-create` envelope on `POST /api/public/ingestion` |
| Re-import | **appends — non-resumable** |

Core spoke two wires for the length of the v4 migration, selected by a
`set_spool_write_path` flag, so kits could cut over one at a time (portal #206 → #210). The
fleet is across; #213 removed the batch path and the flag with it. This file is the record
of what that migration settled — the file name is unchanged because it is cited from the
Contract, the authoring skill and three kit repos.

## Scores are not the last legacy call

They look like one, which is why this section exists. Langfuse's
[deprecated-API migration guide](https://langfuse.com/faq/all/deprecated-api-migration)
states that the ingestion deprecation **"applies only to trace and observation events"**, and
that `score-create` events on `POST /ingestion` are **not deprecated, remain supported after
the v4 cutover, and require no client change**. What *was* deprecated on the score side is
reading — `GET /scores` and `/v2/scores` → `GET /v3/scores` — and the read seam moved onto
that in portal #211.

So the split — observations over OTLP, scores as `score-create` — **is the target
architecture**, not unfinished business. Portal #225 asked the question and closed it as no
change needed on 2026-08-20. Do not "tidy" the score path away, and do not describe it as an
exception.

`synth-authoring conformance` enforces exactly this distinction: it flags a trace or
observation envelope type posted to `/api/public/ingestion`, and says nothing about a
`score-create` one.

## Every write carries `x-langfuse-ingestion-version: 4`

This is **not** a latency optimisation, and mistaking it for one is expensive. Langfuse
processes exported spans on two paths. Without the header a v4 target files the write on the
legacy read path, where it is **invisible to every v4 query endpoint and every v4 dashboard**
— while the legacy endpoints answer it perfectly happily. The failure mode is a deploy that
looks healthy, verifies green against a legacy read, and shows an empty project to anyone
looking at it through v4. Observed on Cloud, 2026-08-20.

Both of the depot's writers say it, and they sit on opposite sides of the determinism line:
the Spool's exporter (`seed/ingest.py`, backdated, golden-gated) and the live-emission seam's
SDK client (`live/emit.py`, wall-clock, outside the gate). Neither may import the other, so
the constant lives above both in `langfuse_synth_core.ingestion` —
`tests/test_ingestion_version_header.py` walks every writer and asserts it.

## Why raw OTLP and not the Langfuse SDK

The SDK stamps wall-clock and exposes no start-time parameter. A Spool is *by definition*
weeks of backdated history, so the SDK would collapse every demo onto the deploy date.
Raw OTLP takes producer-supplied nanosecond timestamps **and** producer-minted ids — core's
BLAKE2b ids are already exactly the 32-hex trace / 16-hex span widths OTLP accepts.

Langfuse's own migration guidance points Python projects at the SDK. That guidance is wrong
for this seam and the deviation is deliberate: **do not "correct" it later.** Live,
wall-clock surfaces (companion apps, playground, workbench) are a different seam and do use
the SDK.

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
else. That rejection was a safety net the OTLP wire removes, so core supplies it instead
(portal #217) — `CONTRACT.md` §"The spool" carries the rule and where it is enforced. The
vocabulary lives in `langfuse_synth_core.observation_types`, above both seams, because both
put a value on the same attribute: this one through the span builder, the live one through
the SDK's `as_type`. The table above is what the rule was measured against.

## What the counts mean

Core mints one root observation per trace, so `count_spool`'s `observations` term includes
one span per trace that no kit built. The trace term is **derived** from distinct trace ids
and is reported in the breakdown but excluded from the billable `total` (portal #220): under
v4 a trace is a *view* over its minted root, not a separately ingested object, so counting it
would bill the same object twice.

That is also why the fleet's cutover moved no deployment's measured volume — the minted roots
raised `observations` by exactly the trace count the retired `trace-create` term dropped. The
plan-time estimate, the cap gate and the over-cap halt needed no code change.

## Non-resumable imports, and how to recover

OTLP has no idempotent upsert. Three identical posts produced three copies of every
observation on a live project — where three identical batch posts produced one row. So
`import-spool` **records that it ran** (a `.imported` file beside the Spool, on the spool
volume so it survives between job containers) and refuses a second attempt with
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

A Spool carrying nothing but scores is exempt: `score-create` envelopes carry deterministic
ids and upsert, so there is nothing for a re-post to duplicate.

Spool-side checkpoints and a read-back probe were both considered and declined — they add
machinery to preserve a property the platform no longer offers.

**Determinism of the *file* is untouched.** `seed + target_traces + declared params →
byte-identical Spool` is the same law it always was, proven offline by the golden gate before
anything is uploaded. What the migration removed is the *replay* guarantee, which was a
property of the old transport rather than of any kit's code.
