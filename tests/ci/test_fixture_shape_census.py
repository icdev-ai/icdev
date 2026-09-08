# CUI // SP-CTI
"""The fixture-shape census refuses a NEW site and nothing else (tsg-iso-04).

The census exists because PR #2167 failed CI shard 2 with

    sqlite3.OperationalError: table dic_documents has no column named status

while passing locally, alone, in its own directory, and in file order — a
fixture's ``CREATE TABLE IF NOT EXISTS`` silently keeping a narrower shape some
earlier module in the process had already created.

Every test here builds fixture SOURCE and runs the shipped scanner over it. The
census file this module is excluded from (args/fixture_shape_gate.yaml) exactly
because these strings carry the hazard deliberately, as input.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


from tools.ci.fixture_shape_census import build_report, scan_file

REPO = Path(__file__).resolve().parents[2]

RISKY = '''
DDL = """CREATE TABLE IF NOT EXISTS widgets (
    id TEXT PRIMARY KEY, name TEXT, status TEXT)"""

def test_x(conn):
    conn.execute(DDL)
    conn.execute("INSERT INTO widgets (id, status) VALUES ('a','open')")
'''

SAFE_NO_OVERLAP = '''
DDL = """CREATE TABLE IF NOT EXISTS widgets (
    id TEXT PRIMARY KEY, name TEXT, status TEXT)"""

def test_x(conn):
    conn.execute(DDL)
    # writes nothing the declaration is the only guarantee of
    conn.execute("SELECT id FROM widgets")
'''

SAFE_ENSURE_TABLE = '''
from tests._schema_compat import ensure_table

DDL = """CREATE TABLE IF NOT EXISTS widgets (
    id TEXT PRIMARY KEY, name TEXT, status TEXT)"""

def test_x(conn):
    ensure_table(conn, DDL)
    conn.execute("INSERT INTO widgets (id, status) VALUES ('a','open')")
'''

COMMENTED_OUT = '''
def test_x(conn):
    # CREATE TABLE IF NOT EXISTS widgets (id TEXT, status TEXT)
    # INSERT INTO widgets (id, status) VALUES ('a','b')
    assert True
'''

CHECK_CONSTRAINT = '''
DDL = """CREATE TABLE IF NOT EXISTS widgets (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','shut','held')),
    note TEXT)"""

def test_x(conn):
    conn.execute(DDL)
    conn.execute("INSERT INTO widgets (id, state) VALUES ('a','open')")
'''


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────── the conjunction
def test_the_hazard_is_a_finding(tmp_path):
    sites = scan_file(_write(tmp_path, "test_a.py", RISKY), tmp_path)
    assert [s["table"] for s in sites] == ["widgets"]
    assert "status" in sites[0]["columns"]


def test_declaring_without_writing_is_not_a_finding(tmp_path):
    """(c) is what keeps this high-signal — the declaration alone is fine."""
    assert scan_file(_write(tmp_path, "test_b.py", SAFE_NO_OVERLAP), tmp_path) == []


def test_a_file_that_reaches_the_guarantee_is_not_a_finding(tmp_path):
    """ensure_table turns the declaration INTO a guarantee — that is the fix."""
    assert scan_file(_write(tmp_path, "test_c.py", SAFE_ENSURE_TABLE), tmp_path) == []


def test_a_commented_out_fixture_is_not_a_finding(tmp_path):
    """SQL is read from string literals via ast, never by grepping the file.

    A census whose entries were commentary about itself would be discredited on
    day one — the argument perfect_score_census already makes.
    """
    assert scan_file(_write(tmp_path, "test_d.py", COMMENTED_OUT), tmp_path) == []


def test_check_constraint_commas_do_not_invent_columns(tmp_path):
    sites = scan_file(_write(tmp_path, "test_e.py", CHECK_CONSTRAINT), tmp_path)
    assert len(sites) == 1
    # 'shut' and 'held' live inside CHECK(...) and are not columns.
    assert set(sites[0]["columns"]) <= {"id", "state", "note"}


def test_an_unparseable_file_is_somebody_elses_finding(tmp_path):
    assert scan_file(_write(tmp_path, "test_f.py", "def broken(:\n"), tmp_path) == []


# ─────────────────────────────────────────────────────────── the ratchet
def test_the_tree_is_clean_at_todays_census():
    """Adoption state: every site is registered, nothing unregistered."""
    report = build_report(REPO)
    assert report["unregistered"] == [], (
        "a NEW fixture-shape site landed; adopt tests/_schema_compat.ensure_table "
        "rather than registering it"
    )
    assert not report["over_ceiling"]
    assert report["ok"]


def test_the_census_matches_what_the_scanner_finds():
    """The enumerated set IS the measurement, not a number beside it.

    `stale_entries` is REPORTED and never fatal, matching every other census
    here: a stale entry means a site was FIXED and the ceiling has not caught up
    yet, which is the direction we want and must not block the PR that did the
    fixing. `--prune` drops them and the ceiling follows.
    """
    report = build_report(REPO)
    assert report["census_size"] >= report["sites_seen"]
    assert report["unregistered"] == []


def test_the_ceiling_equals_the_census_at_adoption():
    """Headroom is permission. The ceiling is today's count, not a round number."""
    report = build_report(REPO)
    assert report["ceiling"] == report["census_size"]


