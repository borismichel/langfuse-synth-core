"""The ``synth`` authoring CLI — minimal dispatcher (#28 adds only ``freeze``).

INTEGRATION RISK: this file and the ``[project.scripts] synth`` entry are shared CLI
surface. #27 (``synth validate``) and #11 (``synth new``) also register subcommands
here. The dispatcher is a plain ``argparse`` with subparsers so each ticket adds one
additive ``_add_*`` block plus one ``subparsers.add_parser`` registration — keep merges
mechanical and avoid reflowing the shared parts.

``synth freeze`` blesses/updates the determinism golden snapshot in one intentional step
(see :mod:`langfuse_synth_core.authoring.golden`). It runs the kit's ``seed`` under the
deny-LLM egress block and writes the byte-identical Spool as the blessed golden.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from langfuse_synth_core.authoring.golden import GoldenSpec, freeze


def _parse_params(raw: str | None) -> dict:
    if not raw:
        return {}
    params = json.loads(raw)
    if not isinstance(params, dict):
        raise ValueError("--params must be a JSON object")
    return params


def _cmd_freeze(args: argparse.Namespace) -> int:
    spec = GoldenSpec(
        seed_ref=args.seed_ref,
        target_traces=args.target_traces,
        golden_path=Path(args.golden),
        params=_parse_params(args.params),
        search_paths=tuple(args.search_path or ()),
    )
    path = freeze(spec)
    print(f"blessed golden: {path} ({path.stat().st_size} bytes)")
    return 0


def _add_freeze(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "freeze",
        help="bless/update the determinism golden snapshot (runs seed under deny-LLM egress)",
    )
    parser.add_argument(
        "seed_ref",
        help="kit seed callable as 'module.path:function' (returns the Spool as bytes)",
    )
    parser.add_argument(
        "--golden", required=True, help="path to write the blessed golden Spool to"
    )
    parser.add_argument(
        "--target-traces", dest="target_traces", type=int, required=True,
        help="the canonical generation.target_traces volume knob",
    )
    parser.add_argument(
        "--params", default=None, help="declared params as a JSON object (default: {})"
    )
    parser.add_argument(
        "--search-path", action="append", default=None,
        help="extra sys.path entry for importing the seed (repeatable)",
    )
    parser.set_defaults(func=_cmd_freeze)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="synth", description="Demo Depot authoring SDK.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_freeze(subparsers)
    # #27 registers `validate`, #11 registers `new` here.
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
