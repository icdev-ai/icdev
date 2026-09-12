# CUI // SP-CTI
"""xrv-mem-03 -- progressive-disclosure retrieval: index -> timeline -> detail by id.

``hybrid_search.py`` returned full-content ranked rows and nothing else, so
every recall paid full-row cost before a reader could decide which rows it
wanted. These tests pin the three layers and the rules that make them safe:

* an ``index`` read of the same ids costs FEWER tokens than the ``detail``
  read (the whole point), and both report ``approx_tokens`` from the
  platform's ONE estimator (``context_budget.estimate_tokens``)
* ``detail`` with unknown ids returns an EMPTY list with the ids NAMED under
  ``missing_ids``; a clearance-withheld row is ``withheld_ids``, never missing
* the default layer is BYTE-IDENTICAL to the pre-card output and carries no
  ``approx_tokens`` (profile_memory, chat_manager, memory_consolidation read
  that shape today)
* ``timeline`` interleaves memory index rows with the activity feed's session
  events in chronological order, and it reaches that feed through the ONE
  query builder ``tools/dashboard/api/activity_query.build_feed_query`` --
  AST-pinned: the UNION is spelled in exactly one file
* a database with no activity tables reports ``unmeasurable``, never an empty
  session
* the ``search_knowledge`` MCP handler accepts ``layer`` and, without it, is
  today's knowledge_patterns search

No live database: every read goes to a SQLite fixture through
``hybrid_search.DB_PATH``; ``semantic_search`` is stubbed so no embedding
provider is contacted.
"""
from __future__ import annotations

import ast
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from tools.db.storage import StorageConnection
from tools.llm.context_budget import estimate_tokens
from tools.memory import hybrid_search as hs

REPO_ROOT = Path(__file__).resolve().parents[2]
HYBRID_SEARCH = REPO_ROOT / "tools" / "memory" / "hybrid_search.py"
ACTIVITY = REPO_ROOT / "tools" / "dashboard" / "api" / "activity.py"
ACTIVITY_QUERY = REPO_ROOT / "tools" / "dashboard" / "api" / "activity_query.py"

_LEGACY_ENTRY_KEYS = {"id", "score", "content", "type", "importance", "created_at"}
_LEGACY_TOP_KEYS = {"classification", "count", "search_type", "semantic_available", "entries"}

# Realistic bodies: the live table's newest rows are multi-sentence lessons,
# not one-liners, and a headline is only a saving against something longer.
_HELM = (
    "Deployed the helm chart to staging, attempt {i}. The rollout waited on the "
    "ingress controller for four minutes because the readiness probe pointed at "
    "the wrong port; corrected the values file and re-ran the pipeline. Lesson: "
    "check the probe path against the service manifest before the first deploy."
)
_OTHER = (
    "Unrelated note number {i} about the lunch rota, the parking permit renewal "
    "and the quarterly badge audit; nothing here concerns a deployment or a chart."
)


