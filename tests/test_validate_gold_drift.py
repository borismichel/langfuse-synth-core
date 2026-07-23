"""Drift test: the relocated schema + validator keep BOTH gold manifests validating.

Pins the relocated Contract against the two vendor-approved gold manifests
(`ev-subsidy-regression`, `lending-copilot-certification`), copied verbatim into
`tests/fixtures/manifests/`. If a future schema or validator edit stops accepting either
gold manifest, this fails — that is the drift guard.

`synth validate` on a gold manifest must be **green by construction** (it is the same
code + schema the portal runs), which is what makes "passes locally" ≡ "passes sync".
"""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("jsonschema", reason="authoring extra not installed")
pytest.importorskip("yaml", reason="authoring extra not installed")

from langfuse_synth_core.authoring.validate import validate_path  # noqa: E402

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("jsonschema") is None,
    reason="authoring extra not installed",
)

FIXTURES = Path(__file__).parent / "fixtures" / "manifests"
GOLD = sorted(FIXTURES.glob("*.usecase.yaml"))


def test_both_gold_manifests_are_present():
    slugs = {p.name for p in GOLD}
    assert slugs == {
        "ev-subsidy-regression.usecase.yaml",
        "lending-copilot-certification.usecase.yaml",
    }, slugs


@pytest.mark.parametrize("manifest", GOLD, ids=lambda p: p.stem)
def test_gold_manifest_validates_green(manifest):
    errs = validate_path(manifest)
    assert errs == [], f"{manifest.name} regressed:\n" + "\n".join(errs)
