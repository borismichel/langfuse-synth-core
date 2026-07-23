"""Authoring toolchain — requires the ``[authoring]`` extra.

Importing this subpackage without ``pip install 'langfuse-synth-core[authoring]'``
raises a clear ``ModuleNotFoundError``. This is the mechanical proof of the
distribution boundary: the lean *runtime* image (deployed kit / portal) carries NONE
of the authoring deps, so this import MUST fail there.

The toolchain lands here in later tickets: ``synth validate`` (#27), the determinism
golden gate + ``synth freeze`` (#28), ``synth new`` (#11-scaffold), and the kit-dev
skills. The ``target_traces`` derivation hook is the one authoring-adjacent piece that
does NOT live here — it runs at seed time and ships in the runtime library
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

__all__: list[str] = []
