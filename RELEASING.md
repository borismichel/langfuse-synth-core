# Releasing `langfuse-synth-core`

Cutting a new version is **not done until every consuming kit is re-pinned to it.** The
lib is a git-pinned dependency, so a tag that no kit references changes nothing — and a
kit left on the old pin silently keeps running the old core. Re-pinning the kits is part
of the release, not a follow-up.

## Consuming kits (re-pin ALL of them on every version bump)

Every kit's runtime, companion and `[authoring]` dependencies **must share the same ref**.
EV and Lender carry three dependency pins because `[companion]` is a separate optional
dependency. The other kits include `[companion]` in their runtime dependency and carry
two pins. Each kit also pins the publish workflow, described below.

| Kit repo | File | Pins to bump |
| --- | --- | --- |
| `langfuse-synth-ev` | `pyproject.toml` | `langfuse-synth-core @ …@<ref>` (runtime) + `langfuse-synth-core[companion] @ …@<ref>` (the `[playground]` extra) + `langfuse-synth-core[authoring] @ …@<ref>` (dev) |
| `langfuse-synth-lender` | `pyproject.toml` | same three pins |
| `langfuse-synth-prompt` | `pyproject.toml` | runtime `langfuse-synth-core[companion]` + `[authoring]` dev pin |
| `langfuse-synth-support` | `pyproject.toml` | same two pins |
| `langfuse-synth-parceldesk` | `pyproject.toml` | same two pins |
| `langfuse-synth-bank-assistant` | `pyproject.toml` | same two pins |

> Add a row here whenever a new kit starts consuming the lib, so this table stays the
> single source of truth for "who must be re-pinned."

## The CI-workflow pin is independent of the runtime pin

Since #102, each kit's `.github/workflows/publish.yml` also pins a ref — the
`workflow_call` reference into this repo's `kit-publish.yml` (build+GHCR-push+cosign
sign; see `docs/CI_SIGNING.md`). That pin is separate from the dependency pins above:
it selects CI *logic*, not library behavior, so bumping it never touches a kit's
determinism golden and does **not** require step 4 of the checklist below. Bump it
independently, whenever a kit wants a `kit-publish.yml` fix or policy change — no need
to wait for (or force) a coordinated runtime re-pin across every kit.

### CI-only releases: step 3 covers the workflow pin alone

A version whose whole delta is **CI logic** — `kit-publish.yml` and friends, nothing under
`src/langfuse_synth_core/` that a kit installs at all — satisfies step 3 by bumping
each kit's **`publish.yml` workflow pin** and leaves the dependency pins where they
are. (That bump is a one-line hand edit — `synth-authoring repin` deliberately moves the
workflow pin *together* with the dependency pins, which is what a full core release wants.) A runtime re-pin would move every kit's dependency ref to a release that changes
nothing it executes, and cost a golden re-proof (step 4) for a delta the kit cannot
observe. State in the release PR that the release is CI-only, so the next
reader can tell a deliberate carve-out from a half-landed re-pin.

**This carve-out does NOT extend to the authoring package.** A kit resolves
`langfuse_synth_core.authoring` through its `[authoring]` dev pin — one of the dependency pins that
must share a ref — so a release that changes the authoring toolchain or the scaffold templates
reaches a kit only via the full step 3. Bumping `[authoring]` alone is precisely the
half-landed re-pin the table above warns about. The golden re-proof in step 4 is not waste
here: it is what proves an authoring change did not move the deterministic pool.

