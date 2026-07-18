# CUI // SP-CTI
"""Behavior tests for tools/data_canvas/data_mesh/lineage_emitter.py (dcpr-qa-01).

When the ``openlineage`` client is absent (the default in this env), the
emitter falls back to writing internal edges into ``dd_lineage``. These tests
exercise that deterministic fallback against a real (tmp-pinned) SQLite DDC DB.

``dd_lineage`` has a NOT NULL FK to ``data_designs(id)`` and the DDC connection
enables ``PRAGMA foreign_keys=ON``, so a parent design row is seeded first.
``emit_lineage_event`` takes no design_id and stamps the row's design_id from
the ``run_id``, so the run_id passed here is the seeded design id.
"""

import importlib

import pytest

from tools.data_canvas.data_mesh import lineage_emitter as le


@pytest.fixture(autouse=True)
def ddc_db(tmp_path, monkeypatch):
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    db_file = tmp_path / "ddc_lineage.db"
    monkeypatch.setattr(init_db, "DB_PATH", str(db_file))
    monkeypatch.setattr(init_db, "_DDC_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_file))
    init_db.init_db()
    # Seed the parent design so dd_lineage's FK is satisfiable.
    conn = init_db.get_connection()
    conn.execute(
        "INSERT INTO data_designs (id, name, graph_json) VALUES (?,?,?)",
        ("design-1", "Test Design", '{"nodes":[],"edges":[],"boundaries":[]}'),
    )
    conn.commit()
    conn.close()
    return str(db_file)


def _lineage_rows(design_id):
    init_db = importlib.import_module("tools.data_canvas.db.init_db")
    conn = init_db.get_connection()
    rows = conn.execute(
        "SELECT * FROM dd_lineage WHERE design_id=?", (design_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def test_emit_lineage_event_internal_fallback_writes_edge():
    # run_id doubles as the design_id stamped on the internal edge, so it must
    # reference the seeded design to satisfy the FK.
    result = le.emit_lineage_event(
        run_id="design-1",
        job_name="etl_step",
        inputs=["src_table"],
        outputs=["dst_table"],
        state="COMPLETE",
    )
    assert result["emitted"] is True
    assert result["method"] == "internal"
    assert result["run_id"] == "design-1"

    rows = _lineage_rows("design-1")
    assert len(rows) == 1
    edge = rows[0]
    assert edge["source_node_id"] == "src_table"
    assert edge["target_node_id"] == "dst_table"
    assert "etl_step" in edge["transform_desc"]


def test_emit_lineage_event_cartesian_of_inputs_outputs():
    le.emit_lineage_event(
        run_id="design-1",
        job_name="join",
        inputs=["a", "b"],
        outputs=["c"],
    )
    rows = _lineage_rows("design-1")
    pairs = {(r["source_node_id"], r["target_node_id"]) for r in rows}
    assert ("a", "c") in pairs
    assert ("b", "c") in pairs


def test_emit_data_product_lineage_no_edges_is_noop():
    out = le.emit_data_product_lineage(product_id="prod-x", design_id="design-1")
    assert out["emitted"] is False
    assert out["edges_emitted"] == 0


def test_emit_data_product_lineage_reemits_existing_edges():
    # First lay down a lineage edge for the design (run_id == design id).
    le.emit_lineage_event(
        run_id="design-1", job_name="load", inputs=["raw"], outputs=["curated"],
    )
    before = len(_lineage_rows("design-1"))
    out = le.emit_data_product_lineage(product_id="prod-1", design_id="design-1")
    assert out["emitted"] is True
    assert out["method"] == "internal"
    assert out["edges_emitted"] >= 1
    # Re-emitted rows tagged with the product are added.
    after = _lineage_rows("design-1")
    assert len(after) >= before
    assert any("product_id=prod-1" in r["transform_desc"] for r in after)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
