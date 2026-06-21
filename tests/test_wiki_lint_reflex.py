# CUI // SP-CTI
"""Tests for the wiki_lint Genesis reflex and memory cross-reference update."""

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# wiki_lint unit tests
# ---------------------------------------------------------------------------

class TestWikiLintOrphans(unittest.TestCase):
    def test_orphan_detection(self):
        from tools.genesis.reflexes.wiki_lint import _scan_orphans
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "linked.md").write_text("# linked", encoding="utf-8")
            (p / "orphan.md").write_text("# orphan", encoding="utf-8")
            linked = {"linked.md": p / "linked.md"}
            findings = _scan_orphans(p, linked)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["type"], "orphan")
            self.assertIn("orphan.md", findings[0]["detail"])

    def test_no_orphans_when_all_linked(self):
        from tools.genesis.reflexes.wiki_lint import _scan_orphans
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "a.md").write_text("# a", encoding="utf-8")
            linked = {"a.md": p / "a.md"}
            self.assertEqual(_scan_orphans(p, linked), [])

    def test_memory_md_excluded_from_orphans(self):
        from tools.genesis.reflexes.wiki_lint import _scan_orphans
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "MEMORY.md").write_text("# index", encoding="utf-8")
            findings = _scan_orphans(p, {})
            self.assertEqual(findings, [])


class TestWikiLintBrokenLinks(unittest.TestCase):
    def test_broken_link_detected(self):
        from tools.genesis.reflexes.wiki_lint import _scan_broken_links
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "a.md").write_text("See [[nonexistent-topic]] for details.", encoding="utf-8")
            linked = {"a.md": p / "a.md"}
            findings = _scan_broken_links(p, linked)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["type"], "broken_link")
            self.assertEqual(findings[0]["slug"], "nonexistent-topic")

    def test_valid_link_not_flagged(self):
        from tools.genesis.reflexes.wiki_lint import _scan_broken_links
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "a.md").write_text("See [[b]] for details.", encoding="utf-8")
            (p / "b.md").write_text("# b content", encoding="utf-8")
            linked = {"a.md": p / "a.md"}
            findings = _scan_broken_links(p, linked)
            self.assertEqual(findings, [])

    def test_link_with_md_extension_resolved(self):
        from tools.genesis.reflexes.wiki_lint import _scan_broken_links
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "a.md").write_text("See [[b.md]] for details.", encoding="utf-8")
            (p / "b.md").write_text("# b", encoding="utf-8")
            linked = {"a.md": p / "a.md"}
            findings = _scan_broken_links(p, linked)
            self.assertEqual(findings, [])


class TestWikiLintStale(unittest.TestCase):
    def test_stale_current_language_flagged(self):
        from tools.genesis.reflexes.wiki_lint import _scan_stale
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            old_date = "2025-01-15"
            (p / "a.md").write_text(
                f"Updated {old_date}. This is currently active and ongoing.", encoding="utf-8"
            )
            linked = {"a.md": p / "a.md"}
            findings = _scan_stale(p, linked, staleness_days=30)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["type"], "stale")

    def test_recent_file_not_flagged(self):
        from tools.genesis.reflexes.wiki_lint import _scan_stale
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            recent = datetime.now(timezone.utc).date().isoformat()
            (p / "a.md").write_text(
                f"Updated {recent}. This is currently active.", encoding="utf-8"
            )
            linked = {"a.md": p / "a.md"}
            findings = _scan_stale(p, linked, staleness_days=30)
            self.assertEqual(findings, [])

    def test_no_current_language_not_flagged(self):
        from tools.genesis.reflexes.wiki_lint import _scan_stale
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "a.md").write_text(
                "Updated 2025-01-01. This was completed.", encoding="utf-8"
            )
            linked = {"a.md": p / "a.md"}
            findings = _scan_stale(p, linked, staleness_days=30)
            self.assertEqual(findings, [])


