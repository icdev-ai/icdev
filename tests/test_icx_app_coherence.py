# CUI // SP-CTI
"""icx-coh-01 -- the generic coherence checks, run against an APPLICATION's directory (not ICDEV itself).

A synthetic legacy app with one defect of each kind; every check finds its own, says pass/fail/warn/skip with the
evidence, and a check that could not run says why (never read as a pass). The bundle section is id + status.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from tools.workflow import app_coherence as ac


def _w(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def app(tmp_path):
    r = tmp_path / "depot"
    _w(r, "depot/store.py", '''
        def save(conn, row):
            try:
                conn.execute("INSERT INTO stock (sku, qty) VALUES (?, ?)", row)
            except Exception:
                pass

        def receive(sku, qty, *, depot="main"):
            return sku, qty, depot
    ''')
    _w(r, "depot/web.py", '''
        @app.route("/stock")
        def stock(): ...
        @app.route("/stock/<sku>/receive")
        def receive(sku): ...
    ''')
    _w(r, "config/settings.yaml", "db_url: x\nunused_flag: true\n")
    _w(r, "depot/db.py", 'URL = "db_url"\n')
    _w(r, "schema.sql", "CREATE TABLE stock_audit (id INT);\nCREATE TABLE stock_history (id INT);\n"
                        "CREATE TRIGGER no_upd BEFORE UPDATE OR DELETE ON stock_history FOR EACH ROW EXECUTE f();\n")
    _w(r, "openapi.yaml", "openapi: 3.0.0\npaths:\n  /stock: {}\n  /stock/{sku}: {}\n")
    _w(r, "tests/test_store.py", '''
        from depot.store import receive
        def test_ok():
            receive("a", 1, depot="b")
        def test_bad():
            receive("a", 1, 2)
            receive("a", 1, where="x")
    ''')
    return r


def _by(rep):
    return {c["id"]: c for c in rep["checks"]}


def test_each_check_finds_its_own_defect(app):
    rep = ac.run(app)
    c = _by(rep)
    assert c["swallowed_persistence"]["status"] == "fail" and "depot/store.py" in c["swallowed_persistence"]["evidence"][0]
    assert c["config_code"]["status"] == "warn" and c["config_code"]["evidence"] == ["config/settings.yaml: unused_flag"]
    assert c["append_only"]["status"] == "fail" and c["append_only"]["evidence"] == ["stock_audit (schema.sql)"]
    assert c["openapi_parity"]["status"] == "fail"
    assert sorted(c["openapi_parity"]["evidence"]) == ["no route: /stock/{}", "undocumented: /stock/{}/receive"]
    sig = c["signature_call"]
    assert sig["status"] == "fail" and len(sig["evidence"]) == 2 and "takes at most 2 positional" in sig["evidence"][0]
    assert rep["counts"] == {"pass": 0, "fail": 4, "warn": 1, "skip": 0}


def test_a_check_that_cannot_run_says_why_and_is_never_a_pass(tmp_path):
    _w(tmp_path, "Main.java", "class Main {}\n")
    c = _by(ac.run(tmp_path))
    assert all(v["status"] == "skip" and v["summary"] for v in c.values())
    assert "Python" in c["swallowed_persistence"]["summary"] and "OpenAPI" in c["openapi_parity"]["summary"]


def test_a_clean_app_passes(tmp_path):
    _w(tmp_path, "svc/api.py", '@app.get("/items/{id}")\ndef item(id): ...\n')
    _w(tmp_path, "openapi.json", json.dumps({"openapi": "3.0.0", "paths": {"/items/{item_id}": {}}}))
    _w(tmp_path, "migrations/1.sql", "CREATE TABLE event_ledger (id INT);\nREVOKE UPDATE, DELETE ON event_ledger FROM app;\n")
    c = _by(ac.run(tmp_path))
    assert c["openapi_parity"]["status"] == "pass" and c["append_only"]["status"] == "pass"


def test_the_bundle_section_and_the_checker_entry_point(app):
    sec = ac.bundle_section(ac.run(app))
    assert sec["tool"] == "icdev-it/app_coherence" and {c["id"] for c in sec["checks"]} == {
        "swallowed_persistence", "config_code", "append_only", "openapi_parity", "signature_call"}
    assert all(set(c) == {"id", "status"} for c in sec["checks"])
    r = subprocess.run([sys.executable, "tools/workflow/coherence_checker.py", "--target", str(app), "--profile", "app",
                        "--gate"], capture_output=True, text=True, cwd=str(ac._BASE), timeout=120)
    assert r.returncode == 1 and json.loads(r.stdout)["profile"] == "app", r.stderr[-500:]
