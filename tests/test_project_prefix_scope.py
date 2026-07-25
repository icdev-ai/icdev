# CUI // SP-CTI
"""Nested task-prefix scoping for project progress cards.

Regression context: `args/projects.yaml` carries `aadc-` alongside `aadc-enh-`
and `aadc-sp-`. The dashboard used to DROP the later of any two overlapping
prefixes, so the 38-epic `aadc` card never rendered on Home. Nesting is a
parent/child namespace and must be resolved by subtraction, not by dropping.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from icdev.tools.project.prefix_scope import child_prefixes, duplicate_prefixes

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestChildPrefixes:
    def test_returns_nested_children_sorted(self):
        assert child_prefixes(
            "aadc-", ["aadc-", "aadc-sp-", "aadc-enh-", "agx-"]
        ) == ["aadc-enh-", "aadc-sp-"]

    def test_child_has_no_children_of_its_own(self):
        assert child_prefixes("aadc-enh-", ["aadc-", "aadc-enh-", "aadc-sp-"]) == []

    def test_unrelated_prefix_yields_nothing(self):
        assert child_prefixes("agx-", ["aadc-", "aadc-enh-", "agx-"]) == []

    def test_self_is_never_its_own_child(self):
        assert "aadc-" not in child_prefixes("aadc-", ["aadc-"])

    def test_multi_level_nesting_lists_all_descendants(self):
        assert child_prefixes("a-", ["a-", "a-b-", "a-b-c-"]) == ["a-b-", "a-b-c-"]

    @pytest.mark.parametrize("empty", ["", None])
    def test_empty_prefix_claims_nothing(self, empty):
        """Guard: an empty prefix must not silently match every project."""
        assert child_prefixes(empty, ["aadc-", "agx-"]) == []

    def test_empty_candidates_are_ignored(self):
        assert child_prefixes("a-", ["a-", "", None, "a-b-"]) == ["a-b-"]


class TestDuplicatePrefixes:
    def test_exact_duplicate_detected(self):
        assert duplicate_prefixes(["a-", "b-", "a-"]) == ["a-"]

    def test_nesting_is_not_a_duplicate(self):
        assert duplicate_prefixes(["a-", "a-b-"]) == []

    def test_clean_list_has_none(self):
        assert duplicate_prefixes(["a-", "b-", "c-"]) == []


class TestRealProjectsYaml:
    """The live config must be resolvable: nesting allowed, duplicates not."""

    @staticmethod
    def _prefixes() -> list[str]:
        data = yaml.safe_load(
            (REPO_ROOT / "args" / "projects.yaml").read_text(encoding="utf-8")
        )
        return [
            (p.get("task_prefix") or "").strip()
            for p in data.get("projects", [])
            if isinstance(p, dict)
        ]

    def test_no_exact_duplicate_prefixes(self):
        dupes = duplicate_prefixes(self._prefixes())
        assert dupes == [], f"unresolvable duplicate task_prefix values: {dupes}"

    def test_every_nested_parent_resolves_to_children(self):
        """Nested prefixes are permitted, but each parent must know its children
        so its queries can subtract them. Asserts the aadc family specifically,
        since that is the case the drop-on-collision bug hid."""
        prefixes = self._prefixes()
        assert "aadc-" in prefixes
        kids = child_prefixes("aadc-", prefixes)
        assert "aadc-enh-" in kids
        assert "aadc-sp-" in kids

    def test_no_project_is_left_unrenderable(self):
        """Every prefix either has no parent, or is a child whose parent
        subtracts it — no prefix should be silently unrepresented."""
        prefixes = [p for p in self._prefixes() if p]
        for p in prefixes:
            parents = [o for o in prefixes if o != p and p.startswith(o)]
            for parent in parents:
                assert p in child_prefixes(parent, prefixes), (
                    f"{p!r} is nested under {parent!r} but the parent does not "
                    f"list it for exclusion — rows would double-count"
                )
