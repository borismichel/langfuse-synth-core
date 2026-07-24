# Releasing `langfuse-synth-core`

Cutting a new version is **not done until every consuming kit is re-pinned to it.** The
lib is a git-pinned dependency, so a tag that no kit references changes nothing — and a
kit left on the old pin silently keeps running the old core. Re-pinning the kits is part
of the release, not a follow-up.

## Consuming kits (re-pin ALL of them on every version bump)

Each kit pins the lib **twice** — a runtime dependency **and** the `[authoring]`
golden-gate dev pin — and the two **must share the same ref**:

| Kit repo | File | Pins to bump |
| --- | --- | --- |
| `langfuse-synth-ev` | `pyproject.toml` | `langfuse-synth-core @ …@<ref>` (runtime) + `langfuse-synth-core[authoring] @ …@<ref>` (dev) |
| `langfuse-synth-lender` | `pyproject.toml` | same two pins |

> Add a row here whenever a new kit starts consuming the lib, so this table stays the
> single source of truth for "who must be re-pinned."

## Release checklist

1. **Bump the version** in `pyproject.toml` (`version = "X.Y.Z"`) on a release branch;
   PR → merge to `main`.
2. **Tag** `vX.Y.Z` on the landed `main` commit and **push the tag** (`git push origin
   vX.Y.Z`). The tag must be on origin before any kit or CI can resolve `@vX.Y.Z`.
3. **Re-pin every kit** in the table above — both pins, to the same `@vX.Y.Z` — on a
   branch per kit; PR → merge.
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