**v1.5.0 (portal #185/#183) was the first release cut this way**: all three kits moved
their workflow pin to `@v1.5.0` and stayed on `@v1.4.0` for runtime. v1.10.0 (the
`verify-version` guard: a kit tag whose `pyproject.toml` `version` disagrees now fails
the publish before anything is built — the regression that shipped EV/Lender v0.4.0 and
support v0.1.4 reporting the previous release's version) was cut the same way. A release
that moves ANY runtime code takes the full step 3 — all dependency pins, every kit.

## v5.1.0

Release preparation: the tag, coordinated consumer pins and qualification are still
required. This is a runtime and authoring release; the CI-only exemption does not apply.
It retains Python >=3.11, Langfuse SDK >=5.0.1,<6 and Langfuse target >=4.59.0 requirements.
Manifest schema version remains 1. The historical Spool format and deterministic
generation are unchanged; consuming kits must prove their existing goldens byte-identical.

### Companion APIs

- `LLMClient.complete(response_schema=...)` requests structured provider output while
  returning the original text and usage. Callers still validate the result; provider
  errors are not retried as unconstrained requests. `provider_tracing=False` disables
  Anthropic's built-in per-request tracing when a caller owns the generation span.
- Trusted `langfuse_factory` and `native_llm_factory` callbacks receive the Adapter's
  resolved connection. Custom Langfuse clients are cached and reused by `emitter()`;
  native clients retain their async, streaming and tool APIs. Construction and readiness
  perform no inference, and core adds no framework dependency (Depot #367).
- Optional `llm.roles` capability version 1 binds independent feature providers/models.
  `role_binding`, `llm_for` and `native_llm_for` expose only enabled declared roles; native
  roles use explicit factories. Readiness binds role-only surfaces without demanding an
  undeclared primary provider. Existing selected-provider semantics remain unchanged.
- Optional `spending_check` capability version 1 provides a fresh, fail-closed
  `check_spending(provider=None)` decision before each paid dispatch, including retries
  and independent roles. Denial and unavailability expose safe reason codes. This is
  hourly-estimate authorization, not a reservation or an exact billing ceiling.

### Live transport and access

- Optional `protected_paths` declares presenter/manage route policy. The Adapter validates
  and consumes Depot's per-instance transport proof before application handlers; ambiguous
  paths and missing protection configuration fail closed. Unprotected components retain
  their behavior. Depot's matching ingress support is required.
- `live.paths.public_origin()` returns the optional validated `PORTAL_LIVE_PUBLIC_ORIGIN`
  for managed-service editor/callback URLs. Ordinary links remain relative.
- `forward_otel(payload)` sends native-runtime OTLP only to the bound Langfuse target,
  preserving trace/span identity, timestamps and the partial-success protobuf response.
  It follows the host egress proxy, rejects redirects, performs no retries and caps payload
  and response size at 1 MiB. Its ten-second response deadline closes slow header/body
  connections without leaving network work running after return. The kit owns authenticated
  local intake, span filtering and child-process lifecycle.

### Authoring and migration

- Validation supports the existing Depot `pipeline[].generate` split-seed contract,
  requires a nonempty `resumable` import command and applies the provider-secret guard
  to generated commands (Depot #366). Malformed role providers return schema findings
  instead of raising an exception.
- New scaffolds align runtime, authoring and publish references on `v5.1.0`.
  `MIN_CORE_REF` remains `v5.0.0`: the emitted templates require no newer core APIs.
  Explicit compatible pins remain supported.
- Re-pin all six kits above, including the separate Support and ParcelDesk repositories,
  and align Depot's API dependency, lockfile and sync workflow pin. Optional role,
  protected-route and spending capabilities require corresponding Depot support before
  deployment. Keep runtime, companion and authoring references aligned; update workflow
  references and prove each kit's full compatibility gates before completing the release.

The [Contract](CONTRACT.md#the-live-surface) defines the factory, role, access, OTLP and
spending obligations. Release completion follows the checklist below.

## v5.0.0 candidate

Requires Python SDK >=5.0.1,<6 and Cloud or self-hosted Langfuse >=4.59.0.
The major version reflects the SDK/target compatibility change; public core interfaces
remain stable. Retains all v4.3.0 contract/setup capabilities. SDK-managed live scores
now batch to `/api/public/scores`; historical Spool ingestion and goldens are unchanged.
Real-SDK tests cover root IO, nesting, typed context, usage/cost, prompt association,
score subjects and flushing. Exact candidate SDK: **5.0.1** (stable, 2026-10-14).

Released guidance: https://langfuse.com/docs/observability/sdk/upgrade-path/python-v4-to-v5

The coordinated four-kit re-pin, released-image admission and scenario read-back are
still required before this release is complete (Depot #318 / #319–#323).

## v4.3.0

Adds optional versioned setup metadata for schedule, field groups, rehearsal presets and
host defaults, with structural and semantic validation. Adds opt-in inclusive calendar
window helpers; existing timestamp helpers retain their behavior. All consuming kits
require the coordinated core re-pin and their own verification before release completion.

## v4.2.0

Adds optional `pipeline[].requires_secrets` for explicit custom setup capabilities.
The allowlist is the Langfuse credential pair and the `LLM_API_KEY` sentinel, which
requires `llm.providers` and is forbidden on canonical steps and commands. Existing
manifests retain their credential behavior; deterministic seed/import/verify receive
no provider key. Depot must adopt this schema and the matching worker enforcement
before a kit declares the new field. No runtime transport or fixture generation changes.

## Release checklist

1. **Bump the version** in `pyproject.toml` (`version = "X.Y.Z"`) on a release branch;
   PR → merge to `main`. On the same branch, bump the release-coupled ref sites:
   `DEFAULT_CORE_REF` in `src/langfuse_synth_core/authoring/scaffold.py` (the pin every
   freshly scaffolded kit resolves — a default left behind emits kits on the old core)
   and the `--core-ref` examples in `README.md` and the shipped
   `authoring-a-demo-kit/references/setup.md`. (`__version__` needs no bump: it is derived
   from the distribution metadata and guarded by `tests/test_runtime_import.py`; #145.)
2. **Tag** `vX.Y.Z` on the landed `main` commit and **push the tag** (`git push origin
   vX.Y.Z`). The tag must be on origin before any kit or CI can resolve `@vX.Y.Z`.
3. **Re-pin every kit** in the table above — on a branch per kit, from the kit checkout:

   ```
   synth-authoring repin vX.Y.Z [--kit-tag vA.B.C] [--dry-run]
   ```

   One command (portal #197) moves ALL the kit's core pins — every `pyproject.toml`
   dependency pin (however many that kit carries) **and** the `publish.yml` workflow pin
   — and fails loudly if any core ref it could not rewrite would be left behind (the old
   "grep for stragglers" step, mechanized). It also prints the portal `registry.yaml`
   snippet for the follow-up registry PR; pass `--kit-tag` with the kit's next release
   tag to resolve the GHCR image digest into the output (it says plainly when that tag's
   image is not published yet). `--dry-run` shows the diff without writing.
   PR → merge, per kit.
4. **Prove each kit still golden-green** on the new ref: run its
   `tests/test_determinism.py::test_full_payload_golden_is_byte_identical` (needs the
   `[dev]` / `[authoring]` extra installed) under the deny-LLM egress block. A red gate
   here means the ref moved the deterministic pool — investigate before shipping.
5. Only when **all** kits are green on the new ref is the release complete.

## Pin to a tag, never a branch

Pin to a **tag or full SHA** — never a moving branch — so a kit's vendor-approved output
can never be silently rewritten by a later lib change. See [`docs/INSTALL.md`](docs/INSTALL.md).

## Squash-merge caveat

If the version-bump PR is **squash-merged**, the commit you tagged pre-merge no longer
exists on `main` under its original SHA. Tag the **landed** `main` commit (or re-point the
tag to it) so `vX.Y.Z` names a commit that is on `main`, then push the tag.
