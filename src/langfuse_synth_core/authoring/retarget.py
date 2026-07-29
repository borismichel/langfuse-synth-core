"""The retargeting gate — proves a kit config honors ``LANGFUSE_BASE_URL`` (portal #187).

``CONTRACT.md`` §"Retargeting" states the rule and why the other gates could not see it. This
module is how it is enforced: inject a probe base URL, load the kit's config through the kit's
**own** loader, and assert the probe won. Both halves of the rule are asserted — env wins, and
with the var absent the committed file value still applies — because each has its own failure
mode (undeployable kit / kit that only works inside the portal).

Deliberately a **config-resolution** check, not a connectivity one: no network, no LLM, no
container, so it belongs in a kit's ordinary pytest run. The scaffold emits
``tests/test_retargeting.py``, which is one call into :func:`assert_retargetable`.

Its limit is worth knowing: it proves the kit *resolves* the injected base URL, not that every
seam dials the resolved value. A kit that resolved ``base_url`` correctly and then built a
client from a literal would still pass. The scaffolded ``seed``/``verify`` both read
``cfg.target.base_url``, so for scaffolded kits that gap is narrow — but it is a gap.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

# The env var the portal worker injects to retarget a deployment (see the portal's
# `api/app/worker/spec.py`).
BASE_URL_ENV = "LANGFUSE_BASE_URL"

# Two distinct, deliberately unroutable probes. Two, not one: a single probe cannot tell
# "honors the env" apart from "hardcoded to exactly this string". `.invalid` is reserved by
# RFC 2606 and port 9 is discard — nothing here is ever dialled, but if a kit ever did, it
# would fail loudly rather than reach something real.
PROBE_BASE_URLS: tuple[str, str] = (
    "http://retarget-probe-one.invalid:9",
    "http://retarget-probe-two.invalid:9",
)


def resolve_base_url(
    load_config: Callable[[Path], Any],
    config_path: str | Path,
    env_base_url: str | None,
) -> str:
    """Return the ``cfg.target.base_url`` the kit resolves with ``LANGFUSE_BASE_URL`` set to
    ``env_base_url`` — or with the var *removed* when ``env_base_url`` is ``None``.

    The var is unset rather than left alone for the ``None`` case: a demo host frequently has
    one exported, and an ambient value would make the fallback half of the contract untestable.
    The ambient environment is restored on the way out, exception or not.
    """
    previous = os.environ.get(BASE_URL_ENV)
    try:
        if env_base_url is None:
            os.environ.pop(BASE_URL_ENV, None)
        else:
            os.environ[BASE_URL_ENV] = env_base_url
        cfg = load_config(Path(config_path))
        return str(cfg.target.base_url)
    finally:
        if previous is None:
            os.environ.pop(BASE_URL_ENV, None)
        else:
            os.environ[BASE_URL_ENV] = previous


def assert_retargetable(
    load_config: Callable[[Path], Any],
    config_path: str | Path,
) -> None:
    """Assert the kit at ``config_path`` is retargetable by the portal, both halves.

    Raises :class:`AssertionError` naming ``LANGFUSE_BASE_URL`` and what the kit resolved
    instead — the message is the whole diagnostic, so a kit's own test suite is just this call.
    """
    for probe in PROBE_BASE_URLS:
        resolved = resolve_base_url(load_config, config_path, probe)
        if resolved.rstrip("/") != probe:
            raise AssertionError(
                f"{config_path} is not retargetable: with {BASE_URL_ENV}={probe} the kit "
                f"resolved target.base_url={resolved!r} instead. The portal retargets a "
                f"deployment by injecting {BASE_URL_ENV}, so this kit would dial its own "
                f"committed base URL wherever it is deployed (portal #187). Let the env win — "
                f"e.g. back `base_url` with a property over a `host` field:\n"
                f'    return os.environ.get("{BASE_URL_ENV}", self.host).rstrip("/")'
            )

    fallback = resolve_base_url(load_config, config_path, None)
    if not fallback or fallback.rstrip("/") in {p.rstrip("/") for p in PROBE_BASE_URLS}:
        raise AssertionError(
            f"{config_path} resolved target.base_url={fallback!r} with {BASE_URL_ENV} unset — "
            f"the env var must OVERRIDE the committed config value, not replace it. Without a "
            f"file fallback the kit only works inside the portal: the author's laptop, the "
            f"determinism golden gate, and every offline run resolve nothing."
        )
