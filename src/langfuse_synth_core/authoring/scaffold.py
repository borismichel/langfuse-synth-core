"""``synth-authoring new`` — the runnable-green walking-skeleton generator (#36).

Emits a fresh kit that is **proven-deterministic before any story logic lands**: the
plumbing (backdated ingestion through the shared library, spool determinism, non-root
uid 10001) is green from the first commit. The scaffold is not a blank commented template
— the author grows the story on top of it, keeping the gates green.

File floor of the emitted kit:

* a schema-valid ``usecase.yaml`` (incl. the canonical ``config_schema.generation.target_traces``
  knob, injected via :func:`langfuse_synth_core.authoring.knob.inject_target_traces`);
* ``seed`` + ``verify`` wired **through the library** (`Ingestor` write / `lfread` read);
* the ``target_traces`` derivation hook pre-wired to the trivial identity derivation;
* a ``render: markdown`` Presenter Runbook stub;
* the reference ``Dockerfile`` (non-root uid 10001);
* a companion-app stub **only on request** (full companion authoring is Spec G).

As its final step the generator **blesses the initial golden** by running the emitted
seed through the determinism golden gate under the deny-LLM egress block — so the freshly
scaffolded kit passes ``synth-authoring validate`` AND the golden gate on first generation,
from green. This module lives behind the ``[authoring]`` extra: it is authoring tooling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import yaml

from langfuse_synth_core.authoring.golden import GoldenSpec, freeze
from langfuse_synth_core.authoring.knob import inject_target_traces

# Slug shape mirrors the manifest schema's `slug` pattern — same identifier the emitted
# usecase.yaml carries, so an invalid slug is rejected here rather than at validate time.
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# The default lib pin the emitted kit references (a TAG, never a branch — see RELEASING.md).
# The author bumps it as the lib releases; `synth-authoring new --core-ref` overrides it.
DEFAULT_CORE_REF = "v1.0.0"

# The determinism oracle is pinned at a small floor: determinism is scale-independent, so a
# tiny committed golden proves the byte-identity law while staying reviewable. The emitted
# test and this freeze share the value (rendered into the test via __GOLDEN_TT__).
GOLDEN_TARGET_TRACES = 24

# (template file in scaffold_files/, destination path relative to the kit root).
BASE_FILES: tuple[tuple[str, str], ...] = (
    ("pyproject.toml.tmpl", "pyproject.toml"),
    ("README.md.tmpl", "README.md"),
    ("Dockerfile.tmpl", "Dockerfile"),
    ("gitignore.tmpl", ".gitignore"),
    ("demo.yaml.tmpl", "config/demo.yaml"),
    ("DEMO_SCRIPT.md.tmpl", "DEMO_SCRIPT.md"),
    ("synth__init__.py.tmpl", "src/synth/__init__.py"),
    ("config.py.tmpl", "src/synth/config.py"),
    ("materialize.py.tmpl", "src/synth/materialize.py"),
    ("seed.py.tmpl", "src/synth/seed.py"),
    ("verify.py.tmpl", "src/synth/verify.py"),
    ("cli.py.tmpl", "src/synth/cli.py"),
    ("golden_seed.py.tmpl", "tests/golden_seed.py"),
    ("test_determinism.py.tmpl", "tests/test_determinism.py"),
    ("test_validate.py.tmpl", "tests/test_validate.py"),
)

# Emitted ONLY when `--companion` is passed (full companion authoring is Spec G).
COMPANION_FILES: tuple[tuple[str, str], ...] = (
    ("companion__init__.py.tmpl", "companion/__init__.py"),
    ("companion_app.py.tmpl", "companion/app.py"),
)


class ScaffoldError(ValueError):
    """A scaffold request that cannot proceed (bad slug, or a non-empty destination)."""


@dataclass
class ScaffoldResult:
    """What ``synth-authoring new`` produced: the kit root, files written, blessed golden."""

    slug: str
    dest: Path
    files: list[str] = field(default_factory=list)
    golden_path: Path | None = None


def slug_to_name(slug: str) -> str:
    """Human title from a kebab-case slug, e.g. ``ev-subsidy`` -> ``Ev Subsidy``."""
    return " ".join(part.capitalize() for part in slug.split("-"))


def _template(name: str) -> str:
    return resources.files(__package__).joinpath("scaffold_files", name).read_text(encoding="utf-8")


def _render(text: str, ctx: dict[str, str]) -> str:
    for key, value in ctx.items():
        text = text.replace(key, value)
    return text


def build_manifest(slug: str) -> dict:
    """Build the schema-valid ``usecase.yaml`` document for ``slug`` (dumped to YAML by
    :func:`scaffold_kit`). The canonical volume knob is injected via the authoring SDK so
    the emitted ``config_schema.generation.target_traces`` is schema-valid by construction.
    """
    config_schema = inject_target_traces(
        {
            "type": "object",
            "properties": {
                "generation.seed": {
                    "type": "integer",
                    "default": 42,
                    "minimum": 0,
                    "title": "Generation seed",
                    "description": (
                        "Deterministic RNG seed for the whole dataset. Vary it to mint a "
                        "distinct trace/id set per deployment; the same seed re-seeds "
                        "idempotently (upsert)."
                    ),
                },
            },
            "required": [],
        }
    )
    return {
        "schema_version": 1,
        "slug": slug,
        "name": slug_to_name(slug),
        "tagline": "A scaffolded Demo Depot synth kit — replace this with the demo's hook.",
        "story": (
            "Scaffolded walking skeleton. Replace this with the story a solutions engineer "
            "walks — the failure the demo stages and the Langfuse feature it lands."
        ),
        "target": {
            "project_hint": "demo",
            "supports": ["cloud_eu", "cloud_us", "self_hosted"],
        },
        "base_config": {"default": "config/demo.yaml"},
        "config_schema": config_schema,
        "pipeline": [
            {"id": "seed", "run": "synth seed --config {config}", "timeout_minutes": 90},
            {"id": "verify", "run": "synth verify --config {config}", "fatal": True},
        ],
        "artifacts": [
            {"path": "DEMO_SCRIPT.md", "render": "markdown", "title": "Presenter Runbook"},
        ],
    }


_MANIFEST_HEADER = (
    "# usecase.yaml — schema version 1 (scaffolded by `synth-authoring new`).\n"
    "# The ONLY integration surface between this kit and the Demo Depot portal. Passes\n"
    "# `synth-authoring validate` as-is; grow it as the kit's story lands.\n"
)


def render_manifest(slug: str) -> str:
    """The full ``usecase.yaml`` text (header comment + dumped, schema-valid document)."""
    body = yaml.safe_dump(build_manifest(slug), sort_keys=False, allow_unicode=True)
    return _MANIFEST_HEADER + body


def _dest_is_occupied(dest: Path) -> bool:
    return dest.exists() and any(dest.iterdir())


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def scaffold_kit(
    slug: str,
    dest: str | Path,
    *,
    with_companion: bool = False,
    core_ref: str = DEFAULT_CORE_REF,
    force: bool = False,
) -> ScaffoldResult:
    """Emit the walking-skeleton kit at ``dest`` and bless its initial determinism golden.

    ``dest`` is the kit root (the caller places it, e.g. ``<parent>/<slug>``). Refuses a
    non-empty ``dest`` unless ``force``. Raises :class:`ScaffoldError` on a bad slug or an
    occupied destination.
    """
    if not SLUG_RE.match(slug):
        raise ScaffoldError(
            f"invalid slug {slug!r}: must be kebab-case matching {SLUG_RE.pattern} "
            "(the manifest's slug pattern)"
        )
    dest = Path(dest)
    if _dest_is_occupied(dest) and not force:
        raise ScaffoldError(
            f"destination {dest} exists and is not empty — pass force=True to write into it"
        )

    slug_under = slug.replace("-", "_")
    ctx = {
        "__SLUG__": slug,
        "__SLUG_UNDER__": slug_under,
        "__NAME__": slug_to_name(slug),
        "__CORE_PIN__": core_ref,
        "__GOLDEN_TT__": str(GOLDEN_TARGET_TRACES),
    }

    result = ScaffoldResult(slug=slug, dest=dest)

    files = list(BASE_FILES)
    if with_companion:
        files += list(COMPANION_FILES)
    for template_name, rel in files:
        _write(dest / rel, _render(_template(template_name), ctx))
        result.files.append(rel)

    # The manifest is generated (not templated) so the canonical knob is injected via the
    # authoring SDK and proven schema-valid by construction.
    _write(dest / "usecase.yaml", render_manifest(slug))
    result.files.append("usecase.yaml")

    # Bless the initial golden: run the just-emitted seed through the determinism golden
    # gate under the deny-LLM egress block. This proves the plumbing (deterministic,
    # model-free, spooled through the library) before any story logic — runnable-green.
    golden_path = dest / "tests" / "golden" / f"{slug_under}_spool.ndjson"
    freeze(
        GoldenSpec(
            seed_ref="golden_seed:seed",
            target_traces=GOLDEN_TARGET_TRACES,
            golden_path=golden_path,
            params={},
            search_paths=(str(dest / "tests"), str(dest / "src")),
        )
    )
    result.golden_path = golden_path
    result.files.append(str(golden_path.relative_to(dest)))
    return result
