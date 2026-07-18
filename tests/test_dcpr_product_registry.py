# CUI // SP-CTI
"""Behavior tests for tools/data_canvas/data_mesh/product_registry.py (dcpr-qa-01).

CRUD + SLA + subscriptions + discoverability scoring against a real
(tmp-pinned) SQLite DDC database.
"""

import importlib

import pytest

from tools.data_canvas.data_mesh import product_registry as pr


@pytest.fixture(autouse=True)
def ddc_db(tmp_path, monkeypatch):
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    db_file = tmp_path / "ddc_product.db"
    monkeypatch.setattr(init_db, "DB_PATH", str(db_file))
    monkeypatch.setattr(init_db, "_DDC_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    init_db.init_db()
    # dm_data_products.domain_id FKs dm_domains(id) (foreign_keys=ON), so seed
    # the parent domains a product can belong to.
    conn = init_db.get_connection()
    for did in ("dom1", "domA", "domB"):
        conn.execute("INSERT INTO dm_domains (id, name) VALUES (?,?)", (did, did))
    conn.commit()
    conn.close()
    return str(db_file)


def test_create_and_get_product_roundtrip():
    created = pr.create_product({"domain_id": "domA", "name": "Orders",
                                 "description": "order events", "status": "published"})
    assert "error" not in created
    fetched = pr.get_product(created["id"])
    assert fetched["name"] == "Orders"
    assert fetched["status"] == "published"


def test_create_product_coerces_invalid_status_to_draft():
    created = pr.create_product({"domain_id": "dom1", "name": "X",
                                 "status": "not-a-status"})
    fetched = pr.get_product(created["id"])
    assert fetched["status"] == "draft"


def test_list_products_filters_by_domain_and_status():
    pr.create_product({"domain_id": "domA", "name": "PA", "status": "published"})
    pr.create_product({"domain_id": "domB", "name": "PB", "status": "draft"})
    by_domain = pr.list_products(domain_id="domA")
    assert [p["name"] for p in by_domain if "error" not in p] == ["PA"]
    by_status = pr.list_products(status="draft")
    names = {p["name"] for p in by_status if "error" not in p}
    assert "PB" in names and "PA" not in names


def test_update_and_delete_product():
    created = pr.create_product({"domain_id": "dom1", "name": "Temp"})
    pid = created["id"]
    updated = pr.update_product(pid, {"name": "Renamed"})
    assert updated["name"] == "Renamed"
    assert pr.delete_product(pid) is True
    assert pr.get_product(pid) is None


def test_add_and_get_slas():
    created = pr.create_product({"domain_id": "dom1", "name": "WithSLA"})
    pid = created["id"]
    sla = pr.add_product_sla(pid, "availability", 99.9, "%")
    assert "error" not in sla
    slas = pr.get_product_slas(pid)
    assert len(slas) == 1
    assert slas[0]["sla_type"] == "availability"


def test_subscribe_and_approve():
    created = pr.create_product({"domain_id": "dom1", "name": "Subbable"})
    pid = created["id"]
    sub = pr.subscribe_to_product(pid, {"subscriber_team": "analytics",
                                        "purpose": "reporting"})
    assert sub["approved"] is False
    assert pr.approve_subscription(sub["id"]) is True


def test_discoverability_score_rewards_metadata():
    # Bare product: only description present (or nothing) → low score.
    bare = pr.create_product({"domain_id": "dom1", "name": "Bare"})
    low = pr.compute_discoverability_score(bare["id"])
    assert low["dimensions"]["has_slas"] is False

    # Product with description + SLA → score climbs by defined increments (20 each).
    rich = pr.create_product({"domain_id": "dom1", "name": "Rich",
                              "description": "a described product"})
    pr.add_product_sla(rich["id"], "latency", 100, "ms")
    high = pr.compute_discoverability_score(rich["id"])
    assert high["dimensions"]["has_description"] is True
    assert high["dimensions"]["has_slas"] is True
    assert high["score"] >= 40
    assert high["score"] > low["score"]
    assert high["label"] in {"Undiscoverable", "Emerging", "Discoverable", "Trusted"}


def test_discoverability_score_missing_product():
    out = pr.compute_discoverability_score("does-not-exist")
    assert out["score"] == 0
    assert "error" in out


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
