# CUI // SP-CTI
"""Tests for the capture strategy / win theme message architecture."""
import importlib

import pytest

from tools.govcon import capture_strategy as cs
from tools.quality.content_grounding import find_placeholders


@pytest.fixture
def strategy():
    return {
        "golden_thread": "We retire your risk before you pay for it.",
        "win_themes": [
            {
                "statement": "Because we model your workflows in a digital twin, you retire "
                "integration risk during the base period.",
                "evidence": "Program Aurora: 30% schedule-variance reduction.",
                "target_eval_factor": "Technical Approach",
                "priority": 1,
            },
            {
                "statement": "Because our lab matures capability at our own risk, you field "
                "proven capability on day one.",
                "evidence": "",  # deliberately unproven
                "priority": 2,
            },
        ],
        "discriminators": [{"statement": "Pre-award digital twin.", "evidence": "x"}],
        "ghosting": [{"statement": "Lab-validated synthetic data shifts risk to the Government."}],
        "hot_buttons": ["Latency at scale"],
        "proof_points": [],
    }


class TestPartPolicy:
    @pytest.mark.parametrize(
        "item,expected",
        [
            ("1.1", "none"), ("1.5", "none"),   # administrative fact
            ("2.1", "full"), ("2.7", "full"),   # technical battleground
            ("3.2", "risk"),
            ("4.1", "light"), ("4.3", "light"),
            ("4.2", "none"),                    # ROM cost table
            ("5.1", "full"),
            ("6.1", "questions"), ("6.4", "questions"),
            ("A", "full"), ("B", "full"),
            ("", "none"),
        ],
    )
    def test_policy_for(self, item, expected):
        assert cs.policy_for(item) == expected

    def test_specific_beats_generic(self):
        """'4.2' must not be resolved by the '4.1'/'4.3' siblings or a bare '4' prefix."""
        assert cs.policy_for("4.2") == "none"
        assert cs.policy_for("4.1") == "light"


class TestStrategyBlock:
    def test_administrative_sections_get_nothing(self, strategy):
        assert cs.build_strategy_block(strategy, "1.1") == ""
        assert cs.build_strategy_block(strategy, "1.4") == ""

    def test_rom_cost_table_gets_nothing(self, strategy):
        assert cs.build_strategy_block(strategy, "4.2") == ""

    def test_technical_section_gets_full_stack(self, strategy):
        block = cs.build_strategy_block(strategy, "2.4")
        assert "Golden thread" in block
        assert "Win themes" in block
        assert "Discriminators" in block
        assert "Ghosting" in block
        assert "NEVER name a competitor" in block

    def test_light_sections_cap_discriminators(self, strategy):
        strategy["discriminators"] = [
            {"statement": "One.", "evidence": "e"},
            {"statement": "Two.", "evidence": "e"},
        ]
        block = cs.build_strategy_block(strategy, "4.3")
        assert "One." in block
        assert "Two." not in block, "light sections take at most one discriminator"

    def test_questions_section_forbids_asserting(self, strategy):
        block = cs.build_strategy_block(strategy, "6.3")
        assert "Do NOT assert" in block
        assert "Ghosting" not in block, "questions to the Government must not ghost"

    def test_empty_strategy_yields_no_block(self):
        assert cs.build_strategy_block({}, "2.4") == ""
        assert cs.build_strategy_block(dict(cs._EMPTY), "2.4") == ""

    def test_mode_override_for_proposals(self, strategy):
        """Proposal drafts carry no RFI item number and pass mode explicitly."""
        assert cs.build_strategy_block(strategy, mode=cs.MODE_FULL)
        assert cs.build_strategy_block(strategy, item_number="1.1", mode=cs.MODE_FULL)


class TestVerifyToken:
    def test_unproven_theme_instructs_the_verify_token(self, strategy):
        block = cs.build_strategy_block(strategy, "2.4")
        assert cs.VERIFY_TOKEN in block
        assert "Do not invent a metric" in block

    def test_proven_theme_shows_its_evidence(self, strategy):
        block = cs.build_strategy_block(strategy, "2.4")
        assert "Program Aurora" in block

    def test_verify_token_is_caught_by_the_export_placeholder_gate(self):
        """The gate regex rejects colons and lowercase.

        A form like '[VERIFY: schedule variance]' would sail straight through
        find_placeholders and ship an unproven claim. The bare token must match.
        """
        assert find_placeholders(f"as proven by {cs.VERIFY_TOKEN}") == ["[VERIFY]"]
        assert find_placeholders("as proven by [VERIFY: schedule variance]") == [], (
            "guard: the colon form is NOT caught — never emit it"
        )


class TestThemeCoverage:
    def _sections(self, technical_body):
        return [
            {"item_number": "1.1", "content": "CAGE code 1ABC2."},
            {"item_number": "2.4", "content": technical_body},
            {"item_number": "4.2", "content": "Labor $1.2M."},
        ]

    def test_excluded_parts_are_not_coverage_failures(self, strategy):
        body = strategy["win_themes"][0]["statement"]
        report = cs.theme_coverage(self._sections(body), strategy)
        items = [f.get("item_number") for f in report["findings"]]
        assert "1.1" not in items and "4.2" not in items

    def test_off_message_section_is_flagged(self, strategy):
        report = cs.theme_coverage(self._sections("Unrelated boilerplate prose."), strategy)
        assert any(f["type"] == "theme_absent" for f in report["findings"])
        assert report["score"] < 100

    def test_matrix_records_where_a_theme_landed(self, strategy):
        body = strategy["win_themes"][0]["statement"]
        report = cs.theme_coverage(self._sections(body), strategy)
        assert any("2.4" in hits for hits in report["matrix"].values())

    def test_delegates_to_win_theme_manager(self, strategy, monkeypatch):
        """Coverage must reuse the existing scorer, not re-implement keyword matching."""
        wtm = importlib.import_module("tools.govcon.win_theme_manager")
        calls = []

        def _spy(text, themes):
            calls.append((text, themes))
            return []

        monkeypatch.setattr(wtm, "check_theme_presence", _spy)
        cs.theme_coverage(self._sections("body"), strategy)
        assert calls, "theme_coverage must call win_theme_manager.check_theme_presence"

    def test_no_themes_is_a_clean_report(self):
        assert cs.theme_coverage([{"item_number": "2.4", "content": "x"}], dict(cs._EMPTY)) == {
            "score": 100,
            "findings": [],
            "matrix": {},
        }


class TestThemeTypeDrift:
    def test_python_constant_matches_sql_check(self):
        """register_theme('ghost_strategy') used to pass Python then violate the CHECK."""
        import re
        from pathlib import Path

        from tools.govcon.win_theme_manager import THEME_TYPES

        root = Path(__file__).resolve().parent.parent
        for ddl_path in ("tools/db/init_icdev_db.py", "icdev/tools/db/init_icdev_db.py"):
            ddl = (root / ddl_path).read_text(encoding="utf-8")
            match = re.search(r"theme_type\s+TEXT NOT NULL CHECK\(theme_type IN \(([^)]*)\)\)", ddl)
            assert match, f"pg_win_themes theme_type CHECK not found in {ddl_path}"
            values = {v.strip().strip("'") for v in match.group(1).split(",")}
            assert values == set(THEME_TYPES), f"{ddl_path}: CHECK {values} != THEME_TYPES {THEME_TYPES}"
