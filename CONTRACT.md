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
3. **This document** — the reserved-verb semantics and filesystem conventions that are
   policy rather than shape, lifted out of the schema's `description` strings so they have
   one readable home.

> "Passes `synth-authoring validate` locally" ≡ "passes portal sync" **by construction** — the
> author's offline lint and the portal's admission gate run the same code and the same
> schema.

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

Rules the validator enforces:

- **`seed` + `verify` are mandatory.** They are the determinism-and-read-back spine every
  spec-compliant kit wires: `seed` produces the deterministic spool, `verify` proves what
  landed. A manifest missing either fails validation.
- **A reserved-verb step must run its verb.** If a step's `id` is a reserved verb, its
  `run` command must invoke `synth <verb>` (e.g. the `seed` step runs `synth seed …`). A
  step named `seed` that actually runs `synth teardown` is a contract violation — the
  reserved id would mislead the portal about the job's kind.

The `run` command receives `{config}` substituted with the resolved `--config` path;
`--set key=value` overrides are appended by the portal. Custom-step ids may run any
container command.

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
  reads to walk the demo.
- **`assets.docs` / `screenshots`** paths are repo-relative and rendered in-portal.
- **Runtime write paths must be writable by uid 10001.** Job containers run as
  `JOB_RUN_USER=10001:10001`, never as the image's build user, and a plain `COPY . .`
  lands root-owned. The image must therefore create and chown the directories the kit
  writes at runtime: **`/app/out/`** (the artifact-collection dir above — contract) and
  **`/app/.synth_spool/`** (where the reference `seed` spools events — convention, but
  every scaffolded kit uses it). The reference Dockerfiles do this with
  `RUN mkdir -p /app/out /app/.synth_spool && chown -R synth:synth /app` before the
  `USER` drop; a kit that skips it dies on `open_spool()` at its first deployment
  (portal #189).

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
expose the canonical knob alongside a bespoke one. The two gold manifests still ship a
bespoke knob pending their Ring 2 migration (#33/#34) to the canonical knob, so a manifest
exposing *only* a bespoke knob is grandfathered today; what is rejected is the ambiguous
half-migrated state of exposing both at once.

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

---

See [`docs/SEAM.md`](docs/SEAM.md) for the library/kit hand-off rule that frames why the
Contract lives here.
