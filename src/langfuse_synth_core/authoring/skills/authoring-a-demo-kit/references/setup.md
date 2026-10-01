# Set up or reuse a kit

Use this reference when selecting the core install, creating a kit or checking artifact
delivery locally. Keep an existing kit's agreed dependency pin.

## Install the authoring tools

Core is distributed by a released git tag, not assumed to be on PyPI:

```bash
pip install 'langfuse-synth-core[authoring] @ git+https://github.com/borismichel/langfuse-synth-core@v4.1.1'
synth-authoring --help
synth-authoring skills --help
```

Documentation from an unreleased core checkout may describe newer features. Use that
checkout with `pip install -e '.[authoring,dev]'` for local development, or stay with the
commands listed by the installed release. Do not substitute an unreleased version tag.

With a core build exposing `--agent`, install the pack for the agent that will author
this kit, then check its installed state:

```bash
synth-authoring skills --install --agent codex   # .agents/skills
synth-authoring skills --status --agent codex
# For Claude Code, use --agent claude (.claude/skills, the default).
```

Installation receipts record the core version and file hashes. Status distinguishes
current, stale, locally modified and unmanaged copies against the running core. Refresh
an unedited copy with `synth-authoring skills --update --agent codex`; use the same agent
and any custom `--dest` for every operation. Replacements preserve the prior copy in a
sibling `.synth-skill-backups` directory. Inspect local changes before an explicit
`--update --force`, which is needed to replace modified or unmanaged copies.

The commands report whether the separate `langfuse` skill exists in conventional
filesystem locations. Keep that prerequisite enabled for observation/evaluator craft;
plugin-managed skills and agent enablement must be checked in the agent itself. A missing
filesystem result alone cannot determine whether a plugin supplied it.

The older v4.1.1 tool supports explicit discovery directories instead:

```bash
synth-authoring skills --install --dest .agents/skills   # Codex project
# For Claude Code: --dest .claude/skills
```

That older installer has no status/update receipt workflow. Preserve an edited copy
manually before using its `--force` replacement.

## Choose one scaffold

```bash
synth-authoring new my-kit --dir ../kits
# Add these options to that invocation only when the beats require them:
# --companion         interactive surface
# --anchors           per-run kit state for the surface
# --core-ref v4.1.1    explicit released core dependency pin
# --starter regression-recovery   complete refund-policy comparison (newer tooling)
```

The opt-in `regression-recovery` starter provides a seeded baseline, stale-policy failure
and recovery, current-run story assertions and a delivered three-beat runbook. It defaults
to 24 traces and preserves the essential comparison at every supported volume (at least
three). Its scores, answers and timings are fixtures; the optional Companion remains a
basic placeholder outside that story. A default `basic` kit still starts at 1,000 traces.
Use this option only when `new --help` lists it; older tooling can build the same story by
editing the basic scaffold.

The directory belongs to the kit. Own its Recipe, verification assertions, tests and
Presenter Runbook. Keep the pipeline declarations in `usecase.yaml` aligned with the
reserved verbs in `src/synth/cli.py`; a step named `seed` runs `synth seed`.
The generated CI provides local checks and the image release workflow; consult the kit's
actual workflow and core pin when extending it.

## Preview an authored Companion

New scaffolds created with `--companion` include a development-only fixture entrypoint.
After installing the kit's dev dependencies, run in a separate process from the kit root:

```bash
python -m synth.companion.preview
```

Open the printed loopback URL (default port 8765; use `--port` to change it). Exercise the
visible fixture interaction through the same app factory as the live surface. The preview
clears inherited credentials and target configuration and blocks outgoing connections,
including loopback. Extend its explicit fixture adapter when your surface needs another
read; unsupported clients fail rather than binding a real service.

Keep the preview banner and fixture labels visible. Its health reports not-ready and it
cannot prove Langfuse writes, live evaluations or admission. Use a fresh process for live
checks. Older kits without `synth.companion.preview` need the preview scaffold changes or
their own equivalent isolated fixture surface; the command is not available merely by
upgrading an existing kit's dependency.

## Rehearse artifact production offline

Run from the kit root after installing its dev dependencies. This checks runbook delivery
without importing data, using isolated state so a dry run cannot invalidate a live receipt:

```bash
rehearsal_state_dir="$(mktemp -d)"
SYNTH_STATE_DIR="$rehearsal_state_dir" SYNTH_OUT_DIR=./out \
  synth seed --config config/demo.yaml --dry-run --set generation.target_traces=24
```

Inspect `out/DEMO_SCRIPT.md` against the committed runbook and the Manifest path. Clean up
the temporary state directory when finished. A golden adapter likewise needs temporary
state and output, independent of inherited `SYNTH_STATE_DIR`; preserve this isolation when
editing it. A successful dry run proves artifact production and local generation, not
live ingestion, verification or rehearsal of the Langfuse UI.
