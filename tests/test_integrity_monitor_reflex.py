# CUI // SP-CTI
"""Tests for the Genesis ``integrity_monitor`` reflex (sipa-reflex-01).

The reflex runs the SIPA engine over a source tree (Mode A / provenance-aware) and
opens a Kanban remediation card for each NEW high-risk / ``unauthorized_capability``
finding versus the last baseline assessment of the same source — deduping so it
never re-alerts.

The full SIPA pipeline runs for real against an in-memory SQLite connection; the
two subprocess-backed scanner seams are stubbed for determinism (same pattern as
``test_integrity_engine.py``), and the RTM coverage-gap pass is stubbed so the
in-memory test stays hermetic.

Core acceptance (per the task): a seeded unauthorized capability produces EXACTLY
one card — and a re-run produces none (dedupe).
"""
import sqlite3

import pytest

from tools.db.storage import StorageConnection
from tools.integrity import scanners, intent_reconciler
from tools.integrity.db import init_db as init_db_mod
from tools.genesis.reflexes import integrity_monitor


# A file exercising exactly ONE high-risk capability (network_egress) and nothing
# else — so reconciliation emits a single unauthorized_capability finding.
_NET_PY = '''\
import socket


def beacon():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("10.0.0.1", 4444))
    return s
'''

# A file with NO capabilities at all — a clean baseline (no findings).
_CLEAN_PY = '''\
"""Pure-compute helper — exercises no network/fs/process capability."""

VALUE = 41


def compute():
    return VALUE + 1
'''


def _write(base, name, content):
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_text(content, encoding="utf-8")