def _make_db(path: Path, *, activity_tables: bool = True) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE memory_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            type TEXT DEFAULT 'event',
            importance INTEGER DEFAULT 5,
            embedding BLOB,
            created_at TEXT,
            user_id TEXT, tenant_id TEXT,
            classification TEXT DEFAULT 'CUI',
            compartment TEXT DEFAULT ''
        );
        CREATE TABLE memory_access_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id TEXT, query TEXT,
            results_count INTEGER, search_type TEXT,
            accessed_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    if activity_tables:
        conn.executescript(
            """
            CREATE TABLE audit_trail (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
                actor TEXT NOT NULL, action TEXT NOT NULL, project_id TEXT,
                classification TEXT DEFAULT 'CUI', created_at TIMESTAMP
            );
            CREATE TABLE hook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                hook_type TEXT NOT NULL, tool_name TEXT, project_id TEXT, payload TEXT,
                classification TEXT DEFAULT 'CUI', signature TEXT, created_at TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, created_at) "
            "VALUES ('code.commit', 'alice', 'committed the probe fix', '2026-09-11T10:02:30')"
        )
        # SQLite's CURRENT_TIMESTAMP spelling (space, not T): the row the naive
        # string compare loses.
        conn.execute(
            "INSERT INTO hook_events (session_id, hook_type, tool_name, created_at) "
            "VALUES ('sess-1', 'post_tool_use', 'Bash', '2026-09-11 10:04:30')"
        )
        conn.execute(
            "INSERT INTO hook_events (session_id, hook_type, tool_name, created_at) "
            "VALUES ('sess-2', 'post_tool_use', 'Read', '2026-09-10T09:00:00')"
        )
    # 2 of 8 rows match the query: BM25's IDF is log((N-n+.5)/(n+.5)), which is
    # exactly ZERO for a term in half the corpus, so a half-matching fixture
    # reports no hits and says nothing about the code.
    for i in range(8):
        body = _HELM.format(i=i) if i < 2 else _OTHER.format(i=i)
        conn.execute(
            "INSERT INTO memory_entries (content, type, importance, created_at, classification) "
            "VALUES (?, ?, ?, ?, ?)",
            (body, "event" if i % 2 else "fact", 5, f"2026-09-11T10:0{i}:00", "SECRET" if i == 2 else "CUI"),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "pd.db")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(hs, "DB_PATH", db)
    monkeypatch.setattr(hs, "semantic_search", lambda q, e: None)
    return db


@pytest.fixture
def no_activity_db(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "pd_noact.db", activity_tables=False)
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(hs, "DB_PATH", db)
    monkeypatch.setattr(hs, "semantic_search", lambda q, e: None)
    return db


def _cost_of(payload: dict) -> int:
    body = {k: v for k, v in payload.items() if k != "approx_tokens"}
    return estimate_tokens(json.dumps(body, default=str, sort_keys=True))


# ── index vs detail ─────────────────────────────────────────────────────


def test_index_is_cheaper_than_detail_for_the_same_ids(fixture_db):
    index = hs.run_layer("index", query="helm chart staging probe", limit=3)
    assert index["layer"] == "index"
    assert index["count"] == 2
    ids = [e["id"] for e in index["entries"]]
    detail = hs.run_layer("detail", ids=ids)
    assert detail["count"] == 2
    assert [e["id"] for e in detail["entries"]] == ids
    assert index["approx_tokens"] < detail["approx_tokens"]


def test_index_row_shape_and_headline_cap(fixture_db):
    index = hs.run_layer("index", query="helm chart staging probe", limit=5)
    for row in index["entries"]:
        assert set(row) == {"id", "ts", "type", "headline", "score"}
        assert len(row["headline"]) <= 120
        assert row["score"] > 0
        assert row["ts"].startswith("2026-09-11T")
    # ranked, not chronological
    scores = [r["score"] for r in index["entries"]]
    assert scores == sorted(scores, reverse=True)


def test_every_layer_reports_the_platform_estimator(fixture_db):
    index = hs.run_layer("index", query="helm chart staging probe", limit=2)
    detail = hs.run_layer("detail", ids=[e["id"] for e in index["entries"]])
    timeline = hs.run_layer("timeline", since="2026-09-11T00:00:00", until="2026-09-11T23:59:59")
    for payload in (index, detail, timeline):
        assert isinstance(payload["approx_tokens"], int)
        assert payload["approx_tokens"] == _cost_of(payload)


# ── detail ──────────────────────────────────────────────────────────────


def test_detail_unknown_ids_returns_empty_list_with_missing_named(fixture_db):
    detail = hs.run_layer("detail", ids="999,1000")
    assert detail["entries"] == []
    assert detail["count"] == 0
    assert detail["missing_ids"] == ["999", "1000"]
    assert detail["withheld_ids"] == []


def test_detail_non_numeric_id_is_missing_without_reaching_sql(fixture_db):
    detail = hs.run_layer("detail", ids=["#1", "abc", "2"])
    assert [e["id"] for e in detail["entries"]] == [1, 2]
    assert detail["missing_ids"] == ["abc"]
    assert detail["requested_ids"] == ["1", "2", "abc"]


def test_detail_returns_full_rows_in_requested_order(fixture_db):
    detail = hs.run_layer("detail", ids=[4, 1])
    assert [e["id"] for e in detail["entries"]] == [4, 1]
    row = detail["entries"][1]
    assert set(row) == {"id", "content", "type", "importance", "created_at", "classification", "compartment"}
    assert row["content"] == _HELM.format(i=0)


def test_detail_clearance_withholds_by_name_never_as_missing(fixture_db):
    # id 3 (i == 2) is SECRET; a CUI reader must not see it, and must not be
    # told it does not exist.
    detail = hs.run_layer("detail", ids=[3, 1], clearance="CUI")
    assert [e["id"] for e in detail["entries"]] == [1]
    assert detail["withheld_ids"] == ["3"]
    assert detail["missing_ids"] == []


def test_detail_requires_ids(fixture_db):
    with pytest.raises(ValueError):
        hs.run_layer("detail", ids="")


def test_run_layer_rejects_unknown_and_full():
    with pytest.raises(ValueError):
        hs.run_layer("summary", query="x")
    with pytest.raises(ValueError):
        hs.run_layer("full", query="x")


# ── timeline ────────────────────────────────────────────────────────────


def test_timeline_interleaves_activity_chronologically(fixture_db):
    tl = hs.run_layer("timeline", since="2026-09-11T10:00:00", until="2026-09-11T10:05:00", limit=10)
    kinds = [e["kind"] for e in tl["events"]]
    assert set(kinds) == {"memory", "audit", "hook"}
    stamps = [e["ts"] for e in tl["events"]]
    assert stamps == sorted(stamps)
    assert tl["counts"] == {"memory": 6, "activity": 2, "total": 8}
    assert tl["activity"] == {"status": "ok"}
    # the space-form hook row (2026-09-11 10:04:30) is inside the window
    hook = next(e for e in tl["events"] if e["kind"] == "hook")
    assert hook["ts"] == "2026-09-11T10:04:30"
    assert hook["headline"] == "Bash"
    for e in tl["events"]:
        assert set(e) >= {"kind", "id", "ts", "type", "headline", "score"}
        assert len(e["headline"]) <= 120


def test_timeline_window_is_respected_on_both_sides(fixture_db):
    tl = hs.run_layer("timeline", since="2026-09-11T10:02:00", until="2026-09-11T10:03:00", limit=10)
    assert tl["counts"]["memory"] == 2
    assert tl["counts"]["activity"] == 1  # the audit row at 10:02:30, not the 09-10 hook
    assert tl["window"] == {"since": "2026-09-11T10:02:00", "until": "2026-09-11T10:03:00", "basis": "explicit"}


def test_timeline_with_query_scores_memory_rows_and_keeps_time_order(fixture_db):
    tl = hs.run_layer("timeline", query="helm chart staging probe", since="2026-09-11T00:00:00",
                      until="2026-09-11T23:59:59", limit=10)
    mem = [e for e in tl["events"] if e["kind"] == "memory"]
    assert len(mem) == 2 and all(e["score"] > 0 for e in mem)
    assert [e["ts"] for e in mem] == sorted(e["ts"] for e in mem)
    assert all(e["score"] is None for e in tl["events"] if e["kind"] != "memory")


def test_timeline_default_window_is_reported_not_silent(fixture_db):
    tl = hs.run_layer("timeline")
    assert tl["window"]["basis"] == f"default_{hs.DEFAULT_TIMELINE_WINDOW_HOURS}h"
    assert tl["window"]["since"] is not None and tl["window"]["until"] is None


def test_timeline_without_activity_tables_is_unmeasurable_never_empty(no_activity_db):
    tl = hs.run_layer("timeline", since="2026-09-11T00:00:00", until="2026-09-11T23:59:59")
    assert tl["activity"]["status"] == "unmeasurable"
    assert tl["activity"]["reason"].startswith("error:")
    assert tl["counts"]["activity"] == 0 and tl["counts"]["memory"] == 8


def test_timeline_session_filter_narrows_hook_events(fixture_db):
    tl = hs.run_layer("timeline", since="2026-09-10T00:00:00", until="2026-09-11T23:59:59",
                      session_id="sess-2", limit=10)
    activity = [e for e in tl["events"] if e["kind"] != "memory"]
    assert [e["actor"] for e in activity] == ["sess-2"]


# ── the default layer is untouched ──────────────────────────────────────


def _translating_conn(path: Path) -> StorageConnection:
    """A fixture connection that still translates %s -> ? (never a bare sqlite3 handle)."""
    return StorageConnection(sqlite3.connect(str(path)), "sqlite")


@pytest.fixture
def legacy_main(fixture_db, monkeypatch):
    """main()'s full layer logs through a bare get_connection(); point it at the fixture."""
    monkeypatch.setattr(hs, "get_connection", lambda *a, **k: _translating_conn(fixture_db))

    def run(argv):
        monkeypatch.setattr("sys.argv", ["hybrid_search.py", *argv])
        hs.main()

    return run


def test_default_layer_is_byte_identical_to_layer_full(legacy_main, capsys):
    legacy_main(["--query", "helm chart staging probe", "--json"])
    default_out = capsys.readouterr().out
    legacy_main(["--query", "helm chart staging probe", "--json", "--layer", "full"])
    full_out = capsys.readouterr().out
    assert default_out == full_out
    payload = json.loads(default_out)
    assert set(payload) == _LEGACY_TOP_KEYS
    assert "approx_tokens" not in payload
    assert payload["count"] == 2
    for entry in payload["entries"]:
        assert set(entry) == _LEGACY_ENTRY_KEYS


def test_default_layer_still_requires_query(legacy_main):
    with pytest.raises(SystemExit) as exc:
        legacy_main(["--json"])
    assert exc.value.code == 2


def test_cli_index_and_detail_layers(legacy_main, capsys):
    legacy_main(["--query", "helm chart staging probe", "--layer", "index", "--json", "--limit", "2"])
    index = json.loads(capsys.readouterr().out)
    assert index["layer"] == "index" and index["count"] == 2 and "approx_tokens" in index
    ids = ",".join(str(e["id"]) for e in index["entries"])
    legacy_main(["--layer", "detail", "--ids", ids + ",999", "--json"])
    detail = json.loads(capsys.readouterr().out)
    assert detail["layer"] == "detail" and detail["count"] == 2
    assert detail["missing_ids"] == ["999"]
    assert index["approx_tokens"] < detail["approx_tokens"]


def test_cli_detail_without_ids_is_a_usage_error(legacy_main):
    with pytest.raises(SystemExit) as exc:
        legacy_main(["--layer", "detail", "--json"])
    assert exc.value.code == 2


def test_cli_human_output_carries_the_cost(legacy_main, capsys):
    legacy_main(["--layer", "timeline", "--since", "2026-09-11T10:00:00", "--until", "2026-09-11T10:05:00"])
    out = capsys.readouterr().out
    assert "approx_tokens:" in out
    assert "audit:1" in out and "hook:" in out


# ── ONE query builder, AST-pinned ───────────────────────────────────────


def _imports_of(path: Path) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                found.add((node.module, alias.name))
    return found


def test_timeline_imports_the_activity_query_builder_rather_than_respelling_the_union():
    assert ("tools.dashboard.api.activity_query", "build_feed_query") in _imports_of(HYBRID_SEARCH)
    assert ("tools.dashboard.api.activity_query", "build_feed_query") in _imports_of(ACTIVITY)
    # Comments and docstrings may SAY "UNION ALL"; only a string CONSTANT can
    # be a second copy of the SQL. Exactly one file spells it.
    def sql_unions(path: Path) -> int:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc = (ast.get_docstring(tree) or "").strip()
        return sum(
            1 for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "UNION ALL" in n.value and n.value.strip() != doc
        )

    assert sql_unions(HYBRID_SEARCH) == 0
    assert sql_unions(ACTIVITY) == 0
    assert sql_unions(ACTIVITY_QUERY) == 1


def test_activity_routes_call_the_builder_and_define_no_local_query():
    tree = ast.parse(ACTIVITY.read_text(encoding="utf-8"))
    assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert "MERGED_QUERY" not in assigned
    callers = set()
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "build_feed_query":
                callers.add(fn.name)
    assert {"activity_feed", "activity_poll"} <= callers


def test_builder_normalises_timestamps_only_when_asked():
    from tools.dashboard.api.activity_query import build_feed_query

    plain, params = build_feed_query("?", since="a", until="b", after="c", limit=5, offset=2)
    assert "replace(" not in plain
    assert params == ["a", "b", "c", 5, 2]
    norm, _ = build_feed_query("?", since="a", normalise_ts=True)
    assert norm.count("replace(created_at, ' ', 'T')") == 2  # the bound and the ORDER BY
    with pytest.raises(ValueError):
        build_feed_query("?", order="sideways")


# ── the MCP door ────────────────────────────────────────────────────────


def test_mcp_handler_accepts_layer_and_defaults_to_todays_search(fixture_db, monkeypatch):
    ks = importlib.import_module("tools.mcp.knowledge_server")
    calls = []

    def fake_import(module, name):
        if module == "tools.knowledge.pattern_detector":
            def search(**kw):
                calls.append(kw)
                return {"query": kw["query"], "results": [], "count": 0}
            return search
        return getattr(importlib.import_module(module), name, None)

    monkeypatch.setattr(ks, "_import_tool", fake_import)
    legacy = ks.handle_search_knowledge({"query": "helm"})
    assert legacy == {"query": "helm", "results": [], "count": 0}
    assert calls and calls[0]["query"] == "helm"

    index = ks.handle_search_knowledge({"query": "helm chart staging probe", "layer": "index", "limit": 2})
    assert index["layer"] == "index" and index["count"] == 2 and "approx_tokens" in index
    detail = ks.handle_search_knowledge({"layer": "detail", "ids": [e["id"] for e in index["entries"]]})
    assert detail["layer"] == "detail" and detail["count"] == 2
    assert len(calls) == 1  # the layered calls never reached knowledge_patterns
    with pytest.raises(ValueError):
        ks.handle_search_knowledge({})


def test_registry_declares_the_layers():
    from tools.mcp.tool_registry import TOOL_REGISTRY

    schema = TOOL_REGISTRY["search_knowledge"]["input_schema"]
    assert tuple(schema["properties"]["layer"]["enum"]) == hs.LAYERS[1:]
    assert "query" not in schema.get("required", [])  # detail needs none
    assert set(schema["properties"]) >= {"ids", "since", "until", "session_id"}
