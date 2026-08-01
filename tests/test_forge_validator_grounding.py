# CUI // SP-CTI
"""Tests: child apps must inherit anti-hallucination grounding (trust-cite-05)
and pass the hardened forge_validator gate (cvx-gen-01).

Covers:
    - child_app_generator.DIRECTORY_TREE includes tools/quality
    - forge_validator FORGE-03c grounding presence + API-freshness (pass/fail)
    - forge_validator FORGE-11 coherence: missing tools/workflow -> FAIL
    - forge_validator FORGE-12 banned DB patterns: sqlite3.connect -> FAIL,
      bare '?' placeholder -> WARN
"""

import importlib

cag = importlib.import_module("tools.builder.child_app_generator")
fv = importlib.import_module("tools.builder.forge_validator")

# Canonical API markers the child grounding modules must carry.
_CONTENT_GROUNDING_STUB = "def ground_content(*args, **kwargs):\n    return None\n"
_CITATION_GROUNDING_STUB = "def classify_confidence(score):\n    return 'include'\n"


def _grounding_check(checks):
    return next((c for c in checks if c.check_id == "FORGE-03c"), None)


def _write_current_grounding(tmp_path):
    q = tmp_path / "tools" / "quality"
    q.mkdir(parents=True)
    (q / "content_grounding.py").write_text(_CONTENT_GROUNDING_STUB, encoding="utf-8")
    (q / "citation_grounding.py").write_text(_CITATION_GROUNDING_STUB, encoding="utf-8")
    return q


def test_directory_tree_includes_quality():
    assert "tools/quality" in cag.DIRECTORY_TREE


def test_directory_tree_includes_workflow():
    assert "tools/workflow" in cag.DIRECTORY_TREE


# ── FORGE-03c: grounding presence + API freshness ──────────────────────────


def test_validator_passes_when_grounding_current(tmp_path):
    # (a) grounding files present AND carry the current public API -> pass
    _write_current_grounding(tmp_path)
    (tmp_path / "tools" / "db").mkdir(parents=True)
    check = _grounding_check(fv._check_tools(tmp_path))
    assert check is not None
    assert check.status == "pass"


def test_validator_fails_when_grounding_missing(tmp_path):
    # tools/ exists with some scripts but no grounding modules
    (tmp_path / "tools" / "db").mkdir(parents=True)
    (tmp_path / "tools" / "db" / "x.py").write_text("# stub\n", encoding="utf-8")
    check = _grounding_check(fv._check_tools(tmp_path))
    assert check is not None
    assert check.status == "fail"
    assert "citation_grounding.py" in check.actual


def test_validator_fails_when_grounding_stale(tmp_path):
    # (b) files present but content_grounding lacks ground_content() -> FAIL
    q = tmp_path / "tools" / "quality"
    q.mkdir(parents=True)
    (tmp_path / "tools" / "db").mkdir(parents=True)
    (q / "content_grounding.py").write_text("# stale pre-ground_content snapshot\n", encoding="utf-8")
    (q / "citation_grounding.py").write_text(_CITATION_GROUNDING_STUB, encoding="utf-8")
    check = _grounding_check(fv._check_tools(tmp_path))
    assert check is not None
    assert check.status == "fail"
    assert "stale" in check.actual
    assert "content_grounding.py" in check.actual


# ── FORGE-11: coherence checker must be present ────────────────────────────


def _coherence_check(checks):
    return next((c for c in checks if c.check_id == "FORGE-11"), None)


def test_coherence_fails_when_workflow_missing(tmp_path):
    # (c) no tools/workflow/coherence_checker.py -> explicit FAIL (not silent pass)
    (tmp_path / "tools" / "db").mkdir(parents=True)
    check = _coherence_check(fv._check_coherence(tmp_path))
    assert check is not None
    assert check.status == "fail"
    assert "missing" in check.message.lower()


# ── FORGE-12: banned DB access patterns ────────────────────────────────────


def _check(checks, check_id):
    return next((c for c in checks if c.check_id == check_id), None)


def test_db_patterns_fails_on_sqlite_connect(tmp_path):
    # (d) sqlite3.connect() in a runtime module -> FORGE-12 FAIL
    mod = tmp_path / "tools" / "svc"
    mod.mkdir(parents=True)
    (mod / "handler.py").write_text(
        "import sqlite3\n\ndef go():\n    return sqlite3.connect('x.db')\n", encoding="utf-8"
    )
    check = _check(fv._check_db_patterns(tmp_path), "FORGE-12")
    assert check is not None
    assert check.status == "fail"
    assert "handler.py" in check.actual


def test_db_patterns_allows_sqlite_connect_in_db_init(tmp_path):
    # sqlite3.connect() is allowed in db/init_*.py -> FORGE-12 pass
    db = tmp_path / "tools" / "db"
    db.mkdir(parents=True)
    (db / "init_db.py").write_text(
        "import sqlite3\n\ndef init():\n    return sqlite3.connect('x.db')\n", encoding="utf-8"
    )
    check = _check(fv._check_db_patterns(tmp_path), "FORGE-12")
    assert check is not None
    assert check.status == "pass"


