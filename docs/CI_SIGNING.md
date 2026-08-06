# Kit CI: build, GHCR push, cosign signing

This is the **contract** produced by Spec E · E7 (#102). It has two sides:

- **This side (build/sign)** — CI in each kit repo builds the kit's image, pushes it to
  GHCR under the allowed org, and cosign-signs it keylessly. Implemented here.
- **The other side (verify)** — the portal worker verifies the signature before running
  any GHCR-referenced image (#104). That gate must be configured with *exactly* the
  identity this doc records — one contract, two sides.

No portal code changes with this ticket. The pull-side digest fence already shipped
(#98, `JOB_GHCR_ALLOWED_ORG`).

## The decisions (and why)

### 1. GHCR org / image naming

Images publish to `ghcr.io/<github.repository>` — i.e. `ghcr.io/borismichel/langfuse-synth-ev`,
`ghcr.io/borismichel/langfuse-synth-lender`, and so on for kit #3..N. No separate
slug→image mapping to maintain: the repo *is* the image name, GHCR's own default. The
allowed org for the portal's pull fence (`JOB_GHCR_ALLOWED_ORG`) is `borismichel`.

### 2. Cadence: build-on-tag, not build-on-push

The publish workflow triggers on `push: tags: ["v*.*.*"]`, not on every push to `main`.
Images are immutable release artifacts, not CI-per-commit churn — this mirrors the
already-established kit release rhythm (a kit repo cuts a semver tag; see each kit's own
release notes). Building on every push would flood GHCR with untagged intermediate
images and burn CI minutes for no reader of that image.

The tag is also **checked against the kit's `pyproject.toml` `version`** before anything
is built: a `verify-version` job fails the whole run when the two disagree. A mismatch
would publish an image whose installed distribution reports the previous release's
version — `pip show` inside it lies about what it is, which is how EV/Lender `v0.4.0`
and support `v0.1.4` actually shipped. The release motion is therefore: bump `version`
on `main`, tag that commit, push the tag.

### 3. Runner: GitHub-hosted, not self-hosted

**Reversed by Spec E · E7b (#113, 2026-07-28).** #102 originally put this job on a
self-hosted runner (see the old runbook this section used to carry, now deleted). That
decision is reversed: the publish jobs run on GitHub-hosted runners (`ubuntu-24.04` and
its arm64 sibling `ubuntu-24.04-arm`, see §5), same as the lint/test jobs in `ci.yml`.

This follows directly from the CLAUDE.md infrastructure policy (2026-07-28): while
running, Demo Depot's total infrastructure footprint is the dedicated depot host plus
GitHub, nothing else. Core and both kits are public repos, so **all public-repo CI/CD
uses GitHub-hosted infrastructure** — GitHub-hosted runners, GHCR for images, GitHub OIDC
for keyless signing. The job's credentials (`packages: write` GHCR token, `id-token:
write` OIDC for cosign) are both minted by GitHub for the run, not *project* secrets
(Infisical values, LLM keys, anything from the portal secret store) — the policy places
those GitHub-minted credentials explicitly out of scope of the "no third-party build
infra touches secrets" directive (portal `PLAN.md` §9/§10), so nothing about this job
requires the dedicated host.

It is also the safer shape on its own terms: fork PRs make a repo-scoped self-hosted
runner on a public repo a code-execution risk, and ephemeral GitHub-hosted runners
strengthen the keyless-signing trust story (the Fulcio certificate is bound to a
short-lived, GitHub-attested identity rather than a long-lived host). No runner
provisioning step remains for this workflow — a freshly scaffolded kit's `publish.yml`
(§4) works the moment its first `v*` tag is pushed, no per-kit-repo GitHub
administration required.

### 4. Kit #3..N inherit the workflow for free

The actual build/push/sign logic lives once, here, in
[`kit-publish.yml`](../.github/workflows/kit-publish.yml) as a `workflow_call` reusable
workflow. Each kit's own `.github/workflows/publish.yml` is a ~10-line caller pinned to
a core ref:

```yaml
on:
  push:
    tags: ["v*.*.*"]
jobs:
  publish:
    uses: borismichel/langfuse-synth-core/.github/workflows/kit-publish.yml@<ref>
    permissions:
      contents: read
      packages: write
      id-token: write
```

`synth-authoring new` scaffolds this file automatically (`publish.yml.tmpl`, pinned to
the same `--core-ref` as the runtime dependency — see `scaffold.py`), so a freshly
scaffolded kit gets build+sign wired with **no manual authoring**. A fix or policy
change made to `kit-publish.yml` reaches every kit the next time it bumps its core pin
— exactly the same "pin to a tag or SHA, never a branch" discipline as the runtime
dependency (see [`RELEASING.md`](../RELEASING.md)).

### 5. Multi-arch images: native runners, one signed index

**Added by portal #185 (2026-07-29).** Until then the workflow built on `ubuntu-24.04`
with no `platforms:` key, so every kit tag published a manifest list holding a single
`linux/amd64` entry. That is fine on the amd64 depot host and breaks on arm64: spawning
an asset from a presenter's Apple-silicon laptop failed with

```
DockerEngineError: docker POST /images/create -> HTTP 404:
  no matching manifest for linux/arm64/v8 in the manifest list entries
```

Kit images now publish for **both `linux/amd64` and `linux/arm64`**, so the daemon picks
the right child manifest on either host and the portal worker's pull needs no
platform-forcing hack of its own.

**Shape: fan-out per architecture, then merge.** A `build` matrix runs one job per
platform on a runner of that architecture — `ubuntu-24.04` and `ubuntu-24.04-arm` — each
pushing an *untagged* manifest with buildx's `push-by-digest`. A `merge` job then assembles
those digests into one index with `docker buildx imagetools create`, and that index is what
carries the release tag. The alternative — a single job with `platforms:
linux/amd64,linux/arm64` plus `docker/setup-qemu-action` — is one line instead of two jobs,
but builds arm64 under emulation; GitHub-hosted arm runners are free on public repos (which
every kit is), so there is nothing to buy by paying the emulation tax.
`tests/test_kit_publish_workflow.py` pins these properties against silent drift.

**What signs, and what the portal pins.** cosign signs the **index** digest — the same ref
`registry.yaml` pins and the portal's E2 gate verifies before `create`. Signing a per-arch
child instead would leave the ref actually pulled unsigned. An index digest is
platform-independent, so the E1 digest-pin fence (`is_ghcr_pinned_ref`) is unaffected, and
so is the identity below: OIDC's `job_workflow_ref` names *this file* for every job in it,
so splitting build from merge does not move the identity #104 verifies.

**The fix only reaches images built after it.** Tags published before this landed stay
single-arch forever — a kit has to cut a new release to gain arm64. See
[`RELEASING.md`](../RELEASING.md).

## The signing-identity contract (for #104 to consume)

Keyless (OIDC) signing — no key material anywhere. Verify with:

```bash
cosign verify \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/borismichel/[^/]+/\.github/workflows/kit-publish\.yml@refs/tags/v.*$' \
  ghcr.io/borismichel/<repo>@sha256:<digest>
```

Why the identity names `kit-publish.yml` (in `langfuse-synth-core`) and not each kit's
own `publish.yml`: cosign's keyless identity is derived from the OIDC token's
`job_workflow_ref` claim, which names the workflow that actually **runs the job** — for
a `workflow_call`, that is the reusable workflow's own file, not the caller's. This is a
feature for the verification side: one identity pattern covers every kit, present and
future, with no per-kit allowlist entry to maintain in #104.

- **Issuer**: `https://token.actions.githubusercontent.com` (GitHub Actions' OIDC
  issuer — always this value, never kit-specific).
- **Identity**: `https://github.com/borismichel/<any repo>/.github/workflows/kit-publish.yml@refs/tags/v<version>`
  — the `[^/]+` segment matches any repo (so it is not an allowlist of kit names to
  maintain), and `refs/tags/v.*` requires the run to have been triggered by a semver
  tag push (§2 — build-on-tag), never a branch push or PR.

## Acceptance criteria crosswalk

- Both kits published, digest-referenced, cosign-signed: this workflow does that on
  every kit tag push (once wired — #102 ships the workflow; the kit repos still need to
  cut a `v*` tag to actually publish their first image).
- Signature verification policy: recorded above, identical for #104.
- New kits inherit build+sign without manual wiring: `synth-authoring new` emits the
  caller (§4).
- Cadence decision recorded: §2 (build-on-tag) + §3 (GitHub-hosted) + §5 (multi-arch on
  native runners).
- No portal code change: none made; §3's move to GitHub-hosted runners removes the
  runner-provisioning step entirely rather than touching portal or infra code.
