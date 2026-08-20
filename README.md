# langfuse-synth-core

Shared synthesis **library** + **Authoring SDK** for Demo Depot demo kits.

This is the consolidated engine library — the machine that speaks the Langfuse data model
bidirectionally — plus an optional authoring toolchain behind the `[authoring]` extra. It
is a **toolbox the kit calls, not an inversion-of-control framework** (T2 verdict:
flexibility > deduplication). See [`docs/SEAM.md`](docs/SEAM.md) for the hand-off rule.

> **Status: Ring 1a landed (#31).** On top of the scaffold (#26) — the distribution spine,
> the runtime-vs-`[authoring]` boundary, and the runtime seams (companion adapter shell,
> `target_traces` derivation hook) — the **byte-identical core** is now extracted from the
> gold-standard kits: RNG/ID substreams, token pricing, statistical distributions, the
> Langfuse client, event emission, and the scenario-agnostic UI primitives (theme / paths).
> EV consumes it and reproduces its Step 0 golden byte-for-byte. Lender is wired in Ring 1b
> (#32), where `v0.1.0` is tagged; the config loader / time sampling / ingest / probe
> ("library-with-parameters" middle field) follow in Ring 2 (#33–#34). Tracked under
> [Spec A · #19](https://github.com/borismichel/langfuse-demo-depot/issues/19).

## Install

```bash
# Runtime (deployed kit / portal) — carries none of the authoring deps:
pip install langfuse-synth-core

# Authoring (a kit author's dev box):
pip install 'langfuse-synth-core[authoring]'
```

Kits pin it as a **git dependency** by tag/SHA. The repo is **public** (consistent with
the public kits it is the shared DNA of), so kits install it with a plain `pip install` —
no build secret — see [`docs/INSTALL.md`](docs/INSTALL.md) and
[`examples/kit.Dockerfile`](examples/kit.Dockerfile).

## Layout

```
src/langfuse_synth_core/
  __init__.py       public surface + version
  companion/        Companion Adapter shell contract seam + shared LLM resolution (runtime; Spec G)
  derivation.py     target_traces derivation hook home (runtime; #29 completes)
  rng.py            single-seed deterministic RNG + W3C-format ID substreams (#31)
  pricing.py        token counts x per-model pricing -> usage/cost details (#31)
  distributions.py  log-normal latency + model-appropriate token sampling (#31)
  lfclient.py       Langfuse v4 SDK client construction (#31)
  read.py           the READ seam — traces/observations/scores/sessions/experiments,
                    normalised off the v4 read APIs (#208, v4-only since #213)
  lfread.py         the raw authenticated GET for the endpoints the seam does not model (#33, #208)
  config.py         structural Protocols (Config/Model/Target) the core reads against (#31)
  timegen.py        the ISO-8601 formatting primitive event bodies need (#31)
  seed/events.py    the Spool's wire-object builders — OTLP spans, `score-create` (#31, #206)
  seed/otlp.py      the OTLP wire model + Spool finalisation (#206)
  live/emit.py      the LIVE-EMISSION seam — wall-clock traces via the Langfuse SDK (#208)
  live/theme.py     Langfuse design tokens + page shell (#31)
  live/paths.py     prefix-aware internal paths (LIVE_BASE_PATH) (#31)
  authoring/        authoring CLI (synth-authoring validate/freeze/new/skills) + scaffold
                    templates + kit-dev skills — import fails without the [authoring] extra
docs/SEAM.md        the library/kit hand-off rule + the "not a framework" verdict
docs/INSTALL.md     git-pinned private install + build-secret pattern
docs/WRITE_PATHS.md how the Spool is written, and why an import is non-resumable
docs/READ_LIVE_SEAMS.md
                    the read seam + the live-emission seam, and the line between them
CONTRACT.md         reserved home for the relocated Contract (#27)
```

## Authoring CLI

The `[authoring]` extra installs the `synth-authoring` console script — the kit authoring
toolchain (namespaced `synth-authoring`, never `synth`, so it can't shadow a kit's own
`synth` runtime entry point):

```bash
# Scaffold a new kit — a runnable-green walking skeleton (#36). Emits the full file floor
# (schema-valid usecase.yaml with the canonical generation.target_traces knob, seed+verify
# wired through the library, the identity derivation hook, a render:markdown Presenter
# Runbook, the reference non-root Dockerfile), then blesses the initial determinism golden
# so the fresh kit passes `synth-authoring validate` AND the golden gate on first generation.
# The emitted kit is v4-native (portal #207): its Spool is a stream of OTLP spans
# (docs/WRITE_PATHS.md) and its verify reads the v4 APIs.
synth-authoring new my-kit                 # -> ./my-kit/
synth-authoring new my-kit --dir ../kits   # parent dir; kit lands at ../kits/my-kit
synth-authoring new my-kit --companion     # also emit the companion stub (full: Spec G)
synth-authoring new my-kit --core-ref v4.0.0    # lib git ref the kit pins to (a tag)

# Offline Contract lint of a manifest (#27) — same validator the portal runs at sync time.
synth-authoring validate path/to/usecase.yaml

# Bless / re-bless the determinism golden for a seed (#28) — runs seed under the deny-LLM
# egress block, so a deliberate pool change is one intentional re-bless, never a hand-edit.
synth-authoring freeze module:seed --golden tests/golden/spool.ndjson --target-traces 300

# Locate / install the kit-dev skills — the agent pack shipped and versioned with the lib
# (#37). `authoring-a-demo-kit` walks a coding agent scaffold -> trace tree -> derivation ->
# runbook -> gates, and delegates Langfuse craft to the existing `langfuse` skill.
synth-authoring skills                 # list the shipped skills + their descriptions
synth-authoring skills --install       # copy them into .claude/skills/ so an agent finds them
```

A freshly scaffolded kit is green from its first commit: `cd my-kit && pip install -e
'.[dev]' && pytest`.

## Kit-dev skills (the agent pack)

The Authoring SDK is **agent-first** — a coding agent authors ~99% of new demos — so the
`[authoring]` extra ships **kit-dev skills** versioned with the library (so the Contract,
its validator, and the skills that teach them can never drift). The orchestrator skill
[`authoring-a-demo-kit`](src/langfuse_synth_core/authoring/skills/authoring-a-demo-kit/SKILL.md)
walks scaffold → model the trace tree → wire the `target_traces` derivation → runbook → run
the gates; it enforces the **model-free-seed** law (with the author-time-LLM-frozen-fixture
escape hatch, re-blessed via `synth-authoring freeze`) and **delegates Langfuse craft**
(which observation type, which evaluator type) to the existing `langfuse` skill rather than
duplicating it. `synth-authoring skills --install` copies the pack into `.claude/skills/`.

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[authoring,dev]'
ruff check .
pytest
```

## Versioning

`langfuse_synth_core.__version__` is derived from the installed distribution's
metadata, so it always matches the `pyproject.toml` version (guarded by
`tests/test_runtime_import.py`; #145). History: **`v0.1.0`** was tagged when the
byte-identical core was extracted and **both** kits went golden-green (Ring 1b, #32);
**`v1.0.0`** after Ring 2 (#34). Kits upgrade deliberately by bumping their pin.

Cutting a version is not done until **every consuming kit is re-pinned to it** — follow
the checklist in [`RELEASING.md`](RELEASING.md), which lists the kits that must be bumped.
