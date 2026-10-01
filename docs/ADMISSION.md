# Released-candidate admission handoff

Use `synth-authoring admit` after a kit release is built and signed and an admin has a
working depot connection. This is an authoring command in core builds that include it;
the older v4.1.1 release does not. Install that authoring build separately from the kit's
pinned runtime dependencies. It uses the existing depot admission API, never a second
build/deployment pipeline, and does not create tags, change the registry or publish kits.

## Connection and credentials

Use the depot API **origin**, such as `https://depot.example.com`, without an API path,
query, fragment or embedded credentials. HTTP is accepted only for a local loopback depot.
An administrator must configure the disposable admission target in the depot first.

The token is the existing **short-lived admin session bearer token**, not a Langfuse key
or a new credential type. Follow the deployed instance's
[admin guide: admission runs](https://github.com/borismichel/langfuse-demo-depot/blob/main/docs/user/admin-guide.md#5-admission-runs-api-only)
for its approved session-token setup. The documented session uses HS256, issuer
`demo-depot-web`, audience `demo-depot-api`, and an expiry, under an active admin identity.
Have the administrator provide/establish that session; the CLI does not mint tokens or
need the deployment's session signing secret.

Load the bearer token into `DEMO_DEPOT_TOKEN` through your existing credential workflow.
Use `--token-env NAME` for a different variable. Supply the **variable name**, never the
token, on the command line. Credentials are read afresh on every invocation. A 401 means
refresh the session and reuse the same progress file; a 403 requires an admin account.
The client verifies HTTPS certificates, follows no redirects, and ignores inherited proxy
and `.netrc` authentication. Network paths requiring those settings need direct API access.

## Start or resume the released candidate

```bash
synth-authoring admit \
  --repo https://github.com/your-org/your-kit \
  --slug your-kit \
  --ref v1.2.3 \
  --portal https://depot.example.com \
  --state .scratch/admission-your-kit-v1.2.3.json \
  --timeout 60 --poll-interval 2
```

Use the Manifest's slug, a credential-free GitHub repository URL and an **immutable**
released `vX.Y.Z` tag (a prerelease suffix is allowed). Never move or reuse that tag, or
rebuild its image under a changed digest. The CLI rejects branch-style refs; a version-tag
spelling alone cannot prove immutability. The depot's initial POST resolves the commit
and signed GHCR digest using the same interfaces as registry sync. The receipt pins that
observed commit/digest and rejects any changed evidence on resume or retry. GET resume
reads the original admission run; it does not re-resolve a moved tag. Preserve the release
immutability contract before using the registry handoff.

The first invocation saves nonsecret candidate intent, then POSTs the candidate. The
server resolves and validates the release before starting its normal scratch pipeline.
Once a run ID is known, subsequent invocations GET that exact run. If the initial POST
had an uncertain outcome, rerunning without a saved ID repeats the same candidate POST;
the depot reuses an in-flight or passed run for that candidate/commit.

The progress file contains only schema version, portal/repository/slug/ref, run ID,
commit SHA, image digest and status. Writes replace it atomically. Keep it with this
candidate; mismatched arguments, unknown fields or malformed saved evidence fail before
requests. Keep one active CLI invocation per progress file. For another release, choose
another file rather than editing the saved identity.

## Read the outcome

The command displays validated rung names and states, numeric exit codes when available,
and a rung-specific next action. Free-form portal/job errors can carry credentials, so
they are neither printed nor saved. Use the authenticated portal's admission/job evidence
for the raw reason and logs, using the displayed run ID. The server owns every verdict.

| Exit | Meaning | Next action |
| --- | --- | --- |
| `0` | All required rungs passed and `eligible_to_pin` is true | Review the printed registry handoff and rehearse in staging |
| `1` | Failed rung, invalid input/receipt/response, or connection/preflight error | Fix the reported condition; retain progress |
| `2` | Valid run remains pending after this polling window | Rerun the same command and state file |

These are admission outcome codes; ordinary CLI argument-parser usage errors also exit `2`.
Check the diagnostic and saved progress when automating resumes.

`--timeout` bounds the polling window; `0` makes one POST or GET with no polling sleep.
Each HTTP request has a 10-second connect/read timeout. An in-flight request can finish
after the polling deadline, so this is not a hard process wall-clock limit. No background
monitor remains after the command exits. Resume later with the same arguments.

Preflight errors give an action without echoing raw response bodies: 422 means inspect
Manifest/register findings; 429 means scratch capacity is busy; 502 means check the tag
and signed image availability; 503 means configure the disposable admission target.
Malformed or inconsistent verdicts never produce a handoff or replace verified evidence.

A failed run is read-only on normal reruns. After repairing a **transient environment**
failure, explicitly append `--retry-failed`. The depot may create a new run, but its commit
and image must match the saved release. For code or data changes, publish a new immutable
tag and use a new state file. Follow the kit's authorised scratch reset procedure before
retrying a partial seed: OTLP appends and importing again can duplicate observations.

## Handoff after success

Only an eligible passing verdict prints the exact `use_cases` YAML entry for the requested
slug/repository/ref, alongside its verified commit and image digest. The command does not
write or merge `registry.yaml`. Review and merge that entry through the existing depot
workflow, then sync. Newly created catalog entries default to staging; existing entries
retain their visibility. Rehearse the delivered Presenter Runbook as an admin before an
explicit publication action. Admission is deployment evidence, not a substitute for that
presenter rehearsal.
