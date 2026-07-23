"""Subprocess entrypoint that materializes a Spool under the deny-LLM egress block.

Invoked as ``python -m langfuse_synth_core.authoring._seed_runner <config.json>`` by
:mod:`langfuse_synth_core.authoring.golden`. It runs in its OWN process so the socket
guard and the proxy env are total for the seed, and so a kit that (illegitimately) calls
an LLM at seed runtime cannot escape the block.

The egress guard is installed **before any kit code is imported**, so the block covers
the agent's kit-owned generation code, not merely the library.

Exit codes:
  * ``0``  — Spool materialized; bytes written to ``out_path``.
  * ``42`` — egress was attempted under the block (``EgressBlockedError``). This is the
             signal the golden gate turns into a failed, model-free assertion.
  * ``1``  — any other error (import failure, seed raised, bad contract).
"""

from __future__ import annotations

import importlib
import json
import sys
import traceback

# Install the egress guard FIRST — before importing the kit — so a planted LLM call in
# kit-owned generation code is denied.
from langfuse_synth_core.authoring.egress import EgressBlockedError, install_guard

EGRESS_EXIT_CODE = 42
# A stable marker on stderr so the parent can distinguish an egress denial from an
# unrelated crash even if exit codes were ever masked by an intermediate shell.
EGRESS_MARKER = "SYNTH_EGRESS_BLOCKED"


def _resolve_seed(seed_ref: str):
    """Resolve a ``module:function`` reference to the kit's seed callable."""
    if ":" not in seed_ref:
        raise ValueError(
            f"seed_ref must be 'module.path:function', got {seed_ref!r}"
        )
    module_name, _, attr = seed_ref.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: _seed_runner <config.json>", file=sys.stderr)
        return 1
    config = json.loads(open(argv[1], encoding="utf-8").read())

    # Kit search paths (e.g. an author's kit dir) go on sys.path BEFORE importing it.
    for path in config.get("search_paths", []):
        if path not in sys.path:
            sys.path.insert(0, path)

    install_guard()

    try:
        seed = _resolve_seed(config["seed_ref"])
        spool = seed(config["target_traces"], config.get("params", {}))
    except EgressBlockedError as exc:
        print(f"{EGRESS_MARKER}: {exc}", file=sys.stderr)
        return EGRESS_EXIT_CODE
    except Exception:  # noqa: BLE001 — surface any seed/contract failure to the parent
        traceback.print_exc()
        return 1

    if not isinstance(spool, (bytes, bytearray)):
        print(
            "seed must return the fully materialized Spool as bytes (the pre-ingestion "
            f"payload); got {type(spool).__name__}",
            file=sys.stderr,
        )
        return 1

    with open(config["out_path"], "wb") as handle:
        handle.write(spool)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
