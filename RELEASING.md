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

## Pending for the next release (unreleased on `main`)

Whoever cuts the next version ships these; delete each entry when its tag lands.

*(nothing pending — v1.4.0 shipped the depot-first scaffold, portal #161)*

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
3. **Re-pin every kit** in the table above — all three pins, to the same `@vX.Y.Z` — on a
   branch per kit; PR → merge. Grep the kit's `pyproject.toml` for
   `langfuse-synth-core` and check every hit moved; a stale one is silent.
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
