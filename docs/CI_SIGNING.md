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
images and burn CI time on the shared self-hosted host for no reader of that image.

### 3. Runner: self-hosted, not GitHub-hosted

The publish job holds a `packages: write` GHCR credential and mints a short-lived
Sigstore Fulcio certificate (`id-token: write`) — both are credentials in the sense the
project's standing directive cares about ("no third-party build infra touches secrets" —
portal `PLAN.md` §9/§10, mirrored in this repo's own `ci.yml` comment, which explicitly
called out that a *future* job handling secrets or a registry belongs on self-hosted, not
here). The lint/test jobs in `ci.yml` stay on GitHub-hosted `ubuntu-24.04` — they touch
no credentials and this repo is public, so those minutes are free. Only the
build+push+sign job moves to self-hosted.

Runner labels: `self-hosted, linux, kit-ci` — deliberately **not** the portal's own
`demo-depot` label, so a kit-repo runner can never pick up a portal job or vice versa.

**Provisioning a kit-repo runner** (one-time, per kit repo, on the same dedicated CI
host that already runs the portal's runner): reuse the portal's existing, already
generic `infra/host/runner-setup.sh` (no portal code change needed — it already takes
`GH_REPO` / `RUNNER_DIR` / `RUNNER_LABELS` as env vars):

```bash
sudo -u ci env \
  GH_ORG=borismichel \
  GH_REPO=langfuse-synth-ev \
  GH_RUNNER_TOKEN=<token from Settings -> Actions -> Runners -> New runner, on that kit repo> \
  RUNNER_DIR=/opt/github-runner-langfuse-synth-ev \
  RUNNER_LABELS=self-hosted,linux,kit-ci \
  bash infra/host/runner-setup.sh
```

Then install as its own systemd service (`sudo $RUNNER_DIR/svc.sh install ci && ...
start`), same as the portal runbook's Step 2d. GitHub self-hosted runners are
repo-scoped for a personal account (no org-level runner available here), so each kit
repo gets its own runner *instance* — but they can all live on the one physical host.
This is an operator/runbook step, not portal or infra **code** — the acceptance
criterion "zero per-kit portal/infra work" is about the workflow itself needing no
hand-authored YAML per kit (it doesn't — see §4), not about runner registration, which
is inherently a one-time per-repo GitHub administration action (the portal's own runner
was provisioned the same way).

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
- Cadence decision recorded: §2 (build-on-tag) + §3 (self-hosted).
- No portal code change: none made; the runner runbook in §3 reuses the portal's
  existing generic script by invocation, not by editing it.
