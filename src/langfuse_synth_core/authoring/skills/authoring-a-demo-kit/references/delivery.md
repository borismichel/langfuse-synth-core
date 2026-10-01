# Release, admission and presenter evidence

Read after local checks pass and the candidate is ready for depot delivery. Use the
[kit author guide](https://github.com/borismichel/langfuse-demo-depot/blob/main/docs/user/kit-author-guide.md)
and the kit's release workflow for the current commands, credentials and environment.
Keep each status separate; readiness is evidence, not a single successful command.

1. **Release the exact candidate through the authorised workflow.** Capture its source
   ref and CI-published signed image digest. Changing the candidate invalidates conclusions
   that depended on its old build. Reuse existing release evidence when it still matches.
2. **Run admission against a disposable target.** Its ladder checks register, build,
   spawn, seed and verify, plus Companion smoke for kits that declare a live component.
   Save the admission run ID, candidate ref, verdict and any failing rung's logs. A local
   conformance pass is not admission. The kit's `verify` owns scenario facts; the portal
   consumes its exit result.
3. **Register the admitted candidate and keep it in staging for rehearsal.** Follow the
   registry/sync workflow only after the candidate is eligible to pin. Check actual
   visibility and member access in the running depot; don't assume that an older
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
