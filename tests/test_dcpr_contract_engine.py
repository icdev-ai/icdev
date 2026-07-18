# CUI // SP-CTI
"""Behavior tests for tools/data_canvas/data_mesh/contract_engine.py (dcpr-qa-01).

Covers ODCS YAML validation/lint (pure) and CRUD + internal contract testing
against a real (tmp-pinned) SQLite DDC database.
"""

import importlib
import importlib.util

import pytest

from tools.data_canvas.data_mesh import contract_engine as ce

_HAS_YAML = importlib.util.find_spec("yaml") is not None
_GOOD_YAML = """
dataContractSpecification: "1.1.0"
id: "urn:datacontract:test:orders"
info:
  title: "Orders"
  owner: "data-team"
models:
  orders:
    type: table
"""
_BAD_YAML = """
info:
  title: "Incomplete"
"""


@pytest.fixture(autouse=True)
def ddc_db(tmp_path, monkeypatch):
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    db_file = tmp_path / "ddc_contract.db"
    monkeypatch.setattr(init_db, "DB_PATH", str(db_file))
    monkeypatch.setattr(init_db, "_DDC_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    init_db.init_db()
    return str(db_file)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def test_create_get_and_list_contract():
    created = ce.create_contract({"domain_id": "d1", "product_id": "p1",
                                  "name": "Orders Contract",
                                  "contract_yaml": _GOOD_YAML})
    assert "error" not in created
    cid = created["id"]
    fetched = ce.get_contract(cid)
    assert fetched["name"] == "Orders Contract"

    by_domain = ce.list_contracts(domain_id="d1")
    assert any(c.get("id") == cid for c in by_domain)
    by_other = ce.list_contracts(domain_id="nope")
    assert all(c.get("id") != cid for c in by_other if "error" not in c)


def test_update_and_delete_contract():
    created = ce.create_contract({"name": "C", "contract_yaml": _GOOD_YAML})
    cid = created["id"]
    updated = ce.update_contract(cid, {"status": "active"})
    assert updated["status"] == "active"
    assert ce.delete_contract(cid) is True
    assert ce.get_contract(cid) is None


# ── Validation / Lint (pure) ──────────────────────────────────────────────────

@pytest.mark.skipif(not _HAS_YAML, reason="PyYAML not installed")
def test_validate_yaml_structure_good():
    out = ce.validate_yaml_structure(_GOOD_YAML)
    assert out["valid"] is True
    assert out["missing_required"] == []
    assert "info" in out["fields_present"]


@pytest.mark.skipif(not _HAS_YAML, reason="PyYAML not installed")
def test_validate_yaml_structure_missing_fields():
    out = ce.validate_yaml_structure(_BAD_YAML)
    assert out["valid"] is False
    assert "models" in out["missing_required"]


@pytest.mark.skipif(not _HAS_YAML, reason="PyYAML not installed")
def test_lint_contract_scores_good_higher_than_bad():
    good = ce.lint_contract(_GOOD_YAML)
    bad = ce.lint_contract(_BAD_YAML)
    assert good["passed"] is True
    assert good["errors"] == []
    assert bad["passed"] is False
    assert bad["errors"]
    assert good["score"] > bad["score"]


@pytest.mark.skipif(not _HAS_YAML, reason="PyYAML not installed")
def test_lint_contract_parse_error():
    out = ce.lint_contract("::: not : valid : yaml :::\n  - [")
    assert out["passed"] is False


# ── Contract testing (internal fallback) ──────────────────────────────────────

@pytest.mark.skipif(
    _HAS_YAML is False or importlib.util.find_spec("datacontract") is not None,
    reason="requires PyYAML and the internal (non-CLI) lint path",
)
def test_test_contract_internal_pass_and_persist():
    created = ce.create_contract({"name": "Testable", "contract_yaml": _GOOD_YAML})
    result = ce.test_contract(created["id"])
    assert result["method"] == "internal"
    assert result["passed"] is True
    assert result["error_count"] == 0
    assert "run_id" in result

    # A run row was persisted.
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    conn = init_db.get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM dm_contract_test_runs WHERE contract_id=?",
        (created["id"],),
    ).fetchone()[0]
    conn.close()
    assert count == 1


def test_test_contract_missing_returns_error():
    result = ce.test_contract("no-such-contract")
    assert result["passed"] is False
    assert result["error"] == "contract not found"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
