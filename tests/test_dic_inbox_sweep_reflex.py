# CUI // SP-CTI
"""The DIC drop folder had no one watching it.

tools/document_intelligence/inbox.py was purpose-built for teams pulling
documents out of SharePoint with a browser session, and had **no caller anywhere
in the repo** — no reflex, no daemon, no route, no MCP tool. This covers its
launcher, including the 3-point registration gotcha that makes a reflex silently
never run.
"""

import importlib
from pathlib import Path

import yaml

from tools.genesis.reflexes import dic_inbox_sweep

REPO_ROOT = Path(__file__).resolve().parents[1]

# Patch the module objects, not dotted strings. The reflex imports
# `ingest_directory` inside run(), so at patch time the submodule may not yet be
# an attribute of its parent package and monkeypatch's string form fails to
# resolve it ("module ... has no attribute 'inbox'") depending on test order.
# `tools.*` is also a shim over `icdev.tools.*`, so importlib + setattr is the
# reliable form here.
_inbox = importlib.import_module("tools.document_intelligence.inbox")
_orch = importlib.import_module("tools.document_intelligence.ingest_orchestrator")


class TestRegistration:
    """All three points, or it silently never runs — the gotcha
    doc_modernization_sweep's own docstring warns about."""

    def test_listed_in_daemon_reflex_names(self):
        from tools.genesis.daemon import REFLEX_NAMES
        assert "dic_inbox_sweep" in REFLEX_NAMES

    def test_configured_in_genesis_config(self):
        cfg = yaml.safe_load((REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
        entry = cfg["reflexes"]["dic_inbox_sweep"]
        assert entry["enabled"] is True
        assert entry["interval_seconds"] == 300, "a drop folder is minutes, not hours"

    def test_module_exposes_the_dispatch_contract(self):
        """daemon.py does importlib.import_module(...).run(config, trust)."""
        assert callable(dic_inbox_sweep.run)


class TestSweep:
    def _ctx(self, tmp_path, **kw):
        ctx = {"watch_dir": str(tmp_path), "collection_id": "change-records"}
        ctx.update(kw)
        return ctx

    def test_reports_failure_when_the_sweep_cannot_run(self, monkeypatch, tmp_path):
        """The sibling sweep returns success=True unconditionally, so a broken
        step degrades to a logger.warning nobody reads. This must not."""
        def boom(**kwargs):
            raise RuntimeError("watch dir unreadable")

        monkeypatch.setattr(_inbox, "ingest_directory", boom)
        out = dic_inbox_sweep.run(self._ctx(tmp_path))
        assert out["success"] is False
        assert "watch dir unreadable" in out["details"]["error"]

    def test_failed_files_are_not_reported_as_success(self, monkeypatch, tmp_path):
        """A drop where every file fails to ingest is not a healthy sweep.
        Silence looking like health is the exact failure this canvas had."""
        monkeypatch.setattr(
            _inbox, "ingest_directory",
            lambda **kw: {"ingested": 0, "skipped_duplicate": 0, "failed": 2,
                          "files": [], "errors": ["a.pdf: boom", "b.pdf: boom"]},
        )
        out = dic_inbox_sweep.run(self._ctx(tmp_path))
        assert out["success"] is False

    def test_empty_folder_is_success_not_failure(self, monkeypatch, tmp_path):
        """Nothing to do is not a problem — most sweeps will find an empty dir."""
        monkeypatch.setattr(
            _inbox, "ingest_directory",
            lambda **kw: {"ingested": 0, "skipped_duplicate": 0, "failed": 0,
                          "files": [], "errors": []},
        )
        out = dic_inbox_sweep.run(self._ctx(tmp_path))
        assert out["success"] is True
        assert out["metric_value"] == 0.0

    def test_metric_is_files_ingested(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            _inbox, "ingest_directory",
            lambda **kw: {"ingested": 3, "skipped_duplicate": 1, "failed": 0,
                          "files": [], "errors": []},
        )
        out = dic_inbox_sweep.run(self._ctx(tmp_path))
        assert out["success"] is True
        assert out["metric_value"] == 3.0

    def test_config_from_context_reaches_the_inbox(self, monkeypatch, tmp_path):
        """genesis_config keys arrive as `context` — the daemon passes the config
        dict straight to module.run()."""
        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return {"ingested": 0, "skipped_duplicate": 0, "failed": 0, "files": [], "errors": []}

        monkeypatch.setattr(_inbox, "ingest_directory", fake)
        dic_inbox_sweep.run(self._ctx(tmp_path, move_processed=True, recursive=True))
        assert captured["collection_id"] == "change-records"
        assert captured["watch_dir"] == str(tmp_path)
        assert captured["move_processed"] is True
        assert captured["recursive"] is True

    def test_blank_watch_dir_falls_back_to_the_inbox_default(self, monkeypatch):
        """genesis_config ships watch_dir: "" — that must mean 'the default',
        not a literal empty path."""
        captured = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return {"ingested": 0, "skipped_duplicate": 0, "failed": 0, "files": [], "errors": []}

        monkeypatch.setattr(_inbox, "ingest_directory", fake)
        dic_inbox_sweep.run({"watch_dir": "", "collection_id": "change-records"})
        assert captured["watch_dir"] is None


class TestEndToEnd:
    def test_a_dropped_file_is_ingested_into_the_collection(self, monkeypatch, tmp_path):
        """The whole point: a file appears in the folder; the sweep notices."""
        (tmp_path / "CR-1234.txt").write_text("Change request: replace CORE-RTR-01.", encoding="utf-8")

        calls = []

        def fake_ingest_file(path, collection_id, **kw):
            calls.append((Path(path).name, collection_id))
            return type("O", (), {"doc_id": "dic_doc_x"})()

        monkeypatch.setattr(_orch, "ingest_file", fake_ingest_file)
        out = dic_inbox_sweep.run({"watch_dir": str(tmp_path), "collection_id": "change-records"})

        assert out["success"] is True
        assert out["metric_value"] == 1.0
        assert calls == [("CR-1234.txt", "change-records")]

    def test_the_same_file_twice_is_deduped_by_content(self, monkeypatch, tmp_path):
        """A re-downloaded CR must not double-ingest. Dedup is by content hash,
        never filename — a revised CR keeps its name and must re-ingest."""
        (tmp_path / "CR-1234.txt").write_text("Change request: replace CORE-RTR-01.", encoding="utf-8")
        calls = []
        monkeypatch.setattr(
            _orch, "ingest_file",
            lambda path, collection_id, **kw: calls.append(Path(path).name)
            or type("O", (), {"doc_id": "dic_doc_x"})(),
        )
        ctx = {"watch_dir": str(tmp_path), "collection_id": "change-records"}
        dic_inbox_sweep.run(ctx)
        dic_inbox_sweep.run(ctx)
        assert len(calls) == 1, "second sweep must skip the unchanged file"

    def test_a_revised_file_reingests_under_the_same_name(self, monkeypatch, tmp_path):
        f = tmp_path / "CR-1234.txt"
        f.write_text("Change request: replace CORE-RTR-01.", encoding="utf-8")
        calls = []
        monkeypatch.setattr(
            _orch, "ingest_file",
            lambda path, collection_id, **kw: calls.append(Path(path).name)
            or type("O", (), {"doc_id": "dic_doc_x"})(),
        )
        ctx = {"watch_dir": str(tmp_path), "collection_id": "change-records"}
        dic_inbox_sweep.run(ctx)
        f.write_text("Change request: replace CORE-RTR-01 AND CORE-RTR-02.", encoding="utf-8")
        dic_inbox_sweep.run(ctx)
        assert len(calls) == 2, "same filename, new content -> must re-ingest"

    def test_dry_run_writes_nothing(self, monkeypatch, tmp_path):
        (tmp_path / "CR-1234.txt").write_text("Change request.", encoding="utf-8")
        called = []
        monkeypatch.setattr(
            _orch, "ingest_file",
            lambda *a, **k: called.append(1), raising=True,
        )
        out = dic_inbox_sweep.run(
            {"watch_dir": str(tmp_path), "collection_id": "change-records", "dry_run": True}
        )
        assert out["success"] is True
        assert called == [], "dry run must not ingest"
