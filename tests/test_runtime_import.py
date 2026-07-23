"""Runtime import smoke — the public surface a deployed kit / the portal relies on.

These assertions must hold on a BARE runtime install (`pip install .`), with none of
the [authoring] deps present.
"""

import importlib.util

import pytest


def test_package_imports_and_is_versioned():
    import langfuse_synth_core as core

    assert isinstance(core.__version__, str)


def test_companion_shell_seam_is_exposed():
    from langfuse_synth_core.companion import CompanionAdapter

    # The seam is a structural Protocol Spec G implements; here we only assert it exists
    # and names the six-thing contract.
    for method in ("invoke", "intake_secrets", "health", "start", "stop",
                   "resolve_llm_credential"):
        assert hasattr(CompanionAdapter, method)


def test_derivation_hook_ships_in_runtime():
    # The hook runs at seed time, so it MUST be importable from the runtime lib — never
    # gated behind [authoring].
    from langfuse_synth_core.derivation import DerivationHook, identity_derivation

    assert DerivationHook is not None
    assert identity_derivation(7, {}) == {"target_traces": 7}


def test_authoring_is_absent_without_the_extra():
    """If the authoring deps are not installed, importing the subpackage must fail loudly.

    On an `[authoring]`-including install this assertion is not applicable, so it is
    skipped — the boundary is proved by the runtime-only CI job.
    """
    if importlib.util.find_spec("jsonschema") is not None:
        pytest.skip("authoring extra is installed; boundary proved by the runtime-only job")
    with pytest.raises(ModuleNotFoundError):
        import langfuse_synth_core.authoring  # noqa: F401
