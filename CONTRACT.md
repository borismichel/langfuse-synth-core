# CONTRACT.md — reserved home (filled by #27)

The Contract — the JSON Schema, the validator logic, the reserved-verb semantics, and
the filesystem conventions — relocates into this repository as its single versioned home
in **#27 (Contract home + `synth validate`)**. It is lifted out of the portal's
`schemas/usecase.schema.json`, `tools/validate_manifest.py`, and the prose scattered
across schema `description` strings.

Until #27 lands, the authoritative Contract still lives in the portal repo
(`langfuse-demo-depot`). This file reserves the home and the name so downstream links are
stable.

See [`docs/SEAM.md`](docs/SEAM.md) for the library/kit hand-off rule.
