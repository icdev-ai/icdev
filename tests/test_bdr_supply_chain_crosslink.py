# CUI // SP-CTI
"""Tests for bdr-feat-4 — /boundary <-> /supply_chain navigation cross-links.

Navigation-only feature: the two ISA stores (bd_isa_tracker in the Boundary
Canvas DB, isa_agreements in the project DB) stay separate. These tests cover:

  * find_boundary_isa_matches() — the conservative case-insensitive matcher that
    backs the /supply_chain "related -> Boundary Canvas" link
    (both-match, no-match, missing-table / fail-closed).
  * Jinja render smoke of the boundary ISA-tracker page cross-link column.
  * Flask test-client smoke of the /supply_chain page + ISA API enrichment.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _NoClose:
    """Proxy that delegates to a real sqlite connection but ignores close()."""

    def __init__(self, conn: sqlite3.Connection):
        self._c = conn

    def execute(self, *a, **kw):
        return self._c.execute(*a, **kw)

    def commit(self):
        return self._c.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _bdc_conn_with(rows_isa, rows_designs, *, create_isa_table=True):
    """Build an in-memory Boundary Canvas DB and return a get_connection stub."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE boundary_designs (id TEXT PRIMARY KEY, name TEXT)"
    )
    if create_isa_table:
        conn.execute(
            "CREATE TABLE bd_isa_tracker ("
            "id TEXT PRIMARY KEY, design_id TEXT, interconnection_id TEXT)"
        )
        for (iid, did, ic) in rows_isa:
            conn.execute(
                "INSERT INTO bd_isa_tracker (id, design_id, interconnection_id) "
                "VALUES (?,?,?)",
                (iid, did, ic),
            )
    for (did, name) in rows_designs:
        conn.execute("INSERT INTO boundary_designs (id, name) VALUES (?,?)", (did, name))
    conn.commit()
    return lambda: _NoClose(conn)


# ---------------------------------------------------------------------------
# find_boundary_isa_matches()
# ---------------------------------------------------------------------------

def test_matcher_both_match(monkeypatch):
    """Matches on interconnection_id AND on boundary design name (case-insensitive)."""
    import tools.boundary_canvas.db.init_db as bdc_init
    stub = _bdc_conn_with(
        rows_isa=[("t1", "d1", "IC-Partner-API"), ("t2", "d1", "ic-vpn-01")],
        rows_designs=[("d1", "Prod ATO Design")],
    )
    monkeypatch.setattr(bdc_init, "get_connection", stub)

    from tools.supply_chain.blueprint import find_boundary_isa_matches

    partner_systems = ["ic-partner-api", "Prod ATO Design", "Unrelated System"]
    result = find_boundary_isa_matches(partner_systems)

    assert result.get("ic-partner-api") is True      # interconnection_id (ci)
    assert result.get("Prod ATO Design") is True      # design name (exact ci)
    assert "Unrelated System" not in result


def test_matcher_no_match(monkeypatch):
    """No overlap -> empty dict, never raises."""
    import tools.boundary_canvas.db.init_db as bdc_init
    stub = _bdc_conn_with(
        rows_isa=[("t1", "d1", "ic-001")],
        rows_designs=[("d1", "Design One")],
    )
    monkeypatch.setattr(bdc_init, "get_connection", stub)

    from tools.supply_chain.blueprint import find_boundary_isa_matches

    result = find_boundary_isa_matches(["Splunk Cloud", "Okta IdP"])
    assert result == {}


def test_matcher_missing_table_fails_closed(monkeypatch):
    """Missing bd_isa_tracker table -> {} (fail closed), never 500s the caller."""
    import tools.boundary_canvas.db.init_db as bdc_init
    stub = _bdc_conn_with(
        rows_isa=[],
        rows_designs=[("d1", "Design One")],
        create_isa_table=False,
    )
    monkeypatch.setattr(bdc_init, "get_connection", stub)

    from tools.supply_chain.blueprint import find_boundary_isa_matches

    result = find_boundary_isa_matches(["anything", "Design One"])
    assert result == {}


