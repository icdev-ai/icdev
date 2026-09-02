"""RFI section prompt templates are config-driven and pursuit-neutral.

THE INCIDENT THIS GUARDS. `rfi_workbench._SECTION_PROMPTS` was a hardcoded dict
carrying one live solicitation's full ROM cost breakdown, an IR&D cost-share
figure and its statutory authority, teaming options, TRL positioning,
commerciality split and risk register -- 11KB of capture strategy committed to a
PUBLIC repository on 2026-06-30. A competitor reading it learned the price, the
cost-share play and the teaming approach.

Two independent guards, because they fail differently:
  * the shipped YAML must carry no bid figures (this file), and
  * the domain leak gate refuses new ones in GovCon source
    (args/domain_leak_gate.yaml :: scoped_patterns).
A pattern gate alone would not stop someone re-adding a Python dict; a content
assertion alone would not stop a figure landing in a neighbouring module.
"""

from __future__ import annotations

import importlib
import re

import pytest
import yaml

from icdev.core.paths import repo_root

MODULE = "tools.govcon.section_prompts"

# The shapes that constituted the incident. Deliberately the same rules the
# domain leak gate enforces, applied here to the YAML's CONTENT rather than to
# a diff -- a template file that never changes again is never re-scanned by a
# staged-diff gate.
_BID_FIGURE_RULES = {
    "ROM or cost-share figure": r"(?i)\b(?:ROM|cost[ _-]?share)\b[^\n]{0,80}\$\s?[\d.,]+\s?[KMB]\b",
    "labor basis of estimate": r"(?i)\blabor\b[^\n]{0,40}\$\s?[\d.,]+\s?[KMB]\b",
    "annual O&M or licensing rate": (
        r"(?i)\bannual\s+(?:O&M|licensing|maintenance)\b[^\n]{0,40}\$\s?[\d.,]+\s?[KMB]\b"
    ),
}

_PROMPTS_YAML = repo_root(__file__) / "args" / "govcon" / "section_prompts.yaml"


@pytest.fixture
def fresh(monkeypatch):
    """Import the module with no overlay configured, cache cleared."""
    monkeypatch.delenv("ICDEV_GOVCON_PROMPTS_PATH", raising=False)
    mod = importlib.import_module(MODULE)
    mod.reload()
    yield mod
    monkeypatch.delenv("ICDEV_GOVCON_PROMPTS_PATH", raising=False)
    mod.reload()


class TestNoPursuitDataInShippedTemplates:
    """The shipped YAML describes the SHAPE of a section, never a bid's content."""

    def test_yaml_carries_no_bid_figures(self):
        text = _PROMPTS_YAML.read_text(encoding="utf-8")
        offenders = {
            name: [m.group(0) for m in re.finditer(pat, text)]
            for name, pat in _BID_FIGURE_RULES.items()
        }
        offenders = {k: v for k, v in offenders.items() if v}
        assert not offenders, (
            f"pursuit bid figures found in {_PROMPTS_YAML.name}: {offenders}. "
            "Real figures belong in the private overlay named by "
            "ICDEV_GOVCON_PROMPTS_PATH, never in this repository."
        )

    def test_rules_discriminate(self):
        """A guard that matches nothing is indistinguishable from no guard.

        Planted control: the literal text from the incident must trip every rule,
        so a future regex edit that quietly stops matching is caught here.
        """
        planted = (
            "Table: Labor ~$1.2M (6 engineers x 6 months), ROM Total ~$1.475M. "
            "Annual O&M: ~$400K/year. Cost Share: ~$490K against $1.475M ROM."
        )
        for name, pat in _BID_FIGURE_RULES.items():
            assert re.search(pat, planted), f"rule {name!r} no longer matches the incident text"

    def test_workbench_has_no_hardcoded_prompt_dict(self):
        """The dict must not come back -- the YAML guard cannot see a Python dict."""
        src = (repo_root(__file__) / "tools" / "govcon" / "rfi_workbench.py").read_text(
            encoding="utf-8"
        )
        assert "_SECTION_PROMPTS = {" not in src, (
            "rfi_workbench.py re-introduced a hardcoded section prompt dict; "
            "templates belong in args/govcon/section_prompts.yaml"
        )
        offenders = {
            name: [m.group(0) for m in re.finditer(pat, src)]
            for name, pat in _BID_FIGURE_RULES.items()
        }
        assert not {k: v for k, v in offenders.items() if v}


