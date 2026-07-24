# Ring 2 — the data-model middle field (EV; #33)

Ring 1 extracted the **byte-identical** core (RNG/ID, pricing, distributions, the Langfuse
client, event emission, UI primitives). Ring 2 moves the **middle field**: the
data-model-facing files whose delta between kits is *values, not logic*. The library is
**born from EV** (the simpler of the two gold-standard kits — no workbench/cert), so each
mover is EV's version, parametrized by explicit args (never by EV's `Config`, so the lib
stays decoupled from the kit's config *shape*).

This is the ledger the acceptance criterion asks for: **each candidate's move-or-fall-back
decision, with the reason.** The rule applied per file:

> A file **moves** iff, after parametrization, EV and Lender would call the *same lib code*
> differing only in *argument values*. It **falls back to the kit** iff the kits need
> *different code paths*, or the file would have to carry *vendor-approved scenario
> substance* across the seam.

## Movers (in the lib now, parametrized)

| File (lib module) | From | Parameter (the "value" delta) | Why it moved |
| --- | --- | --- | --- |
| `config.py` — `load_config` / `apply_overrides` / `set_dotted` | EV `synth/config.py` | a `model_factory` (`dict -> Config`) | Reading YAML + applying `--set dotted.key=value` is scenario-agnostic plumbing. The only per-kit thing is the pydantic model it validates into — passed in, so the lib never imports pydantic or knows the kit's config shape. |
| `seed/ingest.py` — `Ingestor`, `assert_demo_project`, `ensure_score_config` | EV `synth/seed/ingest.py` | `base_url`, keys, `chunk_size`, `project_hint`, score-config bodies | Batch ingestion speaks the Langfuse ingestion API in the abstract. EV's Cloud-hardened version is a strict superset of Lender's; the lib takes the hardened one. The event *bodies* are still composed by the kit from `seed.events` primitives. |
| `timegen.py` — `sample_timestamps` / `sample_in_range` / `hour_weight` / `window_start` / `day_anchor` / `in_window` / `now_utc` / `iso_date` | EV `synth/timegen.py` | the run inputs (`rng` / `run_date` / `window_days` / `n`) | Weighted hourly choice + intra-hour jitter is pure, scenario-agnostic time math. The diurnal/weekly weight curves are the canonical business-day priors (`DIURNAL` / `WEEKLY` module constants) — **not** threaded as per-call knobs: no kit passes an alternate, and one needing a different curve needs a different *algorithm* (see the Lender fall-back below), so a curve param would be speculative generality. |
| `lfread.py` — `auth_from_env` / `get_json` / `scores_path` / `get_all_scores` / `parse_ts` | EV `synth/verify.py` read-helpers | `base_url`, a Cloud `throttle` | The **verify split** (docs/SEAM.md): the auth + paginated GET of scores/traces is the read direction of "the machine that speaks the Langfuse data model." The `run_verify` **assertion body** stays in the kit. |
| `probe.py` — `run_backdate_probe`, `probe_ids` | EV `synth/probe.py` | target (`base_url`/`project_hint`/`seed`), `window_days`, cosmetic trace fields | The probe *flow* (ingest one backdated trace → poll it back → assert the timestamp survived) is scenario-agnostic; the probe trace is a throwaway diagnostic, not scenario substance. EV keeps a thin `synth/probe.py` adapter that maps its `Config` onto these params. |
| `http.py` — `request_retry` | EV `synth/http.py` | `throttle_s` (Cloud spacing) | Retry-After-aware REST resilience — a supporting primitive the ingest + read-client movers both need. Scenario-agnostic; retrying the machine's own REST calls is part of the machine. |

`PyYAML` became a **runtime** dependency of the lib (the config loader parses YAML at
seed/plan/verify time). `jsonschema` remains the sole `[authoring]`-only marker, so the
runtime/authoring boundary test is unaffected.

## Tie-break outcomes (fall-backs)

**No EV file was forced back to the kit.** Every EV candidate is scenario-agnostic — none
imports EV scenario code (`agent` / `content` / `models`), and the two `verify` halves split
cleanly at the read/assert seam. The tie-break's *fall-back* cases were anticipated on the
**Lender** side and are now **ratified in #34** (see *Ring 2 — Lender (#34)* below); they are
recorded here so the lib's EV-born surface stays additive rather than being retrofitted:

- **`timegen` (Lender).** Lender samples **sessions-per-day × log-normal turns** (volume is
  *derived*, not a forced count) via `sample_session_times`, and its `hour_weight` carries a
  timezone offset + a Friday-afternoon decline. That is a *different algorithm*, not a
  re-parametrization of EV's draw-N-over-a-window sampler. Under the toolbox model it becomes
  an **additional** lib function in #34 (or stays in Lender) — it does not re-shape the EV
  mover.
- **`probe` (Lender).** Lender's probe builds its trace with its **scenario** trace-builder
  (`build_trace_events` + `flagged_cases` + `answer_deterministic`) — vendor-approved
  scenario substance. Routing that through a shared probe would drag scenario content across
  the seam, so Lender's probe **falls back to the kit** in #34. EV's probe (generic
  diagnostic trace) moved cleanly.
- **`verify` read-client (Lender).** Lender's read helpers do the same auth + paginated GET
  but with a different inline retry loop (no shared `request_retry`, no Cloud throttle). The
  *pagination shape* is the same, so #34 can adopt `lfread` (a benign retry upgrade) — a
  re-parametrization, not a fall-back — but that is #34's decision to ratify against Lender's
  Step-0 oracle.

## The canonical `target_traces` knob (EV)

