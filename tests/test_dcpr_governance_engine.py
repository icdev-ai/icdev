# CUI // SP-CTI
"""Behavior tests for tools/data_canvas/governance_engine.py (dcpr-qa-01).

The existing test_dcpr_governance_access.py covers the ABAC deny/allow logic of
check_access/_eval_rule. This file covers the *other* public surface — policy
CRUD (create_policy/list_policies) and compute_governance_score — against a real
(tmp-pinned) SQLite DDC database.

Domains are seeded through the DDC connection helper (canvas storage layer) with
integer maturity levels so the scoring path is exercised deterministically.
"""

import importlib

import pytest

from tools.data_canvas import governance_engine as ge


@pytest.fixture(autouse=True)
def ddc_db(tmp_path, monkeypatch):
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    db_file = tmp_path / "ddc_gov.db"
    monkeypatch.setattr(init_db, "DB_PATH", str(db_file))
    monkeypatch.setattr(init_db, "_DDC_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    init_db.init_db()
    return str(db_file)


def _conn():
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    return init_db.get_connection()


def _seed_domain(domain_id, *, owner="", steward="", maturity=0, status="active"):
    conn = _conn()
    conn.execute(
        """INSERT INTO dm_domains (id, name, owner, steward, maturity_level, status)
           VALUES (?,?,?,?,?,?)""",
        (domain_id, domain_id.title(), owner, steward, maturity, status),
    )
    conn.commit()
    conn.close()


def _seed_active_product(product_id, domain_id):
    conn = _conn()
    conn.execute(
        """INSERT INTO dm_data_products (id, domain_id, name, status)
           VALUES (?,?,?, 'active')""",
        (product_id, domain_id, product_id.title()),
    )
    conn.commit()
    conn.close()


# ── Policy CRUD ───────────────────────────────────────────────────────────────

def test_create_policy_persists_and_lists():
    created = ge.create_policy({
        "name": "clearance-policy",
        "rules": [{"attr": "clearance", "op": "eq", "value": "CUI"}],
        "applies_to": "all",
    })
    assert created["id"].startswith("pol-")
    policies = ge.list_policies()
    match = next(p for p in policies if p["id"] == created["id"])
    assert match["name"] == "clearance-policy"
    # rules_json is parsed back into a list on read.
    assert match["rules"] == [{"attr": "clearance", "op": "eq", "value": "CUI"}]


def test_create_policy_requires_name():
    with pytest.raises(ValueError):
        ge.create_policy({"rules": []})
    with pytest.raises(ValueError):
        ge.create_policy({"name": "   "})


def test_list_policies_domain_filter():
    ge.create_policy({"name": "global", "applies_to": "all"})
    ge.create_policy({"name": "fin-only", "applies_to": "finance"})
    fin = ge.list_policies(domain_id="finance")
    names = {p["name"] for p in fin}
    assert {"global", "fin-only"} <= names  # 'all' + domain match
    other = ge.list_policies(domain_id="hr")
    other_names = {p["name"] for p in other}
    assert "fin-only" not in other_names
    assert "global" in other_names


# ── compute_governance_score ─────────────────────────────────────────────────

def test_score_no_domains_is_na():
    out = ge.compute_governance_score()
    assert out["score"] == 0.0
    assert out["grade"] == "N/A"
    assert out["domain_count"] == 0


def test_score_fully_governed_domain_is_A():
    _seed_domain("finance", owner="alice", steward="bob", maturity=3)
    _seed_active_product("orders", "finance")
    ge.create_policy({"name": "p", "applies_to": "all"})
    out = ge.compute_governance_score()
    # All four pillars satisfied → 100 → grade A.
    assert out["score"] == 100.0
    assert out["grade"] == "A"
    assert out["domain_count"] == 1
    assert out["policy_count"] == 1
    assert out["domains"][0]["score"] == 100


def test_score_partial_governance():
    # Only ownership pillar satisfied (no products, no policies, maturity 0).
    _seed_domain("hr", owner="carol", steward="dave", maturity=0)
    out = ge.compute_governance_score()
    assert out["score"] == 25.0   # 1 of 4 pillars
    assert out["grade"] == "F"


def test_score_single_domain_by_id():
    _seed_domain("a", owner="x", steward="y", maturity=3)
    _seed_domain("b")  # inactive-quality domain, no ownership
    out = ge.compute_governance_score(domain_id="a")
    assert out["domain_count"] == 1
    assert out["domains"][0]["id"] == "a"


# ── Regression: create_domain × compute_governance_score (dcpr-fix-08) ────────

def test_governance_score_no_crash_on_create_domain_maturity():
    """dcpr-fix-08: create_domain now maps the default 'defined' label to the
    INTEGER maturity_level (1), and compute_governance_score's int() read is
    guarded, so scoring a create_domain-created domain no longer raises
    ValueError."""
    from tools.data_canvas.data_mesh import domain_manager as dm
    created = dm.create_domain({"name": "Legacy"})  # writes maturity_level=1 (int)
    assert "error" not in created
    assert created["maturity_level"] == 1
    out = ge.compute_governance_score()  # must not raise
    assert isinstance(out["score"], float)
    assert out["domain_count"] >= 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
