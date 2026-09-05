# CUI // SP-CTI
"""rmf-rfp-01 -- the RFP shredder is wired, and there is ONE compliance matrix.

What is pinned here, and the defect each pin was written against:

* An RFP upload seeds workbench sections from SECTION L (one per L.x
  instruction), never from the RFI questionnaire defaults. solicitation_parser
  had no route and no UI; its output reached nothing but response_drafter.
* POST /rfp/upload exists, mirrors /rfi/upload, and builds the L/M matrix for
  an opportunity when handed one -- through compliance_matrix_builder, which
  had zero callers.
* POST /api/proposals/opportunities/<id>/compliance/batch accepts the parsed
  solicitation and populates the matrix: the matrix is populated by a ROUTE,
  not a CLI.
* proposal_compliance_matrix is the ONE matrix. pg_compliance_matrix (0 rows on
  the live board; its only writer had no callers) is folded in and dropped by
  migration 20260903185253, and no runtime SQL names it any more. Two matrices
  is how a coverage number silently describes a subset.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[2]

from tools.db.storage import get_connection  # noqa: E402
from tools.govcon.compliance_matrix_schema import (  # noqa: E402
    ADDRESSED_STATUSES,
    COMPLIANCE_STATUSES,
    REQUIREMENT_TYPES,
    sql_in_list,
)

# ---------------------------------------------------------------------------
# Fixtures: a solicitation_parser-shaped parse, and the two SQLite schemas
# ---------------------------------------------------------------------------

_OPP_ID = "opp-rfp-01"

_PARSED_RFP = {
    "source": "solicitation_document",
    "solicitation_number": "W912DY-26-R-0007",
    "title": "Enterprise DevSecOps Platform Modernization",
    "document_sections": [{"letter": "L", "title": "Instructions"}, {"letter": "M", "title": "Evaluation"}],
    "section_l_instructions": [
        {"number": "L.1", "title": "General Instructions", "text": "Offerors shall submit three volumes."},
        {"number": "L.4.1", "title": "Technical Volume", "text": "Volume I shall describe the technical approach."},
        {"number": "L.4.2", "title": "Past Performance Volume", "text": "Volume II shall list three references."},
    ],
    "volume_structure": [
        {"volume": "I", "title": "Technical"},
        {"volume": "II", "title": "Past Performance"},
    ],
    "section_m_factors": [
        {
            "factor": "1", "name": "Technical Approach", "weight_pct": 60,
            "subfactors": [{"number": "1.1", "name": "Architecture"}],
        },
        {"factor": "2", "name": "Price", "weight_pct": 40, "subfactors": []},
    ],
    "basis_of_award": "best_value_tradeoff",
    "relative_importance": "Factor 1 is significantly more important than Factor 2",
    "clins": [],
    "submission_requirements": {"max_pages": 30},
}

_PARSED_RFI = {
    "rfi_number": "RFI-2026-01",
    "title": "Orchestration RFI",
    "objectives": [{"letter": "A", "title": "Scale"}],
    "questionnaire_parts": [
        {"part": "Part 1", "item_number": "1.1", "topic": "Entity Data", "question": "Who are you?"},
        {"part": "Part 2", "item_number": "2.1", "topic": "TRL", "question": "What TRL?"},
    ],
}

_MATRIX_DDL = f"""
CREATE TABLE proposal_opportunities (
    id TEXT PRIMARY KEY, solicitation_number TEXT NOT NULL, title TEXT NOT NULL,
    agency TEXT, due_date TEXT, proposal_type TEXT, status TEXT DEFAULT 'intake',
    rfp_document_path TEXT, updated_at TEXT
);
CREATE TABLE proposal_sections (id TEXT PRIMARY KEY, opportunity_id TEXT, section_number TEXT, title TEXT, status TEXT);
CREATE TABLE proposal_volumes (id TEXT PRIMARY KEY, opportunity_id TEXT, volume_type TEXT, title TEXT);
CREATE TABLE proposal_compliance_matrix (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    section_ref TEXT NOT NULL,
    volume_ref TEXT,
    requirement_text TEXT NOT NULL,
    requirement_type TEXT DEFAULT 'L' CHECK(requirement_type IN ({sql_in_list(REQUIREMENT_TYPES)})),
    compliance_status TEXT DEFAULT 'not_addressed' CHECK(compliance_status IN ({sql_in_list(COMPLIANCE_STATUSES)})),
    proposal_section_id TEXT,
    response_summary TEXT,
    notes TEXT,
    sort_order INTEGER DEFAULT 0,
    evaluation_factor TEXT,
    evaluation_weight REAL,
    amendment_version INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    tenant_id TEXT
);
INSERT INTO proposal_opportunities (id, solicitation_number, title, agency, due_date, proposal_type, status)
VALUES ('{_OPP_ID}', 'W912DY-26-R-0007', 'Enterprise DevSecOps', 'USACE', '2026-12-31', 'FFP', 'intake');
"""

_RFI_DDL = """
CREATE TABLE rfi_workbench_sessions (
    id TEXT PRIMARY KEY, rfi_number TEXT, rfi_title TEXT, profile_name TEXT DEFAULT 'own_company',
    upload_filename TEXT, parsed_data TEXT, status TEXT NOT NULL DEFAULT 'draft',
    total_sections INTEGER DEFAULT 0, approved_sections INTEGER DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE rfi_workbench_sections (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, part TEXT, item_number TEXT, title TEXT, topic TEXT,
    question_text TEXT, content TEXT, ai_draft TEXT, status TEXT, hitl_comment TEXT,
    generation_count INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT
);
"""


def _sqlite(path: Path, ddl: str) -> Path:
    """Build a scratch SQLite database through the storage layer.

    Through get_connection(db_path=...) rather than a raw sqlite3 handle, so
    every connection in this module is a StorageConnection and the %s SQL the
    runtime modules issue is translated the same way it is in production.
    """
    conn = get_connection(db_path=str(path))
    try:
        for stmt in ddl.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture()
def matrix_db(tmp_path):
    return _sqlite(tmp_path / "matrix.db", _MATRIX_DDL)


@pytest.fixture()
def rfi_db(tmp_path):
    return _sqlite(tmp_path / "rfi.db", _RFI_DDL)


@pytest.fixture()
def builder(matrix_db, monkeypatch):
    """compliance_matrix_builder bound to the scratch matrix db, never data/icdev.db."""
    import tools.govcon.compliance_matrix_builder as cmb

    monkeypatch.setattr(cmb, "_get_db", lambda: get_connection(db_path=str(matrix_db)))
    return cmb


@pytest.fixture()
def workbench(rfi_db, monkeypatch):
    """rfi_workbench bound to the scratch rfi db, with its background threads off."""
    import tools.govcon.rfi_workbench as wb

    monkeypatch.setattr(wb, "get_db", lambda: get_connection(db_path=str(rfi_db)))
    monkeypatch.setattr(wb, "_seed_requirements_background", lambda sid: None)
    monkeypatch.setattr(wb, "_launch_ace_team_background", lambda sid: None)
    return wb


def _rows(db_path: Path, sql: str, params=()):
    conn = get_connection(db_path=str(db_path))
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. An RFP upload seeds sections from Section L
# ---------------------------------------------------------------------------


class TestSectionLSeeding:
    def test_rfp_parse_seeds_one_section_per_l_instruction(self, workbench, rfi_db):
        sid = workbench.create_session("W912DY-26-R-0007", "Test RFP", "own_company", "rfp.pdf", _PARSED_RFP)
        rows = _rows(rfi_db, "SELECT part, item_number, title, topic, question_text FROM rfi_workbench_sections WHERE session_id = %s ORDER BY item_number", (sid,))

        assert [r["item_number"] for r in rows] == ["L.1", "L.4.1", "L.4.2"]
        # No RFI questionnaire default, no Part 6, no appendix leaked in.
        assert not any(r["part"].startswith("part") for r in rows)
        assert not any(r["part"] == "appendix" for r in rows)
        # An instruction that names a parsed volume is grouped under it.
        by_num = {r["item_number"]: r for r in rows}
        assert by_num["L.4.1"]["part"] == "volume_i"
        assert by_num["L.4.1"]["topic"].startswith("Volume I")
        assert by_num["L.4.2"]["part"] == "volume_ii"
        assert by_num["L.1"]["part"] == "section_l"
        assert by_num["L.1"]["question_text"] == "Offerors shall submit three volumes."

        sess = _rows(rfi_db, "SELECT total_sections FROM rfi_workbench_sessions WHERE id = %s", (sid,))[0]
        assert sess["total_sections"] == 3

    def test_rfp_parse_without_l_items_falls_back_to_volumes_then_nothing(self, workbench, rfi_db):
        only_volumes = dict(_PARSED_RFP, section_l_instructions=[])
        sid = workbench.create_session("X", "t", "own_company", "a.pdf", only_volumes)
        rows = _rows(rfi_db, "SELECT item_number, part FROM rfi_workbench_sections WHERE session_id = %s ORDER BY item_number", (sid,))
        assert [r["item_number"] for r in rows] == ["I", "II"]

        nothing = dict(_PARSED_RFP, section_l_instructions=[], volume_structure=[])
        sid2 = workbench.create_session("RFP-UNKNOWN", "t", "own_company", "b.pdf", nothing)
        rows2 = _rows(rfi_db, "SELECT id FROM rfi_workbench_sections WHERE session_id = %s", (sid2,))
        # Seeding the RFI questionnaire here would fabricate what the RFP asks for.
        assert rows2 == []
        summary = workbench.get_parse_summary(workbench.get_session(sid2))
        assert summary["document_kind"] == "rfp"
        assert summary["parse_fallback"] is True

    def test_rfi_parse_still_seeds_questionnaire_parts(self, workbench, rfi_db):
        sid = workbench.create_session("RFI-2026-01", "RFI", "own_company", "rfi.pdf", _PARSED_RFI)
        rows = _rows(rfi_db, "SELECT part, item_number FROM rfi_workbench_sections WHERE session_id = %s", (sid,))
        parts = {r["part"] for r in rows}
        assert {"part1", "part2", "part6", "appendix"} <= parts
        assert not any(r["item_number"].startswith("L.") for r in rows)

    def test_parse_summary_names_the_document_kind(self, workbench):
        sid = workbench.create_session("W912DY-26-R-0007", "RFP", "own_company", "rfp.pdf", _PARSED_RFP)
        summary = workbench.get_parse_summary(workbench.get_session(sid))
        assert summary["document_kind"] == "rfp"
        assert summary["parse_fallback"] is False
        assert summary["section_l_instructions_count"] == 3
        assert summary["section_m_factors_count"] == 2
        assert summary["volumes_count"] == 2

        sid2 = workbench.create_session("RFI-2026-01", "RFI", "own_company", "rfi.pdf", _PARSED_RFI)
        summary2 = workbench.get_parse_summary(workbench.get_session(sid2))
        assert summary2["document_kind"] == "rfi"
        assert summary2["questionnaire_parts_count"] == 2


# ---------------------------------------------------------------------------
# 2. POST /rfp/upload -- the route that did not exist
# ---------------------------------------------------------------------------


@pytest.fixture()
def rfp_client(workbench, tmp_path, monkeypatch):
    from flask import Flask

    import tools.govcon.rfi_canvas_blueprint as bp_mod
    import tools.govcon.solicitation_parser as sp

    monkeypatch.setattr(bp_mod, "_UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(sp, "parse_solicitation", lambda path: dict(_PARSED_RFP))

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp_mod.rfi_canvas_bp)
    return app.test_client()


class TestRfpUploadRoute:
    def test_upload_seeds_from_section_l_and_builds_matrix_for_an_opportunity(self, rfp_client, rfi_db):
        import tools.govcon.compliance_matrix_builder as cmb

        fake_matrix = {"status": "ok", "opportunity_id": _OPP_ID, "created": 8, "duplicates": 0, "total_in_matrix": 8}
        with patch.object(cmb, "ingest_solicitation", MagicMock(return_value=fake_matrix)) as ingest:
            resp = rfp_client.post(
                "/rfp/upload",
                data={
                    "rfp_file": (io.BytesIO(b"%PDF-1.4 fake"), "solicitation.pdf"),
                    "profile": "own_company",
                    "opportunity_id": _OPP_ID,
                },
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200, resp.get_json()
        body = resp.get_json()
        assert body["document_kind"] == "rfp"
        assert body["redirect"] == f"/rfi/{body['session_id']}"
        assert body["parse_summary"]["section_l_instructions_count"] == 3
        assert body["matrix"] == fake_matrix
        ingest.assert_called_once()
        assert ingest.call_args.args[1] == _OPP_ID
        assert ingest.call_args.kwargs["parsed"]["solicitation_number"] == "W912DY-26-R-0007"

        rows = _rows(rfi_db, "SELECT item_number FROM rfi_workbench_sections WHERE session_id = %s ORDER BY item_number", (body["session_id"],))
        assert [r["item_number"] for r in rows] == ["L.1", "L.4.1", "L.4.2"]

    def test_upload_without_opportunity_creates_session_only(self, rfp_client):
        import tools.govcon.compliance_matrix_builder as cmb

        with patch.object(cmb, "ingest_solicitation", MagicMock()) as ingest:
            resp = rfp_client.post(
                "/rfp/upload",
                data={"rfp_file": (io.BytesIO(b"%PDF-1.4 fake"), "solicitation.pdf")},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200
        assert resp.get_json()["matrix"] is None
        assert resp.get_json()["opportunity_id"] is None
        ingest.assert_not_called()

    def test_upload_matrix_failure_is_reported_not_hidden(self, rfp_client):
        import tools.govcon.compliance_matrix_builder as cmb

        with patch.object(cmb, "ingest_solicitation", MagicMock(side_effect=RuntimeError("no such opportunity"))):
            resp = rfp_client.post(
                "/rfp/upload",
                data={"rfp_file": (io.BytesIO(b"x"), "s.docx"), "opportunity_id": "opp-missing"},
                content_type="multipart/form-data",
            )
        assert resp.status_code == 200
        matrix = resp.get_json()["matrix"]
        assert matrix["status"] == "error"
        assert "no such opportunity" in matrix["error"]

    def test_upload_refuses_the_wrong_file_type(self, rfp_client):
        resp = rfp_client.post(
            "/rfp/upload",
            data={"rfp_file": (io.BytesIO(b"x"), "notes.txt")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400
        resp2 = rfp_client.post("/rfp/upload", data={}, content_type="multipart/form-data")
        assert resp2.status_code == 400


# ---------------------------------------------------------------------------
# 3. The builder writes THE matrix, and the readers agree on its vocabulary
# ---------------------------------------------------------------------------


class TestBuilderWritesTheOneMatrix:
    def test_build_from_parsed_stores_l_and_m_rows(self, builder, matrix_db):
        result = builder.build_from_parsed(_OPP_ID, _PARSED_RFP)
        assert result["status"] == "ok"
        assert result["extracted"] == {"L": 3, "M": 5, "C": 0}
        assert result["created"] == 8
        assert result["duplicates"] == 0
        assert result["total_in_matrix"] == 8

        rows = _rows(matrix_db, "SELECT * FROM proposal_compliance_matrix WHERE opportunity_id = %s ORDER BY sort_order", (_OPP_ID,))
        assert len(rows) == 8
        assert all(r["requirement_type"] in REQUIREMENT_TYPES for r in rows)
        assert all(r["compliance_status"] == "not_addressed" for r in rows)

        l_rows = [r for r in rows if r["requirement_type"] == "L"]
        assert [r["section_ref"] for r in l_rows] == ["L.1", "L.4.1", "L.4.2"]
        assert l_rows[1]["volume_ref"] and l_rows[1]["volume_ref"].startswith("Volume I")
        assert l_rows[0]["volume_ref"] is None

        m_rows = [r for r in rows if r["requirement_type"] == "M"]
        factor1 = next(r for r in m_rows if r["section_ref"] == "M Factor 1")
        assert factor1["evaluation_factor"] == "Technical Approach"
        assert factor1["evaluation_weight"] == 60.0
        assert any(r["section_ref"] == "M Subfactor 1.1" for r in m_rows)
        assert any(r["evaluation_factor"] == "basis_of_award" for r in m_rows)
        assert any(r["evaluation_factor"] == "relative_importance" for r in m_rows)

    def test_build_from_parsed_is_idempotent(self, builder):
        first = builder.build_from_parsed(_OPP_ID, _PARSED_RFP)
        second = builder.build_from_parsed(_OPP_ID, _PARSED_RFP)
        assert second["created"] == 0
        assert second["duplicates"] == first["created"]
        assert second["total_in_matrix"] == first["total_in_matrix"]

    def test_section_text_runs_the_regex_extractors(self, builder, matrix_db):
        result = builder.build_from_parsed(
            _OPP_ID,
            None,
            {"C": "C.3.1 Reporting. The contractor shall deliver a monthly status report to the COR."},
        )
        assert result["extracted"]["C"] >= 1
        rows = _rows(matrix_db, "SELECT requirement_type, section_ref FROM proposal_compliance_matrix WHERE opportunity_id = %s", (_OPP_ID,))
        assert rows and all(r["requirement_type"] == "C" for r in rows)

    def test_coverage_and_gate_read_the_proposal_vocabulary(self, builder, matrix_db):
        builder.build_from_parsed(_OPP_ID, _PARSED_RFP)
        gate = builder.evaluate_gate(_OPP_ID)
        assert gate["gate_result"] == "fail"
        assert gate["section_m_gaps"] == 5

        conn = get_connection(db_path=str(matrix_db))
        conn.execute("UPDATE proposal_compliance_matrix SET compliance_status = 'compliant' WHERE requirement_type = 'M'")
        conn.execute("UPDATE proposal_compliance_matrix SET compliance_status = 'partial' WHERE section_ref = 'M Factor 2'")
        conn.commit()
        conn.close()

        gate = builder.evaluate_gate(_OPP_ID)
        assert gate["gate_result"] == "warn"
        cov = builder.get_coverage(_OPP_ID)
        assert cov["by_section"]["M"]["addressed"] == 5
        assert cov["by_section"]["L"]["addressed"] == 0
        assert cov["addressed"] == 5
        assert set(ADDRESSED_STATUSES) == {"compliant", "partial"}

        matrix = builder.build_matrix(_OPP_ID)
        assert matrix["by_section"]["M"] == 5 and matrix["by_section"]["L"] == 3
        assert matrix["by_status"]["compliant"] == 4 and matrix["by_status"]["partial"] == 1

    def test_lifecycle_gate_reads_the_one_matrix(self, builder, matrix_db):
        from tools.govcon.opportunity_lifecycle import _check_map_to_draft

        conn = get_connection(db_path=str(matrix_db))
        ok, reason = _check_map_to_draft(conn, _OPP_ID)
        assert ok is False and "No compliance matrix" in reason

        builder.build_from_parsed(_OPP_ID, _PARSED_RFP)
        ok, reason = _check_map_to_draft(conn, _OPP_ID)
        assert ok is False and "0%" in reason

        conn.execute("UPDATE proposal_compliance_matrix SET compliance_status = 'compliant'")
        conn.commit()
        ok, reason = _check_map_to_draft(conn, _OPP_ID)
        assert ok is True
        conn.close()


class TestReadersAgreeOnTheOneMatrix:
    """The readers that computed coverage over the EMPTY table now read this one."""

    def test_color_review_critic_finds_section_m_gaps_in_the_one_matrix(self, builder, matrix_db, monkeypatch):
        import tools.govcon.color_review_simulator as crs

        monkeypatch.setattr(crs, "_get_db", lambda: get_connection(db_path=str(matrix_db)))
        builder.build_from_parsed(_OPP_ID, _PARSED_RFP)

        findings = crs._run_compliance_critic(_OPP_ID, [], "pink")
        criticals = [f for f in findings if f["severity"] == "critical" and f["category"] == "compliance"]
        assert len(criticals) == 5  # every Section M row is still not_addressed
        assert any("M Factor 1" in f["recommendation"] for f in criticals)
        assert not any("not populated" in f["finding_text"] for f in findings)

        conn = get_connection(db_path=str(matrix_db))
        conn.execute("UPDATE proposal_compliance_matrix SET compliance_status = 'compliant' WHERE requirement_type = 'M'")
        conn.execute("UPDATE proposal_compliance_matrix SET compliance_status = 'partial' WHERE section_ref = 'M Factor 2'")
        conn.commit()
        conn.close()
        findings = crs._run_compliance_critic(_OPP_ID, [], "pink")
        assert [f["severity"] for f in findings if f["category"] == "compliance"] == ["major", "observation"]

    def test_program_bridge_gathers_section_c_rows_from_the_one_matrix(self, builder, matrix_db):
        from tools.govcon.program_bridge import _gather_cdrls

        conn = get_connection(db_path=str(matrix_db))
        assert _gather_cdrls(conn, _OPP_ID) == {"data": [], "record_count": 0}

        builder.build_from_parsed(
            _OPP_ID, _PARSED_RFP,
            {"C": "C.5.2 Deliverables. The contractor shall submit a monthly status report. The contractor shall maintain a risk register."},
        )
        out = _gather_cdrls(conn, _OPP_ID)
        conn.close()
        assert out["record_count"] >= 2
        assert all(r["requirement_type"] == "C" for r in out["data"])
        assert all(r["section_ref"].startswith("C") for r in out["data"])


# ---------------------------------------------------------------------------
# 4. The matrix is populated by a ROUTE
# ---------------------------------------------------------------------------


@pytest.fixture()
def proposals_client(matrix_db, monkeypatch):
    from flask import Flask, g

    import tools.dashboard.api.proposals as papi

    monkeypatch.setattr(papi, "DB_PATH", matrix_db)
    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.before_request
    def _auth():
        g.current_user = {"username": "t", "role": "admin", "email": "t@test.mil", "classification": "CUI"}

    app.register_blueprint(papi.proposals_api)
    return app.test_client()


class TestBatchRoutePopulatesTheMatrix:
    def test_parsed_payload_populates_the_matrix(self, proposals_client, matrix_db):
        resp = proposals_client.post(
            f"/api/proposals/opportunities/{_OPP_ID}/compliance/batch",
            json={"parsed": _PARSED_RFP, "section_text": {"C": "The contractor shall deliver a transition plan."}},
        )
        assert resp.status_code == 201, resp.get_json()
        body = resp.get_json()
        assert body["source"] == "solicitation"
        assert body["created"] == body["total_in_matrix"] >= 9
        assert body["extracted"]["L"] == 3 and body["extracted"]["M"] == 5 and body["extracted"]["C"] >= 1

        listed = proposals_client.get(f"/api/proposals/opportunities/{_OPP_ID}/compliance").get_json()
        assert listed["stats"]["total"] == body["total_in_matrix"]
        assert listed["stats"]["not_addressed"] == body["total_in_matrix"]
        assert {i["requirement_type"] for i in listed["items"]} == {"L", "M", "C"}

    def test_items_payload_still_works(self, proposals_client):
        resp = proposals_client.post(
            f"/api/proposals/opportunities/{_OPP_ID}/compliance/batch",
            json={"items": [
                {"section_ref": "L.2", "requirement_text": "Submit on time."},
                {"section_ref": "M.1", "requirement_text": "Technical merit.", "requirement_type": "M"},
            ]},
        )
        assert resp.status_code == 201
        assert resp.get_json()["created"] == 2

    def test_bad_payloads_are_refused(self, proposals_client):
        assert proposals_client.post(f"/api/proposals/opportunities/{_OPP_ID}/compliance/batch", json={}).status_code == 400
        assert proposals_client.post(
            f"/api/proposals/opportunities/{_OPP_ID}/compliance/batch", json={"parsed": "not-a-dict"}
        ).status_code == 400


# ---------------------------------------------------------------------------
# 5. Exactly one matrix: the migration folds, the tree stops naming the other
# ---------------------------------------------------------------------------

_OLD_PROPOSAL_DDL = """
CREATE TABLE proposal_compliance_matrix (
    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, section_ref TEXT NOT NULL, volume_ref TEXT,
    requirement_text TEXT NOT NULL, requirement_type TEXT DEFAULT 'L',
    compliance_status TEXT DEFAULT 'not_addressed', proposal_section_id TEXT, response_summary TEXT,
    notes TEXT, sort_order INTEGER DEFAULT 0, classification TEXT DEFAULT 'CUI',
    created_at TEXT, updated_at TEXT, tenant_id TEXT
);
CREATE TABLE pg_compliance_matrix (
    id TEXT PRIMARY KEY, opportunity_id TEXT NOT NULL, requirement_id TEXT NOT NULL,
    requirement_text TEXT NOT NULL, source_section TEXT NOT NULL, evaluation_factor TEXT,
    evaluation_weight REAL, assigned_volume TEXT, assigned_section TEXT,
    compliance_status TEXT DEFAULT 'gap', amendment_version INTEGER DEFAULT 0, notes TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, tenant_id TEXT, classification TEXT DEFAULT 'CUI'
);
INSERT INTO proposal_compliance_matrix (id, opportunity_id, section_ref, requirement_text)
VALUES ('p1', 'opp-a', 'L', 'Already here');
INSERT INTO pg_compliance_matrix (id, opportunity_id, requirement_id, requirement_text, source_section,
    evaluation_factor, evaluation_weight, assigned_volume, compliance_status, created_at, updated_at)
VALUES ('c1', 'opp-a', 'req-1', 'Evaluation factor 1', 'M', 'Technical', 60, 'technical', 'addressed', 't0', 't0'),
       ('c2', 'opp-a', 'req-2', 'Contractor shall report monthly', 'C', 'deliverable', NULL, NULL, 'gap', 't0', 't0'),
       ('c3', 'opp-a', 'req-3', 'Already here', 'L', NULL, NULL, NULL, 'na', 't0', 't0');
"""


def _load_migration(name: str):
    import importlib.util

    path = REPO / "tools" / "db" / "migrations" / "20260903185253_rfp_one_compliance_matrix" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"rmf_rfp_01_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestOneMatrix:
    def test_migration_folds_legacy_rows_and_drops_the_table(self, tmp_path):
        from tools.db.storage import table_exists

        db = _sqlite(tmp_path / "fold.db", _OLD_PROPOSAL_DDL)
        up = _load_migration("up")
        conn = get_connection(db_path=str(db))
        result = up.up(conn)

        assert result["status"] == "applied"
        assert set(result["columns_added"]) == {"evaluation_factor", "evaluation_weight", "amendment_version"}
        assert result["copy"] == {"legacy_rows": 3, "copied": 2, "skipped_duplicate": 1}
        assert result["dropped"] == "pg_compliance_matrix"
        assert not table_exists(conn, "pg_compliance_matrix")

        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM proposal_compliance_matrix").fetchall()}
        assert set(rows) == {"p1", "c1", "c2"}
        assert rows["c1"]["requirement_type"] == "M"
        assert rows["c1"]["compliance_status"] == "compliant"
        assert rows["c1"]["evaluation_weight"] == 60.0
        assert rows["c1"]["volume_ref"] == "technical"
        assert rows["c2"]["requirement_type"] == "C"
        assert rows["c2"]["compliance_status"] == "not_addressed"

        # Idempotent: a second run has nothing to fold and does not raise.
        again = up.up(conn)
        assert again["columns_added"] == [] and again["dropped"] is None
        conn.close()

    def test_rollback_recreates_only_an_empty_shell(self, tmp_path):
        from tools.db.storage import table_exists

        db = _sqlite(tmp_path / "roll.db", _OLD_PROPOSAL_DDL)
        up, down = _load_migration("up"), _load_migration("down")
        conn = get_connection(db_path=str(db))
        up.up(conn)
        out = down.down(conn)
        assert out["status"] == "recreated_empty"
        assert table_exists(conn, "pg_compliance_matrix")
        assert conn.execute("SELECT COUNT(*) AS c FROM pg_compliance_matrix").fetchone()["c"] == 0
        # The fold is kept: rows and columns stay on the surviving table.
        assert conn.execute("SELECT COUNT(*) AS c FROM proposal_compliance_matrix").fetchone()["c"] == 3
        conn.close()

    def test_no_runtime_sql_names_the_dropped_table(self):
        """The 'exactly one matrix' invariant, read from the tree.

        SQL that FROM/INTO/UPDATE/JOIN/CREATEs pg_compliance_matrix anywhere
        under tools/ or icdev/tools/ -- outside the migrations that fold it
        and the PG schema snapshot -- is a second matrix coming back. Prose
        that explains the removal is allowed; SQL is not.
        """
        sql_ref = re.compile(
            r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+(?:public\.)?pg_compliance_matrix\b",
            re.IGNORECASE,
        )
        offenders = []
        for base in (REPO / "tools", REPO / "icdev" / "tools"):
            if not base.exists():
                continue
            for path in base.rglob("*.py"):
                rel = path.relative_to(REPO).as_posix()
                if "/migrations/" in rel or "/schema/" in rel:
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if sql_ref.search(text):
                    offenders.append(rel)
        assert offenders == [], f"runtime SQL still names pg_compliance_matrix: {offenders}"

    def test_fresh_schema_declares_one_matrix_with_the_shared_vocabulary(self):
        from tools.db.init_icdev_db import SCHEMA_SQL

        assert "CREATE TABLE IF NOT EXISTS pg_compliance_matrix" not in SCHEMA_SQL
        assert SCHEMA_SQL.count("CREATE TABLE IF NOT EXISTS proposal_compliance_matrix") == 1
        assert "@@CMX_" not in SCHEMA_SQL
        block = SCHEMA_SQL.split("CREATE TABLE IF NOT EXISTS proposal_compliance_matrix", 1)[1].split(");", 1)[0]
        for t in REQUIREMENT_TYPES:
            assert f"'{t}'" in block
        for col, _ddl in (("evaluation_factor", ""), ("evaluation_weight", ""), ("amendment_version", "")):
            assert col in block

    def test_migration_vocabulary_derives_from_the_shared_constants(self):
        """Fold and DDL cannot drift: the migration imports the tuples, never spells them."""
        up_src = (REPO / "tools" / "db" / "migrations" / "20260903185253_rfp_one_compliance_matrix" / "up.py").read_text(encoding="utf-8")
        assert "compliance_matrix_schema import" in up_src
        assert "'not_addressed', 'non_compliant'" not in up_src  # no hand-spelled CHECK list
