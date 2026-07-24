"""``synth-authoring validate`` — the offline, static Contract lint (Spec A, #27).

This is the relocated, single-version home of the Demo Depot **Contract validator**.
It is an *importable strict superset* of the portal's historical
``tools/validate_manifest.py``: it reproduces every Draft7 schema error AND the exact
LLM-provider ``semantic_errors`` rules the portal enforced, then adds the new
kit-authoring checks (:func:`authoring_errors`).

Because it is literally the same code and schema a kit author runs offline and the
portal imports at sync time, "passes ``synth-authoring validate`` locally" ≡ "passes portal sync"
**by construction**. Wiring ``POST /use-cases/sync`` to import this and retiring the
portal's own copy is Spec B — out of scope here; this module only makes the validator
importable and a strict superset.

The schema, the reserved-verb semantics, and the filesystem conventions are documented
in ``CONTRACT.md`` at the repo root. This module lives under
``langfuse_synth_core.authoring`` and therefore requires the ``[authoring]`` extra — it
is authoring tooling, never part of the lean runtime install.
"""

from __future__ import annotations

import json
import shlex
import sys
from importlib import resources
from pathlib import Path

import yaml
from jsonschema import Draft7Validator

# Reserved pipeline-step verbs: an id equal to one of these maps to a job of that kind
# (probe|plan|seed|verify|resume|teardown); any other id maps to kind=custom_step. See
# CONTRACT.md. A reserved-verb step must actually run that verb.
RESERVED_VERBS = frozenset({"probe", "plan", "seed", "verify", "resume", "teardown"})

# The step ids a spec-compliant kit must always wire (the determinism + read-back spine).
REQUIRED_STEPS = ("seed", "verify")

# The single, cross-kit operator volume knob (Spec A §4 / #29). A volume-adjustable kit
# exposes exactly this in its config_schema; a genuinely fixed-volume kit exposes no
# volume param at all.
CANONICAL_VOLUME_KNOB = "generation.target_traces"

# Bespoke operator volume knobs the canonical knob supersedes. Under the new Contract
# these become kit-INTERNAL params (never operator-facing). The two gold manifests still
# ship them pre-migration (their adoption of the canonical knob is Ring 2, #33/#34), so a
# manifest exposing ONLY a bespoke knob is grandfathered here; what is rejected is the
# ambiguous half-migrated state of exposing a bespoke knob ALONGSIDE the canonical one.
BESPOKE_VOLUME_KNOBS = frozenset({"generation.total_traces", "generation.volume.scale"})


class ManifestValidationError(ValueError):
    """A usecase.yaml failed validation — blocks sync with a readable error.

    Preserved from the portal validator: ``POST /use-cases/sync`` maps this to HTTP 422
    and MUST emit an ``audit_event`` for the rejected manifest.
    """

    http_status = 422


def load_schema() -> dict:
    """Load the one versioned copy of the use-case JSON Schema shipped in this package."""
    text = resources.files(__package__).joinpath("usecase.schema.json").read_text()
    return json.loads(text)


# --------------------------------------------------------------------------------------
# Portal-parity layer: reproduced verbatim from tools/validate_manifest.py so the
# relocated validator is a STRICT SUPERSET of what the portal enforced (LAN-378/LAN-400).
# --------------------------------------------------------------------------------------


def _declared_secrets(doc: dict) -> set[str]:
    """Union of every ``requires_secrets`` token across all live components."""
    secrets: set[str] = set()
    components = doc.get("live_components")
    if isinstance(components, list):
        for comp in components:
            if isinstance(comp, dict):
                reqs = comp.get("requires_secrets")
                if isinstance(reqs, list):
                    secrets.update(s for s in reqs if isinstance(s, str))
    return secrets


