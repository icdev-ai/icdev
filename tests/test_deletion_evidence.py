#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for runtime-backed deletion evidence (CodeLens CL-4).

Covers route-pattern extraction/regex, the usage_events traffic lookup
(window filtering + converter matching), the verdict matrix, graceful
DB degradation, and the enrich/run orchestrators. A temp SQLite
`usage_events` table stands in for the live PG table; blueprint source
files are written to a temp tree.
"""

import sqlite3
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.code_intelligence.deletion_evidence import (  # noqa: E402
    enrich,
    extract_route_patterns,
    load_route_traffic,
    match_traffic,
    pattern_to_regex,
    run,
    runtime_evidence_for_file,
    static_prefix,
    verdict_for,
)

NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage_conn(rows):
    """In-memory usage_events table. rows = list of (route, occurred_at)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE usage_events (route TEXT, method TEXT, status_code INT, "
        "occurred_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO usage_events (route, occurred_at) VALUES (?, ?)", rows
    )
    conn.commit()
    return conn


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat()


def _write(root, rel, body):
    fp = root / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(textwrap.dedent(body), encoding="utf-8")
    return fp


# ---------------------------------------------------------------------------
# Route pattern helpers
# ---------------------------------------------------------------------------


def test_extract_route_patterns():
    src = """
        @bp.route("/a")
        def a(): ...

        @app.route('/b/<id>', methods=["POST"])
        def b(id): ...

        app.add_url_rule("/c", view_func=c)
    """
    assert extract_route_patterns(textwrap.dedent(src)) == ["/a", "/b/<id>", "/c"]


def test_static_prefix():
    assert static_prefix("/api/x/<id>") == "/api/x/"
    assert static_prefix("/api/x") == "/api/x"


def test_pattern_to_regex_segment_vs_path():
    seg = pattern_to_regex("/item/<id>")
    assert seg.match("/item/123")
    assert not seg.match("/item/123/sub")      # single segment only
    assert seg.match("/item/123/")             # trailing slash tolerated

    p = pattern_to_regex("/files/<path:sub>")
    assert p.match("/files/a/b/c")             # path converter spans slashes


# ---------------------------------------------------------------------------
# Traffic lookup
# ---------------------------------------------------------------------------


def test_load_traffic_counts_and_last_hit():
    conn = _usage_conn([("/x", _iso(1)), ("/x", _iso(3)), ("/y", _iso(2))])
    traffic = load_route_traffic(conn, _iso(30))
    t = match_traffic(["/x"], traffic)
    assert t["hit_count"] == 2
    assert t["last_hit"] == _iso(1)
    assert t["matched_routes"] == ["/x"]


def test_load_traffic_window_excludes_old():
    conn = _usage_conn([("/x", _iso(40)), ("/x", _iso(50))])
    traffic = load_route_traffic(conn, _iso(30))
    t = match_traffic(["/x"], traffic)
    assert t["hit_count"] == 0


def test_match_traffic_converter():
    conn = _usage_conn([("/item/1", _iso(1)), ("/item/2", _iso(2)), ("/other", _iso(1))])
    traffic = load_route_traffic(conn, _iso(30))
    t = match_traffic(["/item/<id>"], traffic)
    assert t["hit_count"] == 2
    assert set(t["matched_routes"]) == {"/item/1", "/item/2"}


def test_load_traffic_db_error_degrades():
    conn = sqlite3.connect(":memory:")  # no usage_events table
    conn.row_factory = sqlite3.Row
    assert load_route_traffic(conn, _iso(30)) is None
    assert load_route_traffic(None, _iso(30)) is None


# ---------------------------------------------------------------------------
# File-level evidence
# ---------------------------------------------------------------------------


def _traffic(rows):
    return load_route_traffic(_usage_conn(rows), _iso(30))


def test_evidence_runtime_hot(tmp_path):
    _write(tmp_path, "tools/bp.py", '@bp.route("/foo")\ndef foo(): ...\n')
    ev = runtime_evidence_for_file("tools/bp.py", tmp_path, _traffic([("/foo", _iso(2))]), NOW)
    assert ev["signal"] == "runtime_hot"
    assert ev["hit_count"] == 1
    assert ev["days_since_last_hit"] == 2


def test_evidence_runtime_cold(tmp_path):
    _write(tmp_path, "tools/bp.py", '@bp.route("/foo")\ndef foo(): ...\n')
    ev = runtime_evidence_for_file("tools/bp.py", tmp_path, _traffic([("/bar", _iso(2))]), NOW)
    assert ev["signal"] == "runtime_cold"
    assert ev["routes_defined"] == 1


def test_evidence_no_routes(tmp_path):
    _write(tmp_path, "tools/helper.py", "def util():\n    return 1\n")
    ev = runtime_evidence_for_file("tools/helper.py", tmp_path, _traffic([("/foo", _iso(1))]), NOW)
    assert ev["signal"] == "no_signal"


