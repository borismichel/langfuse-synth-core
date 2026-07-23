"""Authoring toolchain — requires the ``[authoring]`` extra.

Importing this subpackage without ``pip install 'langfuse-synth-core[authoring]'``
raises a clear ``ModuleNotFoundError``. This is the mechanical proof of the
distribution boundary: the lean *runtime* image (deployed kit / portal) carries NONE
of the authoring deps, so this import MUST fail there.

The toolchain lands here in later tickets: ``synth validate`` (#27, shipped), the
determinism golden gate + ``synth freeze`` (#28), ``synth new`` (#11-scaffold), and the
kit-dev skills. The ``target_traces`` derivation hook is the one authoring-adjacent piece
that does NOT live here — it runs at seed time and ships in the runtime library
(``langfuse_synth_core.derivation``, #29).
"""

try:
    import jsonschema as _jsonschema  # noqa: F401  — extra-only dependency
    import yaml as _yaml  # noqa: F401  — extra-only dependency (PyYAML)
except ModuleNotFoundError as exc:  # pragma: no cover — exercised by the boundary CI job
    raise ModuleNotFoundError(
        "langfuse_synth_core.authoring requires the [authoring] extra: "
        "pip install 'langfuse-synth-core[authoring]'"
    ) from exc

# The Contract validator's importable API — a strict superset of the portal's historical
# tools/validate_manifest.py (#27). The portal's POST /use-cases/sync imports this in
# Spec B; kit authors reach the same code offline through `synth validate`.
from langfuse_synth_core.authoring.validate import (  # noqa: E402
    ManifestValidationError,
    authoring_errors,
    load_schema,
    semantic_errors,
    validate_doc,
    validate_file,
    validate_path,
)

__all__ = [
    "ManifestValidationError",
    "authoring_errors",
    "load_schema",
    "semantic_errors",
    "validate_doc",
    "validate_file",
    "validate_path",
]