class TestWikiLintOverflow(unittest.TestCase):
    def test_overflow_flagged(self):
        from tools.genesis.reflexes.wiki_lint import _scan_overflow
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "MEMORY.md").write_text("", encoding="utf-8")
            findings = _scan_overflow(p, 170, 160)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["type"], "overflow")

    def test_under_limit_not_flagged(self):
        from tools.genesis.reflexes.wiki_lint import _scan_overflow
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "MEMORY.md").write_text("", encoding="utf-8")
            self.assertEqual(_scan_overflow(p, 100, 160), [])


class TestWikiLintParseIndex(unittest.TestCase):
    def test_parse_memory_index(self):
        from tools.genesis.reflexes.wiki_lint import _parse_memory_index
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "MEMORY.md").write_text(
                "- [NOVA](project-nova.md) — nova description\n"
                "- [DIC](dic-canvas.md) — dic description\n",
                encoding="utf-8",
            )
            linked, line_count = _parse_memory_index(p)
            self.assertIn("project-nova.md", linked)
            self.assertIn("dic-canvas.md", linked)
            self.assertEqual(line_count, 2)

    def test_missing_memory_md_returns_empty(self):
        from tools.genesis.reflexes.wiki_lint import _parse_memory_index
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            linked, count = _parse_memory_index(p)
            self.assertEqual(linked, {})
            self.assertEqual(count, 0)


class TestWikiLintPredictionId(unittest.TestCase):
    def test_deterministic_id(self):
        from tools.genesis.reflexes.wiki_lint import _pred_id
        id1 = _pred_id("orphan", "my-file")
        id2 = _pred_id("orphan", "my-file")
        self.assertEqual(id1, id2)
        self.assertTrue(id1.startswith("wl-"))

    def test_different_types_different_ids(self):
        from tools.genesis.reflexes.wiki_lint import _pred_id
        self.assertNotEqual(_pred_id("orphan", "x"), _pred_id("stale", "x"))


# ---------------------------------------------------------------------------
# memory_write cross-reference tests
# ---------------------------------------------------------------------------

class TestUpdateCrossrefs(unittest.TestCase):
    def test_crossref_added_on_keyword_overlap(self):
        from tools.memory.memory_write import update_crossrefs
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            # Existing file shares keywords with new content
            (p / "existing-topic.md").write_text(
                "# PostgreSQL primary\n"
                "This discusses postgres storage backend configuration.\n",
                encoding="utf-8",
            )
            new_content = (
                "# PostgreSQL Remediation\n"
                "Fixes the SQLite-first to postgres primary storage backend.\n"
            )
            updated = update_crossrefs("pgp-remediation", new_content, memory_dir=p)
            self.assertEqual(len(updated), 1)
            text = (p / "existing-topic.md").read_text(encoding="utf-8")
            self.assertIn("[[pgp-remediation]]", text)

    def test_no_crossref_on_low_overlap(self):
        from tools.memory.memory_write import update_crossrefs
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "unrelated.md").write_text(
                "# Completely unrelated topic\n"
                "About cooking recipes.\n",
                encoding="utf-8",
            )
            new_content = "# PostgreSQL Primary config fix."
            updated = update_crossrefs("pgp-fix", new_content, memory_dir=p)
            self.assertEqual(updated, [])

    def test_no_duplicate_crossref(self):
        from tools.memory.memory_write import update_crossrefs
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "existing.md").write_text(
                "# Topic\npostgres storage config fix.\n[[pgp-fix]]\n",
                encoding="utf-8",
            )
            new_content = "# PGP postgres storage config fix"
            updated = update_crossrefs("pgp-fix", new_content, memory_dir=p)
            self.assertEqual(updated, [])

    def test_memory_md_excluded(self):
        from tools.memory.memory_write import update_crossrefs
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "MEMORY.md").write_text(
                "postgres storage config index fix memory.\n" * 5,
                encoding="utf-8",
            )
            new_content = "postgres storage config fix"
            updated = update_crossrefs("pgp", new_content, memory_dir=p)
            names = [Path(u).name for u in updated]
            self.assertNotIn("MEMORY.md", names)

    def test_nonexistent_dir_returns_empty(self):
        from tools.memory.memory_write import update_crossrefs
        updated = update_crossrefs("slug", "content", memory_dir=Path("/nonexistent/path"))
        self.assertEqual(updated, [])


if __name__ == "__main__":
    unittest.main()
