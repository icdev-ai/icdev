# CUI // SP-CTI
"""DIC inbox — folder-drop ingestion into a collection.

ingest_file() takes one file in-process, so a folder of documents pulled out of
SharePoint had no landing zone — acquisition was automated, ingestion was not.
These pin the behaviour that matters: content-hash dedup (NOT filename), and
per-file failure isolation.

No network, no LLM — ingest_file is stubbed throughout.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _Outcome:
    def __init__(self, doc_id="doc-1"):
        self.doc_id = doc_id


@pytest.fixture
def drop(tmp_path):
    d = tmp_path / "dic_inbox"
    d.mkdir()
    return d


def _write(d: Path, name: str, content: str = "hello") -> Path:
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


class TestDiscovery:
    def test_only_supported_types_are_picked_up(self, drop):
        from tools.document_intelligence.inbox import discover

        _write(drop, "a.pdf"); _write(drop, "b.docx"); _write(drop, "c.exe")
        _write(drop, "d.zip"); _write(drop, ".hidden.pdf")
        names = [p.name for p in discover(drop)]
        assert names == ["a.pdf", "b.docx"]

    def test_missing_directory_is_not_an_error(self, tmp_path):
        from tools.document_intelligence.inbox import discover

        assert discover(tmp_path / "nope") == []

    def test_recursive_is_opt_in(self, drop):
        from tools.document_intelligence.inbox import discover

        sub = drop / "sub"; sub.mkdir()
        _write(sub, "deep.pdf")
        assert discover(drop) == []
        assert [p.name for p in discover(drop, recursive=True)] == ["deep.pdf"]


class TestIngestion:
    def test_files_land_in_the_named_collection(self, drop):
        from tools.document_intelligence import inbox

        _write(drop, "cr-101.pdf")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   return_value=_Outcome("doc-9")) as ing:
            out = inbox.ingest_directory(drop, "change-records")

        assert out["ingested"] == 1
        assert ing.call_args.args[1] == "change-records"
        assert out["files"][0]["doc_id"] == "doc-9"

    def test_dry_run_writes_nothing(self, drop):
        from tools.document_intelligence import inbox

        _write(drop, "a.pdf")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file") as ing:
            out = inbox.ingest_directory(drop, "c", dry_run=True)
        assert out["ingested"] == 1 and not ing.called
        # no state persisted -> a real run still ingests it
        assert not (drop / ".dic_inbox_state.json").exists()


class TestDedupIsByContentNotFilename:
    def test_unchanged_file_is_not_re_ingested(self, drop):
        from tools.document_intelligence import inbox

        _write(drop, "cr.pdf", "v1")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   return_value=_Outcome()) as ing:
            first = inbox.ingest_directory(drop, "c")
            second = inbox.ingest_directory(drop, "c")

        assert first["ingested"] == 1
        assert second["ingested"] == 0 and second["skipped_duplicate"] == 1
        assert ing.call_count == 1

    def test_revised_file_with_the_SAME_name_is_re_ingested(self, drop):
        """The case filename-dedup gets wrong: a CR revised and re-exported keeps
        its filename but is different evidence and must re-ingest."""
        from tools.document_intelligence import inbox

        _write(drop, "cr.pdf", "v1")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   return_value=_Outcome()) as ing:
            inbox.ingest_directory(drop, "c")
            _write(drop, "cr.pdf", "v2-revised")   # same name, new content
            out = inbox.ingest_directory(drop, "c")

        assert out["ingested"] == 1, "revised content must re-ingest"
        assert ing.call_count == 2

    def test_same_content_into_a_different_collection_still_ingests(self, drop):
        from tools.document_intelligence import inbox

        _write(drop, "a.pdf", "same")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   return_value=_Outcome()) as ing:
            inbox.ingest_directory(drop, "coll-a")
            out = inbox.ingest_directory(drop, "coll-b")
        assert out["ingested"] == 1 and ing.call_count == 2

    def test_corrupt_state_file_degrades_to_re_ingest(self, drop):
        from tools.document_intelligence import inbox

        _write(drop, "a.pdf")
        (drop / ".dic_inbox_state.json").write_text("{ not json", encoding="utf-8")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   return_value=_Outcome()):
            out = inbox.ingest_directory(drop, "c")
        assert out["ingested"] == 1  # never wedged


class TestFailureIsolation:
    def test_one_bad_file_does_not_strand_the_drop(self, drop):
        from tools.document_intelligence import inbox

        _write(drop, "a.pdf"); _write(drop, "b.pdf")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   side_effect=[RuntimeError("corrupt"), _Outcome()]):
            out = inbox.ingest_directory(drop, "c")
        assert out["ingested"] == 1 and out["failed"] == 1
        assert len(out["errors"]) == 1

    def test_a_failed_file_is_retried_next_sweep(self, drop):
        """A failure must not be recorded as processed."""
        from tools.document_intelligence import inbox

        _write(drop, "a.pdf")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   side_effect=RuntimeError("transient")):
            inbox.ingest_directory(drop, "c")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   return_value=_Outcome()) as ing:
            out = inbox.ingest_directory(drop, "c")
        assert out["ingested"] == 1 and ing.call_count == 1


class TestMoveProcessed:
    def test_processed_files_are_moved_when_asked(self, drop):
        from tools.document_intelligence import inbox

        _write(drop, "a.pdf")
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   return_value=_Outcome()):
            inbox.ingest_directory(drop, "c", move_processed=True)
        assert not (drop / "a.pdf").exists()
        assert (drop / "processed" / "a.pdf").exists()


class TestWatcher:
    def test_watch_is_bounded_for_tests_and_aggregates(self, drop):
        from tools.document_intelligence.inbox import InboxWatcher

        _write(drop, "a.pdf")
        w = InboxWatcher(str(drop), "c", poll_interval=0)
        with patch("tools.document_intelligence.ingest_orchestrator.ingest_file",
                   return_value=_Outcome()):
            totals = w.watch(max_cycles=1)
        assert totals["cycles"] == 1 and totals["ingested"] == 1
