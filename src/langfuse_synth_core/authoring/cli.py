"""The ``synth`` authoring CLI — a minimal subcommand dispatcher (Spec A, #27).

This ticket ships exactly one subcommand, ``synth validate`` (the offline Contract
lint). The dispatcher is deliberately thin so later tickets bolt their subcommands on
without reshaping it: ``synth freeze`` (the determinism golden gate, #28) and
``synth new`` (the scaffold, #11) register alongside ``validate`` here.

The CLI lives under ``langfuse_synth_core.authoring`` and so requires the ``[authoring]``
extra — importing this module without it raises the boundary ``ModuleNotFoundError``
(see ``authoring/__init__.py``). It is authoring tooling, never part of the runtime image.
"""

from __future__ import annotations

import argparse
import sys

from langfuse_synth_core.authoring import validate as _validate


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synth",
        description="Demo Depot kit authoring toolchain (langfuse-synth-core).",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_validate = sub.add_parser(
        "validate",
        help="offline static Contract lint of one or more usecase.yaml manifests",
    )
    p_validate.add_argument(
        "paths",
        nargs="+",
        metavar="usecase.yaml",
        help="path(s) to the manifest(s) to validate",
    )
    # Later tickets register `freeze` (#28) and `new` (#11) subparsers on `sub` here.
    return parser


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    ns = parser.parse_args(args)
    if ns.command == "validate":
        return _validate.run(ns.paths)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
