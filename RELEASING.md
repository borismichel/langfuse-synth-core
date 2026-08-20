# Releasing `langfuse-synth-core`

Cutting a new version is **not done until every consuming kit is re-pinned to it.** The
lib is a git-pinned dependency, so a tag that no kit references changes nothing — and a
kit left on the old pin silently keeps running the old core. Re-pinning the kits is part
of the release, not a follow-up.

## Consuming kits (re-pin ALL of them on every version bump)

Each kit pins the lib **three times** — a runtime dependency, the `[companion]` extra its
live Surface serves through, **and** the `[authoring]` golden-gate dev pin — and all three
**must share the same ref**. (It was two until the Companion Adapter migrations, Spec G
G4/G5: each kit's `[playground]` extra collapsed onto `langfuse-synth-core[companion]`, so
the web-server deps became a third core ref. Miss it and a re-pin half-lands — the kit runs
the new core but serves through the old one.)

| Kit repo | File | Pins to bump |
| --- | --- | --- |
| `langfuse-synth-ev` | `pyproject.toml` | `langfuse-synth-core @ …@<ref>` (runtime) + `langfuse-synth-core[companion] @ …@<ref>` (the `[playground]` extra) + `langfuse-synth-core[authoring] @ …@<ref>` (dev) |
| `langfuse-synth-lender` | `pyproject.toml` | same three pins |
| `langfuse-synth-support` | `pyproject.toml` | the runtime pin IS `langfuse-synth-core[companion] @ …@<ref>` (scaffolded with `--companion`, so the extra rides the runtime dependency) + `langfuse-synth-core[authoring] @ …@<ref>` (dev) |

> Add a row here whenever a new kit starts consuming the lib, so this table stays the
> single source of truth for "who must be re-pinned."

## The CI-workflow pin is independent of the runtime pin

Since #102, each kit's `.github/workflows/publish.yml` also pins a ref — the
`workflow_call` reference into this repo's `kit-publish.yml` (build+GHCR-push+cosign
sign; see `docs/CI_SIGNING.md`). That pin is **not** one of the two runtime pins above:
it selects CI *logic*, not library behavior, so bumping it never touches a kit's
determinism golden and does **not** require step 4 of the checklist below. Bump it
independently, whenever a kit wants a `kit-publish.yml` fix or policy change — no need
to wait for (or force) a coordinated runtime re-pin across every kit.

### CI-only releases: step 3 covers the workflow pin alone

A version whose whole delta is **CI logic** — `kit-publish.yml` and friends, nothing under
`src/langfuse_synth_core/` that a kit installs at all — satisfies step 3 by bumping
each kit's **`publish.yml` workflow pin** and leaves the three runtime pins where they
are. (That bump is a one-line hand edit — `synth-authoring repin` deliberately moves the
workflow pin *together* with the dependency pins, which is what a full core release wants.) A runtime re-pin would move every kit's dependency ref to a release that changes
nothing it executes, and cost a golden re-proof (step 4) for a delta the kit cannot
observe. State in the release PR that the release is CI-only, so the next
reader can tell a deliberate carve-out from a half-landed re-pin.

**This carve-out does NOT extend to the authoring package.** A kit resolves
`langfuse_synth_core.authoring` through its `[authoring]` dev pin — one of the three pins that
must share a ref — so a release that changes the authoring toolchain or the scaffold templates
reaches a kit only via the full step 3. Bumping `[authoring]` alone is precisely the
half-landed re-pin the table above warns about. The golden re-proof in step 4 is not waste
here: it is what proves an authoring change did not move the deterministic pool.

**v1.5.0 (portal #185/#183) was the first release cut this way**: all three kits moved
their workflow pin to `@v1.5.0` and stayed on `@v1.4.0` for runtime. v1.10.0 (the
`verify-version` guard: a kit tag whose `pyproject.toml` `version` disagrees now fails
the publish before anything is built — the regression that shipped EV/Lender v0.4.0 and
support v0.1.4 reporting the previous release's version) was cut the same way. A release
that moves ANY runtime code takes the full step 3 — all three pins, every kit.

## Pending for the next release (unreleased on `main`)

Whoever cuts the next version ships these; delete each entry when its tag lands.

- **`v3.0.0` — the read seam retires its compatibility front (portal #211).** This is
  the release the three kit read-seam cutovers repin to, and it is **major** because it
  removes a public function:
  - `lfread.get_all_scores` is **gone**. It rendered the read seam's rows back into the
    deprecated `value` / `stringValue` / `traceId` dict shape so a kit that had not been
    rewired kept reading a v4 project. All three kits read `reader.scores(...)` now, so
    it retires with its last caller — as `docs/READ_LIVE_SEAMS.md` said it would. A kit
    pinned below v3.0.0 is unaffected; a kit that takes this release and still calls it
    fails at import.
  - `langfuse_synth_core.target` is new: the Cloud/self-hosted profile both kits carried
    a byte-identical copy of, plus `resolved()` — the probe that makes a v4 host
    something a kit *recognises*. Kit-side `target.py` re-exports it.
  - The **Langfuse SDK floor moves to `>=4.14`**, deliberately: 4.14.0 is the first SDK
    with a client for `/api/public/experiments`, which is where a dataset run lives under
    v4. Taking this release upgrades the SDK in every kit image.
  - The live-emission seam now sends `x-langfuse-ingestion-version: 4` on its SDK
    client, so a live trace is readable in seconds rather than up to fifteen minutes.
    The header constant moved above the determinism line (`langfuse_synth_core.ingestion`)
    because both write paths send it and neither may import the other.
  - The scaffold's `verify.py` template is born on the read seam, so `MIN_CORE_REF` moves
    to `v3.0.0`: a freshly scaffolded kit no longer runs on an older core.

## Release checklist

1. **Bump the version** in `pyproject.toml` (`version = "X.Y.Z"`) on a release branch;
   PR → merge to `main`. On the same branch, bump the release-coupled ref sites:
   `DEFAULT_CORE_REF` in `src/langfuse_synth_core/authoring/scaffold.py` (the pin every
   freshly scaffolded kit resolves — a default left behind emits kits on the old core)
   and the `--core-ref` examples in `README.md` and the shipped
   `authoring-a-demo-kit/SKILL.md`. (`__version__` needs no bump: it is derived from the
   distribution metadata and guarded by `tests/test_runtime_import.py`; #145.)
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
