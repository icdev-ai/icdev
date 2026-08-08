#!/usr/bin/env python3
# CUI // SP-CTI
"""sbx-fnd-01 — pin the SBOM generator as canonical.

`tools/compliance/sbom_generator.py` carried `# DEPRECATED: unused as of 2026-05-09.
Remove after 2026-08-01.` while ~25 live call sites and a BLOCKING bdc_canvas gate
depended on it, and three ACE role cards cited a `tools/sbom/` package that has never
existed. These tests keep both mistakes from coming back.
"""

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

ROOT_COPY = REPO_ROOT / "tools" / "compliance" / "sbom_generator.py"
MIRROR_COPY = REPO_ROOT / "icdev" / "tools" / "compliance" / "sbom_generator.py"

MODULE_NAMES = ("tools.compliance.sbom_generator", "icdev.tools.compliance.sbom_generator")

# "Remove after <date>", "Remove by 2026-08-01", "DEPRECATED: ... 2026-05-09" -- any
# comment that pairs a removal/deprecation word with a date is the marker we removed.
_REMOVAL_WORD = re.compile(r"\b(deprecat\w*|remove\s+(after|by|before|on))\b", re.IGNORECASE)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Role cards under tools/ace/ cite module paths in backticks; every one must resolve.
_BACKTICK_PATH = re.compile(r"`([A-Za-z0-9_./-]+\.py)[^`]*`")


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_both_namespaces_import_and_expose_generate_sbom(module_name):
    """Both the canonical and the legacy-shim namespace must import and export the API."""
    module = importlib.import_module(module_name)
    assert hasattr(module, "generate_sbom"), f"{module_name} does not expose generate_sbom"
    assert callable(module.generate_sbom)

    params = inspect.signature(module.generate_sbom).parameters
    assert "project_id" in params, f"{module_name}.generate_sbom lost its project_id parameter"


@pytest.mark.parametrize("path", [ROOT_COPY, MIRROR_COPY], ids=["root", "mirror"])
def test_no_removal_date_comment(path):
    """No comment may pair a deprecation/removal word with a date."""
    offenders = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        comment = line.split("#", 1)[1] if line.lstrip().startswith("#") else ""
        if comment and _REMOVAL_WORD.search(comment) and _DATE.search(comment):
            offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "sbom_generator.py is canonical -- it must carry no removal-date comment:\n"
        + "\n".join(offenders)
    )


def test_root_and_mirror_stay_in_sync():
    """Root-only or mirror-only authoring silently drifts the two copies apart."""
    assert ROOT_COPY.read_text(encoding="utf-8") == MIRROR_COPY.read_text(encoding="utf-8"), (
        "tools/compliance/sbom_generator.py and icdev/tools/compliance/sbom_generator.py "
        "have diverged -- author changes in both."
    )


@pytest.mark.parametrize("path", [ROOT_COPY, MIRROR_COPY], ids=["root", "mirror"])
def test_module_is_marked_canonical(path):
    """The replacement marker must survive, so the next reader does not re-deprecate it."""
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:12])
    assert "CANONICAL" in head, f"{path.name} lost its CANONICAL header marker"


def test_manifest_shard_does_not_mark_the_generator_deprecated():
    shard = REPO_ROOT / "tools" / "manifest" / "compliance-engine.md"
    rows = [
        line
        for line in shard.read_text(encoding="utf-8").splitlines()
        if "tools/compliance/sbom_generator.py" in line
    ]
    assert rows, "SBOM Generator row vanished from tools/manifest/compliance-engine.md"
    for row in rows:
        assert "[DEPRECATED]" not in row, f"manifest still marks the SBOM generator deprecated: {row}"


def test_ace_role_cards_cite_only_real_module_paths():
    """`tools/sbom/sbom_generator.py` never existed; no role card may cite a phantom path."""
    offenders = []
    for card in sorted((REPO_ROOT / "tools" / "ace").rglob("*.md")):
        for lineno, line in enumerate(card.read_text(encoding="utf-8").splitlines(), start=1):
            for cited in _BACKTICK_PATH.findall(line):
                if not cited.startswith("tools/"):
                    continue
                if not (REPO_ROOT / cited).exists():
                    rel = card.relative_to(REPO_ROOT).as_posix()
                    offenders.append(f"{rel}:{lineno} cites missing {cited}")
    assert not offenders, "ACE role cards cite module paths that do not exist:\n" + "\n".join(offenders)


def test_generate_sbom_is_reachable_without_importing():
    """Source-level guard: the def survives even if imports are stubbed in some env."""
    tree = ast.parse(ROOT_COPY.read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert "generate_sbom" in names
