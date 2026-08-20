# The read seam and the live-emission seam

Langfuse Cloud becomes **v4-only on 2026-11-16**. `docs/WRITE_PATHS.md` covers the Spool's
write path; these are the other two conversations core has with Langfuse (portal #208,
spec H #204):

| Seam | Module | Timestamps | Transport | Golden-gated |
| ---- | ------ | ---------- | --------- | ------------ |
| Spool write | `seed.events` + `seed.ingest` | producer-supplied, **backdated** | raw OTLP / batch | yes |
| **Read** | `read` | — | raw REST, v4 or deprecated | no |
| **Live emission** | `live.emit` | **wall clock** | Langfuse **SDK** | no, by design |

---

## The read seam — `langfuse_synth_core.read`

Every kit's `verify` reads Langfuse over HTTP today, so the v3→v4 read remap would
otherwise be done three times and drift three ways. It is done here once, and what a kit
gets back is the same normalised row whichever API generation answered.

### What moved

| Read | Deprecated (v3-era) | v4 |
| ---- | ------------------- | -- |
| a trace | `GET /api/public/traces/{id}` | `GET /api/public/v2/observations?traceId=` — **assembled**, there is no trace entity |
| the trace list | `GET /api/public/traces` | derived from `/v2/observations` (a sample, never a total) |
| observations | `GET /api/public/observations` | `GET /api/public/v2/observations` |
| a session | `GET /api/public/sessions/{id}` | `/v2/observations?sessionId=` |
| scores | `GET /api/public/v2/scores` | `GET /api/public/v3/scores` |
| dataset runs | `GET /api/public/datasets/{name}/runs[/{run}]` | `GET /api/public/experiments` → `/api/public/experiment-items` |

Not remapped, because they were never deprecated and the seam does not model them:
`/api/public/projects`, `/datasets`, `/dataset-items`, `/score-configs`, `/v2/prompts`,
`/annotation-queues`, `/health`, `/models`. Read those with `lfread.get_json` (or
`adapter.read_json`).

### Which generation answers

Probed, not configured: one deprecated endpoint is called once per reader, and a `404`
means the target has cut over. `SYNTH_LANGFUSE_READ_API=legacy|v4` pins it for a test or an
operator.

**Legacy is preferred while it lives.** The v4 read APIs serve data written by an exporter
that does not send `x-langfuse-ingestion-version: 4` with a delay of up to 15 minutes, and
every Spool written on the batch path is exactly that data (measured on Cloud, 2026-08-19:
legacy 21.7s, v4 still nothing at 5 minutes — portal #205). Preferring v4 before a kit's
write path has moved would read a demo back as half-empty.

### What normalisation actually undoes

* **The v3 scores shape.** One typed `value` (float for `NUMERIC`/`BOOLEAN`, a label for
  `CATEGORICAL`/`TEXT`/`CORRECTION`) where v2 had `value` **plus** `stringValue`; and a
  discriminated `subject` object where v2 had flat `traceId` / `observationId` /
  `sessionId` / `datasetRunId` columns. `Score` carries `numeric_value`, `string_value` and
  all four flattened subjects, so an assertion reads the same on both.
* **Cursor pagination.** The v4 APIs answer `meta.cursor` (base64url) and **no total**; the
  deprecated ones answer `meta.totalPages`. Both are followed to the end here.
* **Renamed observation columns.** `providedModelName` → `model`, `totalCost` →
  `total_cost` (legacy's `calculatedTotalCost`), and the io / model / usage / prompt columns
  are opt-in **field groups** on v4 — the seam always asks for them, so a row never comes
  back with silent holes.
* **The missing trace.** Under v4 a trace is the set of observations sharing an id: its
  name, user, session and tags are attributes copied onto every span, and its overall input
  and output live on the **root** observation. `reader.trace(id)` assembles that.
* **Trace-level attributes on legacy observations.** Legacy keeps user/session/tags on the
  trace body, so the seam pushes them down onto the observations it returns.

### Five things the live API does that its own generated types do not say

Each was found by running `examples/v4_seam_check.py` against a real Cloud project on
2026-08-19, and each would have broken a `verify` on a cut-over target:

1. **v4 returns `input` / `output` as raw JSON strings.** The deprecated endpoints returned
   parsed objects, and `parseIoAsJson=true` is now **rejected** — `400`, "no longer
   supported on the v2 observations endpoint. Input/output fields are always returned as
   raw strings." Every kit assertion that indexes into a chat-shaped input depends on the
   seam decoding them, which it does (a string that is not JSON stays that string).
2. **The model column is `model`.** The generated SDK type documents `providedModelName`;
   the wire sends `model` (plus `modelId` / `internalModelId`). The seam reads either.
3. **Unset string columns come back as `""`, not `null`** — `promptName`, `version`,
   `traceName`, `statusMessage`. "Is this generation prompt-linked?" must be a falsy
   *absence*, so the seam normalises empty to `None`.
4. **A categorical score carries `value: 0` on the deprecated API**, beside its
   `stringValue`. Read as a number it would drag any mean a kit computes toward zero, so a
   string-typed score reports no numeric value at all.
5. **Spans and scores become readable at different times.** They travel different paths —
   spans over OTLP, scores as `score-create` envelopes on the legacy ingestion endpoint —
   and on Cloud the spans were queryable ~11s after emission while the scores needed ~30s
   more. Anything that reads back a freshly written story must wait for the *whole* story,
   not the first part of it to arrive.

### One place the generations genuinely differ, and it is visible

`reader.traces()` is a **sample**, not a project total. There is no trace list to page under
v4, so the seam groups observations by trace id; ask `reader.trace(id)` for one trace's
complete set. Counting a *whole project* is a different problem with a different answer —
the Metrics API — and it belongs to the portal (#205), which is metered for it.

### Which host is this, anyway — `langfuse_synth_core.target`

`TargetProfile.detect(url)` answers the free question (is this Cloud? then space the
one-at-a-time REST calls out) without a request. `.resolved()` answers the paid one by
asking the target through the seam's probe, and hands back a profile that knows its
generation — `is_v4`, and a `label` that says so in the `verify` log. The host name cannot
answer it: Cloud cuts over on 2026-11-16 and a self-hosted target whenever its operator
upgrades. `profile.reader()` then builds a reader that inherits both, so nothing probes
twice.

### Retired

`lfread.scores_path()` — it probed `/api/public/v2/scores` and fell back to
`/api/public/scores`. v4 `404`s both, so the question no longer has a right answer.

`lfread.get_all_scores()` — the **compatibility front** that rendered the seam's rows back
into the legacy `value` / `stringValue` / `traceId` dict shape, so a kit that had not been
rewired kept working on either generation. All three kits read `reader.scores(...)` as of
#211, so it retired with its last caller in **core v3.0.0**, and the legacy row shape —
including a categorical score reporting `value: 0` beside its label — is gone from the
codebase. Read a label with `Score.string_value`.

What is left in `lfread` is auth, one authenticated GET, and timestamp parsing: the way to
read the endpoints the migration left alone without losing the Retry-After-aware backoff.

---

## The live-emission seam — `langfuse_synth_core.live.emit`

Companion Apps, playground submissions, workbench runs and experiment tasks emit at *now*.
They currently borrow the Spool's builders and its `Ingestor`, which couples a live surface
to machinery whose entire purpose is backdating.

```python
emitter = adapter.emitter()                       # or LiveEmitter.from_env(base_url)
with emitter.trace("playground_submission", user_id="playground_user",
                   session_id=session, tags=["playground"], input=application) as trace:
    with trace.generation("decision", model=model, input=messages, prompt=prompt) as gen:
        result = llm.complete(...)
        gen.update(output=result.text,
                   usage={"input": result.input_tokens, "output": result.output_tokens})
    trace.update(output=decision)
link = trace_url(trace.id)
emitter.score("user_disagreement", 1, trace_id=trace.id, data_type="BOOLEAN")
```

* **The SDK is the right answer here and the wrong one for the Spool.** It stamps wall
  clock and has no start-time parameter — disqualifying for weeks of backdated history,
  exactly right for a live submission — and it brings context propagation, nesting through
  real parent span context, and the v4 ingestion header for free.
* **It takes no timestamp.** Deliberately. A live emitter that accepted one would be a
  second, unblessed backdating path.
* **Trace attributes are propagated, not set on a trace.** There is no trace body under v4,
  so `trace()` opens the root observation *and* propagates name / user / session / tags /
  environment onto everything nested inside it. Overall input and output go on the root
  observation; the deprecated trace-IO helpers are forbidden.
* **Scores stay on their own path.** `emitter.score(...)` rides the SDK's `create_score`,
  which posts a `score-create` envelope to the legacy ingestion endpoint — the one envelope
  type that survives the cutover. Same decision as the Spool's `score_event`.
* **Flush is delivery, not readability.** The block flushes on exit so the trace is on its
  way before the surface answers its user; Langfuse's ingestion is asynchronous, so an
  immediate read-back may still miss it.
* **It sends the real-time ingestion header.** `x-langfuse-ingestion-version: 4`, on the
  SDK client's OTLP exporter — the same header the Spool's writer sends. Without it a v4
  target processes the spans on the slow path and the trace is unreadable for up to fifteen
  minutes, which for a surface that answers with a deep link means answering with a link to
  nothing. The constant lives in `langfuse_synth_core.ingestion`, above the determinism
  line, because both writers send it and neither may import the other. Reaching the
  exporter through `additional_headers` is why the SDK floor is a deliberate pin.

### The determinism line

Nothing on this seam enters the Spool, and nothing here is byte-compared against a golden.
`tests/test_determinism_line.py` enforces both directions: the live seam imports nothing
from `seed`, and the Spool's modules import no Langfuse SDK. The companion smoke keeps
proving that live traces land; it gains no golden coverage.

---

## Verified against a real project

Both seams were exercised against a real Langfuse Cloud project on **2026-08-19** with
`examples/v4_seam_check.py`. Mocked tests do not prove backend behaviour; that is why the
check exists. What that run established:

- One trace emitted through the live seam — root observation, nested span, nested
  generation with model / usage / cost, a numeric trace score and a categorical
  observation score — read back **identically on both arms**, attribute for attribute.
- A two-item dataset with one experiment run (`SEAM_CHECK_CREATE_EXPERIMENT=1`) read back
  through the **Experiments API** on v4 and through `/datasets/{name}/runs` on legacy: the
  run listed and its items carrying their traces on both.
- The five wire behaviours above, each of which the seam now handles.

**What it does not establish.** Cloud still dual-serves both generations, so the v4 arm was
reached by pinning `read_api="v4"` rather than by a project that has actually cut over. The
*resolution* step — a deprecated endpoint answering `404` and the reader switching arms by
itself — is unit-tested, not observed on a cut-over server, and no such project exists to
test against yet (the research notes carry the same gap). That is the one piece of this
seam that only 2026-11-16, or a self-hosted v4 server, can prove.
