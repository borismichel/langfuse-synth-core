# langfuse-synth-core

Shared synthesis **library** + **Authoring SDK** for Demo Depot demo kits.

This is the consolidated engine library — the machine that speaks the Langfuse data model
bidirectionally — plus an optional authoring toolchain behind the `[authoring]` extra. It
is a **toolbox the kit calls, not an inversion-of-control framework** (T2 verdict:
flexibility > deduplication). See [`docs/SEAM.md`](docs/SEAM.md) for the hand-off rule.

> **Status: scaffold (#26).** This repo currently establishes the distribution spine, the
> runtime-vs-`[authoring]` boundary, and the runtime seams (companion adapter shell,
> `target_traces` derivation hook). The synthesis machinery is extracted from the two
> gold-standard kits in the Ring 1 / Ring 2 migration (#31–#34). Tracked under
> [Spec A · #19](https://github.com/borismichel/langfuse-demo-depot/issues/19).

## Install

```bash
# Runtime (deployed kit / portal) — carries none of the authoring deps:
pip install langfuse-synth-core

# Authoring (a kit author's dev box):
pip install 'langfuse-synth-core[authoring]'
```

Kits pin it as a **git dependency** by tag/SHA and install it in Docker via a **build
secret** — see [`docs/INSTALL.md`](docs/INSTALL.md) and
[`examples/kit.Dockerfile`](examples/kit.Dockerfile).

## Layout

```
src/langfuse_synth_core/
  __init__.py       public surface + version
  companion.py      Companion Adapter shell contract seam (runtime; Spec G implements)
  derivation.py     target_traces derivation hook home (runtime; #29 completes)
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