EV's bespoke `generation.total_traces` **operator** knob is replaced by the canonical
`generation.target_traces` (the shape `inject_target_traces(minimum=100, maximum=6000,
default=800)` emits). `total_traces` survives as an **internal** config field only. EV's
kit-side **direct-count** derivation hook (`config.direct_count_derivation`,
`target_traces -> {"total_traces": N}`) runs at config-load (`resolve_target_traces`), so the
portal stays zero-code: it passes `--set generation.target_traces=N` verbatim.

Proven: the full-payload golden gate is byte-identical when `target_traces` is turned through
the hook (the golden adapter routes the real `--set` → hook → internal path); the split
`verify` yields identical assertions against a canned seeded env; and no EV scenario logic
changed.

# Ring 2 — Lender (#34): ratified outcomes

Lender is the harder kit — it has genuine extra phases EV lacks (certification suite,
seeded experiment runs, the review queue, a live `certify`, the workbench) — so it is the
real test of whether the EV-born surface refuses to force scenario logic. It does. Moving
Lender onto the Ring 2 surface, **each candidate resolved exactly as the tie-break predicted**,
proven byte-for-byte against Lender's Step-0 oracle:

## Movers (Lender now consumes the lib)

| File (lib module) | Outcome | Proof |
| --- | --- | --- |
| `config.load_config` / `apply_overrides` | **Moved.** Lender deletes its local loader and passes `Config.model_validate` as the `model_factory`; its pydantic schema stays in the kit. | Golden byte-identical; `--set` coercion test green. |
| `seed.ingest` (`Ingestor`, `assert_demo_project`, `ensure_score_config`) | **Moved.** Lender's local copy deleted; the four import sites repoint to the lib. The lib's Cloud-hardened `Ingestor` is a strict superset — its **spool serialization is byte-identical** (`json.dumps(…, separators=(",", ":"))`), only the network `_post_chunk` gained Retry-After handling. | Golden gate (`dry_run`, no network) byte-identical; the write path the golden exercises is unchanged. |
| `lfread` + `http.request_retry` (the `verify` read-client) | **Moved.** `verify._auth/_get/_get_scores` rebind to `auth_from_env` / `get_json` / `get_all_scores`; `run_verify` assertions are byte-unchanged. The one raw-response existence helper (`_get_resp`, tolerate-404) stays kit-side but rides the shared `request_retry`. | `test_verify_split` proves the split yields identical assertions on a canned seeded env. |

## Fall-backs (stayed in the kit — the seam refused to force logic)

- **`timegen` (whole module) — falls back.** The tie-break called `sample_session_times` a
  *different algorithm*; migration confirmed the entanglement runs deeper: Lender's
  `hour_weight` carries a Berlin timezone offset **and a Friday-afternoon `×0.5` decline**, and
  its `sample_timestamps` / `sample_in_range` *close over that `hour_weight`*. So even the
  helpers that are line-for-line identical to the lib's would silently change the sampled
  timestamps if swapped (they'd bind the lib's plain `DIURNAL[dt.hour]` curve). The module is
  scenario-entwined as a unit; extracting the four incidental generic helpers to dedup them
  would fragment it for a line-count saving the SEAM's T2 verdict (flexibility > deduplication)
  explicitly declines. **The golden gate is the proof:** it stays byte-identical precisely
  because `timegen` did not move.
- **`probe` (Lender) — falls back.** Unchanged from the anticipation: Lender's probe builds its
  trace from scenario substance (`build_trace_events` + `flagged_cases`), so routing it through
  a shared probe would drag scenario content across the seam. Its one `seed.ingest` import
  repoints to the lib; the probe body stays in the kit.

## The canonical `target_traces` knob (Lender: derive-scale)

Lender's bespoke `generation.volume.scale` **operator** knob is replaced by the canonical
`generation.target_traces` (the shape `inject_target_traces(minimum=1000, maximum=15000,
default=5000)` emits). `volume.scale` survives as an **internal** config field only. Unlike
EV's identity direct-count, Lender has **no absolute trace-count knob** — total traces are
session-*derived* — so its kit-side **derive-scale** hook (`config.derive_scale_derivation`,
`target_traces -> {"volume.scale": target_traces / 10111}`) divides by the reference yield
(~10,111 traces at `scale = 1.0`, seed 47) and runs at config-load (`resolve_target_traces`).
`target_traces` is therefore an **advisory** volume dial (monotone, floor-bound by per-weekday
session rounding), never an exact count — consistent with "traces are DERIVED, not forced".
Crucially, `volume.scale` drives **only** ambient session volume: the certification suite,
seeded experiment runs, and review queue are config-sized and stay **unscaled**.

Proven: the full-payload golden gate is byte-identical when `target_traces=150` is turned
through the hook (the golden adapter routes the real `--set generation.target_traces=` → hook
→ `volume.scale` path, replacing the Step-0 stub's bespoke assignment); the split `verify`
yields identical assertions against a canned seeded env; and no Lender scenario logic changed
(the certification/`cert_runs`/`certify` phases and the entire `workbench/` framework were
**not** extracted — extracting a framework from a single example is speculative generality).

## The `synth` CLI-name collision (resolved)

A kit installs `langfuse-synth-core`, so both console scripts land on the same PATH. There is
**no collision**: the lib's authoring CLI is namespaced **`synth-authoring`** (not `synth`),
while the kit's runtime CLI keeps the bare **`synth`** (the portal integration surface —
every manifest pipeline runs `synth <verb> --config {config}`). Wiring EV to the lib in #33
confirmed the two entry points coexist; the spec's "one unified command" option was not
needed because the namespacing already keeps them apart.
