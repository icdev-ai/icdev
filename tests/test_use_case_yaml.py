# CUI // SP-CTI
"""Tests for args/use_cases.yaml structural validity.

Parameterized over all use cases so test failures name the exact broken entry.
No DB or Flask needed — pure YAML load.

Run:
    pytest tests/test_use_case_yaml.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tools.requirements.use_case_validator import (
    VALID_PRIORITIES,
    VALID_TYPES,
    validate_use_cases,
)

YAML_PATH = Path(__file__).resolve().parent.parent / "args" / "use_cases.yaml"


def _load_use_cases() -> list[dict]:
    with open(YAML_PATH, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("use_cases", [])


ALL_USE_CASES: list[dict] = _load_use_cases()
UC_IDS: list[str] = [uc["id"] for uc in ALL_USE_CASES]


# ---------------------------------------------------------------------------
# Gate test — one assertion covers all 13 use cases at once
# ---------------------------------------------------------------------------


def test_validator_reports_zero_violations():
    """Full validator run on args/use_cases.yaml must report zero violations.

    This is the audit gate. If it fails, run:
        python tools/requirements/use_case_validator.py --json
    to see the exact violations.
    """
    result = validate_use_cases()
    assert result["status"] == "ok", (
        f"Found {len(result['violations'])} violation(s):\n"
        + "\n".join(
            f"  {v['uc_id']}[{v['req_index']}].{v['field']}: {v['error']}"
            for v in result["violations"]
        )
    )
    assert result["violations"] == []


# ---------------------------------------------------------------------------
# Per-use-case structural checks (parameterized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uc", ALL_USE_CASES, ids=UC_IDS)
def test_each_use_case_has_template_requirements(uc: dict):
    """Every use case must have at least one template_requirement."""
    reqs = uc.get("template_requirements") or []
    assert len(reqs) >= 1, f"{uc['id']} has no template_requirements"


@pytest.mark.parametrize("uc", ALL_USE_CASES, ids=UC_IDS)
def test_requirement_types_are_valid(uc: dict):
    """All template_requirement type values must match the DB CHECK constraint set."""
    for i, req in enumerate(uc.get("template_requirements") or []):
        assert req.get("type") in VALID_TYPES, (
            f"{uc['id']}[{i}] has invalid type={req.get('type')!r}; "
            f"must be one of {sorted(VALID_TYPES)}"
        )


@pytest.mark.parametrize("uc", ALL_USE_CASES, ids=UC_IDS)
def test_requirement_priorities_are_valid(uc: dict):
    """All template_requirement priority values must match the DB CHECK constraint set."""
    for i, req in enumerate(uc.get("template_requirements") or []):
        assert req.get("priority") in VALID_PRIORITIES, (
            f"{uc['id']}[{i}] has invalid priority={req.get('priority')!r}; "
            f"must be one of {sorted(VALID_PRIORITIES)}"
        )


@pytest.mark.parametrize("uc", ALL_USE_CASES, ids=UC_IDS)
def test_requirement_text_nonempty(uc: dict):
    """All template_requirements must have non-empty text."""
    for i, req in enumerate(uc.get("template_requirements") or []):
        assert (req.get("text") or "").strip(), (
            f"{uc['id']}[{i}] has empty or missing text"
        )


@pytest.mark.parametrize("uc", ALL_USE_CASES, ids=UC_IDS)
def test_requirement_criteria_present_and_nonempty(uc: dict):
    """All template_requirements must have a non-empty criteria field."""
    for i, req in enumerate(uc.get("template_requirements") or []):
        assert "criteria" in req, (
            f"{uc['id']}[{i}] is missing the criteria key"
        )
        assert (req.get("criteria") or "").strip(), (
            f"{uc['id']}[{i}] has empty criteria"
        )
