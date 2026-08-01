# CUI // SP-CTI
"""Route + DB + IQE + grounding tests for Mission Control (cnr-mc-01..03).

Covers the fixes:
  cnr-mc-01 — index/IQE hit the real Mission Canvas DB and real tables.
  cnr-mc-02 — mission.* IQE collections resolve against real tables.
  cnr-mc-03 — index renders real panels (no prompt()/alert()); narrative output
              is grounded via tools.quality.citation_grounding.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def mission_db(tmp_path, monkeypatch):
    """Point the Mission Canvas at a fresh temp SQLite DB, schema initialized."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("MCAN_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "mission_canvas.db"
    monkeypatch.setenv("MCAN_DB_PATH", str(db_path))
    from tools.mission_canvas.db.init_db import init_db
    init_db()
    return str(db_path)


def _seed_design(design_id="m-cnr-1"):
    from tools.mission_canvas.db.init_db import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO mission_designs (id, name, description, design_type, classification) "
            "VALUES (%s,%s,%s,%s,%s)",
            (design_id, "Operation Sentinel", "Test mission", "operational", "CUI"),
        )
        conn.execute(
            "INSERT INTO mission_evidence (id, design_id, evidence_type, title, source, classification) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            ("ev-1", design_id, "document", "Recon report", "sat-feed", "CUI"),
        )
        conn.execute(
            "INSERT INTO mission_security_posture (id, design_id, zta_score, fedramp_status, il_level) "
            "VALUES (%s,%s,%s,%s,%s)",
            ("po-1", design_id, 35.0, "in_process", "IL5"),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# cnr-mc-01 — DB wiring
# ---------------------------------------------------------------------------

def test_get_connection_hits_canvas_tables(mission_db):
    _seed_design()
    from tools.mission_canvas.db.init_db import get_connection
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, name FROM mission_designs").fetchall()
        assert any(dict(r)["id"] == "m-cnr-1" for r in rows)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# cnr-mc-02 — IQE adapter + seed queries
# ---------------------------------------------------------------------------

def test_iqe_collections_registered():
    import tools.iqe.adapters.mission_canvas  # noqa: F401
    from tools.iqe.executor import list_collections
    cols = set(list_collections())
    assert {"mission.sessions", "mission.twins", "mission.evidence", "mission.alerts"} <= cols


def test_iqe_seed_queries_execute(mission_db):
    _seed_design()
    import tools.iqe.adapters.mission_canvas  # noqa: F401
    from tools.iqe.executor import execute_query
    from tools.iqe.parser import parse

    qdir = _ROOT / "context" / "iqe" / "queries" / "mission_canvas"
    files = sorted(qdir.glob("*.iqe"))
    assert len(files) >= 3
    for f in files:
        ast = parse(f.read_text(encoding="utf-8"))
        rows = execute_query(ast, conn=None)
        # Each seeded row should be matched by its query.
        assert isinstance(rows, list) and len(rows) >= 1, f"{f.name} returned no rows"


def test_iqe_sessions_adapter_direct(mission_db):
    _seed_design()
    import tools.iqe.adapters.mission_canvas as adapter
    rows = adapter.sessions_adapter(None)
    assert any(r["id"] == "m-cnr-1" for r in rows)


# ---------------------------------------------------------------------------
# cnr-mc-03 — real UI panels + narrative grounding
# ---------------------------------------------------------------------------

@pytest.fixture
def client(mission_db, icdev_db, monkeypatch):
    """Full dashboard app with the mission_canvas blueprint registered."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    monkeypatch.setenv("ICDEV_MISSION_CANVAS_ENABLED", "true")
    import tools.dashboard.auth as _auth
    monkeypatch.setattr(_auth, "DB_PATH", str(icdev_db), raising=False)

    # Pre-seed the air-gap detector cache so importing the dashboard app does not
    # perform live localhost LLM probes (which hang under a network sandbox and
    # are irrelevant to this route test).
    import tools.airgap.detector as _det
    monkeypatch.setattr(_det, "_cached_result", {"airgap": False, "local_llm_servers": []}, raising=False)

    from tools.dashboard.app import app
    from tools.mission_canvas.blueprint import create_mission_canvas_blueprint

    if "mission_canvas" not in app.blueprints:
        bp = create_mission_canvas_blueprint()
        if bp is not None:
            app.register_blueprint(bp, url_prefix="/mission-canvas")

    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        yield c


def test_index_renders_missions_no_popups(client):
    _seed_design()
    resp = client.get("/mission-canvas/")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", "replace")
    # Real canvas data surfaced.
    assert "Operation Sentinel" in body
    # Demo-grade UI removed: the specific prompt()/alert() capability driver is
    # gone. base.html chrome may use prompt() elsewhere, so assert the exact demo
    # patterns rather than bare substrings.
    assert 'prompt("Enter mission ID' not in body
    assert "alert(JSON.stringify" not in body
    # Result panel + IQE widget present.
    assert 'id="mc-result"' in body
    assert "/mission-canvas/api/iqe-query" in body
    # Dead 'drift' capability removed.
    assert "runApi('drift')" not in body


def test_narrative_output_is_grounded(mission_db, monkeypatch):
    """The narrative wrapper attaches a citation-grounding report and flags a
    hallucinated citation (a source id outside the provided evidence)."""
    import tools.studio.wne.narrative_generator as ng

    class _StubNarrative:
        executive_summary = "Threat posture is degrading [source: 9]."

    class _StubGen:
        def generate(self, ctx=None):
            return _StubNarrative()

    monkeypatch.setattr(ng, "NarrativeGenerator", _StubGen)

    from tools.mission_canvas.narrative import generate_narrative
    result = generate_narrative(
        mission_id="m-cnr-1",
        topic="Status",
        sources=[{"id": "1", "text": "evidence a"}],  # only source '1' available
    )
    assert result["status"] == "ok"
    assert "grounding" in result
    grounding = result["grounding"]
    # [source: 9] references an unavailable source -> hallucinated, not grounded.
    assert grounding["grounded"] is False
    assert grounding["report"]["hallucinated_citations"] == ["9"]
