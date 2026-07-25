"""The ``synth-authoring`` CLI — a subcommand dispatcher (Spec A).

Ships ``synth-authoring validate`` (#27, the offline Contract lint),
``synth-authoring freeze`` (#28, the determinism golden gate), and
``synth-authoring new`` (#36, the walking-skeleton scaffold generator). The dispatcher
is plain ``argparse`` with subparsers, each command adding one ``_add_*`` block plus a
``set_defaults(func=...)`` — so later tickets bolt on mechanically without reshaping the
shared parts.

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
from langfuse_synth_core.authoring.scaffold import (
    DEFAULT_CORE_REF,
    ScaffoldError,
    scaffold_kit,
)


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


# ── synth-authoring new (#36) ───────────────────────────────────────────────────────────
def _cmd_new(args: argparse.Namespace) -> int:
    dest = Path(args.dir) / args.slug
    try:
        result = scaffold_kit(
            args.slug,
            dest,
            with_companion=args.companion,
            core_ref=args.core_ref,
            force=args.force,
        )
    except ScaffoldError as exc:
        print(f"✗ synth-authoring new: {exc}", file=sys.stderr)
        return 2
    print(f"✓ scaffolded kit {result.slug!r} at {result.dest} ({len(result.files)} files)")
    print(f"  blessed determinism golden: {result.golden_path}")
    print("  next: cd into it, `pip install -e '.[dev]'`, then `pytest` (green from the start).")
    return 0


def _add_new(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "new",
        help="scaffold a runnable-green walking-skeleton kit (passes validate + the golden gate)",
    )
    parser.add_argument(
        "slug", help="kebab-case kit slug (also the manifest slug + image name)"
    )
    parser.add_argument(
        "--dir", default=".",
        help="parent directory to create the kit in (default: cwd); the kit lands at <dir>/<slug>",
    )
    parser.add_argument(
        "--companion", action="store_true",
        help="also emit the companion-app stub (full companion authoring is Spec G)",
    )
    parser.add_argument(
        "--core-ref", default=DEFAULT_CORE_REF,
        help=f"langfuse-synth-core git ref the kit pins to (default: {DEFAULT_CORE_REF})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="write into <dir>/<slug> even if it exists and is non-empty",
    )
    parser.set_defaults(func=_cmd_new)


# ── dispatcher ────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synth-authoring",
        description="Demo Depot kit authoring toolchain (langfuse-synth-core).",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    _add_validate(subparsers)
    _add_freeze(subparsers)
    _add_new(subparsers)
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
