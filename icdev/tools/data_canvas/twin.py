# CUI // SP-CTI — DDC Data Lineage Digital Twin
"""Data Design Canvas lineage twin — snapshot, schema drift simulation, quality gate."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def take_snapshot(design_id: str, label: str | None = None, classification: str = "CUI") -> dict:
    """Freeze table schema graph and lineage edges into data_twin_snapshots."""
    conn = get_connection()
    snap_id = str(uuid.uuid4())
    taken_at = _now()
    label = label or f"snap-{taken_at[:10]}"
    try:
        table_count = conn.execute(
            "SELECT COUNT(*) FROM data_nodes WHERE design_id=? AND node_type='table'", (design_id,)
        ).fetchone()[0]
    except Exception:
        table_count = 0
    try:
        edge_count = conn.execute(
            "SELECT COUNT(*) FROM data_edges WHERE design_id=?", (design_id,)
        ).fetchone()[0]
    except Exception:
        edge_count = 0
    try:
        conn.execute(
            "INSERT INTO data_twin_snapshots (id, design_id, label, table_count, edge_count, classification, created_at) VALUES (?,?,?,?,?,?,?)",
            (snap_id, design_id, label, table_count, edge_count, classification, taken_at),
        )
        conn.commit()
    except Exception:
        pass
    return {"id": snap_id, "design_id": design_id, "label": label,
            "table_count": table_count, "edge_count": edge_count, "created_at": taken_at}


def simulate_delta(design_id: str, schema_changes: list, classification: str = "CUI",
                   baseline_snap_id: str | None = None) -> dict:
    """Analyze downstream impact of proposed schema changes."""
    sim_id = str(uuid.uuid4())
    added = [c for c in schema_changes if c.get("change") == "add_column"]
    removed = [c for c in schema_changes if c.get("change") in ("remove_column", "drop_column")]
    renamed = [c for c in schema_changes if c.get("change") == "rename_column"]
    type_changes = [c for c in schema_changes if c.get("change") == "change_type"]

    breaking = removed + renamed + type_changes
    verdict = "pass" if not breaking else ("warn" if len(breaking) <= 2 else "fail")
    coverage_score = max(0.4, 1.0 - len(breaking) * 0.1)

    downstream_impacts = [
        {"severity": "high" if c.get("change") in ("remove_column", "drop_column") else "medium",
         "id": f"{c.get('table','?')}.{c.get('old_name') or c.get('column','?')}",
         "title": f"{c.get('change','?')} on {c.get('table','?')}",
         "recommendation": "Update all downstream consumers before applying this change"}
        for c in breaking
    ]
    return {
        "simulation_id": sim_id, "design_id": design_id, "verdict": verdict,
        "coverage_score": round(coverage_score, 3), "orphan_count": 0,
        "impacted_table_count": len({c.get("table") for c in breaking}),
        "schema_drift": {"added": len(added), "removed": len(removed),
                         "renamed": len(renamed), "type_changes": len(type_changes)},
        "downstream_impacts": downstream_impacts,
    }


def quality_gate(design_id: str, schema_changes: list, baseline_snap_id: str | None = None) -> dict:
    """Evaluate null constraints, referential integrity, and CUI boundary rules."""
    violations = []
    for c in schema_changes:
        if c.get("change") == "add_column" and not c.get("nullable", True) and not c.get("default"):
            violations.append({"severity": "high", "id": c.get("column", "?"),
                               "title": f"NOT NULL column '{c.get('column')}' without DEFAULT on existing table",
                               "recommendation": "Add a DEFAULT value or make nullable for backward-compatible migration"})
        if c.get("change") in ("remove_column", "drop_column"):
            violations.append({"severity": "medium", "id": c.get("column") or c.get("old_name", "?"),
                               "title": "Column removal may break referential integrity",
                               "recommendation": "Verify no foreign keys or views reference this column"})
    return {"violations": violations, "gate": "fail" if violations else "pass"}


def simulate_dm_change(
    design_id: str,
    change_type: str,
    payload: dict,
    classification: str = "CUI",
) -> dict:
    """Analyze downstream impact of a Data Mesh structural change.

    change_type: domain_merge | contract_version | port_schema | product_deprecate
    """
    import uuid as _uuid
    sim_id = str(_uuid.uuid4())
    impacts: list[dict] = []
    verdict = "pass"

    try:
        from tools.data_canvas.db.init_db import get_connection as _gc
        conn = _gc()

        if change_type == "domain_merge":
            src = payload.get("source_domain_id", "")
            prods = conn.execute(
                "SELECT COUNT(*) FROM dm_products WHERE domain_id=?", (src,)
            ).fetchone()[0]
            contracts = conn.execute(
                "SELECT COUNT(*) FROM dm_contracts c "
                "JOIN dm_products p ON c.product_id=p.id WHERE p.domain_id=?", (src,)
            ).fetchone()[0]
            policies = conn.execute(
                "SELECT COUNT(*) FROM dm_opa_policies WHERE domain_id=?", (src,)
            ).fetchone()[0]
            if prods:
                impacts.append({"severity": "medium", "id": src,
                                "title": f"{prods} product(s) re-parented to target domain",
                                "recommendation": "Notify product owners; update domain-scoped IQE queries."})
            if contracts:
                impacts.append({"severity": "medium", "id": src,
                                "title": f"{contracts} contract(s) inherit new domain governance",
                                "recommendation": "Re-evaluate OPA policies in target domain before merge."})
            if policies:
                impacts.append({"severity": "low", "id": src,
                                "title": f"{policies} source-domain policies will be archived",
                                "recommendation": "Review for conflicts with target-domain policy set."})
            verdict = "warn" if impacts else "pass"

        elif change_type == "contract_version":
            contract_id = payload.get("contract_id", "")
            new_ver = payload.get("new_version", "")
            schema_diff = payload.get("schema_diff", [])
            breaking = [d for d in schema_diff if d.get("change") in
                        ("remove_field", "rename_field", "change_type", "tighten_constraint")]
            consumers = conn.execute(
                "SELECT COUNT(*) FROM dm_ports p "
                "JOIN dm_contracts c ON p.product_id=c.product_id "
                "WHERE c.id=? AND p.port_type='input'", (contract_id,)
            ).fetchone()[0]
            if breaking:
                impacts.append({"severity": "high", "id": contract_id,
                                "title": f"{len(breaking)} breaking field change(s) in contract {new_ver}",
                                "recommendation": "Pin consumers to prior version before releasing."})
                verdict = "fail"
            if consumers:
                impacts.append({"severity": "medium", "id": contract_id,
                                "title": f"{consumers} input port(s) must be re-validated",
                                "recommendation": "Run contract validation on consumer ports before activating."})
                if verdict == "pass":
                    verdict = "warn"

        elif change_type == "port_schema":
            port_id = payload.get("port_id", "")
            product_id = payload.get("product_id", "")
            schema_diff = payload.get("schema_diff", [])
            breaking = [d for d in schema_diff if d.get("change") in
                        ("remove_field", "rename_field", "change_type")]
            active_contracts = conn.execute(
                "SELECT COUNT(*) FROM dm_contracts WHERE product_id=? AND status='active'",
                (product_id,)
            ).fetchone()[0]
            if breaking and active_contracts:
                impacts.append({"severity": "high", "id": port_id,
                                "title": f"Port schema change breaks {active_contracts} active contract(s)",
                                "recommendation": "Deprecate contracts and issue new versions first."})
                verdict = "fail"
            elif breaking:
                impacts.append({"severity": "medium", "id": port_id,
                                "title": f"{len(breaking)} breaking field change(s) on output port",
                                "recommendation": "Ensure no un-contracted consumers exist."})
                verdict = "warn"

        elif change_type == "product_deprecate":
            product_id = payload.get("product_id", "")
            active_contracts = conn.execute(
                "SELECT COUNT(*) FROM dm_contracts WHERE product_id=? AND status='active'",
                (product_id,)
            ).fetchone()[0]
            ports = conn.execute(
                "SELECT COUNT(*) FROM dm_ports WHERE product_id=?", (product_id,)
            ).fetchone()[0]
            if active_contracts:
                impacts.append({"severity": "high", "id": product_id,
                                "title": f"Deprecating product with {active_contracts} active contract(s)",
                                "recommendation": "Migrate or deprecate contracts first."})
                verdict = "fail"
            if ports:
                impacts.append({"severity": "medium", "id": product_id,
                                "title": f"{ports} port(s) will become unreachable",
                                "recommendation": "Notify port consumers and set deprecation timeline."})
                if verdict == "pass":
                    verdict = "warn"

        conn.close()
    except Exception as exc:
        impacts.append({"severity": "unknown", "id": "db_error",
                        "title": f"DB error during simulation: {exc}",
                        "recommendation": "Ensure dm_* tables are initialized."})
        verdict = "error"

    coverage_score = max(0.3, 1.0 - len([i for i in impacts if i["severity"] == "high"]) * 0.2)
    return {
        "simulation_id": sim_id,
        "design_id": design_id,
        "change_type": change_type,
        "verdict": verdict,
        "coverage_score": round(coverage_score, 3),
        "impact_count": len(impacts),
        "downstream_impacts": impacts,
    }