class TestLoader:
    def test_known_item_returns_its_template(self, fresh):
        assert "{entity_name}" in fresh.get_prompt("1.1")

    def test_unknown_item_falls_back_to_generic(self, fresh):
        """An unknown item is NOT an error -- the generic default is correct.

        This mirrors the `.get(item, <generic>)` behaviour of the dict it
        replaced, so callers needed no change.
        """
        out = fresh.get_prompt("99.99")
        assert "{title}" in out and "{question_text}" in out

    def test_every_template_uses_only_supported_variables(self, fresh):
        """A template naming an unsupported variable raises KeyError at draft time."""
        supported = {
            "entity_name", "rfi_number", "rfi_title", "primary_naics", "business_size",
            "ndc_status", "clearances", "objectives_list", "hitl_context", "title",
            "question_text", "capability_context", "solution_name",
        }
        for item in fresh.known_items():
            used = set(re.findall(r"\{(\w+)\}", fresh.get_prompt(item)))
            assert used <= supported, f"item {item} uses unsupported vars: {used - supported}"

    def test_status_reports_no_overlay(self, fresh):
        st = fresh.load_status()
        assert st["overlay_applied"] is False
        assert st["overlay_path"] is None
        assert st["default_error"] is None
        assert st["total_templates"] > 0


class TestPrivateOverlay:
    def test_overlay_merges_per_item(self, tmp_path, monkeypatch):
        overlay = tmp_path / "overlay.yaml"
        overlay.write_text(
            yaml.safe_dump({"prompts": {"4.2": "OVERLAY for {entity_name}"}}), encoding="utf-8"
        )
        monkeypatch.setenv("ICDEV_GOVCON_PROMPTS_PATH", str(overlay))
        mod = importlib.import_module(MODULE)
        mod.reload()
        try:
            assert mod.get_prompt("4.2") == "OVERLAY for {entity_name}"
            # untouched items keep the in-repo default
            assert "Part 1.1" in mod.get_prompt("1.1")
            st = mod.load_status()
            assert st["overlay_applied"] is True
            assert st["overlay_template_count"] == 1
        finally:
            monkeypatch.delenv("ICDEV_GOVCON_PROMPTS_PATH", raising=False)
            mod.reload()

    def test_malformed_overlay_degrades_and_reports(self, tmp_path, monkeypatch):
        """A broken private file must not kill a drafting run -- nor pass silently.

        Silent degradation is the worse failure: a response drafted from generic
        templates when an operator expected their tailored ones is a document
        nobody meant to send.
        """
        bad = tmp_path / "bad.yaml"
        bad.write_text("this: [is: not: valid\n", encoding="utf-8")
        monkeypatch.setenv("ICDEV_GOVCON_PROMPTS_PATH", str(bad))
        mod = importlib.import_module(MODULE)
        mod.reload()
        try:
            assert "Part 4.2" in mod.get_prompt("4.2")  # still serves defaults
            st = mod.load_status()
            assert st["overlay_applied"] is False
            assert st["overlay_error"]  # and says why
        finally:
            monkeypatch.delenv("ICDEV_GOVCON_PROMPTS_PATH", raising=False)
            mod.reload()

    def test_missing_overlay_path_reports_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ICDEV_GOVCON_PROMPTS_PATH", str(tmp_path / "nope.yaml"))
        mod = importlib.import_module(MODULE)
        mod.reload()
        try:
            st = mod.load_status()
            assert st["overlay_applied"] is False
            assert "not_found" in (st["overlay_error"] or "")
        finally:
            monkeypatch.delenv("ICDEV_GOVCON_PROMPTS_PATH", raising=False)
            mod.reload()