def test_evidence_no_traffic_map(tmp_path):
    _write(tmp_path, "tools/bp.py", '@bp.route("/foo")\ndef foo(): ...\n')
    ev = runtime_evidence_for_file("tools/bp.py", tmp_path, None, NOW)
    assert ev["signal"] == "no_signal"


# ---------------------------------------------------------------------------
# Verdict matrix
# ---------------------------------------------------------------------------


def test_verdict_orphan_hot_is_keep():
    f = {"kind": "orphan_file", "confidence": "medium"}
    ev = {"signal": "runtime_hot", "routes_defined": 2, "hit_count": 5, "days_since_last_hit": 1}
    v = verdict_for(f, ev, 30)
    assert v["recommendation"] == "keep"
    assert v["confidence"] == "low"


def test_verdict_orphan_cold_is_delete_high():
    f = {"kind": "orphan_file", "confidence": "medium"}
    ev = {"signal": "runtime_cold", "routes_defined": 3}
    v = verdict_for(f, ev, 30)
    assert v["recommendation"] == "delete"
    assert v["confidence"] == "high"


def test_verdict_orphan_no_signal_is_review():
    f = {"kind": "orphan_file", "confidence": "medium"}
    v = verdict_for(f, {"signal": "no_signal"}, 30)
    assert v["recommendation"] == "review"
    assert v["confidence"] == "medium"


def test_verdict_dead_function_hot_is_review_not_delete():
    f = {"kind": "dead_function", "confidence": "medium"}
    ev = {"signal": "runtime_hot", "hit_count": 9}
    v = verdict_for(f, ev, 30)
    # never auto-delete a symbol on file-level traffic
    assert v["recommendation"] == "review"
    assert v["confidence"] == "low"


def test_verdict_dead_function_no_signal_keeps_base():
    f = {"kind": "dead_class", "confidence": "medium"}
    v = verdict_for(f, {"signal": "no_signal"}, 30)
    assert v["recommendation"] == "review"
    assert v["confidence"] == "medium"


# ---------------------------------------------------------------------------
# enrich() + run()
# ---------------------------------------------------------------------------


def test_enrich_caches_file_evidence_and_degrades_without_db(tmp_path):
    _write(tmp_path, "tools/bp.py", '@bp.route("/foo")\ndef foo(): ...\n')
    findings = [
        {"kind": "orphan_file", "name": "tools.bp", "file": "tools/bp.py",
         "line": None, "confidence": "medium"},
        {"kind": "unused_dependency", "name": "flask", "file": "requirements.txt",
         "line": None, "confidence": "medium"},
    ]
    # conn_factory raises -> degrade, no crash
    def boom():
        raise RuntimeError("no db")

    out = enrich(findings, tmp_path, window_days=30, conn_factory=boom, now=NOW)
    assert out[0]["runtime"]["signal"] == "no_signal"
    assert out[0]["verdict"]["recommendation"] == "review"
    # dependency finding gets the not-applicable branch
    assert out[1]["runtime"]["signal"] == "no_signal"


def test_run_contract(tmp_path):
    # orphan blueprint with a cold route -> confirmed deletion
    _write(tmp_path, "tools/dead_bp.py", '@bp.route("/never")\ndef never(): ...\n')
    conn = _usage_conn([("/somethingelse", _iso(1))])
    report = run(project_dir=str(tmp_path / "tools"), base=tmp_path,
                 window_days=30, conn=conn, now=NOW, checks=["orphans"])
    assert report["tool"] == "deletion_evidence"
    s = report["summary"]
    assert set(s) >= {"findings", "by_runtime_signal", "by_recommendation",
                      "confirmed_deletions", "runtime_false_positives"}
    # the cold orphan blueprint should be a confirmed deletion
    dead = [f for f in report["findings"]
            if (f["file"] or "").replace("\\", "/") == "tools/dead_bp.py"]
    assert dead and dead[0]["verdict"]["recommendation"] == "delete"
    assert s["confirmed_deletions"] >= 1


def test_run_rescues_false_positive(tmp_path):
    # orphan blueprint whose route IS hit -> rescued as keep
    _write(tmp_path, "tools/live_bp.py", '@bp.route("/live")\ndef live(): ...\n')
    conn = _usage_conn([("/live", _iso(1)), ("/live", _iso(2))])
    report = run(project_dir=str(tmp_path / "tools"), base=tmp_path,
                 window_days=30, conn=conn, now=NOW, checks=["orphans"])
    live = [f for f in report["findings"]
            if (f["file"] or "").replace("\\", "/") == "tools/live_bp.py"]
    assert live and live[0]["verdict"]["recommendation"] == "keep"
    assert report["summary"]["runtime_false_positives"] >= 1