def semantic_errors(doc: dict) -> list[str]:
    """LLM-provider contract rules not expressible in JSON Schema (LAN-378 / LAN-400).

    Reproduced exactly from the portal validator:

      (a) ``LLM_API_KEY`` in any ``requires_secrets`` requires a top-level ``llm`` block.
      (b) a manifest may NOT mix ``LLM_API_KEY`` and ``ANTHROPIC_API_KEY``.
      (c) ``llm.models`` keys must be a subset of ``llm.providers``.

    Back-compat: a bare ``ANTHROPIC_API_KEY`` with no ``llm`` block trips none of these.
    """
    errors: list[str] = []
    secrets = _declared_secrets(doc)
    llm = doc.get("llm")
    has_llm = isinstance(llm, dict)

    if "LLM_API_KEY" in secrets and not has_llm:
        errors.append(
            "  at (root): a live component's requires_secrets lists the LLM_API_KEY "
            "sentinel but there is no top-level `llm` block declaring providers"
        )
    if "LLM_API_KEY" in secrets and "ANTHROPIC_API_KEY" in secrets:
        errors.append(
            "  at (root): requires_secrets may not mix LLM_API_KEY and "
            "ANTHROPIC_API_KEY — declare one or the other, not both"
        )
    if has_llm:
        providers = llm.get("providers")
        models = llm.get("models")
        if isinstance(models, dict) and isinstance(providers, list):
            provider_set = {p for p in providers if isinstance(p, str)}
            extra = sorted(k for k in models if k not in provider_set)
            if extra:
                errors.append(
                    f"  at llm/models: model key(s) {extra} not in llm.providers "
                    f"{list(providers)}"
                )
    return errors


# --------------------------------------------------------------------------------------
# New authoring checks (#27): seed+verify present · reserved-verb semantics ·
# >=1 render:markdown artifact · canonical target_traces knob consistency.
# --------------------------------------------------------------------------------------


def _pipeline_steps(doc: dict) -> list[dict]:
    steps = doc.get("pipeline")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _synth_subcommand(run: str) -> str | None:
    """The subcommand a ``run`` command invokes, i.e. the token after ``synth``.

    Returns ``None`` when the command does not invoke a ``synth`` entry point (a kit is
    free to run arbitrary containers for custom steps; reserved-verb semantics only bind
    steps whose id is a reserved verb).
    """
    try:
        tokens = shlex.split(run)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        # Match a bare `synth` or a pathed one (e.g. /usr/local/bin/synth).
        if tok == "synth" or tok.rsplit("/", 1)[-1] == "synth":
            return tokens[i + 1] if i + 1 < len(tokens) else None
    return None


def _config_schema_properties(doc: dict) -> dict:
    cfg = doc.get("config_schema")
    if isinstance(cfg, dict):
        props = cfg.get("properties")
        if isinstance(props, dict):
            return props
    return {}


def _required_steps_errors(doc: dict) -> list[str]:
    ids = {s.get("id") for s in _pipeline_steps(doc)}
    return [
        f"  at pipeline: missing the required `{step}` step "
        f"(a spec-compliant kit wires both {' + '.join(REQUIRED_STEPS)})"
        for step in REQUIRED_STEPS
        if step not in ids
    ]


def _reserved_verb_errors(doc: dict) -> list[str]:
    """A step whose id is a reserved verb must actually invoke ``synth <verb>``."""
    errors: list[str] = []
    for i, step in enumerate(_pipeline_steps(doc)):
        sid = step.get("id")
        run = step.get("run")
        if sid in RESERVED_VERBS and isinstance(run, str):
            sub = _synth_subcommand(run)
            if sub != sid:
                invoked = f"`synth {sub}`" if sub else "no `synth` subcommand"
                errors.append(
                    f"  at pipeline/{i}/run: step id `{sid}` is a reserved verb but the "
                    f"run command invokes {invoked} — a reserved-verb step must run "
                    f"`synth {sid}`"
                )
    return errors


def _markdown_artifact_errors(doc: dict) -> list[str]:
    artifacts = doc.get("artifacts")
    has_md = isinstance(artifacts, list) and any(
        isinstance(a, dict) and a.get("render") == "markdown" for a in artifacts
    )
    if has_md:
        return []
    return [
        "  at artifacts: at least one artifact must declare `render: markdown` "
        "(the Presenter Runbook the operator reads to walk the demo)"
    ]


