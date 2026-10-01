---
name: authoring-a-demo-kit
description: >-
  Author or adapt a Demo Depot synth kit with langfuse-synth-core. Define a three-beat
  presenter journey, build a small complete walkthrough, then scale, verify and rehearse
  its delivered surfaces. Use for a new demo package, a Demo Depot use case, or story
  changes in a kit that pins langfuse-synth-core. Enforces model-free seed generation and
  delegates Langfuse observation and evaluator choices to the langfuse skill.
---

# Authoring a Demo Kit

Build a demonstrable outcome, then add volume. A **Recipe** owns the scenario; core owns
configuration, event transport, ingestion and reads. The **Manifest** (`usecase.yaml`) is
the portal's only integration surface. Keep scenario logic in the kit, not in the depot.

Follow these stages for new work. For an existing kit, retain its working beats and enter
at the earliest stage affected by the request. Preserve unrelated code and the agreed core
pin. **Seed runtime is model-free:** replay static fixtures with deterministic generation.
Read [model-free-seed.md](references/model-free-seed.md) before generating fixture content
with a model or debugging an egress failure.

## 1. Frame the demo

Extract the **audience**, business **problem**, **visible failure**, **presenter action**
and measurable **payoff** from the brief and existing kit. State reasonable defaults.
Ask only about missing decisions that would change the story or delivered interaction;
continue independent setup while awaiting an answer.

Write **three presenter beats** into `DEMO_SCRIPT.md` before expanding the dataset:

| Beat | Screen | Presenter action | Expected result | Evidence |
| --- | --- | --- | --- | --- |
| Expose the problem | Name the delivered screen or view | Exact click/filter/input | Visible failure tied to the business problem | Representative trace or score to seed |
| Investigate | Name the next screen or view | Exact inspection or comparison | Cause becomes understandable | Observation, metadata or comparison that demonstrates it |
| Demonstrate the payoff | Name the final screen or view | Exact fix/replay/compare action | Measurable improvement or decision | Expected score, outcome or before/after record |

Adapt the beats to the brief; a static comparison is sufficient when no live intervention
is promised. Give every beat a concrete expected result and evidence to create. An empty
trace list or a successful HTTP response is not yet the story's payoff.

**Complete when:** the three beats explain why this audience cares and specify what to
build and verify. Record assumptions in the runbook so the presenter can review them.

## 2. Reuse or scaffold

Inspect existing kits for the same journey before starting another. Reuse a working
Recipe where it reduces work; preserve its Manifest and library seams. Add a **Companion**
only if a beat requires an interaction the Langfuse UI cannot deliver. Use `--anchors`
when that surface needs kit-owned per-run state. For a refund-policy comparison, select
`--starter regression-recovery` to begin with linked baseline, failure and recovery
evidence; adapt the supplied runbook and assertions to the audience.

For a new kit, read [setup.md](references/setup.md) for installation and the scaffold
options, then run one suitable command, for example:

```bash
synth-authoring new my-kit --dir ../kits
cd ../kits/my-kit
pip install -e '.[dev]'
```

The scaffold supplies the manifest, seed/verify path, small golden and runbook artifact.
When building a Companion, follow setup’s credential-free preview instructions to check
its navigation and fixture interactions before connecting a live target.
For Langfuse **observation** and **evaluator** choices, use the `langfuse` skill and
[langfuse-craft.md](references/langfuse-craft.md); confirm current semantics instead of
inventing them from memory. A missing skill is an explicit setup gap, not evidence that
those choices were checked.

**Complete when:** the chosen kit installs and its existing offline checks pass. Keep
any pre-existing failures visible before changing its story.

## 3. Build the small walkthrough

Build the **smallest complete walkthrough** that supports all three beats. Start with a
few deliberately selected examples, including failure and successful comparison data;
choose the minimum volume needed for the story's statistics. Make the whole journey
coherent before increasing volume or polishing the interface.

Model the trace tree in `src/synth/materialize.py` with core event builders. Use seeded
RNG substreams, stable IDs and offsets from the provided `run_date`; sort unordered
collections. When changing generation, the volume derivation, retargeting, observation
vocabulary or transport, read [recipe-and-transport.md](references/recipe-and-transport.md).

**Edit and add scenario tests.** Assert the story's intended contrasts and representative
records through the kit's public generation and verification interfaces. Golden snapshots
are different: preserve them until a deliberate content change is reviewed, then use
`synth-authoring freeze` as described in [goldens.md](references/goldens.md).

Grow `verify` alongside the story. Check representative IDs from the **current seeded
run**, their relationships, timestamps and expected outcomes through core read interfaces.
A bounded run receipt or existing anchors should connect seed to verify without
clobbering Companion state. Unrelated project traces or any score named `quality` cannot
satisfy these assertions. Cover missing evidence and delayed visibility with bounded
polling and specific failures; preserve deterministic Spool generation.

