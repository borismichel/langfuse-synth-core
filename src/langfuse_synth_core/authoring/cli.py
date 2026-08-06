"""The ``synth-authoring`` CLI — a subcommand dispatcher (Spec A).

Ships ``synth-authoring validate`` (#27, the offline Contract lint),
``synth-authoring conformance`` (portal #198, the Contract as executable checks),
``synth-authoring freeze`` (#28, the determinism golden gate),
``synth-authoring new`` (#36, the walking-skeleton scaffold generator), and
``synth-authoring skills`` (#37, locate/install the shipped kit-dev skills). The dispatcher
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

from langfuse_synth_core.authoring import conformance as _conformance
from langfuse_synth_core.authoring import repin as _repin
from langfuse_synth_core.authoring import skills as _skills
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


# ── synth-authoring conformance (portal #198) ───────────────────────────────────────────
def _add_conformance(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "conformance",
        help="the Contract as executable checks against a kit checkout — every finding "
        "cites CONTRACT.md; --advisory reports without failing (pre-portal kits)",
    )
    _conformance.add_arguments(parser)
    parser.set_defaults(func=_conformance.execute)


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
            with_anchors=args.anchors,
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
        "--anchors", action="store_true",
        help="also emit the per-run anchors wiring (portal #199): seed writes "
        ".synth_state.json via the core anchors mechanism; a --companion surface reads it "
        "back. Without it the kit is stateless.",
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


# ── synth-authoring repin (portal #197) ─────────────────────────────────────────────────
def _cmd_repin(args: argparse.Namespace) -> int:
    try:
        result = _repin.repin_kit(
            Path(args.kit),
            args.core_ref,
            kit_tag=args.kit_tag,
            repo_url=args.repo_url,
            dry_run=args.dry_run,
        )
    except _repin.RepinError as exc:
        print(f"✗ synth-authoring repin: {exc}", file=sys.stderr)
        return 2
    if result.dry_run:
        for diff in result.diffs.values():
            print(diff, end="")
        print("(dry-run: nothing written)")
    else:
        print(
            f"✓ repinned {result.slug!r} to core {result.core_ref}: "
            f"{result.pyproject_moves} pyproject pin(s) + {result.workflow_moves} workflow pin(s)"
        )
    print("\nportal registry.yaml snippet (paste into the registry PR):")
    print(result.snippet, end="")
    print(result.image_note)
    return 0


def _add_repin(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "repin",
        help="move ALL of a kit's core pins (pyproject + publish workflow) to a core "
        "release in one step, and emit the portal registry snippet",
    )
    parser.add_argument(
        "core_ref",
        help="the langfuse-synth-core release to pin to — a vX.Y.Z tag or full 40-hex SHA",
    )
    parser.add_argument(
        "--kit", default=".",
        help="path to the kit checkout to repin (default: cwd)",
    )
    parser.add_argument(
        "--kit-tag", default=None, metavar="vA.B.C",
        help="the kit's NEXT release tag, for the registry snippet's ref + GHCR digest "
        "lookup (omit if undecided — the snippet carries a placeholder)",
    )
    parser.add_argument(
        "--repo-url", default=None,
        help="the kit's GitHub repo URL (default: derived from `git remote get-url origin`)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the unified diff and the snippet without writing anything",
    )
    parser.set_defaults(func=_cmd_repin)


# ── synth-authoring skills (#37) ────────────────────────────────────────────────────────
# Default target for `--install`: the directory Claude Code discovers project skills in.
DEFAULT_SKILLS_DEST = ".claude/skills"


def _cmd_skills(args: argparse.Namespace) -> int:
    names = _skills.list_skills()
    if args.install:
        dest = Path(args.dest)
        try:
            written = _skills.install_skills(dest, force=args.force)
        except FileExistsError as exc:
            print(f"✗ synth-authoring skills: {exc}", file=sys.stderr)
            return 2
        for path in written:
            print(f"✓ installed skill {path.name!r} -> {path}")
        print(f"  {len(written)} skill(s) now discoverable under {dest}")
        return 0
    # Locate mode: name every shipped skill and its triggering description.
    if not names:
        print("no kit-dev skills ship in this build.")
        return 0
    print("kit-dev skills shipped in langfuse-synth-core[authoring]:")
    for name in names:
        meta = _skills.skill_frontmatter(name)
        print(f"  {name} — {meta.get('description', '').strip()}")
    print("\ninstall them into a skills dir (default .claude/skills) with:")
    print("  synth-authoring skills --install")
    return 0


def _add_skills(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "skills",
        help="locate or install the shipped kit-dev skills (the agent pack)",
    )
    parser.add_argument(
        "--install", action="store_true",
        help="copy the skills into --dest (default: .claude/skills) so an agent discovers them",
    )
    parser.add_argument(
        "--dest", default=DEFAULT_SKILLS_DEST,
        help=f"skills directory to install into (default: {DEFAULT_SKILLS_DEST})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite an existing skill dir in --dest (replaces an edited copy)",
    )
    parser.set_defaults(func=_cmd_skills)


# ── dispatcher ────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synth-authoring",
        description="Demo Depot kit authoring toolchain (langfuse-synth-core).",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    _add_validate(subparsers)
    _add_conformance(subparsers)
    _add_freeze(subparsers)
    _add_new(subparsers)
    _add_repin(subparsers)
    _add_skills(subparsers)
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
