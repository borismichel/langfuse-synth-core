"""Authoring toolchain — requires the ``[authoring]`` extra.

Importing this subpackage without ``pip install 'langfuse-synth-core[authoring]'``
raises a clear ``ModuleNotFoundError``. This is the mechanical proof of the
distribution boundary: the lean *runtime* image (deployed kit / portal) carries NONE
of the authoring deps, so this import MUST fail there.

The toolchain lands here in later tickets: ``synth validate`` (#27), the determinism
golden gate + ``synth freeze`` (#28), ``synth new`` (#11-scaffold), and the kit-dev
skills. The ``target_traces`` derivation HOOK is the one authoring-adjacent piece that
does NOT live here — it runs at seed time and ships in the runtime library
(``langfuse_synth_core.derivation``, #29). The author-time knob **injector** for that
same volume knob (``inject_target_traces``) does live here: it needs ``jsonschema`` to
prove the emitted knob is schema-valid, and it only ever runs at authoring time.
"""

try:
    import jsonschema as _jsonschema  # noqa: F401  — extra-only dependency
    import yaml as _yaml  # noqa: F401  — extra-only dependency (PyYAML)
except ModuleNotFoundError as exc:  # pragma: no cover — exercised by the boundary CI job
    raise ModuleNotFoundError(
        "langfuse_synth_core.authoring requires the [authoring] extra: "
        "pip install 'langfuse-synth-core[authoring]'"
    ) from exc

# The SDK one-liner that declares the canonical generation.target_traces volume knob in a
# kit's config_schema (#29). Author-time helper — its jsonschema dependency is why it
# lives behind this extra; the runtime DerivationHook + identity default stay in
# langfuse_synth_core.derivation.
from langfuse_synth_core.authoring.knob import (  # noqa: E402
    inject_target_traces,
    target_traces_knob,
)

__all__: list[str] = ["inject_target_traces", "target_traces_knob"]