Every presenter action must be reachable from the **delivered surfaces**: a **Companion
route** or the Langfuse UI. Keep shell instructions in a marked **developer-mode** section.
Presenter controls should be tucked away in a muted footer/disclosure, with their location
explained in the runbook. A CLI-only evaluation trigger cannot fulfil an interactive beat
(the EV failure in depot #180). A local preview can prove navigation; it cannot establish
that live data or an evaluation landed.

**Walk the small journey before scaling.** Execute the available local surfaces and
record each beat's outcome. If an essential beat requires live data, perform stage 6
with the small dataset before stage 5. Keep scaling pending until the complete small
walkthrough works; useful offline fixes can continue while a target is unavailable.

**Complete when:** all three beats have implementation, scenario assertions and an
observed small walkthrough. Identify any live outcomes still pending.

## 4. Check offline

Run the relevant local tests while editing. Before moving on, use the installed core's
`check` command, selecting the Python environment that holds the kit's dev dependencies:

```bash
synth-authoring check /absolute/path/to/my-kit --python /absolute/path/to/my-kit/.venv/bin/python --json
```

This works outside the kit directory and reports each stage independently. Inspect every
stage and require `local_ready: true`; failed, unavailable or skipped checks need action.
The selected interpreter's installed dependencies are the ones checked. If the installed
authoring tool is v4.1.1 or otherwise lacks `check`, run the existing commands from the
kit root with its dev environment activated:

```bash
synth-authoring validate usecase.yaml
synth-authoring conformance .
pytest
```

These gates cover Manifest shape, contract conformance, golden content, retargeting and
the kit's scenario tests. Inspect skipped conformance checks and install the kit's declared
dev/Companion dependencies when needed; a skip is an unmet check, not a pass. Keep local
validation separate from live verification and admission. Rerun affected offline checks
after changes; repeat the full local suite when the candidate is ready.

The scaffold's seed copies the committed runbook into `/app/out/`. To check artifact
delivery locally, select a writable output and temporary state directory; the developer
example is in [setup.md](references/setup.md). Check the declared artifact's actual file
and content. Wire and check every additional artifact's producing step.

**Complete when:** required offline gates pass, the small golden remains reviewable, and
the declared runbook is readable from the produced artifacts. This establishes offline
readiness only.

## 5. Scale the story

After the small walkthrough works, grow background history through the existing
`generation.target_traces` knob and deterministic derivation hook. Preserve the examples
needed by every beat, fixed evaluation assets and the intended proportions. Keep the
golden small; a larger production dataset does not require a huge committed snapshot.

Verify the story at the small and intended demo volumes. Use the same seed and date for
reproducible comparisons, check the evidence for each beat, then run the offline gates.
Improve presentation polish after those checks preserve the story.

**Complete when:** both scales retain the expected contrasts and presenter evidence.

## 6. Verify the live run

Use the authorised demo or disposable target. Record the kit revision, generation inputs,
target and representative evidence, excluding secrets. Seed once when that dataset is
needed, then read it back:

```bash
synth verify --config config/demo.yaml
```

Use the same configuration and environment as the successful seed. Test exact current-run
records and the scenario's payoff; inspect the verification report rather than accepting
aggregate counts. For delayed visibility, poll within the bounded timeout before deciding
whether ingestion failed.

**The Spool appends; it does not upsert.** `import-spool` is **non-resumable**. Re-seeding
an uncleared project duplicates observations. A documentation or UI edit normally needs
no seed. For changed data or a failed partial import, use the established authorised reset
of the deployment's demo data, or a fresh target, before a new import. Put that reset
procedure and its effect in the runbook; it is not a presenter's shell command.

**Complete when:** live verification passes against the recorded seeded run. If credentials,
a target or access are unavailable, report **live verification pending** with the missing
prerequisite. Continue offline work; never describe an unexecuted check as passed.

## 7. Admit and rehearse

For depot delivery, read [delivery.md](references/delivery.md). Follow the published
release/admission workflow for the exact candidate, retain the admission run and failed-rung
evidence, then rehearse through the actual delivered surfaces in staging. Local checks
and a healthy Companion endpoint do not substitute for admission or the presenter journey.

Execute every runbook beat as the presenter: screen, action, expected result. Record the
actual outcome, representative links/IDs and a timestamped action log or screenshots.
Check links, reset instructions and artifact access. Fix failed beats and repeat affected
steps; expand the rehearsal when the change affects the whole journey.

**Complete when:** the delivered journey has evidence for all three beats and an explicit
admission result. Keep **admission pending** or **rehearsal pending** when access is missing.
Publishing remains a distinct authorised action after staging rehearsal.

## What "done" looks like

Hand over the kit revision, Presenter Runbook, representative evidence and readiness status:

- **Offline:** validation, conformance and scenario/determinism tests passed; skips disclosed.
- **Live:** current-run verification passed, or its missing prerequisite is named.
- **Admission:** the exact candidate's run and verdict, or pending with a reason.
- **Rehearsal:** all beats worked through the delivered surfaces, with presenter controls
  tucked away and evidence attached; otherwise list the failed or unexecuted beats.

A kit is demo-ready only when its required live, admission and rehearsal stages have passed.
For measured time-to-demo comparisons, use the core
[benchmark protocol](https://github.com/borismichel/langfuse-synth-core/blob/main/docs/benchmarks/README.md)
when available in the installed core. Compare fixed briefs and observed outcomes; shorter
instructions or passing offline tests alone do not demonstrate a faster successful demo.
