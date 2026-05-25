# CUI // SP-CTI
"""cam_seed_demo.py — Seed the Analytics Platform K8s → AWS demo migration project.

Reads context/migration/projects/analytics-k8s-aws.yaml and seeds data across:
  MC  — mc_projects, mc_project_phases, mc_project_phase_sops, mc_ai_opportunities,
         mc_srv_sessions, mc_app_inventory, mc_app_data_sources, mc_data_migration, mc_wave_plans
  DDC — data_designs, data_nodes, dd_lineage
  IDC — infra_designs
  NDC — topologies

Usage:
  python tools/migration_canvas/cam_seed_demo.py
  python tools/migration_canvas/cam_seed_demo.py --reset
  python tools/migration_canvas/cam_seed_demo.py --json
  python tools/migration_canvas/cam_seed_demo.py --project analytics-k8s-aws
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = get_logger("icdev.cam_seed_demo")

_PROJECTS_DIR = Path(__file__).resolve().parent.parent.parent / "context" / "migration" / "projects"

# Stable IDs so re-runs stay idempotent
_PROJECT_ID = "proj-analytics-k8s-aws"
_SESSION_ID  = "sess-analytics-k8s-aws"
_DDC_DESIGN_ID = "ddc-design-analytics-oracle-aurora"
_IDC_DESIGN_ID = "idc-design-analytics-k8s-eks"
_NDC_TOPO_ID   = "ndc-topo-analytics-aws-vpc"


# ── DB helpers ──────────────────────────────────────────────────────────────

def _mc_conn():
    from tools.migration_canvas.db.init_db import get_connection
    return get_connection()


def _ddc_conn():
    from tools.data_canvas.db.init_db import get_connection
    return get_connection()


def _idc_conn():
    from tools.infra_canvas.db.init_db import get_connection
    return get_connection()


def _ndc_conn():
    from tools.network.db.init_db import get_connection
    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return str(uuid.uuid4())


# ── SOP resolution ──────────────────────────────────────────────────────────

def _find_sop_any_canvas(*keywords: str) -> tuple[str, str, str] | tuple[None, None, None]:
    """Search mc → idc → ddc → ndc for a SOP matching any keyword.

    Returns (sop_id, sop_title, canvas) or (None, None, None).
    """
    from tools.migration_canvas.cam_engine import _find_sop
    for canvas in ("mc", "idc", "ddc", "ndc"):
        sop_id, sop_title = _find_sop(None, canvas, *keywords)
        if sop_id:
            return sop_id, sop_title, canvas
    return None, None, None


# ── Reset helpers ────────────────────────────────────────────────────────────

def _reset(project_id: str) -> None:
    mc = _mc_conn()
    try:
        mc.execute("DELETE FROM mc_project_phase_sops WHERE project_id=?", (project_id,))
        mc.execute("DELETE FROM mc_ai_opportunities WHERE project_id=?", (project_id,))
        mc.execute("DELETE FROM mc_project_phases WHERE project_id=?", (project_id,))
        mc.execute("DELETE FROM mc_wave_plans WHERE design_id=?", (project_id,))
        mc.execute("DELETE FROM mc_projects WHERE id=?", (project_id,))
        mc.execute("DELETE FROM migration_designs WHERE id=? AND description LIKE 'CAM project anchor%'", (project_id,))
        mc.execute("DELETE FROM mc_data_migration WHERE session_id=?", (_SESSION_ID,))
        mc.execute("DELETE FROM mc_app_data_sources WHERE app_id IN "
                   "(SELECT id FROM mc_app_inventory WHERE session_id=?)", (_SESSION_ID,))
        mc.execute("DELETE FROM mc_app_inventory WHERE session_id=?", (_SESSION_ID,))
        mc.execute("DELETE FROM mc_srv_sessions WHERE id=?", (_SESSION_ID,))
        mc.commit()
    finally:
        mc.close()

    ddc = _ddc_conn()
    try:
        ddc.execute("DELETE FROM dd_lineage WHERE design_id=?", (_DDC_DESIGN_ID,))
        ddc.execute("DELETE FROM data_nodes WHERE design_id=?", (_DDC_DESIGN_ID,))
        ddc.execute("DELETE FROM data_designs WHERE id=?", (_DDC_DESIGN_ID,))
        ddc.commit()
    finally:
        ddc.close()

    idc = _idc_conn()
    try:
        idc.execute("DELETE FROM infra_designs WHERE id=?", (_IDC_DESIGN_ID,))
        idc.commit()
    finally:
        idc.close()

    ndc = _ndc_conn()
    try:
        ndc.execute("DELETE FROM topologies WHERE id=?", (_NDC_TOPO_ID,))
        ndc.commit()
    finally:
        ndc.close()


# ── DDC seed ─────────────────────────────────────────────────────────────────

def _seed_ddc() -> str:
    """Seed Oracle → Aurora lineage design in DDC. Returns design_id."""
    nodes = [
        # Oracle source tables
        {"id": "node-ora-users",     "label": "USERS",       "node_type": "table", "x": 100, "y":  80, "properties_json": json.dumps({"platform": "oracle", "schema": "APP", "rows_est": 2000000})},
        {"id": "node-ora-orders",    "label": "ORDERS",      "node_type": "table", "x": 100, "y": 220, "properties_json": json.dumps({"platform": "oracle", "schema": "APP", "rows_est": 45000000})},
        {"id": "node-ora-products",  "label": "PRODUCTS",    "node_type": "table", "x": 100, "y": 360, "properties_json": json.dumps({"platform": "oracle", "schema": "APP", "rows_est": 120000})},
        {"id": "node-ora-analytics", "label": "ANALYTICS_EVENTS", "node_type": "table", "x": 100, "y": 500, "properties_json": json.dumps({"platform": "oracle", "schema": "APP", "rows_est": 300000000})},
        # DMS replication instance
        {"id": "node-dms",           "label": "AWS DMS\nReplication", "node_type": "process", "x": 420, "y": 290, "properties_json": json.dumps({"service": "aws_dms", "engine": "aurora-postgresql", "mode": "full_load_cdc"})},
        # Aurora target tables
        {"id": "node-aur-users",     "label": "users",       "node_type": "table", "x": 740, "y":  80, "properties_json": json.dumps({"platform": "aurora_postgresql", "schema": "public", "extension": "pgvector"})},
        {"id": "node-aur-orders",    "label": "orders",      "node_type": "table", "x": 740, "y": 220, "properties_json": json.dumps({"platform": "aurora_postgresql", "schema": "public"})},
        {"id": "node-aur-products",  "label": "products",    "node_type": "table", "x": 740, "y": 360, "properties_json": json.dumps({"platform": "aurora_postgresql", "schema": "public"})},
        {"id": "node-aur-analytics", "label": "analytics_events", "node_type": "table", "x": 740, "y": 500, "properties_json": json.dumps({"platform": "aurora_postgresql", "schema": "public", "partitioned": True})},
    ]
    lineage = [
        {"id": "lin-ora-users-dms",     "source_node_id": "node-ora-users",     "target_node_id": "node-dms",         "lineage_type": "replication", "transform_desc": "DMS full-load + CDC"},
        {"id": "lin-ora-orders-dms",    "source_node_id": "node-ora-orders",    "target_node_id": "node-dms",         "lineage_type": "replication", "transform_desc": "DMS full-load + CDC"},
        {"id": "lin-ora-products-dms",  "source_node_id": "node-ora-products",  "target_node_id": "node-dms",         "lineage_type": "replication", "transform_desc": "DMS full-load"},
        {"id": "lin-ora-analytics-dms", "source_node_id": "node-ora-analytics", "target_node_id": "node-dms",         "lineage_type": "replication", "transform_desc": "DMS full-load + CDC"},
        {"id": "lin-dms-aur-users",     "source_node_id": "node-dms",           "target_node_id": "node-aur-users",   "lineage_type": "flow",        "transform_desc": "SCT schema conversion"},
        {"id": "lin-dms-aur-orders",    "source_node_id": "node-dms",           "target_node_id": "node-aur-orders",  "lineage_type": "flow",        "transform_desc": "SCT schema conversion"},
        {"id": "lin-dms-aur-products",  "source_node_id": "node-dms",           "target_node_id": "node-aur-products","lineage_type": "flow",        "transform_desc": "SCT schema conversion"},
        {"id": "lin-dms-aur-analytics", "source_node_id": "node-dms",           "target_node_id": "node-aur-analytics","lineage_type": "flow",       "transform_desc": "SCT + partition strategy"},
    ]

    graph_json = json.dumps({
        "nodes": [{"id": n["id"], "label": n["label"], "type": n["node_type"], "x": n["x"], "y": n["y"]} for n in nodes],
        "edges": [{"id": l["id"], "source": l["source_node_id"], "target": l["target_node_id"], "type": l["lineage_type"]} for l in lineage],
        "boundaries": [],
    })

    ddc = _ddc_conn()
    try:
        ddc.execute(
            "INSERT OR IGNORE INTO data_designs (id, name, description, graph_json, classification) VALUES (?,?,?,?,?)",
            (_DDC_DESIGN_ID, "Oracle → Aurora Migration Schema",
             "Data lineage: Oracle 19c source tables → AWS DMS replication → Aurora PostgreSQL target",
             graph_json, "CUI // SP-CTI"),
        )
        for n in nodes:
            ddc.execute(
                "INSERT OR IGNORE INTO data_nodes (id, design_id, node_type, label, x, y, properties_json, classification) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (n["id"], _DDC_DESIGN_ID, n["node_type"], n["label"], n["x"], n["y"],
                 n.get("properties_json", "{}"), "CUI // SP-CTI"),
            )
        for l in lineage:
            ddc.execute(
                "INSERT OR IGNORE INTO dd_lineage (id, design_id, source_node_id, target_node_id, lineage_type, transform_desc, classification) "
                "VALUES (?,?,?,?,?,?,?)",
                (l["id"], _DDC_DESIGN_ID, l["source_node_id"], l["target_node_id"],
                 l["lineage_type"], l["transform_desc"], "CUI // SP-CTI"),
            )
        ddc.commit()
    finally:
        ddc.close()
    return _DDC_DESIGN_ID


# ── IDC seed ─────────────────────────────────────────────────────────────────

def _seed_idc() -> str:
    """Seed K8s → EKS Fargate infra design in IDC. Returns design_id."""
    graph = {
        "nodes": [
            {"id": "n-k8s-cluster",   "label": "K8s Cluster\n(on-prem)",   "type": "cluster",   "x": 80,  "y": 200},
            {"id": "n-k8s-oracle",    "label": "Oracle Pod",               "type": "container", "x": 80,  "y": 80},
            {"id": "n-k8s-redis",     "label": "Redis Pod",                "type": "container", "x": 80,  "y": 160},
            {"id": "n-k8s-es",        "label": "Elasticsearch Pod",        "type": "container", "x": 80,  "y": 240},
            {"id": "n-k8s-nifi",      "label": "NiFi Pod",                 "type": "container", "x": 80,  "y": 320},
            {"id": "n-k8s-react",     "label": "React/Nginx Pod",          "type": "container", "x": 80,  "y": 400},
            {"id": "n-direct-connect","label": "Direct Connect / VPN",     "type": "link",      "x": 380, "y": 200},
            {"id": "n-eks-fargate",   "label": "EKS Fargate\n(AWS)",       "type": "cluster",   "x": 680, "y": 200},
            {"id": "n-aurora",        "label": "Aurora PostgreSQL",        "type": "database",  "x": 680, "y": 80},
            {"id": "n-elasticache",   "label": "ElastiCache Redis",        "type": "database",  "x": 680, "y": 160},
            {"id": "n-opensearch",    "label": "OpenSearch Service",       "type": "search",    "x": 680, "y": 240},
            {"id": "n-glue-msk",      "label": "Glue + MSK",              "type": "service",   "x": 680, "y": 320},
            {"id": "n-amplify",       "label": "Amplify + CloudFront",    "type": "cdn",       "x": 680, "y": 400},
        ],
        "edges": [
            {"id": "e-k8s-dc",   "source": "n-k8s-cluster",    "target": "n-direct-connect", "type": "migration"},
            {"id": "e-dc-eks",   "source": "n-direct-connect",  "target": "n-eks-fargate",    "type": "migration"},
            {"id": "e-ora-aur",  "source": "n-k8s-oracle",      "target": "n-aurora",         "type": "replatform"},
            {"id": "e-red-ec",   "source": "n-k8s-redis",       "target": "n-elasticache",    "type": "replatform"},
            {"id": "e-es-os",    "source": "n-k8s-es",          "target": "n-opensearch",     "type": "replatform"},
            {"id": "e-nifi-glue","source": "n-k8s-nifi",        "target": "n-glue-msk",       "type": "rearchitect"},
            {"id": "e-react-amp","source": "n-k8s-react",       "target": "n-amplify",        "type": "replatform"},
        ],
    }
    idc = _idc_conn()
    try:
        idc.execute(
            "INSERT OR IGNORE INTO infra_designs (id, name, description, graph_json, classification) VALUES (?,?,?,?,?)",
            (_IDC_DESIGN_ID, "K8s → EKS Fargate Migration Infrastructure",
             "Infrastructure design: on-prem K8s cluster → EKS Fargate + Aurora + ElastiCache + OpenSearch + Amplify",
             json.dumps(graph), "CUI // SP-CTI"),
        )
        idc.commit()
    finally:
        idc.close()
    return _IDC_DESIGN_ID


# ── NDC seed ─────────────────────────────────────────────────────────────────

def _seed_ndc() -> str:
    """Seed AWS Landing Zone topology in NDC. Returns topology_id."""
    graph = {
        "nodes": [
            {"id": "n-igw",      "label": "Internet Gateway",   "type": "gateway",  "x": 600, "y": 50},
            {"id": "n-nat-az1",  "label": "NAT GW (AZ1)",       "type": "gateway",  "x": 200, "y": 200},
            {"id": "n-nat-az2",  "label": "NAT GW (AZ2)",       "type": "gateway",  "x": 500, "y": 200},
            {"id": "n-nat-az3",  "label": "NAT GW (AZ3)",       "type": "gateway",  "x": 800, "y": 200},
            {"id": "n-pub-az1",  "label": "Public Subnet AZ1",  "type": "subnet",   "x": 200, "y": 350},
            {"id": "n-pub-az2",  "label": "Public Subnet AZ2",  "type": "subnet",   "x": 500, "y": 350},
            {"id": "n-pub-az3",  "label": "Public Subnet AZ3",  "type": "subnet",   "x": 800, "y": 350},
            {"id": "n-prv-az1",  "label": "Private Subnet AZ1", "type": "subnet",   "x": 200, "y": 500},
            {"id": "n-prv-az2",  "label": "Private Subnet AZ2", "type": "subnet",   "x": 500, "y": 500},
            {"id": "n-prv-az3",  "label": "Private Subnet AZ3", "type": "subnet",   "x": 800, "y": 500},
            {"id": "n-tgw",      "label": "Transit Gateway",    "type": "gateway",  "x": 500, "y": 650},
            {"id": "n-dx",       "label": "Direct Connect",     "type": "link",     "x": 200, "y": 750},
            {"id": "n-onprem",   "label": "On-Prem K8s",        "type": "datacenter","x": 200, "y": 900},
        ],
        "edges": [
            {"id": "e-igw-pub1", "source": "n-igw",     "target": "n-pub-az1", "type": "internet"},
            {"id": "e-igw-pub2", "source": "n-igw",     "target": "n-pub-az2", "type": "internet"},
            {"id": "e-igw-pub3", "source": "n-igw",     "target": "n-pub-az3", "type": "internet"},
            {"id": "e-nat1-prv1","source": "n-nat-az1", "target": "n-prv-az1", "type": "nat"},
            {"id": "e-nat2-prv2","source": "n-nat-az2", "target": "n-prv-az2", "type": "nat"},
            {"id": "e-nat3-prv3","source": "n-nat-az3", "target": "n-prv-az3", "type": "nat"},
            {"id": "e-tgw-dx",   "source": "n-tgw",     "target": "n-dx",      "type": "transit"},
            {"id": "e-dx-onprem","source": "n-dx",      "target": "n-onprem",  "type": "directconnect"},
        ],
    }
    ndc = _ndc_conn()
    try:
        ndc.execute(
            "INSERT OR IGNORE INTO topologies (id, name, description, graph_json, classification) VALUES (?,?,?,?,?)",
            (_NDC_TOPO_ID, "AWS Landing Zone — Analytics VPC",
             "AWS VPC: 3 AZs, public/private subnets, IGW, NAT, Transit Gateway, Direct Connect to on-prem K8s",
             json.dumps(graph), "CUI // SP-CTI"),
        )
        ndc.commit()
    finally:
        ndc.close()
    return _NDC_TOPO_ID


# ── MC seed ──────────────────────────────────────────────────────────────────

def _seed_mc(project_def: dict, ddc_id: str, idc_id: str, ndc_id: str) -> None:
    mc = _mc_conn()
    try:
        # 1. Session (app inventory bucket)
        mc.execute(
            "INSERT OR IGNORE INTO mc_srv_sessions "
            "(id, migration_type, tgt_platform, tgt_region, status, classification) VALUES (?,?,?,?,?,?)",
            (_SESSION_ID, "app_cloud_migration", "aws", "us-east-1", "completed", "CUI // SP-CTI"),
        )

        # 2. App inventory — one row per source component
        source_stack = project_def.get("source_stack", [])
        app_map: dict[str, str] = {}  # tech → app_id
        app_configs = {
            "oracle": {
                "name": "Oracle DB 19c", "app_type": "database", "language": "SQL",
                "framework": "Oracle", "migration_strategy": "rearchitect",
                "criticality": "mission_critical", "environment": "production",
            },
            "redis": {
                "name": "Redis 7 Cache", "app_type": "database", "language": "scripting",
                "framework": "Redis", "migration_strategy": "replatform",
                "criticality": "high", "environment": "production",
            },
            "elasticsearch": {
                "name": "Elasticsearch 8", "app_type": "database", "language": "JSON",
                "framework": "Elasticsearch", "migration_strategy": "replatform",
                "criticality": "high", "environment": "production",
            },
            "nifi": {
                "name": "Apache NiFi 1.25", "app_type": "middleware", "language": "java",
                "framework": "Apache NiFi", "migration_strategy": "rearchitect",
                "criticality": "high", "environment": "production",
            },
            "react": {
                "name": "React 18 Frontend", "app_type": "web", "language": "javascript",
                "framework": "React", "migration_strategy": "replatform",
                "criticality": "medium", "environment": "production",
            },
            "kubernetes": {
                "name": "Kubernetes 1.28 Infra", "app_type": "middleware", "language": "yaml",
                "framework": "Kubernetes", "migration_strategy": "rehost",
                "criticality": "mission_critical", "environment": "production",
            },
        }
        for item in source_stack:
            tech = item.get("tech", "")
            cfg = app_configs.get(tech)
            if not cfg:
                continue
            app_id = f"app-{_PROJECT_ID}-{tech}"
            app_map[tech] = app_id
            mc.execute(
                "INSERT OR IGNORE INTO mc_app_inventory "
                "(id, session_id, name, version, language, framework, app_type, "
                " migration_strategy, criticality, environment, migration_status, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    app_id, _SESSION_ID, cfg["name"], item.get("version", ""),
                    cfg["language"], cfg["framework"], cfg["app_type"],
                    cfg["migration_strategy"], cfg["criticality"], cfg["environment"],
                    "planned", "CUI // SP-CTI",
                ),
            )

        # 3. App data sources (oracle, redis, elasticsearch)
        data_source_cfgs = [
            {
                "tech": "oracle", "source_type": "oracle", "port": 1521,
                "database_name": "ORCL", "schema_version": "19c",
                "estimated_size_gb": 450.0, "migration_method": "cdc",
            },
            {
                "tech": "redis", "source_type": "redis", "port": 6379,
                "database_name": "0", "schema_version": "7.2",
                "estimated_size_gb": 12.0, "migration_method": "dump_restore",
            },
            {
                "tech": "elasticsearch", "source_type": "elasticsearch", "port": 9200,
                "database_name": "analytics", "schema_version": "8.11",
                "estimated_size_gb": 280.0, "migration_method": "dump_restore",
            },
        ]
        for ds_cfg in data_source_cfgs:
            tech = ds_cfg["tech"]
            if tech not in app_map:
                continue
            mc.execute(
                "INSERT OR IGNORE INTO mc_app_data_sources "
                "(id, app_id, source_type, port, database_name, schema_version, "
                " estimated_size_gb, migration_method, migration_status) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"ds-{_PROJECT_ID}-{tech}", app_map[tech],
                    ds_cfg["source_type"], ds_cfg["port"], ds_cfg["database_name"],
                    ds_cfg["schema_version"], ds_cfg["estimated_size_gb"],
                    ds_cfg["migration_method"], "planned",
                ),
            )

        # 4. Data migration records (oracle → aurora, redis → elasticache)
        if "oracle" in app_map:
            mc.execute(
                "INSERT OR IGNORE INTO mc_data_migration "
                "(id, session_id, app_id, source_type, source_db, target_type, target_db, "
                " migration_method, estimated_size_gb, cutover_type, status, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"dm-{_PROJECT_ID}-oracle", _SESSION_ID, app_map["oracle"],
                    "oracle", "ORCL", "aurora_postgresql", "analytics",
                    "aws_dms", 450.0, "online_with_cdc", "planned", "CUI",
                ),
            )
        if "redis" in app_map:
            mc.execute(
                "INSERT OR IGNORE INTO mc_data_migration "
                "(id, session_id, app_id, source_type, source_db, target_type, target_db, "
                " migration_method, estimated_size_gb, cutover_type, status, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"dm-{_PROJECT_ID}-redis", _SESSION_ID, app_map["redis"],
                    "redis", "db0", "elasticache_redis", "cluster",
                    "dump_restore", 12.0, "offline", "planned", "CUI",
                ),
            )

        # 4b. Anchor migration_designs row (satisfies FK on mc_wave_plans.design_id)
        mc.execute(
            "INSERT OR IGNORE INTO migration_designs "
            "(id, name, description, migration_type, classification) VALUES (?,?,?,?,?)",
            (
                _PROJECT_ID,
                project_def.get("name", "Analytics Platform — K8s to AWS Cloud Native"),
                "CAM project anchor — wave plans reference this design ID",
                "application", "CUI // SP-CTI",
            ),
        )

        # 5. Wave plans (waves 1–3; wave 0 is assessment / pre-work)
        wave_configs = [
            {"num": 1, "name": "Wave 1 — Foundation & Data Tier",  "strategy": "replatform", "estimated_hours": 240, "risk_score": 0.65},
            {"num": 2, "name": "Wave 2 — Integration & App Tier",  "strategy": "rearchitect", "estimated_hours": 320, "risk_score": 0.70},
            {"num": 3, "name": "Wave 3 — AI Modernization & Cutover", "strategy": "rearchitect", "estimated_hours": 160, "risk_score": 0.45},
        ]
        for wc in wave_configs:
            mc.execute(
                "INSERT OR IGNORE INTO mc_wave_plans "
                "(id, design_id, wave_number, name, strategy, status, estimated_hours, risk_score) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"wave-{_PROJECT_ID}-{wc['num']}", _PROJECT_ID,
                    wc["num"], wc["name"], wc["strategy"], "planned",
                    wc["estimated_hours"], wc["risk_score"],
                ),
            )

        # 6. Project row
        source_stack_json = json.dumps([
            {"tech": s.get("tech"), "version": s.get("version"), "platform": s.get("platform")}
            for s in source_stack
        ])
        target_stack_json = json.dumps([
            {"tech": "aurora-postgresql", "version": "15.4", "platform": "aws"},
            {"tech": "elasticache-redis", "version": "7.1",  "platform": "aws"},
            {"tech": "opensearch",        "version": "2.11", "platform": "aws"},
            {"tech": "glue",              "version": "4.0",  "platform": "aws"},
            {"tech": "msk-kafka",         "version": "3.5",  "platform": "aws"},
            {"tech": "amplify",           "version": "Gen2",  "platform": "aws"},
            {"tech": "eks-fargate",       "version": "1.29", "platform": "aws"},
        ])
        mc.execute(
            "INSERT OR IGNORE INTO mc_projects "
            "(id, name, description, customer, status, owner, classification, impact_level, "
            " mc_session_id, ddc_design_id, idc_design_id, ndc_topology_id, "
            " source_stack_json, target_stack_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _PROJECT_ID,
                project_def.get("name", "Analytics Platform — K8s to AWS Cloud Native"),
                project_def.get("description", ""),
                project_def.get("customer", "Demo Customer"),
                project_def.get("status", "active"),
                project_def.get("owner", "cloud-ops"),
                project_def.get("classification", "CUI // SP-CTI"),
                project_def.get("impact_level", "IL4"),
                _SESSION_ID, ddc_id, idc_id, ndc_id,
                source_stack_json, target_stack_json,
            ),
        )

        # 7. Phases + SOP links
        for phase_def in project_def.get("phases", []):
            phase_id = f"phase-{_PROJECT_ID}-{phase_def['phase_num']}"
            mc.execute(
                "INSERT OR IGNORE INTO mc_project_phases "
                "(id, project_id, phase_num, title, description, wave_num, "
                " duration_days, maintenance_window, rollback_criteria, status, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    phase_id, _PROJECT_ID,
                    phase_def["phase_num"],
                    phase_def.get("title", ""),
                    phase_def.get("description", "").strip(),
                    phase_def.get("wave_num", 0),
                    phase_def.get("duration_days", 0),
                    phase_def.get("maintenance_window", ""),
                    phase_def.get("rollback_criteria", ""),
                    "planned", "CUI // SP-CTI",
                ),
            )
            # SOP links
            for order, kw_group in enumerate(phase_def.get("sop_keywords", []), start=1):
                if isinstance(kw_group, str):
                    kw_group = [kw_group]
                sop_id, sop_title, sop_source = _find_sop_any_canvas(*kw_group)
                if not sop_id:
                    logger.debug("Phase %d: no SOP found for keywords %s", phase_def["phase_num"], kw_group)
                    continue
                link_id = f"ps-{phase_id}-{order}"
                mc.execute(
                    "INSERT OR IGNORE INTO mc_project_phase_sops "
                    "(id, phase_id, project_id, sop_source, sop_id, sop_title, display_order) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (link_id, phase_id, _PROJECT_ID, sop_source, sop_id, sop_title, order),
                )

        # 8. AI opportunities
        for ai_def in project_def.get("ai_opportunities", []):
            tech = ai_def.get("component", "")
            app_id = app_map.get(tech)
            mc.execute(
                "INSERT OR IGNORE INTO mc_ai_opportunities "
                "(id, project_id, app_id, component_name, opportunity_title, "
                " aws_ai_service, benefit_category, effort_days, status, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"ai-{_PROJECT_ID}-{tech}",
                    _PROJECT_ID, app_id, tech,
                    ai_def.get("opportunity_title", ""),
                    ai_def.get("aws_ai_service", ""),
                    ai_def.get("benefit_category", ""),
                    ai_def.get("effort_days", 0),
                    "identified",
                    ai_def.get("notes", ""),
                ),
            )

        mc.commit()
    finally:
        mc.close()


# ── Entry point ──────────────────────────────────────────────────────────────

def seed(project_slug: str = "analytics-k8s-aws", reset: bool = False) -> dict:
    """Seed the demo migration project. Returns summary dict."""
    # First ensure SOPs are seeded
    from tools.migration_canvas.cam_seed_sops import seed as seed_sops
    seed_sops()

    # Load project definition
    yaml_path = _PROJECTS_DIR / f"{project_slug}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"Project definition not found: {yaml_path}")
    project_def = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    if reset:
        _reset(project_def.get("id", _PROJECT_ID))

    # Check if already seeded
    mc = _mc_conn()
    try:
        existing = mc.execute("SELECT id FROM mc_projects WHERE id=?", (_PROJECT_ID,)).fetchone()
    finally:
        mc.close()

    if existing and not reset:
        logger.info("Project %s already seeded — skipping (use --reset to re-seed)", _PROJECT_ID)
        return {"status": "already_exists", "project_id": _PROJECT_ID}

    ddc_id = _seed_ddc()
    idc_id = _seed_idc()
    ndc_id = _seed_ndc()
    _seed_mc(project_def, ddc_id, idc_id, ndc_id)

    # Count what was seeded
    mc = _mc_conn()
    try:
        phase_count = mc.execute(
            "SELECT COUNT(*) FROM mc_project_phases WHERE project_id=?", (_PROJECT_ID,)
        ).fetchone()[0]
        sop_link_count = mc.execute(
            "SELECT COUNT(*) FROM mc_project_phase_sops WHERE project_id=?", (_PROJECT_ID,)
        ).fetchone()[0]
        ai_count = mc.execute(
            "SELECT COUNT(*) FROM mc_ai_opportunities WHERE project_id=?", (_PROJECT_ID,)
        ).fetchone()[0]
        app_count = mc.execute(
            "SELECT COUNT(*) FROM mc_app_inventory WHERE session_id=?", (_SESSION_ID,)
        ).fetchone()[0]
    finally:
        mc.close()

    return {
        "status": "ok",
        "project_id": _PROJECT_ID,
        "phases": phase_count,
        "sop_links": sop_link_count,
        "ai_opportunities": ai_count,
        "app_components": app_count,
        "ddc_design_id": ddc_id,
        "idc_design_id": idc_id,
        "ndc_topology_id": ndc_id,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Seed the Analytics Platform K8s→AWS demo project")
    p.add_argument("--project", default="analytics-k8s-aws", help="Project slug (YAML filename without .yaml)")
    p.add_argument("--reset", action="store_true", help="Delete existing data before seeding")
    p.add_argument("--json", dest="as_json", action="store_true", help="Output results as JSON")
    args = p.parse_args()

    result = seed(project_slug=args.project, reset=args.reset)

    if args.as_json:
        print(json.dumps(result))
    else:
        if result["status"] == "already_exists":
            print(f"Project {result['project_id']} already exists — use --reset to re-seed")
        else:
            print(f"Seeded project: {result['project_id']}")
            print(f"  Phases:           {result['phases']}")
            print(f"  SOP links:        {result['sop_links']}")
            print(f"  AI opportunities: {result['ai_opportunities']}")
            print(f"  App components:   {result['app_components']}")
            print(f"  DDC design:       {result['ddc_design_id']}")
            print(f"  IDC design:       {result['idc_design_id']}")
            print(f"  NDC topology:     {result['ndc_topology_id']}")


if __name__ == "__main__":
    main()
