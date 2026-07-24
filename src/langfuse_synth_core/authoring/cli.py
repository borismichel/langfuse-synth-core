"""The ``synth-authoring`` CLI — a subcommand dispatcher (Spec A).

Ships ``synth-authoring validate`` (#27, the offline Contract lint) and
``synth-authoring freeze`` (#28, the determinism golden gate). ``synth-authoring new``
(#11) registers alongside them here. The dispatcher is plain ``argparse`` with
subparsers, each command adding one ``_add_*`` block plus a ``set_defaults(func=...)`` —
so later tickets bolt on mechanically without reshaping the shared parts.

The CLI lives under ``langfuse_synth_core.authoring`` and so requires the ``[authoring]``
extra — importing this module without it raises the boundary ``ModuleNotFoundError``
(see ``authoring/__init__.py``). It is authoring tooling, never part of the runtime image.

NAMING: this console script is ``synth-authoring``, NOT ``synth``. The bare ``synth``
name is the kits' OWN runtime console script (``synth probe|plan|seed|verify|...``) and
the portal integration surface (CONTRACT.md reserved-verb table). Namespacing the
authoring CLI is what keeps the two from colliding when a kit installs the lib as a
dependency (Ring 1, #31). See ``[project.scripts]`` in pyproject.toml.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from langfuse_synth_core.authoring import validate as _validate
from langfuse_synth_core.authoring.golden import GoldenSpec, freeze


# ── synth-authoring validate (#27) ──────────────────────────────────────────────────────
def _cmd_validate(args: argparse.Namespace) -> int:
    return _validate.run(args.paths)


def _add_validate(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "validate",
        help="offline static Contract lint of one or more usecase.yaml manifests",
    )
    parser.add_argument(
        "paths", nargs="+", metavar="usecase.yaml",
        help="path(s) to the manifest(s) to validate",
    )
    parser.set_defaults(func=_cmd_validate)


# ── synth-authoring freeze (#28) ────────────────────────────────────────────────────────
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


# ── dispatcher ────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synth-authoring",
        description="Demo Depot kit authoring toolchain (langfuse-synth-core).",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    _add_validate(subparsers)
    _add_freeze(subparsers)
    # #11 registers `new` here.
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not hasattr(args, "func"):  # no subcommand given
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
