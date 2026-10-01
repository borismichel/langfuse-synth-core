# Time-to-demo benchmark, protocol v1

Use this workflow when comparing core or authoring-skill changes. It runs locally and
writes ordinary JSON records plus evidence files. Install the candidate core with its
`[authoring,dev]` extras; `synth-authoring benchmark --help` lists the commands. Core's
Recipe/toolbox separation and Manifest contract are unchanged.

## Before each run

1. Use a clean, pinned core checkout. Keep the same machine, Python/dependency versions,
   agent/model/settings, available tools, starting kit and deployment environment across
   a baseline/candidate pair. Run one authoring session at a time in a fresh worktree
   and fresh agent context. Give the agent only the chosen brief plus the usual installed
   skills; record other supplied context. Reserve the same time budget for each run.
2. Print one fixed brief with `synth-authoring benchmark brief new-spool` (also
   `adapt-kit` and `interactive-companion`). Brief content is shipped in the wheel.
   For adaptation, select a working support-answer kit once and record its repository
   and full starting SHA under `inputs`; give every compared run that exact fixture.
   Keep the run pending if this prerequisite is unavailable.
3. Initialise the record immediately before giving the brief to the agent:

   ```bash
   synth-authoring benchmark init new-spool --source /path/to/core \
     --agent codex --model ACTUAL_MODEL --settings '{"reasoning":"high"}' \
     --out results/before-new-spool-01.json
   ```

   This captures the core SHA, working-tree cleanliness, UTC start and installed Python
   dependencies. Complete `environment.context` with hardware, agent version, available
   skills/tools, time budget, external service versions and network conditions. Record
   exact starting fixture refs in `inputs`; record the authoring skill version and any
   difference from the bundled copy in `notes`. If comparing a skill-only change, use
   its committed core revision as the candidate dimension. Reuse identical environment
   metadata for a pair only when the actual conditions match.

## Observe and finish an authoring run

Start the elapsed timer when the agent receives the brief. Stop it at the first complete
walkthrough satisfying that brief's three-beat criterion. Count all elapsed wall time,
including tool waits and human assistance. Save a session transcript and a timestamped
screen recording or equivalent action/outcome log covering every beat. An operator
checks the evidence against the brief before marking `first_walkthrough.status` as
`passed` and entering elapsed `seconds` and evidence references.

Count each human intervention after the initial brief that corrects, unblocks or supplies
missing task context. Routine permission clicks do not count. Record zero explicitly
when none occurred, and link the session log in `intervention_evidence` either way.

Run admission and rehearse through the delivered surfaces. Set each outcome to `passed`
or `failed` and attach its actual evidence; retain `pending` when unavailable. Admission
means the depot's admission run passed, not a local conformance check. Rehearsal means a
presenter executed every beat with the expected result, without developer-only actions.
Use file references relative to the record or durable URLs; keep referenced evidence
available with the record and exclude credentials from logs.

Set overall `status` to `completed` only when the first walkthrough and intervention
count are recorded and admission/rehearsal have observed outcomes (which may fail).
Use `failed` for an ended attempt that could not reach a coherent walkthrough, and
`pending` for incomplete observations. Preserve failures and pending runs alongside
completed runs; never replace them with zero-duration or passing measurements.

Validate and summarise the records:

```bash
synth-authoring benchmark validate results/*.json
synth-authoring benchmark summary results/*.json > summary.json
```

Validation checks record structure, finite nonnegative measurements, evidence references
for claimed outcomes, and consistency of pending/completed states. It does not authenticate
an evidence URL or decide whether the recorded walkthrough truly meets the brief; the
operator owns that check. Summary rejects duplicate run IDs, excludes dirty, failed and
pending runs from metrics, and reports excluded counts. It groups completed records by
brief, protocol, agent/settings, inputs and exact environment; revisions remain separate
within each group for comparison. Compare only revision columns within the same group.
Always report excluded counts with the means: completed-run timing alone hides failures.
Admission and rehearsal counts are passed counts out of that revision's `runs` denominator.

Repeat each brief at least three times per revision. Report sample count and variation
from the raw records alongside means; a single sample is smoke evidence, not a speed claim.
Rerun the unchanged briefs after rollout. If a brief or timing definition changes, increment
the protocol version and collect a new baseline instead of comparing unlike tasks.

## Offline component baseline

Measure scaffold and checks separately, using the same Python environment for both
revisions. The candidate benchmark command invokes the selected source checkout without
installing it into or modifying the active environment:

```bash
synth-authoring benchmark offline --source /path/to/baseline-core \
  --out results/before-offline-spool.json
synth-authoring benchmark offline --source /path/to/baseline-core --companion \
  --out results/before-offline-companion.json
```

Each command records subprocess wall time and output for scaffold, manifest validation,
conformance and the generated kit's tests. It stops after a failing check and preserves
that evidence. Reusing an output or evidence path is rejected. No dependency install,
container build, authored story, model interaction or deployment occurs. Source commands
execute locally, so use trusted core checkouts. Filesystem caches and machine load are
uncontrolled and are recorded as a limitation.

These are component measurements for the two scaffold shapes, not completed runs of
those briefs. Offline records keep agent, intervention and live measurements pending;
they occupy different summary groups from authoring records. Existing conformance
checks can skip optional checks; inspect the saved output before interpreting coverage.

## Recorded baseline

[2026-10-01 baseline](baseline-2026-10-01/summary.json) measures clean core revision
`7eabcbb5e71a447fba40ddbbd3de5785bf257db0`, before the demo-authoring improvements.
The two JSON records and all four command logs per record are committed alongside it.

| Offline shape | Scaffold | Validate | Conformance | Kit tests |
| --- | ---: | ---: | ---: | ---: |
| Pure-Spool | 0.224 s | 0.102 s | 0.097 s | 0.238 s |
| Companion | 0.208 s | 0.099 s | 0.251 s | 0.242 s |

Use the JSON values as the authoritative timings. Each row is a single observed run on
the recorded local environment. Agent-authoring timings, the adaptation fixture/run,
human interventions, live admission and presentation rehearsal are **pending**. This
baseline establishes the recording workflow; it does not establish overall time-to-demo
or an improvement percentage.
