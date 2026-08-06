# The Contract

This is the single, versioned home of the **Demo Depot Contract** — the agreement a demo
kit and the portal share so that adding use case #3..N requires **zero portal code
changes**. Everything the portal needs to catalog, deploy, and operate a kit is declared
in the kit's root `usecase.yaml`, validated against the JSON Schema shipped alongside this
file (`src/langfuse_synth_core/authoring/usecase.schema.json`).

The Contract has three parts, all versioned together in this repo:

1. **The JSON Schema** — the machine-checkable shape of `usecase.yaml` (schema version 1).
2. **The validator** — `langfuse_synth_core.authoring.validate`, exposed offline as
   `synth-authoring validate <path>` and importable by the portal's `POST /use-cases/sync`. It is a
   **strict superset** of the portal's historical `tools/validate_manifest.py`: it
   reproduces every Draft7 schema error and every LLM-provider semantic rule, then adds
   the kit-authoring checks below.
3. **This document** — everything that is policy rather than shape: the reserved-verb
   semantics, the container invocation, the environment and filesystem contracts, the
   live-surface rules, and the per-run anchors concept.

> "Passes `synth-authoring validate` locally" ≡ "passes portal sync" **by construction** — the
> author's offline lint and the portal's admission gate run the same code and the same
> schema.

