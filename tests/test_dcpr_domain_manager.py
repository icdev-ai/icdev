# CUI // SP-CTI
"""Behavior tests for tools/data_canvas/data_mesh/domain_manager.py (dcpr-qa-01).

CRUD + maturity scoring against a real (tmp-pinned) SQLite DDC database.
"""

import importlib

import pytest

from tools.data_canvas.data_mesh import domain_manager as dm
from tools.data_canvas.data_mesh import product_registry as pr


@pytest.fixture(autouse=True)
def ddc_db(tmp_path, monkeypatch):
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    db_file = tmp_path / "ddc_domain.db"
    monkeypatch.setattr(init_db, "DB_PATH", str(db_file))
    monkeypatch.setattr(init_db, "_DDC_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    init_db.init_db()
    return str(db_file)


def test_create_and_get_domain_roundtrip():
    created = dm.create_domain({"name": "Finance", "owner_team": "fin-team",
                                "owner_email": "fin@x.mil"})
    assert "error" not in created
    domain_id = created["id"]
    fetched = dm.get_domain(domain_id)
    assert fetched is not None
    assert fetched["name"] == "Finance"
    assert fetched["owner_team"] == "fin-team"


def test_list_domains_reflects_inserts():
    dm.create_domain({"name": "Alpha"})
    dm.create_domain({"name": "Beta"})
    domains = dm.list_domains()
    names = {d["name"] for d in domains if "error" not in d}
    assert {"Alpha", "Beta"} <= names


def test_update_domain_changes_fields():
    created = dm.create_domain({"name": "Gamma", "owner_team": "old"})
    updated = dm.update_domain(created["id"], {"owner_team": "new"})
    assert updated["owner_team"] == "new"


def test_delete_domain_blocked_when_products_exist():
    created = dm.create_domain({"name": "Delta"})
    domain_id = created["id"]
    pr.create_product({"domain_id": domain_id, "name": "P1"})
    # Referenced by a product ⇒ delete refused.
    assert dm.delete_domain(domain_id) is False
    assert dm.get_domain(domain_id) is not None


def test_delete_domain_succeeds_when_unreferenced():
    created = dm.create_domain({"name": "Epsilon"})
    assert dm.delete_domain(created["id"]) is True
    assert dm.get_domain(created["id"]) is None


def test_compute_domain_maturity_counts_and_labels():
    created = dm.create_domain({"name": "Zeta"})
    domain_id = created["id"]
    # No products/contracts/policies ⇒ score 0 ⇒ "defined".
    mat0 = dm.compute_domain_maturity(domain_id)
    assert mat0["product_count"] == 0
    assert mat0["maturity_score"] == 0.0
    assert mat0["maturity_label"] == "defined"

    # Add two products ⇒ score 0.8 (0.4 each) ⇒ "optimizing".
    pr.create_product({"domain_id": domain_id, "name": "P1"})
    pr.create_product({"domain_id": domain_id, "name": "P2"})
    mat1 = dm.compute_domain_maturity(domain_id)
    assert mat1["product_count"] == 2
    assert mat1["maturity_score"] == 0.8
    assert mat1["maturity_label"] == "optimizing"


def test_list_domain_products():
    created = dm.create_domain({"name": "Eta"})
    domain_id = created["id"]
    pr.create_product({"domain_id": domain_id, "name": "OnlyProduct"})
    products = dm.list_domain_products(domain_id)
    assert [p["name"] for p in products if "error" not in p] == ["OnlyProduct"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