def test_matcher_empty_input_short_circuits(monkeypatch):
    """Empty / blank partner list returns {} without touching the DB."""
    import tools.boundary_canvas.db.init_db as bdc_init

    def _boom():
        raise AssertionError("DB should not be queried for empty input")

    monkeypatch.setattr(bdc_init, "get_connection", _boom)

    from tools.supply_chain.blueprint import find_boundary_isa_matches

    assert find_boundary_isa_matches([]) == {}
    assert find_boundary_isa_matches(["", "  ", None]) == {}


# ---------------------------------------------------------------------------
# Boundary ISA-tracker page — Jinja render smoke (cross-link column)
# ---------------------------------------------------------------------------

def _render_isa_tracker(isas):
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    tmpl_dir = ROOT / "tools" / "dashboard" / "templates"
    env = Environment(
        loader=ChoiceLoader([
            DictLoader({"base.html": "{% block content %}{% endblock %}"}),
            FileSystemLoader(str(tmpl_dir)),
        ]),
        autoescape=True,
    )
    return env.get_template("boundary_canvas/isa_tracker.html").render(isas=isas)


def test_isa_tracker_renders_supply_chain_link():
    html = _render_isa_tracker([
        {"design_name": "Prod ATO", "interconnection_id": "ic-001",
         "status": "active", "expiry_date": "2027-01-01", "owner": "alice",
         "review_date": "2026-06-01"},
    ])
    assert "View in Supply Chain" in html
    assert "/supply_chain#isa" in html
    assert "ic-001" in html


def test_isa_tracker_empty_renders_no_link():
    html = _render_isa_tracker([])
    assert "No ISA tracker entries" in html
    assert "View in Supply Chain" not in html


# ---------------------------------------------------------------------------
# /supply_chain page — Jinja render smoke (Boundary column + backlink)
# ---------------------------------------------------------------------------

def _render_supply_chain_page():
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    tmpl_dir = ROOT / "tools" / "dashboard" / "templates"
    env = Environment(
        loader=ChoiceLoader([
            DictLoader({
                "base.html": "{% block content %}{% endblock %}",
                "includes/iqe_query_widget.html": "<!-- iqe widget stub -->",
            }),
            FileSystemLoader(str(tmpl_dir)),
        ]),
        autoescape=True,
    )
    return env.get_template("supply_chain.html").render(
        classification_banner="CUI // SP-CTI",
        iqe_canvas="supply_chain",
        iqe_api_route="/api/supply_chain/iqe-query",
        iqe_title="Supply Chain IQE",
        iqe_examples=[],
    )


def test_supply_chain_page_has_boundary_column_and_backlink():
    html = _render_supply_chain_page()
    # New nav column header in the ISA table.
    assert "<th>Boundary</th>" in html
    # Backlink label + target rendered by the ISA loader JS (nav only).
    assert "View in Boundary Canvas" in html
    assert "/boundary/isa-tracker" in html
    # Deep-link handler that opens the ISA tab from #isa.
    assert "_openTabFromHash" in html


# ---------------------------------------------------------------------------
# /api/supply_chain/isa-agreements — minimal-app smoke (enrichment + no 500)
# ---------------------------------------------------------------------------

def test_isa_agreements_api_never_500s_and_flags_boundary(monkeypatch, tmp_path):
    """The ISA API must return 200 with a boundary_match flag on every row,
    and must never 500 even when the boundary DB lookup is unavailable."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))

    from flask import Flask
    from tools.supply_chain.blueprint import create_supply_chain_blueprint

    app = Flask(__name__)
    app.register_blueprint(create_supply_chain_blueprint())
    app.config["TESTING"] = True

    with app.test_client() as c:
        resp = c.get("/api/supply_chain/isa-agreements")
    assert resp.status_code == 200
    rows = resp.get_json()
    assert isinstance(rows, list)
    for r in rows:
        assert "boundary_match" in r
