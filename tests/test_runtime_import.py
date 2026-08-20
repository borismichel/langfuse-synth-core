"""Runtime import smoke — the public surface a deployed kit / the portal relies on.

These assertions must hold on a BARE runtime install (`pip install .`), with none of
the [authoring] deps present.
"""

import importlib.util
import tomllib
from pathlib import Path

import pytest


def test_package_imports_and_is_versioned():
    import langfuse_synth_core as core

    assert isinstance(core.__version__, str)


def test_version_matches_packaging_version():
    # #145 (Spec G · G6): `__version__` sat at the pre-Ring-1 "0.0.0" for five releases
    # because nothing asserted it. It is now derived from the installed distribution's
    # metadata, and this guard pins that metadata to `pyproject.toml`, so the attribute
    # can never silently drift from the packaging version again. (A stale editable
    # install fails here by design — reinstall with `pip install -e .`.)
    import langfuse_synth_core as core

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    packaged = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert core.__version__ == packaged


def test_companion_shell_seam_is_exposed():
    # Spec G · G2 (#140) FINALIZED the seam Spec A shipped as a placeholder: the concrete
    # runtime shell + its structural Protocol + the invocation/readiness value types are the
    # public surface a deployed companion relies on. All must resolve on a BARE runtime
    # install — the web-server deps are imported lazily and ride the [companion] extra.
    from langfuse_synth_core.companion import (
        CompanionAdapter,
        CompanionAdapterContract,
        Invocation,
        ReadinessReport,
        parse_invocation,
    )

    # The concrete shell exposes the six-responsibility surface (+ readiness).
    # (`ingestor` was in this list until #213: the Spool's backdating writer came off the
    # Adapter with the batch path — a live surface stamps wall-clock.)
    for method in ("langfuse", "emitter", "reader", "read_json", "llm",
                   "readiness", "mount_health", "serve", "make_server", "run"):
        assert callable(getattr(CompanionAdapter, method))
    # The seam stays a runtime-checkable Protocol the shell structurally satisfies.
    assert isinstance(parse_invocation(["-c", "x.yaml", "--host", "0.0.0.0", "--port", "9"]),
                      Invocation)
    assert hasattr(ReadinessReport, "ok")
    assert hasattr(CompanionAdapterContract, "readiness")


def test_companion_llm_ships_in_runtime_without_sdks():
    # G1 (#138): the extracted LLM-resolution module must be importable on a bare
    # runtime install — the anthropic/openai SDKs are imported lazily at client
    # construction and ride the [companion] extra (G2), never the runtime dep list.
    from langfuse_synth_core.companion import llm

    for name in ("resolve_provider", "resolve_model", "get_llm", "LLMClient", "ChatResult"):
        assert hasattr(llm, name)


def test_the_read_and_live_seams_ship_in_runtime():
    # Both v4 seams (portal #208) are imported by deployed kit code — `verify` reads through
    # one and a Companion App emits through the other — so they must resolve on a BARE
    # runtime install, with the Langfuse SDK imported lazily inside the call that needs it.
    from langfuse_synth_core.live.emit import LiveEmitter, LiveTrace
    from langfuse_synth_core.read import LangfuseReader, Observation, Score, Trace

    for method in ("trace", "traces", "observations", "scores", "session", "experiments",
                   "experiment_items", "ping"):
        assert hasattr(LangfuseReader, method)
    for method in ("trace", "score", "flush", "shutdown"):
        assert callable(getattr(LiveEmitter, method))
    for method in ("span", "generation", "event", "update", "score"):
        assert callable(getattr(LiveTrace, method))
    assert Score("s", "n").value is None and Trace("t").root is None
    assert Observation("o").is_root


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