def test_db_patterns_warns_on_bare_placeholder(tmp_path):
    # (e) bare '?' placeholder in a runtime module -> FORGE-12a WARN
    mod = tmp_path / "tools" / "svc"
    mod.mkdir(parents=True)
    (mod / "query.py").write_text(
        'def q(cur, x):\n    return cur.execute("SELECT 1 WHERE id = ?", (x,))\n', encoding="utf-8"
    )
    check = _check(fv._check_db_patterns(tmp_path), "FORGE-12a")
    assert check is not None
    assert check.status == "warn"
    assert "query.py" in check.actual


# ── FORGE-13: 8-component canvas completeness gate (cvx-gen-02) ──────────────


def _write_complete_canvas(root, key="foo"):
    """Build a child tree with one canvas satisfying all 8 completeness points.

    Mirrors what child_app_generator would emit for a well-formed canvas so the
    reused validate_canvas_completeness (pointed at repo_root=root) passes.
    """
    def _w(rel, text="x\n"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    # Registry with a single canvas declaring the 8-point paths.
    _w(
        "args/component_registry.yaml",
        (
            "components:\n"
            f"- key: {key}\n"
            "  kind: canvas\n"
            f"  display_name: {key.title()}\n"
            f"  module: tools.{key}.blueprint\n"
            f"  blueprint_attr: create_{key}_blueprint\n"
            f"  url_prefix: /{key}\n"
            "  nav:\n"
            "    section: Build\n"
            f"    label: {key.title()}\n"
            "  iqe:\n"
            f"    adapter_module: tools.iqe.adapters.{key}\n"
            "  completeness:\n"
            f"    template: tools/dashboard/templates/{key}/page.html\n"
            f"    constants: tools/{key}/constants.py\n"
            f"    db_migration: tools/{key}/db/migrations/001_init.sql\n"
            f"    seed_queries: context/iqe/queries/{key}\n"
            "    nav_link: true\n"
        ),
    )
    # Point 1 + 2: template in both the app tree and the icdev/ mirror
    _w(f"tools/dashboard/templates/{key}/page.html", "<html>page</html>\n")
    _w(f"icdev/tools/dashboard/templates/{key}/page.html", "<html>page</html>\n")
    # Point 3: blueprint with a @bp.route decorator
    _w(
        f"tools/{key}/blueprint.py",
        (
            "bp = object()\n\n"
            "@bp.route('/')\n"
            "def index():\n    return 'ok'\n"
        ),
    )
    # Point 4: backing module (non-blueprint, non-init)
    _w(f"tools/{key}/service.py", "def run():\n    return 1\n")
    _w(f"tools/{key}/__init__.py", "")
    # Point 5: constants
    _w(f"tools/{key}/constants.py", "OBJECT_TYPES = []\n")
    # Point 6: DB migration
    _w(f"tools/{key}/db/migrations/001_init.sql", "-- init\n")
    # Point 8: IQE adapter + seed queries
    _w(f"tools/iqe/adapters/{key}.py", "def register():\n    return []\n")
    _w(f"context/iqe/queries/{key}/q1.json", "{}\n")
    return root


def _completeness_check(checks, key="foo"):
    return next((c for c in checks if c.check_id == f"FORGE-13-{key}"), None)


def test_completeness_passes_for_complete_canvas(tmp_path):
    # A fully-generated canvas passes the 8-component gate.
    _write_complete_canvas(tmp_path)
    check = _completeness_check(fv._check_canvas_completeness(tmp_path))
    assert check is not None, "expected a FORGE-13-foo per-canvas result"
    assert check.status == "pass", check.actual


def test_completeness_fails_when_icdev_mirror_missing(tmp_path):
    # Template present in only one tree (no icdev/ mirror) -> FAIL.
    _write_complete_canvas(tmp_path)
    (tmp_path / "icdev" / "tools" / "dashboard" / "templates" / "foo" / "page.html").unlink()
    check = _completeness_check(fv._check_canvas_completeness(tmp_path))
    assert check is not None
    assert check.status == "fail"
    assert "icdev_mirror" in check.actual


def test_completeness_fails_when_iqe_adapter_missing(tmp_path):
    # IQE adapter declared in registry but the adapter file is absent -> FAIL.
    _write_complete_canvas(tmp_path)
    (tmp_path / "tools" / "iqe" / "adapters" / "foo.py").unlink()
    check = _completeness_check(fv._check_canvas_completeness(tmp_path))
    assert check is not None
    assert check.status == "fail"
    assert "iqe_integration" in check.actual


def test_completeness_skips_without_registry(tmp_path):
    # No args/component_registry.yaml -> pass (nothing to validate), base ID.
    (tmp_path / "tools").mkdir()
    check = _check(fv._check_canvas_completeness(tmp_path), "FORGE-13")
    assert check is not None
    assert check.status == "pass"
    assert "skipped" in check.message.lower()
