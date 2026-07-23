# The seam — where the library ends and a kit begins

`langfuse-synth-core` is a **toolbox library**, deliberately **not** an
inversion-of-control framework. No framework loop calls into a kit. The kit owns its
orchestration skeleton and may override any of it. This is the ratified **T2 verdict:
flexibility beats deduplication** — the ability to build use cases we cannot foresee
today outweighs the line-count a framework loop would save, and a plugin/hook framework
would force vendor-approved scenario substance through a contract at exactly the seam
where fidelity leaks.

## The hand-off rule

> **Library = the machine that speaks the Langfuse data model, bidirectionally.
> Kit = everything that speaks the scenario. The orchestration skeleton is a kit-owned
> scaffold. Edge cases break to the kit.**

The rule is symmetric on both directions:

- **Write path:** the library emits an `events`-level primitive; the kit composes the
  scenario-specific trace tree from stable primitives.
- **Read path:** the library fetches from Langfuse (authenticated, paginated read
  client); the kit asserts what should have landed.

### Library (byte-identical, uncontested)
RNG / ID substreams · token pricing · statistical distributions · the Langfuse client ·
event emission · scenario-agnostic UI primitives (theme / paths).

### Kit (strongly divergent, uncontested)
The deterministic answer-engine · the content corpus · the scenario generator — this
*is* the scenario.

### Library-with-parameters (middle field, data-model-facing; delta is values, not logic)
Ingest · time generation · probe · the config loader. **Tie-break:** if a "delta" turns
out to be *logic* during migration, the file falls back to the kit.

### `verify` is split
Read-helpers (auth, paginated GET of scores / traces across the Langfuse REST API) →
library read-client. The `run_verify` body (scenario assertions, golden-path) → kit.

## Distribution

- Kits consume the library as a **git-pinned dependency** (tag/SHA in each kit's
  `pyproject.toml`), so every kit upgrades **deliberately**, when its owner chooses —
  protecting the gold-standard kits.
- The library is baked into each kit's **prebuilt Docker image** at build time. A shared
  base image is permitted **only as a layer-cache optimization, never as a version
  contract**.
- Kits remain **three separate repositories**; no monorepo.

## Runtime vs `[authoring]`

The **runtime** install carries only the library (plus the companion adapter shell and
the `target_traces` derivation hook, both of which run where the lib runs). The
**`[authoring]`** extra carries the authoring toolchain (`synth new / validate / freeze`
and the kit-dev skills), kept out of the lean runtime image a deployed kit ships.