def _volume_knob_errors(doc: dict) -> list[str]:
    """Canonical ``generation.target_traces`` knob consistency (Spec A §4 / #29).

    The Contract: a volume-adjustable kit exposes the canonical ``generation.target_traces``
    integer knob as its *sole* operator volume control; a fixed-volume kit exposes no
    volume param. This check enforces that consistency without regressing the pre-migration
    gold manifests (which still ship a bespoke ``total_traces`` / ``volume.scale`` knob):

      * when the canonical knob is present it MUST be an integer knob, and
      * it may NOT be exposed alongside a bespoke knob (the ambiguous half-migrated state).

    A manifest exposing only a bespoke knob is grandfathered (its retirement to the
    canonical knob is Ring 2, #33/#34); a manifest exposing neither is a fixed-volume kit.
    """
    props = _config_schema_properties(doc)
    canonical = props.get(CANONICAL_VOLUME_KNOB)
    has_canonical = CANONICAL_VOLUME_KNOB in props
    bespoke = sorted(k for k in props if k in BESPOKE_VOLUME_KNOBS)

    errors: list[str] = []
    if has_canonical:
        if not (isinstance(canonical, dict) and canonical.get("type") == "integer"):
            errors.append(
                f"  at config_schema/properties/{CANONICAL_VOLUME_KNOB}: the canonical "
                "volume knob must be declared as an integer"
            )
        if bespoke:
            errors.append(
                f"  at config_schema/properties: exposes the canonical "
                f"{CANONICAL_VOLUME_KNOB} alongside bespoke volume knob(s) {bespoke} — "
                "the canonical knob must be the sole operator volume control"
            )
    return errors


def authoring_errors(doc: dict) -> list[str]:
    """The new #27 kit-authoring checks, layered on top of schema + semantic parity."""
    errors: list[str] = []
    errors.extend(_required_steps_errors(doc))
    errors.extend(_reserved_verb_errors(doc))
    errors.extend(_markdown_artifact_errors(doc))
    errors.extend(_volume_knob_errors(doc))
    return errors


# --------------------------------------------------------------------------------------
# The single choke point: Draft7 schema + semantic parity + new authoring checks.
# --------------------------------------------------------------------------------------


def validate_doc(doc: dict, validator: Draft7Validator) -> list[str]:
    """Validate an already-parsed manifest mapping; return human-readable errors."""
    if not isinstance(doc, dict):
        return ["manifest is not a mapping / object"]
    errors: list[str] = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"  at {loc}: {err.message}")
    errors.extend(semantic_errors(doc))
    errors.extend(authoring_errors(doc))
    return errors


def validate_file(path: Path, validator: Draft7Validator) -> list[str]:
    """Return a list of human-readable errors for the manifest at ``path`` (empty = valid)."""
    try:
        doc = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as exc:  # pragma: no cover - surfaced to user
        return [f"YAML parse error: {exc}"]
    return validate_doc(doc, validator)


def validate_path(path: str | Path) -> list[str]:
    """Convenience one-call API for a downstream consumer (loads the pinned schema).

    This is the importable surface the portal's ``POST /use-cases/sync`` uses in Spec B.
    """
    return validate_file(Path(path), Draft7Validator(load_schema()))


def run(paths: list[str]) -> int:
    """``synth-authoring validate <path>...`` — readable red/green per manifest. 0 = all valid."""
    if not paths:
        print("usage: synth-authoring validate <path/to/usecase.yaml> [more.yaml ...]")
        return 2
    validator = Draft7Validator(load_schema())
    ok = True
    for arg in paths:
        path = Path(arg)
        errs = validate_file(path, validator)
        if errs:
            ok = False
            print(f"✗ {path} — INVALID")
            print("\n".join(errs))
        else:
            doc = yaml.safe_load(path.read_text())
            slug = doc.get("slug") if isinstance(doc, dict) else None
            print(f"✓ {path} — valid (slug: {slug})")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """Entry point used when ``synth-authoring validate`` is dispatched from the CLI."""
    args = list(sys.argv[1:] if argv is None else argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
