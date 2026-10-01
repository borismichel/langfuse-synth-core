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

The backwards-compatible skill install uses an explicit discovery directory:

```bash
synth-authoring skills --install --dest .agents/skills   # Codex project
# Alternatively, for Claude Code:
synth-authoring skills --install --dest .claude/skills
```

If `skills --help` exposes `--agent`, `--status` and `--update`, use the matching agent
option for installation and subsequent status/update checks. Inspect local modifications
before replacing copies; use the installed tool's documented backup policy. Older installs
refuse an existing destination unless forced; preserve your edited copy yourself before
forcing them. Keep the separate `langfuse` skill discoverable and enabled for craft work.

## Choose one scaffold

```bash
synth-authoring new my-kit --dir ../kits
# Add these options to that invocation only when the beats require them:
# --companion         interactive surface
# --anchors           per-run kit state for the surface
# --core-ref v4.1.1    explicit released core dependency pin
```

The directory belongs to the kit. Own its Recipe, verification assertions, tests and
Presenter Runbook. Keep the pipeline declarations in `usecase.yaml` aligned with the
reserved verbs in `src/synth/cli.py`; a step named `seed` runs `synth seed`.
The generated CI provides local checks and the image release workflow; consult the kit's
actual workflow and core pin when extending it.

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
