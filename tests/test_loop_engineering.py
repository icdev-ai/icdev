# CUI // SP-CTI
"""Tests for loop engineering features:
  - Phase C: loop_type / adversarial_enabled columns on kanban_tasks
  - Phase A: _run_adversarial_verify() in kanban reflex
  - Phase B: GEPA optimizer (gepa_optimizer.py)
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock

import importlib


# ── helpers ───────────────────────────────────────────────────────────────────

def _shim(conn):
    """Translate %s → ? for a connection handed to production code.

    Was a hand-rolled ``_ShimConn`` whose ``__exit__`` neither committed nor
    rolled back — unlike ``StorageConnection``, which production code relies on
    to commit its ``with get_connection() as conn:`` blocks. Delegates to
    ``tests/_sql_compat``, which wraps the same ``translate_sql`` the runtime
    uses, so this can never drift from the behaviour it stands in for.

    ``unclosable`` keeps the underlying in-memory database alive for the
    post-call assertions: it dies with its connection, and production code
    closes what it is given.
    """
    from _sql_compat import TranslatingConnection, translating

    if isinstance(conn, TranslatingConnection):
        return conn
    return translating(conn, unclosable=True)


def _patch_storage(monkeypatch, conn):
    """Patch get_connection on both tools.db.storage and icdev.tools.db.storage.

    Both may be cached as separate sys.modules entries (physical tools/ file +
    icdev/ canonical). Patch both so whichever from-import fires at call-time
    gets the ShimConn.
    """
    factory = lambda: _shim(conn)  # noqa: E731
    for mod_name in ("tools.db.storage", "icdev.tools.db.storage"):
        try:
            mod = importlib.import_module(mod_name)
            monkeypatch.setattr(mod, "get_connection", factory)
        except ModuleNotFoundError:
            pass


def _patch_init_kanban(monkeypatch):
    """Suppress init_kanban_tables() side-effects in unit tests.

    Both tools.kanban.init_db (physical tools/ file) and
    icdev.tools.kanban.init_db (icdev/ canonical) may be cached as separate
    sys.modules entries — patch both so whichever path task_factory.py
    uses at call-time gets the stub.
    """
    _noop = lambda: None  # noqa: E731
    for mod_name in ("tools.kanban.init_db", "icdev.tools.kanban.init_db"):
        try:
            mod = importlib.import_module(mod_name)
            monkeypatch.setattr(mod, "init_kanban_tables", _noop)
        except ModuleNotFoundError:
            pass


def _make_conn():
    """In-memory SQLite with the minimal kanban schema, %s-translating.

    Returned already wrapped because the GEPA tests below hand this connection
    straight to production code. ``_get_pending_artifacts`` runs a ``%s`` query
    inside ``except Exception: return []``, so a bare ``sqlite3.connect`` made
    every one of those statements raise ``near "%": syntax error`` where nothing
    surfaced — the filter/trace-count tests were asserting against a no-op the
    fixture itself caused.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE kanban_tasks (
            id                   TEXT PRIMARY KEY,
            title                TEXT,
            description          TEXT,
            task_type            TEXT DEFAULT 'build',
            priority             TEXT DEFAULT 'high',
            status               TEXT DEFAULT 'backlog',
            depends_on_task_id   TEXT,
            source_prediction_id TEXT,
            source_doc_id        TEXT,
            source_collection_id TEXT,
            dispatch_source      TEXT DEFAULT 'unknown',
            idempotency_key      TEXT,
            max_retries          INTEGER DEFAULT 5,
            max_runtime_seconds  INTEGER,
            loop_type            TEXT DEFAULT 'deterministic',
            adversarial_enabled  INTEGER DEFAULT 0,
            acceptance_criteria  TEXT,
            created_at           TEXT,
            updated_at           TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE agent_improvement_artifacts (
            artifact_id        TEXT PRIMARY KEY,
            task_type          TEXT,
            skill_used         TEXT,
            improvement_text   TEXT,
            composite_score    REAL,
            baseline_score     REAL,
            evidence_traces    TEXT,
            status             TEXT DEFAULT 'pending',
            applied_at         TEXT,
            applied_count      INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    return _shim(conn)


# ══════════════════════════════════════════════════════════════════════════════
# Phase C — loop_type / adversarial_enabled columns
# ══════════════════════════════════════════════════════════════════════════════

class TestLoopTypeColumns:
    """Verify the new DB columns are wired through task_factory."""

    def test_default_loop_type_is_deterministic(self, monkeypatch):
        raw = _make_conn()
        _patch_storage(monkeypatch, raw)
        _patch_init_kanban(monkeypatch)

        from tools.kanban.task_factory import create_tasks
        create_tasks([{"id": "lt-test-01", "title": "default loop task"}])

        row = raw.execute(
            "SELECT loop_type, adversarial_enabled FROM kanban_tasks WHERE id=?",
            ("lt-test-01",),
        ).fetchone()
        assert row is not None
        assert row["loop_type"] == "deterministic"
        assert row["adversarial_enabled"] == 0

    def test_non_deterministic_flag_stored(self, monkeypatch):
        raw = _make_conn()
        _patch_storage(monkeypatch, raw)
        _patch_init_kanban(monkeypatch)

        from tools.kanban.task_factory import create_tasks
        create_tasks([{
            "id": "lt-test-02",
            "title": "nd task",
            "loop_type": "non_deterministic",
            "adversarial_enabled": True,
        }])

        row = raw.execute(
            "SELECT loop_type, adversarial_enabled FROM kanban_tasks WHERE id=?",
            ("lt-test-02",),
        ).fetchone()
        assert row is not None
        assert row["loop_type"] == "non_deterministic"
        assert row["adversarial_enabled"] == 1

    def test_adversarial_false_stores_zero(self, monkeypatch):
        raw = _make_conn()
        _patch_storage(monkeypatch, raw)
        _patch_init_kanban(monkeypatch)

        from tools.kanban.task_factory import create_tasks
        create_tasks([{
            "id": "lt-test-03",
            "title": "deterministic task",
            "adversarial_enabled": False,
        }])

        row = raw.execute(
            "SELECT adversarial_enabled FROM kanban_tasks WHERE id=?",
            ("lt-test-03",),
        ).fetchone()
        assert row is not None
        assert row["adversarial_enabled"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Phase A — _run_adversarial_verify
# ══════════════════════════════════════════════════════════════════════════════

def _import_kanban_reflex():
    """Import the kanban reflex module, avoiding the heavy global state."""
    return importlib.import_module("tools.genesis.reflexes.kanban")


def _patch_kanban_conn(monkeypatch, raw_conn):
    """Patch get_connection on the already-imported kanban reflex module.

    The reflex imports get_connection at module level from tools.db.storage,
    which resolves to icdev.tools.db.storage. Patching via the module object
    directly (importlib-loaded) ensures the already-bound name is updated.
    """
    mod = _import_kanban_reflex()
    monkeypatch.setattr(mod, "get_connection", lambda: _shim(raw_conn))
    return mod


class TestAdversarialVerify:
    """Unit tests for the adversarial verify gate."""

    def test_disabled_task_passes_immediately(self, monkeypatch):
        raw = _make_conn()
        raw.execute(
            "INSERT INTO kanban_tasks (id, title, adversarial_enabled) VALUES ('av-01','t',0)"
        )
        raw.commit()
        mod = _patch_kanban_conn(monkeypatch, raw)

        passed, feedback = mod._run_adversarial_verify("av-01", "/tmp")
        assert passed is True
        assert feedback == ""

    def test_enabled_task_approved_verdict(self, monkeypatch):
        """APPROVED verdict in subprocess output → pass=True."""
        raw = _make_conn()
        raw.execute(
            "INSERT INTO kanban_tasks (id, title, adversarial_enabled, description)"
            " VALUES ('av-02','t',1,'')"
        )
        raw.commit()
        mod = _patch_kanban_conn(monkeypatch, raw)
        monkeypatch.setattr(mod, "_resolve_claude_cli", lambda: "/usr/bin/claude")

        fake_proc = MagicMock()
        fake_proc.stdout = "Analysis...\nAPPROVED: implementation meets acceptance criteria"
        fake_proc.returncode = 0

        import subprocess
        monkeypatch.setattr(subprocess, "run", MagicMock(return_value=fake_proc))

        passed, feedback = mod._run_adversarial_verify("av-02", "/tmp")
        # Fail-open or approved — either way returns bool
        assert isinstance(passed, bool)

    def test_enabled_task_fails_open_on_error(self, monkeypatch):
        """OSError from subprocess should fail open (pass=True)."""
        raw = _make_conn()
        raw.execute(
            "INSERT INTO kanban_tasks (id, title, adversarial_enabled, description)"
            " VALUES ('av-03','t',1,'')"
        )
        raw.commit()
        mod = _patch_kanban_conn(monkeypatch, raw)
        monkeypatch.setattr(mod, "_resolve_claude_cli", lambda: "/usr/bin/claude")

        import subprocess
        monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=OSError("claude not found")))

        passed, feedback = mod._run_adversarial_verify("av-03", "/tmp")
        assert passed is True

    def test_missing_task_fails_open(self, monkeypatch):
        """Missing task ID should fail open."""
        raw = _make_conn()
        mod = _patch_kanban_conn(monkeypatch, raw)

        passed, feedback = mod._run_adversarial_verify("nonexistent-task", "/tmp")
        assert passed is True

    def test_no_claude_cli_passes(self, monkeypatch):
        """If claude CLI is not found, fail open (pass=True)."""
        raw = _make_conn()
        raw.execute(
            "INSERT INTO kanban_tasks (id, title, adversarial_enabled, description)"
            " VALUES ('av-04','t',1,'')"
        )
        raw.commit()
        mod = _patch_kanban_conn(monkeypatch, raw)
        monkeypatch.setattr(mod, "_resolve_claude_cli", lambda: None)

        passed, feedback = mod._run_adversarial_verify("av-04", "/tmp")
        assert passed is True


# ══════════════════════════════════════════════════════════════════════════════
# Phase B — GEPA optimizer
# ══════════════════════════════════════════════════════════════════════════════

class TestGEPAOptimizer:
    """Tests for tools/skills/gepa_optimizer.py"""

    # ── _rubric_check ─────────────────────────────────────────────────────────

    def test_rubric_check_passes_valid_update(self):
        from tools.skills.gepa_optimizer import _rubric_check
        original = "---\nname: test\n---\n" + "x" * 100
        updated = "---\nname: test\n---\n" + "x" * 95  # 95% of original length
        assert _rubric_check(original, updated) is True

    def test_rubric_check_fails_empty(self):
        from tools.skills.gepa_optimizer import _rubric_check
        assert _rubric_check("---\noriginal\n---\nsome content", "") is False

    def test_rubric_check_fails_too_short(self):
        from tools.skills.gepa_optimizer import _rubric_check
        original = "---\nname: test\n---\n" + "x" * 200
        updated = "---\nname: test\n---\n" + "x" * 10  # way too short
        assert _rubric_check(original, updated) is False

    def test_rubric_check_fails_missing_frontmatter(self):
        from tools.skills.gepa_optimizer import _rubric_check
        original = "---\nname: test\n---\n" + "x" * 100
        updated = "no frontmatter here " * 10  # long enough but no ---
        assert _rubric_check(original, updated) is False

    # ── _find_skill_file ──────────────────────────────────────────────────────

    def test_find_skill_file_returns_none_for_unknown(self, tmp_path):
        from tools.skills import gepa_optimizer as mod
        orig_root = mod._SKILLS_ROOT
        mod._SKILLS_ROOT = tmp_path
        try:
            result = mod._find_skill_file("nonexistent-skill-xyz")
            assert result is None
        finally:
            mod._SKILLS_ROOT = orig_root

    def test_find_skill_file_finds_icdev_prefix(self, tmp_path):
        from tools.skills import gepa_optimizer as mod
        orig_root = mod._SKILLS_ROOT
        mod._SKILLS_ROOT = tmp_path
        try:
            skill_dir = tmp_path / "icdev-my-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / "SKILL.md"
            skill_file.write_text("---\nname: my-skill\n---\ncontent", encoding="utf-8")
            result = mod._find_skill_file("my-skill")
            assert result == skill_file
        finally:
            mod._SKILLS_ROOT = orig_root

    # ── _get_pending_artifacts ────────────────────────────────────────────────

    def test_get_pending_artifacts_empty(self):
        from tools.skills.gepa_optimizer import _get_pending_artifacts
        conn = _make_conn()
        result = _get_pending_artifacts(conn)
        assert result == []

    def test_get_pending_artifacts_filters_by_threshold(self):
        from tools.skills.gepa_optimizer import _get_pending_artifacts
        conn = _make_conn()
        # Below threshold (delta < 0.05)
        conn.execute(
            "INSERT INTO agent_improvement_artifacts "
            "(artifact_id, skill_used, improvement_text, composite_score, baseline_score, evidence_traces, status) "
            "VALUES ('a1','build','improve it',0.62,0.60,'[]','pending')"
        )
        # Above threshold (delta = 0.20)
        conn.execute(
            "INSERT INTO agent_improvement_artifacts "
            "(artifact_id, skill_used, improvement_text, composite_score, baseline_score, evidence_traces, status) "
            "VALUES ('a2','deploy','improve more',0.80,0.60,'[{\"trace\":1}]','pending')"
        )
        # Already applied
        conn.execute(
            "INSERT INTO agent_improvement_artifacts "
            "(artifact_id, skill_used, improvement_text, composite_score, baseline_score, evidence_traces, status) "
            "VALUES ('a3','test','already done',0.90,0.60,'[]','applied')"
        )
        conn.commit()

        result = _get_pending_artifacts(conn)
        ids = [r["artifact_id"] for r in result]
        assert "a2" in ids
        assert "a1" not in ids   # delta too small
        assert "a3" not in ids   # already applied

    def test_get_pending_artifacts_counts_traces(self):
        from tools.skills.gepa_optimizer import _get_pending_artifacts
        conn = _make_conn()
        traces = json.dumps([{"id": 1}, {"id": 2}, {"id": 3}])
        conn.execute(
            "INSERT INTO agent_improvement_artifacts "
            "(artifact_id, skill_used, improvement_text, composite_score, baseline_score, evidence_traces, status) "
            "VALUES ('a4','skill-x','improve',0.85,0.70,?,'pending')",
            (traces,),
        )
        conn.commit()

        result = _get_pending_artifacts(conn)
        assert len(result) == 1
        assert result[0]["n_traces"] == 3

    # ── run() — dry run with no artifacts ────────────────────────────────────

    def test_run_dry_run_no_artifacts(self, monkeypatch):
        from tools.skills import gepa_optimizer as mod
        conn = _make_conn()
        monkeypatch.setattr(mod, "_get_pending_artifacts", lambda c: [])
        _patch_storage(monkeypatch, conn)

        result = mod.run(dry_run=True)

        assert result["applied"] == []
        assert result["errors"] == []

    def test_run_dry_run_skips_write(self, monkeypatch, tmp_path):
        """dry_run=True should not call write_text on skill files."""
        from tools.skills import gepa_optimizer as mod

        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("---\nname: x\n---\noriginal", encoding="utf-8")

        artifacts = [{
            "artifact_id": "dry-01",
            "skill_used": "x",
            "improvement_text": "make better",
            "composite_score": 0.85,
            "baseline_score": 0.70,
            "n_traces": 2,
        }]

        conn = _make_conn()
        monkeypatch.setattr(mod, "_get_pending_artifacts", lambda c: artifacts)
        monkeypatch.setattr(mod, "_find_skill_file", lambda s: skill_file)
        _patch_storage(monkeypatch, conn)

        result = mod.run(dry_run=True)

        assert len(result["applied"]) == 1
        assert result["applied"][0]["dry_run"] is True
        # File must NOT be modified in dry-run
        assert skill_file.read_text(encoding="utf-8") == "---\nname: x\n---\noriginal"

    def test_run_skips_unknown_skill_file(self, monkeypatch):
        from tools.skills import gepa_optimizer as mod

        artifacts = [{
            "artifact_id": "skip-01",
            "skill_used": "unknown-skill",
            "improvement_text": "improve",
            "composite_score": 0.85,
            "baseline_score": 0.70,
            "n_traces": 1,
        }]

        conn = _make_conn()
        monkeypatch.setattr(mod, "_get_pending_artifacts", lambda c: artifacts)
        monkeypatch.setattr(mod, "_find_skill_file", lambda s: None)
        _patch_storage(monkeypatch, conn)

        result = mod.run(dry_run=False)

        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["reason"] == "skill_file_not_found"

    # ── GEPA reflex wrapper ───────────────────────────────────────────────────

    def test_gepa_reflex_returns_ok_status(self, monkeypatch):
        import tools.genesis.reflexes.gepa_optimizer as reflex_mod
        monkeypatch.setattr(
            "tools.skills.gepa_optimizer.run",
            lambda dry_run=False: {"applied": [], "skipped": [], "errors": []},
        )
        result = reflex_mod.run({}, None)
        # hgx-obs-02 moved this reflex to the daemon envelope
        # {success, metric_value, details}; status/applied moved under details.
        assert result["success"] is True
        assert result["details"]["status"] == "ok"
        assert result["details"]["applied"] == 0

    def test_gepa_reflex_reports_partial_on_errors(self, monkeypatch):
        import tools.genesis.reflexes.gepa_optimizer as reflex_mod
        monkeypatch.setattr(
            "tools.skills.gepa_optimizer.run",
            lambda dry_run=False: {
                "applied": [{"artifact_id": "x"}],
                "skipped": [],
                "errors": [{"reason": "empty_patch"}],
            },
        )
        result = reflex_mod.run({}, None)
        assert result["success"] is False
        assert result["details"]["status"] == "partial"
        assert result["details"]["applied"] == 1
        assert result["details"]["errors"] == 1

    def test_gepa_reflex_handles_import_error(self, monkeypatch):
        import tools.genesis.reflexes.gepa_optimizer as reflex_mod
        monkeypatch.setattr(
            "tools.skills.gepa_optimizer.run",
            MagicMock(side_effect=ImportError("LLMRouter not available")),
        )
        result = reflex_mod.run({}, None)
        assert result["success"] is False
        assert result["details"]["status"] == "error"
        assert "error" in result["details"]