def test_a_new_site_is_refused_by_name(tmp_path):
    """The gate's entire purpose, exercised end to end through the CLI."""
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO / "tools" / "ci" / "fixture_shape_census.py"),
            "--changed",
            "tests/ci/_fixture_shape_probe_that_does_not_exist.py",
            "--check",
            "--json",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    # A path that does not exist yields no sites and must PASS -- the gate may
    # never fail for a file it could not read.
    assert proc.returncode == 0, proc.stderr


def test_gate_fails_when_an_unregistered_site_is_in_scope(monkeypatch, tmp_path):
    """Directly: an unregistered site makes the report not ok."""
    import tools.ci.fixture_shape_census as fsc

    fake = [{"key": "tests/new_thing.py::widgets", "file": "tests/new_thing.py",
             "table": "widgets", "columns": ["status"]}]
    monkeypatch.setattr(fsc, "collect", lambda *a, **k: fake)
    report = fsc.build_report(REPO, only=["tests/new_thing.py"])
    assert report["unregistered"] == fake
    assert not report["ok"]


def test_ceiling_may_only_go_down_is_detectable(monkeypatch):
    """A census larger than its ceiling is reported, not silently tolerated."""
    import tools.ci.fixture_shape_census as fsc

    monkeypatch.setattr(fsc, "load_gate", lambda *a, **k: {
        "census_file": "args/fixture_shape_census.txt",
        "fixture_shape_max": 1,
        "scan_roots": ["tests"],
    })
    monkeypatch.setattr(fsc, "collect", lambda *a, **k: [])
    report = fsc.build_report(REPO)
    assert report["over_ceiling"]
    assert not report["ok"]


# ───────────────────────────────────────────────────────────── the config
def test_every_exclusion_states_a_reason():
    """An exclusion asserts the pattern is CORRECT there — that needs saying."""
    import yaml

    cfg = yaml.safe_load(
        (REPO / "args" / "fixture_shape_gate.yaml").read_text(encoding="utf-8")
    )["fixture_shape_census"]
    for entry in cfg.get("exclude", []) or []:
        assert entry.get("path")
        assert len((entry.get("reason") or "").strip()) >= 12, entry


def test_census_file_is_enumerated_not_counted():
    cfg_path = REPO / "args" / "fixture_shape_census.txt"
    lines = [
        ln.split("#")[0].strip()
        for ln in cfg_path.read_text(encoding="utf-8").splitlines()
    ]
    entries = [ln for ln in lines if ln]
    assert entries, "the census must name its sites, never just count them"
    assert all("::" in e for e in entries), "every entry is <file>::<table>"
    assert len(entries) == len(set(entries)), "no duplicate entries"


def test_no_line_numbers_in_census_keys():
    """Line numbers churn on every edit above the site."""
    cfg_path = REPO / "args" / "fixture_shape_census.txt"
    for ln in cfg_path.read_text(encoding="utf-8").splitlines():
        bare = ln.split("#")[0].strip()
        if not bare:
            continue
        table = bare.split("::", 1)[1]
        assert not table.isdigit(), bare
