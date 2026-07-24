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
  companion.py      Companion Adapter shell contract seam (runtime; Spec G implements)
  derivation.py     target_traces derivation hook home (runtime; #29 completes)
  rng.py            single-seed deterministic RNG + W3C-format ID substreams (#31)
  pricing.py        token counts x per-model pricing -> usage/cost details (#31)
  distributions.py  log-normal latency + model-appropriate token sampling (#31)
  lfclient.py       Langfuse v4 SDK client construction (#31)
  config.py         structural Protocols (Config/Model/Target) the core reads against (#31)
  timegen.py        the ISO-8601 formatting primitive event bodies need (#31)
  seed/events.py    batch-ingestion event-envelope builders (#31)
  live/theme.py     Langfuse design tokens + page shell (#31)
  live/paths.py     prefix-aware internal paths (LIVE_BASE_PATH) (#31)
  authoring/        authoring toolchain — import fails without the [authoring] extra
docs/SEAM.md        the library/kit hand-off rule + the "not a framework" verdict
docs/INSTALL.md     git-pinned private install + build-secret pattern
CONTRACT.md         reserved home for the relocated Contract (#27)
```

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[authoring,dev]'
ruff check .
pytest
```

## Versioning

`0.0.0` = pre-Ring-1 scaffold. **`v0.1.0`** is tagged only when the byte-identical core is
extracted and **both** kits are golden-green (Ring 1b, #32); **`v1.0.0`** after Ring 2
(#34). Kits upgrade deliberately by bumping their pin.