**How this document is versioned.** The Contract rides the library release: a
`vX.Y.Z` tag pins schema, validator, and this document together, and the portal enforces
exactly one pinned version (`api/app/contract_pin.py` reads the pin in the portal's
`api/pyproject.toml`). **Both sides cite this document instead of asserting their own
copy** (portal #196): portal container-spec docstrings and kit/scaffold docstrings
reference a section here rather than restating the rule in their own words. The recurring
bug class this kills is "the two sides disagreed about an implicit contract" — `--set`
passed to a live command that cannot parse it (LAN-165), kits ignoring
`LANGFUSE_BASE_URL` (portal #187), a kit assuming a spool mount the portal deliberately
didn't provide (portal #193).

---

## Reserved-verb semantics (the pipeline)

`pipeline` is an ordered list of steps; each step becomes one Job. A step's `id` is not
just a label — it selects the **job kind** the portal runs it as:

| Reserved verb (`id`) | Job kind    | Meaning                                                        |
| -------------------- | ----------- | -------------------------------------------------------------- |
| `probe`              | `probe`     | Pre-flight reachability / credential check before real work.   |
| `plan`               | `plan`      | Dry-run that prints the projected volume (parsed into a gate). |
| `seed`               | `seed`      | Deterministic backdated ingestion — the byte-identical spool.  |
| `verify`             | `verify`    | Post-ingestion read-back assertions against the seeded env.    |
| `resume`             | `resume`    | Resume a partially-completed run.                              |
| `teardown`           | `teardown`  | Project-level teardown / cleanup.                              |

Any **other** `id` (e.g. `evaluators`, `memo`) maps to `kind=custom_step`. The portal
never assumes a built-in step exists; a kit that has no `probe` simply omits it.

Two step fields spawn **portal-synthesized** job kinds that are never authored as ids:
a step that declares `generate:` is split into `generate-spool` (materialize the Spool,
no Langfuse writes) followed by `import-spool` (replay the Spool via the step's
`resumable` command) with the portal's billable-volume cap gate between them (Spec D);
`resumable:` also backs the `resume` flow. A `generate` declaration **requires**
`resumable`.

Rules the validator enforces:

- **`seed` + `verify` are mandatory.** They are the determinism-and-read-back spine every
  spec-compliant kit wires: `seed` produces the deterministic spool, `verify` proves what
  landed. A manifest missing either fails validation.
- **A reserved-verb step must run its verb.** If a step's `id` is a reserved verb, its
  `run` command must invoke `synth <verb>` (e.g. the `seed` step runs `synth seed …`). A
  step named `seed` that actually runs `synth teardown` is a contract violation — the
  reserved id would mislead the portal about the job's kind.

Whether a command receives `--set` overrides depends on the invocation class, not on the
verb — see "The container invocation" below.

---

## The container invocation

A kit image ships **no `ENTRYPOINT`/`CMD`**. The portal supplies the full command at
container-create time, from the manifest, with exactly one placeholder substituted:
`{config}` becomes the resolved `--config` path (`base_config.default`, or the matching
per-host-kind entry). There is no other templating.

Which invocations carry `--set dotted.key=value` overrides is fixed:

| Invocation class                                  | Command source                     | `--set` appended? |
| ------------------------------------------------- | ---------------------------------- | ----------------- |
| Forward pipeline step (`probe`/`plan`/`seed`/`verify`/custom) | the step's `run`       | **yes**           |
| `generate-spool` (synthesized)                    | the step's `generate`              | **yes**           |
| `import-spool` / `resume` (synthesized)           | the step's `resumable`, verbatim   | **never** (LAN-277) |
| Live component                                    | `live_components[].command`        | **never** (LAN-165) |

The two "never" rows are law, written down because each was once broken in production:
appending `--set` to a live command killed every UI deployment's live asset on argv parse
(LAN-165), and appending it to `resume` killed every resume on `No such option: --set`
(LAN-277). A live command and a resume command are **different kit entrypoints** from a
pipeline step; the manifest's command string is authoritative and only `{config}` is
templated. Runtime configuration of a live component rides **env only** (LAN-173) — see
the environment contract.

Override mechanics, for the classes that do receive them: flags are appended as
`--set key=value` pairs, keys taken **verbatim** from `config_schema` property names (no
portal-side mapping table — the zero-code invariant), values YAML-coerced by the kit's
loader (`800`→int, `true`→bool, `1.5`→float; booleans are emitted lowercased). Kits get
this for free from `langfuse_synth_core.config.load_config`.

The **companion invocation** is its own fixed shape:
`synth companion --config {config} --host 0.0.0.0 --port <port>` (verb name is
kit-chosen; EV/Lender use `playground`). The adapter's `parse_invocation` accepts exactly
`--config`/`--host`/`--port` — an unexpected flag is a contract violation and argparse
rejects it (exit 2). This is the D3 hard edge that surfaces a stray pipeline `--set`
immediately instead of mid-demo.

---

## The environment contract

What the portal injects, by container class. A kit must not depend on any env var not
listed here, and must tolerate every listed one.

**Every kit container (job and live):**

| Var | Meaning |
| --- | ------- |
| `LANGFUSE_BASE_URL` | The deployment's Langfuse target. **Overrides** the committed config value — see "Retargeting" below. |
| `SYNTH_STATE_DIR` | Always `/app/.synth_spool` — the spool mount. Kits resolve their state/spool location from this var, never hardcode it. |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` (+ lowercase twins) | Present when the depot runs an egress proxy; the kit's HTTP stack must honor them (httpx does by default). |

**Job containers additionally:**

| Var | Meaning |
| --- | ------- |
| `PORTAL_JOB_ID` | The portal job row this container executes. |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | The step-declared project credentials — the **only** secrets a pipeline step may declare. |

**Pipeline steps never receive a provider LLM key.** Seed-family steps
(`seed`/`generate-spool`/`import-spool`/`resume`) have every provider key and the
`LLM_API_KEY` sentinel defensively stripped: `generate` materializes the deterministic
Spool with no LLM at runtime, and `import` replays bytes. Only `live_components` may
declare an LLM secret.

**Live containers additionally:**

| Var | Meaning |
| --- | ------- |
| `PORTAL_LIVE_INSTANCE_ID` | The live-instance identity. |
| `LIVE_BASE_PATH` | `/live/{instance_id}` — the public path prefix. Routes stay mounted at `/` (the proxy strips the prefix); the kit uses this var **only** to render internal hrefs/redirects (`langfuse_synth_core.live.paths.local`). Never for external URLs. |
| declared `requires_secrets` | The Langfuse pair, plus the selected provider's key under its **canonical** name (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`). The manifest's `LLM_API_KEY` sentinel is resolved portal-side; no container ever carries a literal `LLM_API_KEY` env. |
| `LLM_PROVIDER` / `LLM_MODEL` | Which provider/model the deployment selected (sentinel components only). Env-only by law (LAN-173); read by `langfuse_synth_core.companion.llm`. A pinned `LLM_MODEL` outranks any per-request model choice. |
| `ANTHROPIC_METADATA_USER_ID` | Attribution id, when the deployment has one; the kit passes it through as `metadata.user_id` on LLM calls. |

**Owned by the kit image, not injected:** `SYNTH_OUT_DIR=/app/out` (the artifact dir —
container-local by design; run state must never live there, LAN-276), `WORKDIR /app`, and
the runtime uid `10001` (see filesystem conventions).

---

## Filesystem conventions

- **`usecase.yaml` at the repo root** is the ONLY integration surface between a kit and
  the portal. It is committed verbatim to each kit's repository root.
- **Config files** live in the kit repo and are named by `base_config` (a `default` path,
  plus optional per-host-kind entries). The portal writes the chosen file as `--config`;
  in v1 there is no portal-side YAML templating — overrides ride `--set` on top of the
  chosen base.
- **`config_schema`** is JSON Schema for the deploy wizard. Each property **name** is a
  dotted kit-config key passed verbatim as `--set <name>=<value>` — there is no
  portal-side mapping table, which is what preserves the zero-code invariant.
- **Artifacts** are files the worker collects from the container's `/app/out/` directory
  after the producing step exits; each `path` is relative to that artifact dir. At least
  **one** artifact must declare `render: markdown` — the Presenter Runbook the operator
  reads to walk the demo. `/app/out` is **container-local**: it is lifted from the exited
  container, never shared between steps, and never mounted into live surfaces.
- **`assets.docs` / `screenshots`** paths are repo-relative and rendered in-portal.
- **Runtime write paths must be writable by uid 10001.** Job containers run as
  `JOB_RUN_USER=10001:10001`, never as the image's build user, and a plain `COPY . .`
  lands root-owned. The image must therefore create and chown the directories the kit
  writes at runtime: **`/app/out/`** (the artifact-collection dir above — contract) and
  **`/app/.synth_spool/`** (the spool mount point below). The reference Dockerfiles do
  this with `RUN mkdir -p /app/out /app/.synth_spool && chown -R synth:synth /app` before
  the `USER` drop; a kit that skips it dies on `open_spool()` at its first deployment
  (portal #189).

### The spool

One named volume per deployment — `spool-{deployment_id}` — is mounted at
**`/app/.synth_spool`** in every container of that deployment, and `SYNTH_STATE_DIR`
always points at it. Mount mode is the whole access-control story:

- **Job steps mount it writable.** This is what makes the seed split and resume work
  across containers: `generate-spool` materializes `events.ndjson` (the Spool — NDJSON,
  one ingestion envelope per line) and `import-spool` replays those exact bytes from a
  different container.
- **Live surfaces and the portal's cap-gate count container mount it read-only**
  (portal #193 / portal PR #194, and Spec D's measured verdict respectively). A live
  surface reads run state; it never writes it — **job steps are the only writers**. A kit
  whose live page tries to write under `SYNTH_STATE_DIR` fails on the read-only mount, by
  design.

The Spool's billable volume is **measured, not trusted**: the portal counts the actual
bytes on the volume (`langfuse_synth_core.seed.count.count_spool`) before `import-spool`
may upload them.

---

## Per-run anchors (opt-in)

Recorded decisions (epic portal #195, 2026-08-06): **per-run anchors are a contract
concept, opt-in per kit** (decision 1); **the anchors mechanism lives once in core**
(decision 2); **transport is the read-only spool mount into live surfaces** (decision 3).

**What anchors are.** A seed run makes choices no other party can reconstruct — the run
date, the resolved Langfuse project id, example trace ids, prompt versions, headline
figures. Anchors are those facts, written once by `synth seed` so every later reader
(`verify`, `script`, the kit's live pages, the Presenter Runbook) agrees with the data
actually in Langfuse: date-coherent dashboards, deep links that resolve, numbers that
match.

**The file.** `.synth_state.json` in `SYNTH_STATE_DIR` — i.e. on the spool volume at
`/app/.synth_spool/.synth_state.json`, beside `events.ndjson`. It must live on the spool
(the only cross-container surface); the artifact dir is container-local and would strand
it (LAN-276). The payload is **opaque kit territory**: the portal transports the file and
never parses it; its schema is whatever the kit's seed and readers agree on.

**Transport.** The read-only spool mount above is the entire mechanism. The portal stays
a pure transport layer — no anchor-aware env injection, no portal-side anchor schema.
`seed` (a job step, writable mount) writes the file; live surfaces (read-only mount) read
it. **Live surfaces never write anchors** — a companion that wants to persist state back
into the spool is out of contract (see migration debt).

**Opt-in.** A kit that emits anchors writes the file; a kit that doesn't, doesn't — there
is no manifest flag, and the mount is unconditional, so a stateless kit simply sees no
state file. **Console-style companions may stay stateless by design** (the support kit's
console derives its scene from config + adapter and reads no run state); statelessness is
a legitimate contract citizen, not a gap.

**Mechanism.** The read/write plumbing ships **once, in this library** —
`langfuse_synth_core.anchors` (portal #199): the canonical filename, the location
resolved from `SYNTH_STATE_DIR` at call time, and the `AnchorsIO` mixin a kit's payload
dataclass inherits `save`/`load`/`exists` from (`load` tolerates unknown keys, so an
older state file survives a payload change). The anchor *fields* stay kit-owned. EV and
Lender consume this mechanism (their formerly diverged kit-local copies are retired), and
the scaffold hands it to future kits behind `synth-authoring new --anchors`.

---

## Retargeting: `LANGFUSE_BASE_URL` overrides the config file

A kit ships **one** config and the portal points it at whatever Langfuse a deployment targets,
by injecting **`LANGFUSE_BASE_URL`** into the container. So:

- **`cfg.target.base_url` MUST let `LANGFUSE_BASE_URL` win** over whatever the committed config
  file says. The idiomatic shape is a `host` field with `base_url` as a property over it:
  `os.environ.get("LANGFUSE_BASE_URL", self.host).rstrip("/")`.
- **With the var absent, the committed value MUST still apply.** The env var *overrides* the
  file; it does not replace it. Otherwise the kit only works inside the portal — the author's
  laptop, the determinism golden gate, and every offline run resolve nothing.

This is a *behavioural* requirement, so the `Target` Protocol cannot express it: a kit with a
plain `base_url` field satisfies the shape and is still undeployable. It is gated instead —
every scaffolded kit carries `tests/test_retargeting.py`, one call into
`langfuse_synth_core.authoring.retarget.assert_retargetable`, which injects a probe base URL and
asserts it won.

The gate's limit, stated so it is not mistaken for a deployability proof: it checks that the kit
**resolves** the injected base URL, not that every seam dials the resolved value. A kit that
resolved `base_url` correctly and then built a client from a literal would pass it. The
scaffolded `seed` / `verify` both read `cfg.target.base_url`, so the remaining gap is narrow —
a real portal deployment is still the only end-to-end proof.

Worth stating why this needed its own gate: **every other authoring gate configures by file
while the portal configures by env.** `validate` lints the manifest, the golden gate seeds from a
fixed config, the live verify reads that same file. A kit that ignored the var passed all three
and then dialled `localhost:3000` on its first deployment (portal #187).

---

## The canonical volume knob

Every volume-adjustable kit exposes the **same** operator volume control:
`generation.target_traces` (an integer, with `minimum` / `maximum` / `default` / `title` /
`description`), declared in `config_schema`. The operator turns one uniform knob across
every kit; a kit-side deterministic **derivation hook**
(`langfuse_synth_core.derivation`) maps `target_traces` to the kit's internals at seed
time (EV: direct count; Lender: derived `scale`, with the golden suite/experiments/queue
left unscaled). Because the mapping is kit-side and deterministic, the portal stays
zero-code: it passes `--set generation.target_traces=N` verbatim.

- A **genuinely fixed-volume** kit exposes no volume param at all.
- Bespoke operator knobs (`generation.total_traces`, `generation.volume.scale`) are
  superseded by the canonical knob and become kit-**internal** params only.

Validator rule (`synth-authoring validate`): when `generation.target_traces` is exposed it must be
an integer, and it must be the **sole** operator volume control — a manifest may not
expose the canonical knob alongside a bespoke one; that ambiguous half-migrated state is
rejected. A manifest exposing *only* a bespoke knob is still **accepted** — validator
grandfathering, kept while old manifests existed. Both gold kits completed their Ring 2
migration (#33 EV direct count, #34 Lender derived scale), so no shipping manifest relies
on it anymore: the leniency is validator behavior, not a contract option — new kits use
the canonical knob or none.

---

## LLM-provider rules (semantic, not schema-expressible)

Reproduced exactly from the portal validator (LAN-378 / LAN-400):

- `LLM_API_KEY` in any live component's `requires_secrets` requires a top-level `llm`
  block declaring providers (otherwise the sentinel is unresolvable).
- A manifest may not **mix** `LLM_API_KEY` and `ANTHROPIC_API_KEY` — the two express the
  same slot ambiguously.
- `llm.models` keys must be a subset of `llm.providers`.

Back-compat: a manifest with a bare `ANTHROPIC_API_KEY` and no `llm` block trips none of
these — it stays valid and behaves as an implicit `providers: [anthropic]`.

At runtime, provider/model selection reaches the live container as env only
(`LLM_PROVIDER` / `LLM_MODEL` + the resolved key — see the environment contract), never
as a command flag.

---

## The live surface

A kit's live surface (companion) is declared in `live_components[]`: `command`, `port`,
`health_path`, optional `routes`, `requires_secrets`, `default_ttl_hours` (default 168),
`max_ttl_hours` (default 720), `alpha`. What it signs up to:

- **Bind `0.0.0.0` on the declared port.** The portal never port-publishes a live
  container; its sole ingress is the reverse proxy over the internal `live` network, and
  the portal's health probe hits the container directly by name.
- **Serve routes at `/`** and render internal links through `LIVE_BASE_PATH` (see the
  environment contract). The public URL is `/live/{instance_id}/…`; the prefix is
  stripped before the container sees the request.
- **Invocation** is the fixed `--config/--host/--port` shape, never `--set` — see "The
  container invocation".
- **Health (liveness):** the portal GETs `health_path` and treats any status `< 400` as
  up — at admission (with a restart-once budget), and re-polled in steady state.
- **Readiness (adapter):** the admission smoke additionally parses the health body as the
  adapter's readiness report — `{ready, langfuse_write_ok, llm_bound, detail}` — and
  asserts the adapter *lands*: Langfuse writable, LLM bound. A body that isn't that JSON
  degrades gracefully to liveness-only. The target shape points `health_path` at the
  adapter's readiness route (scaffold: `/healthz`), which **must differ from `/`** so it
  never collides with the scene's own index page.
- **The Companion Adapter** (`langfuse_synth_core.companion`) is the surface between the
  portal-injected env and kit code: it owns secret intake (kit code never touches a raw
  key — D4), Langfuse client/ingestor/read access, LLM provider resolution (pinned
  `LLM_MODEL` > per-request model > kit default > provider built-in), the readiness
  report, and serving. `CompanionAdapterContract` is the structural seam the portal's
  live runtime relies on.
- **Run state:** read-only, via the spool mount — see "Per-run anchors". A live surface
  may be stateless instead.

---

## The target shape, and migration debt

**The v2 scaffold shape is the target; the contract carries no legacy carve-outs**
(epic portal #195, decision 4). What `synth-authoring new` emits is the normative shape
of a kit:

- `src/synth/` package with the scenario modules and nothing else: config **shape** +
  derivation hook (loading/`--set` via `langfuse_synth_core.config.load_config`), `seed`
  orchestration, model-free `materialize`, scenario-only `verify`, artifacts publication,
  and — on opt-in — a `companion/` subpackage whose app factory takes the adapter
  (`create_app(adapter)`).
- CLI verbs `seed` + `verify` (+ the companion verb), one `synth` console script, plumbing
  from the library, `tests/test_retargeting.py`, the blessed golden, the ownership-fixed
  Dockerfile, and the `ci`/`publish` workflow pair.

A kit that deviates from this shape is carrying **migration debt, not exercising a
contract option**. The known debt (EV `v0.3.0` / Lender `v0.3.0`, recorded here so it is
answerable from one place; **this list is descriptive, never normative**):

- **Kit-local anchors/run-state modules** — *retired by portal #199 on the kit mains*
  (the shipped `v0.3.0` images predate the migration): each `src/synth/state.py` now
  keeps only the kit-owned payload dataclass on `langfuse_synth_core.anchors.AnchorsIO`;
  the byte-duplicated plumbing halves are deleted and Lender's tolerant `load()` became
  the shared behavior.
- **Lender writes run state from live containers.** `workbench/runner.py` and
  `certify/run.py` persist back into `.synth_state.json`, and `workbench/results.py`
  keeps a second run store under `.workbench/runs/` — both collide with the read-only
  spool mount. Writes must move into job steps (or workbench state out of the spool).
- **Kit-local client wiring.** `clients.py` in both kits (adapter-vs-env forks) plus
  `live/submit.py` with hand-rolled trace deep links, and a headless `synth submit` verb
  that exists only to serve that fork. The v2 shape takes clients off the adapter.
- **Live package shape.** `live/` package with `create_app(cfg, adapter)` vs the target
  `companion/` with `create_app(adapter)`; manifest `health_path: "/"` (liveness-only
  smoke) vs the target adapter readiness route.
- **Assorted layout drift.** 30/44 modules vs ~10; `playground` extra naming; editable
  installs in the kit Dockerfiles; Lender's `artifacts.py` importing `REPO_ROOT` from
  `state.py`.

Not debt: Lender's kit-local `timegen` and scenario-entwined `probe` are **ratified
fall-backs** (Ring 2, #34) — scenario substance stays in the kit by the seam rule
([`docs/SEAM.md`](docs/SEAM.md)).

---

See [`docs/SEAM.md`](docs/SEAM.md) for the library/kit hand-off rule that frames why the
Contract lives here.
