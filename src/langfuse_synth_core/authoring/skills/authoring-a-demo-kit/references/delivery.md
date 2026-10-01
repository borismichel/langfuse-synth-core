# Release, admission and presenter evidence

Read after local checks pass and the candidate is ready for depot delivery. Use the
[kit author guide](https://github.com/borismichel/langfuse-demo-depot/blob/main/docs/user/kit-author-guide.md)
and the kit's release workflow for the current commands, credentials and environment.
Keep each status separate; readiness is evidence, not a single successful command.

## Start or resume admission

Use a current depot with the admission Manifest-slug guard and a configured disposable
target. Follow the [admission connection and handoff guide](https://github.com/borismichel/langfuse-synth-core/blob/main/docs/ADMISSION.md)
and the deployed instance's [admin session setup](https://github.com/borismichel/langfuse-demo-depot/blob/main/docs/user/admin-guide.md#5-admission-runs-api-only).
An administrator establishes a short-lived admin session bearer token. Load it into
`DEMO_DEPOT_TOKEN` through the approved credential workflow; this is not a Langfuse key,
and the CLI does not need the session signing secret. For another variable, pass its
name with `--token-env NAME`; never put the token in command arguments or progress files.

With an authoring build whose help lists `admit`, run:

```bash
synth-authoring admit \
  --repo https://github.com/your-org/your-kit \
  --slug your-kit \
  --ref v1.2.3 \
  --portal https://depot.example.com \
  --state .scratch/admission-your-kit-v1.2.3.json \
  --timeout 60 --poll-interval 2
```

Use the Manifest's exact slug and a released, signed candidate. Keep the `vX.Y.Z` tag
and image immutable; never move a tag or rebuild its digest. The portal value is the API
origin without an API path. The first call POSTs the candidate; once saved, its run ID
is resumed with GET. Repeat the same command and progress file to resume. The file holds
only nonsecret candidate/run identity, resolved commit, image digest and status; keep
one active invocation per file. Resume reads that run, not a newly resolved tag.

Exit `0` means eligible and passed; `1` means failed or an input/connection error; `2`
means the valid run is still pending after the polling window. CLI usage errors also
exit `2`, so inspect the diagnostic before scheduling a resume. The polling window is
bounded by `--timeout`; each HTTP request has a 10-second connect/read timeout and may
finish after that window. No background monitor remains. Refresh an expired session
and resume with the same file. Missing access means **admission pending**.

A failed run stays read-only unless explicitly retried with `--retry-failed` after a
transient environment repair. A retry must retain the saved commit and image digest;
code/data changes need a new immutable release and state file. Retain the failed rung
and inspect detailed logs in the authenticated portal rather than copying secrets into
handoff notes. Follow the authorised scratch reset procedure before retrying a partial
seed, because imported observations append.

Older authoring builds such as v4.1.1 lack this command. Use the admin guide's existing
manual admission POST/GET workflow with the same candidate and evidence requirements,
or install a newer authoring build separately while preserving the kit's runtime pin.
Neither path creates a release, writes the registry or publishes a kit automatically.

## Delivery evidence checklist

1. **Release the exact candidate through the authorised workflow.** Capture its source
   ref and CI-published signed image digest. Changing the candidate invalidates conclusions
   that depended on its old build. Reuse existing release evidence when it still matches.
2. **Run admission against a disposable target.** Its ladder checks register, build,
   spawn, seed and verify, plus Companion smoke for kits that declare a live component.
   Save the admission run ID, candidate ref, verdict and any failing rung's logs. A local
   conformance pass is not admission. The kit's `verify` owns scenario facts; the portal
   consumes its exit result.
3. **Register the admitted candidate and keep it in staging for rehearsal.** Review and
   merge the passing command's registry snippet through the existing manual registry/sync
   workflow only after the candidate is eligible to pin. New catalog entries default to
   staging; existing entries retain visibility. Check actual visibility and member access in the running depot; don't assume that an older
   deployment has the new staging default. Use the supported admin staging controls
   before exposing the kit to members.
4. **Rehearse the delivered runbook as the presenter.** Open the produced artifact and
   every link. Execute the three beats through the Companion or Langfuse UI; verify the
   expected failure, investigation and payoff. Place presenter-only controls in a muted,
   collapsed disclosure or footer. Shell commands belong only in developer-mode notes.
5. **Hand over observed evidence.** Record the kit ref, run/config inputs, target, runbook
   artifact, representative IDs/links, admission verdict and a timestamped beat-by-beat
   action/outcome log or screenshots. Exclude credentials. Mark every failed or unexecuted
   step explicitly; use pending when a required target, role or credential is unavailable.

A smoke check proves only that the surface starts. Rehearsal proves the complete promised
journey. Resolve failures before publication, and publish only within the user's authority.
Record remaining gaps plainly rather than fabricating live results from offline fixtures.