# Minimal kanban_tasks table carrying the columns the reflex reads/writes.
_KANBAN_DDL = """
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT,
    task_type        TEXT DEFAULT 'build',
    priority         TEXT DEFAULT 'high',
    status           TEXT DEFAULT 'backlog',
    executor_type    TEXT DEFAULT 'claude_cli',
    dispatch_source  TEXT DEFAULT 'unknown',
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture
def conn():
    """A SQLite connection wrapped exactly as production hands one to the reflex.

    The reflex authors its SQL PostgreSQL-first (``%s`` placeholders), per
    CLAUDE.md. A bare ``sqlite3.Connection`` rejects ``%s`` outright — the
    reflex's queries died with ``near "%": syntax error``, the error was
    swallowed into ``details["errors"]``, and every assertion downstream saw
    zero cards. Production never hit this because ``get_connection()`` returns a
    StorageConnection, whose translate_sql rewrites ``%s`` -> ``?`` for SQLite.

    So the connection must be wrapped here too, or the test is exercising a
    code path that does not exist in production.
    """
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    init_db_mod.init_db(raw)        # SIPA integrity tables
    raw.execute(_KANBAN_DDL)        # board the reflex writes cards to
    raw.commit()
    yield StorageConnection(raw, "sqlite")
    raw.close()


@pytest.fixture
def deterministic_scanners(monkeypatch, tmp_path):
    """Make scan_all deterministic + fast, isolate quarantine, hermetic RTM."""
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(scanners, "_invoke_scanner", lambda cmd, timeout: (0, "{}", ""))
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: None)
    # The Mode A coverage-gap pass builds the full RTM against the real DB — stub it.
    monkeypatch.setattr(intent_reconciler, "_coverage_gaps", lambda *a, **k: [])


def _count_cards(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM kanban_tasks").fetchone()["n"]


# --------------------------------------------------------------------------- #
# Helper / unit coverage
# --------------------------------------------------------------------------- #
def test_rel_path_strips_quarantine_prefix():
    # <quarantine>/<assessment_id>/<relpath> -> <relpath>
    assert integrity_monitor._rel_path("/q/dir/7/net.py", 7) == "net.py"
    assert integrity_monitor._rel_path("/q/dir/7/sub/x.py", 7) == "sub/x.py"
    # No marker -> basename fallback.
    assert integrity_monitor._rel_path("/somewhere/else/y.py", 99) == "y.py"
    assert integrity_monitor._rel_path("", 1) == ""


# --------------------------------------------------------------------------- #
# Acceptance: a seeded unauthorized capability produces EXACTLY one card
# --------------------------------------------------------------------------- #
def test_seeded_unauthorized_capability_opens_exactly_one_card(
    conn, deterministic_scanners, tmp_path
):
    src = tmp_path / "selfcode"

    # 1. First run over a clean tree: establishes the baseline silently (no cards).
    _write(src, "ok.py", _CLEAN_PY)
    r1 = integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)
    assert r1["success"] is True
    assert r1["details"]["baseline_established"] is True
    assert r1["flagged"] == 0
    assert _count_cards(conn) == 0

    # 2. A new unauthorized capability appears -> exactly one remediation card.
    _write(src, "evil.py", _NET_PY)
    r2 = integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)
    assert r2["status"] == "ok"
    assert r2["success"] is True
    assert r2["details"]["baseline_established"] is False
    assert r2["flagged"] == 1, r2["details"]
    assert _count_cards(conn) == 1

    card = conn.execute(
        "SELECT title, description, status, task_type, dispatch_source FROM kanban_tasks"
    ).fetchone()
    assert card["status"] == "suggested"
    assert card["task_type"] == "fix"
    assert card["dispatch_source"] == "integrity_monitor"
    assert "evil.py" in card["title"]
    assert "network_egress" in card["title"]

    # opx-sipa-01: the persisted card body must carry BOTH the rel path and the
    # raw finding path, plus the assessment-scoped triage SQL, so basename
    # collisions can be disambiguated (the wrong-triage incident on
    # task-d7e78493f3 was caused by a bare-basename rel path).
    body = card["description"]
    assert "File (rel):" in body
    assert "File (raw):" in body
    assert "evil.py" in body                      # raw path retains the real filename
    assert "SELECT" in body and "integrity_findings" in body
    assert "WHERE assessment_id =" in body


def test_rerun_does_not_realert_same_capability(conn, deterministic_scanners, tmp_path):
    src = tmp_path / "selfcode"
    _write(src, "ok.py", _CLEAN_PY)
    integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)  # baseline

    _write(src, "evil.py", _NET_PY)
    integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)  # one card
    assert _count_cards(conn) == 1

    # 3. Re-run with no new capability: baseline now contains the signature AND an
    #    open card already exists -> zero new cards (dedupe, no re-alert).
    r3 = integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)
    assert r3["flagged"] == 0
    assert _count_cards(conn) == 1


def test_dry_run_opens_no_cards(conn, deterministic_scanners, tmp_path):
    src = tmp_path / "selfcode"
    _write(src, "ok.py", _CLEAN_PY)
    integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)  # baseline

    _write(src, "evil.py", _NET_PY)
    r = integrity_monitor.run({"target": str(src), "mode": "aware", "dry_run": True}, conn)
    assert r["details"]["cards"], "dry-run should still report the would-be card"
    assert all(c.get("dry_run") for c in r["details"]["cards"])
    assert _count_cards(conn) == 0


def test_reflex_has_cadence_and_status_contract():
    # Follows the ndc_topology_drift contract: CADENCE_HOURS + run() -> status dict.
    assert isinstance(integrity_monitor.CADENCE_HOURS, int)
    assert integrity_monitor.IMPLEMENTATION_STATUS == "full"


# --------------------------------------------------------------------------- #
# opx-sipa-01: card body carries the raw path + assessment_id (unambiguous)
# --------------------------------------------------------------------------- #
def test_card_description_includes_raw_and_rel_paths():
    """A remediation card must print BOTH the normalized rel path AND the raw
    finding path + assessment-scoped triage SQL. rel_path is often a bare
    basename (the _rel_path fallback) and 6+ files share names like posture.py,
    so rel_path alone is not enough to identify the file."""
    info = {
        "finding_type": "unauthorized_capability",
        "severity": "high",
        "rel_path": "posture.py",  # ambiguous bare basename
        "file_path": "/quarantine/4242/tools/sipa/canvas_compliance/posture.py",
        "line": 87,
        "capability_type": "network_egress",
        "detail": {"reason": "connects to 10.0.0.1"},
    }
    body = integrity_monitor._card_description(
        info, assessment_id=4242, source_ref="icdev-tools-rtm", verdict="review"
    )
    # Both the rel path and the unambiguous raw path appear.
    assert "File (rel):" in body and "posture.py" in body
    assert "File (raw):" in body
    assert "/quarantine/4242/tools/sipa/canvas_compliance/posture.py" in body
    # assessment_id appears in the header AND the triage SQL (never truncated).
    assert "Assessment ID:   4242" in body
    assert "WHERE assessment_id = 4242" in body
    assert "SELECT" in body and "integrity_findings" in body


def test_card_description_raw_path_survives_basename_fallback():
    """When _rel_path collapsed the path to a basename, the raw path must still
    preserve the directory so the card is not ambiguous."""
    raw = "/quarantine/9/tools/ai_augmentation/db/init_db.py"
    info = {
        "finding_type": "unauthorized_capability",
        "severity": "medium",
        "rel_path": "init_db.py",
        "file_path": raw,
        "line": None,
        "capability_type": "filesystem",
        "detail": {},
    }
    body = integrity_monitor._card_description(
        info, assessment_id=9, source_ref="icdev-tools-rtm", verdict="review"
    )
    assert raw in body
    assert "tools/ai_augmentation/db" in body


# --------------------------------------------------------------------------- #
# opx-sipa-02: dir-preserving rel-path fallback (flag-gated) + baseline transition
# --------------------------------------------------------------------------- #
def test_rel_path_dirs_flag_preserves_directories(monkeypatch):
    """With ICDEV_SIPA_RELPATH_DIRS on, the marker-absent fallback keeps the
    normalized relative posix path (directory preserved) instead of the bare
    basename — resolving the posture.py x6 / iac_generator.py x24 ambiguity."""
    # Default (flag OFF): legacy basename fallback — unchanged live behaviour.
    assert integrity_monitor._rel_path("tools/sipa/canvas_compliance/posture.py", 5) == "posture.py"
    assert integrity_monitor._rel_path("infra\\k8s_generator.py", 5) == "k8s_generator.py"

    monkeypatch.setenv("ICDEV_SIPA_RELPATH_DIRS", "1")
    # Marker absent -> directory preserved, separators normalized to posix.
    assert integrity_monitor._rel_path("tools/sipa/canvas_compliance/posture.py", 5) \
        == "tools/sipa/canvas_compliance/posture.py"
    assert integrity_monitor._rel_path("infra\\k8s_generator.py", 5) == "infra/k8s_generator.py"
    # Mixed-separator variants of the SAME file collapse to ONE signature (the
    # only 'collisions' the measurement found — a correct merge, not ambiguity).
    assert integrity_monitor._rel_path("agent\\topology.py", 5) \
        == integrity_monitor._rel_path("agent/topology.py", 5) == "agent/topology.py"
    # A stray absolute path is still made relative (stable signature).
    assert integrity_monitor._rel_path("/abs/tools/x.py", 5) == "abs/tools/x.py"
    # Marker STILL wins when present (unchanged), and empty stays empty.
    assert integrity_monitor._rel_path("/q/dir/7/sub/x.py", 7) == "sub/x.py"
    assert integrity_monitor._rel_path("", 1) == ""


def test_transition_run_opens_zero_cards(conn, deterministic_scanners, tmp_path, monkeypatch):
    """A forced baseline-transition pass re-baselines silently: even with a
    brand-new capability AND a prior baseline present, ZERO cards open."""
    src = tmp_path / "selfcode"

    # Establish a real prior baseline (first run over a clean tree).
    _write(src, "ok.py", _CLEAN_PY)
    integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)
    assert _count_cards(conn) == 0

    # New capability appears, but the transition flag forces a silent re-baseline.
    _write(src, "evil.py", _NET_PY)
    monkeypatch.setenv("ICDEV_SIPA_RELPATH_DIRS", "1")
    monkeypatch.setenv("ICDEV_SIPA_RELPATH_TRANSITION", "1")
    r = integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)
    assert r["success"] is True
    assert r["details"]["baseline_established"] is True
    assert r["details"]["baseline_transition"] is True   # prior existed -> transition, not first-run
    assert r["flagged"] == 0
    assert _count_cards(conn) == 0


def test_post_transition_opens_cards_for_new_findings(conn, deterministic_scanners, tmp_path, monkeypatch):
    """After the transition (flag dropped, DIRS kept), a genuinely-new capability
    opens a card as normal."""
    monkeypatch.setenv("ICDEV_SIPA_RELPATH_DIRS", "1")
    src = tmp_path / "selfcode"

    # First run establishes the baseline (prior_ids empty) — silent, no cards.
    _write(src, "ok.py", _CLEAN_PY)
    r1 = integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)
    assert r1["details"]["baseline_established"] is True
    assert _count_cards(conn) == 0

    # Transition flag is NOT set now -> a new capability opens exactly one card.
    _write(src, "evil.py", _NET_PY)
    r2 = integrity_monitor.run({"target": str(src), "mode": "aware"}, conn)
    assert r2["details"]["baseline_established"] is False
    assert r2["details"]["baseline_transition"] is False
    assert r2["flagged"] == 1, r2["details"]
    assert _count_cards(conn) == 1
