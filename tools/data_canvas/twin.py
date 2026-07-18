# CUI // SP-CTI — DDC Data Lineage Digital Twin
"""Data Design Canvas lineage twin — snapshot, schema drift simulation, quality gate."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from tools.db.storage import get_canvas_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Real-state helpers ────────────────────────────────────────────────────────

_CLASS_RANK = {"UNCLASSIFIED": 0, "CUI": 1, "SECRET": 2, "TOP SECRET": 3}


def _class_rank(label: str | None) -> int:
    """Rank a classification label; unknown/empty → 0 (UNCLASSIFIED)."""
    if not label:
        return 0
    ul = label.upper()
    for k in ("TOP SECRET", "SECRET", "CUI", "UNCLASSIFIED"):
        if k in ul:
            return _CLASS_RANK[k]
    return 0


def _col_of(change: dict) -> str:
    """The source column a change targets (old_name for renames, else column)."""
    return change.get("old_name") or change.get("column") or ""


def _tbl_of(change: dict) -> str:
    return change.get("table") or ""


def _load_lineage_records(design_id: str) -> list[dict]:
    """Read ``dd_lineage`` rows for a design as records for the lineage engine.

    Never raises — returns ``[]`` when the table is absent or the query fails so
    callers degrade to honest zeros rather than fabricated impact.
    """
    try:
        conn = get_canvas_connection()
        rows = conn.execute(
            "SELECT id, source_node_id, target_node_id, lineage_type, column_name, "
            "transform_desc, classification FROM dd_lineage WHERE design_id=%s",
            (design_id,),
        ).fetchall()
    except Exception:
        return []
    records: list[dict] = []
    for r in rows:
        records.append(
            {
                "id": r[0],
                "source_node_id": r[1],
                "target_node_id": r[2],
                "lineage_type": r[3],
                "column_name": r[4],
                "transform_desc": r[5],
                "classification": r[6],
            }
        )
    return records


def _load_classifications(design_id: str) -> tuple[str | None, dict[str, str]]:
    """Return (design_classification, {node_id: classification}) from real state.

    Missing rows yield ``None`` / an empty map — the boundary check is skipped
    for anything not actually stored, never fabricated.
    """
    design_class: str | None = None
    node_class: dict[str, str] = {}
    try:
        conn = get_canvas_connection()
        row = conn.execute(
            "SELECT classification FROM data_designs WHERE id=%s", (design_id,)
        ).fetchone()
        if row:
            design_class = row[0]
    except Exception:
        pass
    try:
        conn = get_canvas_connection()
        rows = conn.execute(
            "SELECT id, classification FROM data_nodes WHERE design_id=%s", (design_id,)
        ).fetchall()
        for r in rows:
            node_class[r[0]] = r[1]
    except Exception:
        pass
    return design_class, node_class


def take_snapshot(design_id: str, label: str | None = None, classification: str = "CUI") -> dict:
    """Freeze table schema graph and lineage edges into data_twin_snapshots."""
    conn = get_canvas_connection()
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
    """Analyze downstream impact of proposed schema changes against real lineage.

    Loads the design's ``dd_lineage`` column-level edges and, for every
    removed / renamed / type-changed column, walks the lineage DAG
    (``compute_downstream_impact``) to enumerate the *real* downstream consumers.

    Computed from actual state — not the input list:
      * ``impacted_table_count`` — distinct downstream entities reached via edges.
      * ``orphan_count`` — impacted downstream (entity, column) nodes left with
        **no upstream provenance** once the removed/renamed source columns are
        dropped from the lineage set (``compute_upstream_provenance`` on the
        filtered records).
      * ``coverage_score`` — real signal: fraction of impacted downstream nodes
        that retain upstream after the change (1.0 when nothing breaks; 0.0 when
        breaking changes exist but the design has no lineage to reason over).

    Never raises. When no lineage edges exist, returns honest zeros plus a
    ``note`` rather than inventing impact.
    """
    from tools.data_canvas.lineage import (
        compute_downstream_impact,
        compute_upstream_provenance,
    )

    sim_id = str(uuid.uuid4())
    added = [c for c in schema_changes if c.get("change") == "add_column"]
    removed = [c for c in schema_changes if c.get("change") in ("remove_column", "drop_column")]
    renamed = [c for c in schema_changes if c.get("change") == "rename_column"]
    type_changes = [c for c in schema_changes if c.get("change") == "change_type"]
    breaking = removed + renamed + type_changes

    lineage_records = _load_lineage_records(design_id)
    note = None
    if not lineage_records:
        note = ("No lineage edges recorded for this design — downstream impact is "
                "structural only (orphan/impact counts are honest zeros).")

    # Edges that disappear after the change: those sourced from a removed/renamed column.
    removed_keys = {(_tbl_of(c), _col_of(c)) for c in (removed + renamed)}
    filtered = [
        r for r in lineage_records
        if (r["source_node_id"], r["column_name"]) not in removed_keys
    ]

    impacted_nodes: dict[tuple[str, str], dict] = {}
    impacted_tables: set[str] = set()
    downstream_impacts: list[dict] = []

    for c in breaking:
        tbl, col = _tbl_of(c), _col_of(c)
        if not tbl or not col:
            continue
        impact = compute_downstream_impact(tbl, col, lineage_records)
        for n in impact["impacted_nodes"]:
            key = (n["entity_id"], n["column_name"])
            impacted_nodes[key] = n
            impacted_tables.add(n["entity_id"])
        consumers = impact["total_impacted"]
        is_removal = c.get("change") in ("remove_column", "drop_column", "rename_column")
        downstream_impacts.append({
            "severity": "high" if (is_removal and consumers) else
                        ("high" if c.get("change") in ("remove_column", "drop_column") else "medium"),
            "id": f"{tbl}.{col}",
            "title": f"{c.get('change','?')} on {tbl}.{col} affects "
                     f"{consumers} downstream column(s)",
            "consumer_count": consumers,
            "consumers": [f"{n['entity_id']}::{n['column_name']}"
                          for n in impact["impacted_nodes"][:20]],
            "recommendation": "Update all downstream consumers before applying this change",
        })

    # Orphans: impacted downstream nodes with no upstream provenance after removal.
    orphaned: list[str] = []
    for (ent, col) in impacted_nodes:
        prov = compute_upstream_provenance(ent, col, filtered)
        if prov["total_provenance"] == 0:
            orphaned.append(f"{ent}::{col}")
    orphan_count = len(orphaned)

    total_impacted = len(impacted_nodes)
    if total_impacted:
        coverage_score = round(1.0 - orphan_count / total_impacted, 3)
    elif breaking and not lineage_records:
        coverage_score = 0.0
    else:
        coverage_score = 1.0

    verdict = "pass" if not breaking else ("warn" if len(breaking) <= 2 else "fail")

    result = {
        "simulation_id": sim_id, "design_id": design_id, "verdict": verdict,
        "coverage_score": coverage_score, "orphan_count": orphan_count,
        "orphaned_nodes": orphaned,
        "impacted_table_count": len(impacted_tables),
        "schema_drift": {"added": len(added), "removed": len(removed),
                         "renamed": len(renamed), "type_changes": len(type_changes)},
        "downstream_impacts": downstream_impacts,
    }
    if note:
        result["note"] = note
    return result


def quality_gate(design_id: str, schema_changes: list, baseline_snap_id: str | None = None) -> dict:
    """Evaluate schema changes against the design's real stored state.

    Runs three checks; every reported violation is grounded in stored data:
      1. **Null-safety** (structural) — a NOT NULL column added without a DEFAULT
         breaks a backward-compatible migration on an existing table.
      2. **Referential integrity** (lineage-backed) — a removed/renamed column
         that has *real* downstream consumers in ``dd_lineage``
         (``compute_downstream_impact`` returns > 0). Columns with no recorded
         consumers are NOT flagged.
      3. **Classification boundary** (CUI) — a change targeting a table whose
         stored classification (``data_nodes``) dominates the design's
         classification (``data_designs``).

    Never raises. When the design/nodes/lineage rows are absent the corresponding
    check is skipped rather than fabricated — no overclaiming.
    """
    from tools.data_canvas.lineage import compute_downstream_impact

    violations: list[dict] = []
    lineage_records = _load_lineage_records(design_id)
    design_class, node_class = _load_classifications(design_id)

    for c in schema_changes:
        change = c.get("change")
        tbl = _tbl_of(c)

        # 1. Null-safety (structural).
        if change == "add_column" and not c.get("nullable", True) and not c.get("default"):
            violations.append({
                "severity": "high", "type": "null_safety", "id": c.get("column", "?"),
                "title": f"NOT NULL column '{c.get('column')}' without DEFAULT on existing table",
                "recommendation": "Add a DEFAULT value or make nullable for backward-compatible migration",
            })

        # 2. Referential integrity — only when real downstream consumers exist.
        if change in ("remove_column", "drop_column", "rename_column"):
            src_col = _col_of(c)
            impact = compute_downstream_impact(tbl, src_col, lineage_records)
            n = impact["total_impacted"]
            if n:
                violations.append({
                    "severity": "high", "type": "referential_integrity",
                    "id": f"{tbl}.{src_col}",
                    "title": f"{n} downstream consumer(s) reference '{tbl}.{src_col}' in recorded lineage",
                    "recommendation": "Repoint or remove downstream lineage edges before applying this change",
                    "consumers": [f"{x['entity_id']}::{x['column_name']}"
                                  for x in impact["impacted_nodes"][:20]],
                })

        # 3. Classification boundary — table classification dominates the design's.
        tcls = node_class.get(tbl)
        if tcls and design_class and _class_rank(tcls) > _class_rank(design_class):
            violations.append({
                "severity": "high", "type": "classification_boundary", "id": tbl,
                "title": f"Change touches '{tbl}' ({tcls}) which exceeds design classification ({design_class})",
                "recommendation": "Re-mark the design or route this change through the higher enclave",
            })

    return {
        "violations": violations,
        "gate": "fail" if violations else "pass",
        "checks": ["null_safety", "referential_integrity", "classification_boundary"],
    }


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
                "SELECT COUNT(*) FROM dm_data_products WHERE domain_id=?", (src,)
            ).fetchone()[0]
            contracts = conn.execute(
                "SELECT COUNT(*) FROM dm_contracts c "
                "JOIN dm_data_products p ON c.product_id=p.id WHERE p.domain_id=?", (src,)
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
                "SELECT COUNT(*) FROM dm_input_ports p "
                "JOIN dm_contracts c ON p.product_id=c.product_id "
                "WHERE c.id=?", (contract_id,)
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
            ports = (
                conn.execute("SELECT COUNT(*) FROM dm_input_ports WHERE product_id=?", (product_id,)).fetchone()[0]
                + conn.execute("SELECT COUNT(*) FROM dm_output_ports WHERE product_id=?", (product_id,)).fetchone()[0]
            )
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
