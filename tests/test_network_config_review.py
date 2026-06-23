# CUI // SP-CTI
"""Tests for the Network Configuration Review Assistant.

Covers:
  - tools.network.config_review pure functions
  - tools.network.constants role/question wiring
  - tools.iqe.adapters.ndc new collections (config_reviews, config_review_findings)
  - tools.network.blueprint config-review routes (with mocked LLM)
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest


# ── config_review unit tests ────────────────────────────────────────────


def test_get_roles_returns_all_roles():
    from tools.network.config_review import get_roles

    roles = get_roles()
    assert "network_engineer" in roles
    assert "security_auditor" in roles
    assert "technical_writer" in roles
    assert roles["network_engineer"]["label"] == "Network Engineer"


def test_get_questions_returns_role_specific_questions():
    from tools.network.config_review import get_questions

    sec = get_questions("security_auditor")
    assert any("STIG" in q["question"] for q in sec)
    arch = get_questions("network_architect")
    assert any("naming" in q["question"].lower() for q in arch)


def test_generate_guided_prompts_returns_prompts_for_role():
    from tools.network.config_review import generate_guided_prompts

    prompts = generate_guided_prompts("network_architect", "cisco_ios")
    assert len(prompts) >= 5
    for p in prompts:
        assert "title" in p
        assert "preview" in p
        assert "prompt" in p


def test_build_llm_prompt_includes_role_and_answers():
    from tools.network.config_review import build_llm_prompt

    cfg = "hostname rtr-01\ninterface Gig0/0\n ip address 10.0.0.1 255.255.255.0\n"
    answers = {"strict_acls": "no", "redundancy": "yes"}
    prompt = build_llm_prompt("network_engineer", cfg, "cisco_ios", answers, hostname="rtr-01")
    assert "Network Engineer" in prompt
    assert "rtr-01" in prompt
    assert "strict_acls" in prompt or "strict" in prompt
    assert "JSON" in prompt


def test_parse_review_response_extracts_json():
    from tools.network.config_review import parse_review_response

    raw = json.dumps({
        "security_compliance": [{"title": "T", "severity": "CAT2", "detail": "D", "remediation": "R", "sample_config_snippet": "S"}],
        "optimization": [{"title": "O", "detail": "D", "recommendation": "R"}],
        "remediation": [{"title": "M", "priority": "high", "steps": ["s1"], "verification": "v"}],
        "sample_template": "template text",
        "explanation": "explanation text",
        "topology_graph": {"nodes": [{"id": "n1", "label": "R1", "type": "router", "x": 0, "y": 0}], "edges": []},
    })
    result = parse_review_response(raw, "cisco_ios")
    assert len(result["security_compliance"]) == 1
    assert result["sample_template"] == "template text"
    assert len(result["topology_graph"]["nodes"]) == 1


def test_parse_review_response_falls_back_on_invalid_json():
    from tools.network.config_review import parse_review_response

    result = parse_review_response("not json", "cisco_ios")
    assert result["security_compliance"]
    assert "fallback" in result["explanation"].lower() or "unavailable" in result["explanation"].lower()
    assert result["sample_template"]


def test_compute_config_hash_is_stable():
    from tools.network.config_review import compute_config_hash

    cfg = "hostname rtr\n"
    assert compute_config_hash(cfg) == compute_config_hash(cfg)
    assert compute_config_hash(cfg) != compute_config_hash(cfg + "!")


# ── IQE adapter tests ───────────────────────────────────────────────────


@pytest.fixture()
def ndc_schema() -> str:
    return """
    CREATE TABLE topologies (id TEXT PRIMARY KEY, name TEXT);
    CREATE TABLE nc_config_reviews (
        id TEXT PRIMARY KEY, title TEXT, vendor TEXT, role_key TEXT,
        answers_json TEXT, config_text_hash TEXT, status TEXT, result_json TEXT,
        created_at TEXT, updated_at TEXT
    );
    CREATE TABLE nc_config_review_findings (
        id TEXT PRIMARY KEY, review_id TEXT, category TEXT, severity TEXT,
        title TEXT, detail TEXT, remediation TEXT, sample_config_snippet TEXT,
        references_json TEXT, created_at TEXT
    );
    """


@pytest.fixture()
def ndc_adapter_db(ndc_schema) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(ndc_schema)
    conn.execute("INSERT INTO topologies VALUES ('t1','Alpha')")
    conn.execute(
        "INSERT INTO nc_config_reviews VALUES ('r1','Review','cisco_ios','network_engineer','{}','hash','complete','{}','now','now')"
    )
    conn.execute(
        "INSERT INTO nc_config_review_findings VALUES ('f1','r1','security_compliance','CAT2','Weak SNMP','detail','remediation','snmp snippet','[]','now')"
    )
    conn.commit()
    return conn


def test_adapter_lists_config_review_collections():
    from tools.iqe.adapters.ndc import NDCAdapter

    cols = NDCAdapter().list_collections()
    assert "network.config_reviews" in cols
    assert "network.config_review_findings" in cols


def test_adapter_query_config_reviews(ndc_adapter_db):
    from tools.iqe.adapters.ndc import NDCAdapter

    rows = NDCAdapter()._query(ndc_adapter_db, "network.config_reviews", None)
    assert len(rows) == 1
    assert rows[0]["vendor"] == "cisco_ios"


def test_adapter_query_config_review_findings(ndc_adapter_db):
    from tools.iqe.adapters.ndc import NDCAdapter

    rows = NDCAdapter()._query(ndc_adapter_db, "network.config_review_findings", None)
    assert len(rows) == 1
    assert rows[0]["severity"] == "CAT2"


# ── Blueprint route tests ───────────────────────────────────────────────


@pytest.fixture(scope="module")
def _network_app(tmp_path_factory):
    """Module-scoped Flask app with the Network Canvas blueprint."""
    import os

    # Force SQLite so init_db creates tables without PG.
    os.environ["NC_STORAGE_BACKEND"] = "sqlite"
    os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
    os.environ["ICDEV_NETWORK_ENABLED"] = "true"
    os.environ["ICDEV_NDC_ENABLED"] = "true"

    # Point network db to a module-scoped temp path.
    tmp_dir = tmp_path_factory.mktemp("nc_config_review")
    db_path = tmp_dir / "network_canvas.db"
    import tools.network.db.init_db as ndc_init

    ndc_init.DB_PATH = db_path
    ndc_init._NC_BACKEND = "sqlite"
    ndc_init.init_db()

    from flask import Flask

    app = Flask(__name__, template_folder="tools/dashboard/templates")
    app.secret_key = "test"
    app.config["TESTING"] = True

    from tools.network.blueprint import create_network_blueprint

    bp = create_network_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/network")

    return app


@pytest.fixture
def network_client(_network_app):
    """Fresh test client per test against the module-scoped app."""
    with _network_app.test_client() as client:
        yield client


_NDC_MOCK_RESPONSE = json.dumps({
    "security_compliance": [{"title": "SC1", "severity": "CAT2", "detail": "D", "remediation": "R", "sample_config_snippet": "S", "references": ["STIG-1"]}],
    "optimization": [{"title": "OP1", "detail": "D", "recommendation": "R"}],
    "remediation": [{"title": "REM1", "priority": "high", "steps": ["step"], "sample_config_snippet": "snip", "verification": "ver"}],
    "sample_template": "template",
    "explanation": "explanation",
    "topology_graph": {"nodes": [], "edges": []},
})


def test_page_loads(network_client):
    r = network_client.get("/network/config-review")
    assert r.status_code == 200
    assert b"Network Configuration Review" in r.data


def test_create_review_returns_questions_and_prompts(network_client):
    r = network_client.post(
        "/network/api/config-review",
        json={"config_text": "!\nhostname rtr\ninterface GigabitEthernet0/0\n ip address 10.0.0.1 255.255.255.252\n", "role": "network_engineer"},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert "id" in data
    assert data["vendor"] == "cisco_ios"
    assert data["questions"]
    assert data["prompts"]


@patch("tools.llm.router.LLMRouter")
def test_analyze_review_returns_result(mock_llm_router_cls, network_client):
    mock_router = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = _NDC_MOCK_RESPONSE
    mock_router.invoke.return_value = mock_resp
    mock_llm_router_cls.return_value = mock_router

    create_r = network_client.post(
        "/network/api/config-review",
        json={"config_text": "!\nhostname rtr\ninterface GigabitEthernet0/0\n", "role": "security_auditor"},
    )
    review_id = create_r.get_json()["id"]

    r = network_client.post(
        f"/network/api/config-review/{review_id}/analyze",
        json={"answers": {"default_creds": "no"}, "prompt_title": ""},
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data["status"] == "complete"
    assert data["result"]["security_compliance"]

    # Retrieve persisted review.
    get_r = network_client.get(f"/network/api/config-review/{review_id}")
    assert get_r.status_code == 200
    persisted = get_r.get_json()
    assert persisted["review"]["status"] == "complete"
    assert persisted["findings"]


def test_export_config_and_topology(network_client):
    r = network_client.post(
        "/network/api/config-review",
        json={"config_text": "!\nhostname rtr\ninterface GigabitEthernet0/0\n", "role": "network_engineer"},
    )
    review_id = r.get_json()["id"]

    cfg_r = network_client.post(f"/network/api/config-review/{review_id}/export-config")
    assert cfg_r.status_code == 200
    assert "Starter config" in cfg_r.get_json()["config"]

    topo_r = network_client.post(f"/network/api/config-review/{review_id}/export-topology")
    assert topo_r.status_code == 200
    assert "graph" in topo_r.get_json()


def test_iqe_query_endpoint(network_client):
    with patch("tools.iqe.nl_to_iqe.nl_to_iqe") as mock_nl:
        mock_nl.return_value = {"iqe": "SELECT * FROM network.config_reviews", "explanation": "exp"}
        r = network_client.post(
            "/network/api/iqe-query",
            json={"question": "show config reviews", "execute": False},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "iqe" in data
        assert data["explanation"] == "exp"


def test_unknown_role_returns_error(network_client):
    r = network_client.post(
        "/network/api/config-review",
        json={"config_text": "hostname rtr\n", "role": "not_a_role"},
    )
    assert r.status_code == 400
    assert "Unknown role" in r.get_json()["error"]
