# CUI // SP-CTI
"""ICDEV™ Data Design Canvas — Data Assessment Engine.

Pure functions for data compliance assessment, classification coverage,
gap detection, and control mapping against data design graphs.

No Flask dependency — takes graph data and returns results.
No LLM dependency — all checks are deterministic.
"""

from datetime import datetime, timezone
from pathlib import Path

from tools.data_canvas.constants import (
    DATA_COMPLIANCE_RULES,
    DATA_NIST_FAMILIES,
    EDGE_TYPE_COLUMN_LINEAGE,
)
from tools.data_canvas.lineage import (
    build_column_lineage_dag,
    compute_downstream_impact,
    compute_upstream_provenance,
    generate_contract_assertions,
    summarize_lineage,
    validate_lineage_edge,
)

try:
    import yaml as _yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "args" / "data_canvas_config.yaml"


def _load_config() -> dict:
    """Load DDC engine config from args/data_canvas_config.yaml."""
    if not _HAS_YAML or not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
        return _yaml.safe_load(_f) or {}


_DDC_CONFIG = _load_config()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _node_types(nodes):
    """Return {node_id: node_type} dict."""
    return {n["id"]: n.get("type", "") for n in nodes}


def _label_map(nodes):
    """Return {node_id: label} dict."""
    return {n["id"]: n.get("label", n["id"]) for n in nodes}


def _build_adjacency(edges):
    """Build undirected adjacency: {node_id: {neighbor_id, ...}}."""
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    return adj


def _is_entity(ntype):
    """Check if a node type is a data entity."""
    return ntype.startswith("ent-")


def _is_column(ntype):
    """Check if a node type is a column."""
    return ntype.startswith("col-")


def _is_flow(ntype):
    """Check if a node type is a data flow."""
    return ntype.startswith("flow-")


def _is_control(ntype):
    """Check if a node type is a data control."""
    return ntype.startswith("ctrl-")


def _is_boundary(ntype):
    """Check if a node type is a boundary."""
    return ntype.startswith("bnd-")


def _get_entity_nodes(nodes, ntypes):
    """Return list of entity nodes."""
    return [n for n in nodes if _is_entity(ntypes.get(n["id"], ""))]


def _get_column_children(entity_id, edges, ntypes):
    """Return column node IDs attached to an entity via edges."""
    cols = set()
    for e in edges:
        if e["source"] == entity_id and _is_column(ntypes.get(e["target"], "")):
            cols.add(e["target"])
        if e["target"] == entity_id and _is_column(ntypes.get(e["source"], "")):
            cols.add(e["source"])
    return cols


def _entity_has_column_type(entity_id, col_type, edges, ntypes):
    """Check if an entity has a column of a specific type connected."""
    col_ids = _get_column_children(entity_id, edges, ntypes)
    return any(ntypes.get(cid, "") == col_type for cid in col_ids)


def _entity_in_boundary(entity_id, boundaries, boundary_type=None):
    """Check if an entity is contained within a boundary (optionally of a specific type)."""
    for b in boundaries:
        if boundary_type and b.get("type") != boundary_type:
            continue
        contained = b.get("contained_nodes", [])
        if entity_id in contained:
            return True
    return False


def _entity_classification_zone(entity_id, boundaries):
    """Return the classification zone boundary containing this entity, or None."""
    for b in boundaries:
        if b.get("type") == "bnd-classification":
            if entity_id in b.get("contained_nodes", []):
                return b
    return None


def _flow_crosses_classification(edge, boundaries):
    """Check if a flow edge crosses between different classification zones."""
    src = edge["source"]
    tgt = edge["target"]
    src_zones = set()
    tgt_zones = set()
    for b in boundaries:
        if b.get("type") != "bnd-classification":
            continue
        contained = b.get("contained_nodes", [])
        if src in contained:
            src_zones.add(b["id"])
        if tgt in contained:
            tgt_zones.add(b["id"])
    if not src_zones and not tgt_zones:
        return False
    return src_zones != tgt_zones


def _posture_grade(risk_score):
    """Convert a risk score (0-100) to a letter grade."""
    if risk_score >= 90:
        return "A"
    elif risk_score >= 75:
        return "B"
    elif risk_score >= 60:
        return "C"
    elif risk_score >= 40:
        return "D"
    else:
        return "F"


# ── Individual Check Functions ───────────────────────────────────────────────


def _check_cui_encrypted_at_rest(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-ENC-001: CUI entities must have encryption control connected."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    for ent in entities:
        has_cui = _entity_has_column_type(ent["id"], "col-cui", edges, ntypes)
        if not has_cui:
            continue
        neighbors = adj.get(ent["id"], set())
        has_enc = any(ntypes.get(nb, "") == "ctrl-encryption" for nb in neighbors)
        if not has_enc:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_secret_encryption(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-ENC-002: SECRET zone entities must have encryption control."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    for ent in entities:
        zone = _entity_classification_zone(ent["id"], boundaries)
        if not zone:
            continue
        zone_label = (zone.get("label", "") or "").upper()
        if "SECRET" not in zone_label:
            continue
        neighbors = adj.get(ent["id"], set())
        has_enc = any(ntypes.get(nb, "") == "ctrl-encryption" for nb in neighbors)
        if not has_enc:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_cross_domain_guard(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-ENC-003: Flows crossing classification zones must use cross-domain flow."""
    findings = []
    for e in edges:
        src_t = ntypes.get(e["source"], "")
        tgt_t = ntypes.get(e["target"], "")
        # Only check flows between two entities or flow nodes — skip column/control edges
        src_relevant = _is_entity(src_t) or _is_flow(src_t)
        tgt_relevant = _is_entity(tgt_t) or _is_flow(tgt_t)
        if not (src_relevant and tgt_relevant):
            continue
        if not _flow_crosses_classification(e, boundaries):
            continue
        # Flow crosses classification — must be a cross-domain flow type
        edge_type = e.get("type", "")
        if edge_type != "flow-cross-domain":
            findings.append(
                {
                    "affected_entity": f"{labels.get(e['source'], '')} -> {labels.get(e['target'], '')}",
                    "affected_type": "edge",
                }
            )
    return findings


def _check_pii_masking(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-ACC-001: Entities with PII columns should have masking control."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    for ent in entities:
        has_pii = _entity_has_column_type(ent["id"], "col-pii", edges, ntypes)
        if not has_pii:
            continue
        neighbors = adj.get(ent["id"], set())
        has_mask = any(ntypes.get(nb, "") == "ctrl-masking" for nb in neighbors)
        if not has_mask:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_rbac_enforced(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-ACC-002: All entities should have RBAC control connected."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    # Global RBAC nodes cover all entities
    rbac_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "ctrl-rbac"}
    for ent in entities:
        neighbors = adj.get(ent["id"], set())
        has_rbac = bool(neighbors & rbac_ids)
        if not has_rbac:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_audit_logging_classified(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-AUD-001: CUI/SECRET entities must have audit logging control."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    audit_log_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "ctrl-audit-log"}
    for ent in entities:
        has_cui = _entity_has_column_type(ent["id"], "col-cui", edges, ntypes)
        has_secret = _entity_has_column_type(ent["id"], "col-secret", edges, ntypes)
        in_secret_zone = False
        zone = _entity_classification_zone(ent["id"], boundaries)
        if zone:
            zone_label = (zone.get("label", "") or "").upper()
            in_secret_zone = "SECRET" in zone_label or "CUI" in zone_label
        if not (has_cui or has_secret or in_secret_zone):
            continue
        neighbors = adj.get(ent["id"], set())
        has_audit = bool(neighbors & audit_log_ids)
        if not has_audit:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_audit_columns_present(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-AUD-002: All entities should include audit trail columns."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    for ent in entities:
        has_audit_col = _entity_has_column_type(ent["id"], "col-audit", edges, ntypes)
        if not has_audit_col:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_retention_policy(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-RET-001: All entities should have a retention policy control."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    retention_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "ctrl-retention"}
    for ent in entities:
        neighbors = adj.get(ent["id"], set())
        has_ret = bool(neighbors & retention_ids)
        if not has_ret:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_backup_policy(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-BAK-001: Production entities must have backup policy control."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    backup_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "ctrl-backup-policy"}
    for ent in entities:
        neighbors = adj.get(ent["id"], set())
        has_bak = bool(neighbors & backup_ids)
        if not has_bak:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_entity_classified(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-CLS-001: Every entity must reside within a classification zone boundary."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    for ent in entities:
        in_zone = _entity_in_boundary(ent["id"], boundaries, "bnd-classification")
        if not in_zone:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_data_residency(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-SOV-001: PII/PHI/CUI entities should be in a data residency boundary."""
    findings = []
    entities = _get_entity_nodes(nodes, ntypes)
    for ent in entities:
        has_pii = _entity_has_column_type(ent["id"], "col-pii", edges, ntypes)
        has_phi = _entity_has_column_type(ent["id"], "col-phi", edges, ntypes)
        has_cui = _entity_has_column_type(ent["id"], "col-cui", edges, ntypes)
        if not (has_pii or has_phi or has_cui):
            continue
        in_region = _entity_in_boundary(ent["id"], boundaries, "bnd-region")
        if not in_region:
            findings.append(
                {
                    "affected_entity": labels.get(ent["id"], ent["id"]),
                    "affected_type": "node",
                }
            )
    return findings


def _check_dlp_on_egress(nodes, edges, boundaries, ntypes, labels, adj):
    """DDC-DLP-001: Data export and API egress flows should have DLP policy."""
    findings = []
    dlp_ids = {n["id"] for n in nodes if ntypes.get(n["id"]) == "ctrl-dlp"}
    if dlp_ids:
        return findings  # DLP control exists in design — pass
    # Check if there are export/API flows leaving classification zones
    for e in edges:
        edge_type = e.get("type", "")
        if edge_type in ("flow-export", "flow-api"):
            if _flow_crosses_classification(e, boundaries):
                findings.append(
                    {
                        "affected_entity": f"{labels.get(e['source'], '')} -> {labels.get(e['target'], '')}",
                        "affected_type": "edge",
                    }
                )
    return findings


# Map check names to functions
_CHECK_DISPATCH = {
    "cui_encrypted_at_rest": _check_cui_encrypted_at_rest,
    "secret_encryption": _check_secret_encryption,
    "cross_domain_guard": _check_cross_domain_guard,
    "pii_masking": _check_pii_masking,
    "rbac_enforced": _check_rbac_enforced,
    "audit_logging_classified": _check_audit_logging_classified,
    "audit_columns_present": _check_audit_columns_present,
    "retention_policy": _check_retention_policy,
    "backup_policy": _check_backup_policy,
    "entity_classified": _check_entity_classified,
    "data_residency": _check_data_residency,
    "dlp_on_egress": _check_dlp_on_egress,
}


# ── Main Assessment ──────────────────────────────────────────────────────────


def assess_data_design(design_id, graph_data, rules=None):
    """Evaluate data compliance rules against a data design.

    Args:
        design_id: UUID of the data design.
        graph_data: Dict with "nodes", "edges", and "boundaries".
        rules: Optional list of rules (defaults to DATA_COMPLIANCE_RULES).

    Returns:
        Dict with findings, scores by category, risk_score, and posture_grade.
        When auto_detect_pii is enabled the result also contains a
        ``pii_scan`` key with the raw output from ``pii_scanner.scan_graph``.
    """
    if rules is None:
        rules = DATA_COMPLIANCE_RULES

    # ── PII auto-detection ──────────────────────────────────────────────────
    pii_scan: dict = {}
    auto_detect_pii = _DDC_CONFIG.get("classification", {}).get("auto_detect_pii", False)
    if auto_detect_pii:
        try:
            from tools.data_canvas.pii_scanner import scan_graph as _scan_pii

            pii_scan = _scan_pii(graph_data)
            # Use the auto-tagged node list so downstream checks see col-pii
            # on columns the scanner promoted (non-mutating — pii_scanner
            # returns a fresh list, original graph_data is unchanged).
            if pii_scan.get("auto_tagged_nodes"):
                graph_data = dict(graph_data, nodes=pii_scan["auto_tagged_nodes"])
        except Exception:  # noqa: BLE001
            pass  # scanner unavailable — degrade gracefully

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    boundaries = graph_data.get("boundaries", [])
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)
    adj = _build_adjacency(edges)

    findings = []

    for rule in rules:
        check_fn = _CHECK_DISPATCH.get(rule["check"])
        if not check_fn:
            continue
        rule_findings = check_fn(nodes, edges, boundaries, ntypes, labels, adj)
        for rf in rule_findings:
            findings.append(
                {
                    "rule_id": rule["id"],
                    "title": rule["title"],
                    "severity": rule["severity"],
                    "category": rule["category"],
                    "description": rule["description"],
                    "affected_entity": rf["affected_entity"],
                    "affected_type": rf["affected_type"],
                }
            )

    # Compute scores per category
    total_rules = len(rules)
    scores = {}
    for cat in {r["category"] for r in rules}:
        cat_rules = [r for r in rules if r["category"] == cat]
        cat_findings = [f for f in findings if f["category"] == cat]
        # Deduplicate by rule_id for scoring (one rule can fire multiple times)
        cat_failed_rules = len({f["rule_id"] for f in cat_findings})
        scores[cat] = {
            "total": len(cat_rules),
            "passed": len(cat_rules) - cat_failed_rules,
            "failed": cat_failed_rules,
        }

    # Merge PII scanner compliance findings (if any) into the findings list.
    # These are appended after rule-based findings so they don't affect
    # per-rule pass/fail scoring — they are informational auto-detections.
    pii_findings = pii_scan.get("compliance_findings", [])
    all_findings = findings + pii_findings

    # Risk score: 100 - (weighted findings penalty)
    severity_weights = {"CAT1": 10, "CAT2": 5, "CAT3": 2}
    penalty = sum(severity_weights.get(f["severity"], 2) for f in all_findings)
    risk_score = max(0.0, min(100.0, 100.0 - penalty))

    result = {
        "design_id": design_id,
        "findings": all_findings,
        "total_rules": total_rules,
        "passed": total_rules - len({f["rule_id"] for f in findings}),
        "failed": len({f["rule_id"] for f in findings}),
        "finding_count": len(all_findings),
        "scores": scores,
        "risk_score": round(risk_score, 1),
        "posture_grade": _posture_grade(risk_score),
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }
    if pii_scan:
        result["pii_scan"] = {
            "total_scanned": pii_scan.get("total_scanned", 0),
            "pii_detected": pii_scan.get("pii_detected", 0),
            "tagged_columns": pii_scan.get("tagged_columns", []),
            "scanned_at": pii_scan.get("scanned_at", ""),
        }
    return result


# ── Classification Coverage ──────────────────────────────────────────────────


def compute_classification_coverage(graph_data):
    """Compute what percentage of entities are inside classification zones.

    Args:
        graph_data: Dict with "nodes", "edges", and "boundaries".

    Returns:
        Dict with total_entities, classified, unclassified, coverage_pct,
        and per_zone breakdown.
    """
    nodes = graph_data.get("nodes", [])
    boundaries = graph_data.get("boundaries", [])
    ntypes = _node_types(nodes)
    labels = _label_map(nodes)

    entities = _get_entity_nodes(nodes, ntypes)
    total = len(entities)
    if total == 0:
        return {
            "total_entities": 0,
            "classified": 0,
            "unclassified": 0,
            "coverage_pct": 100.0,
            "zones": [],
            "unclassified_entities": [],
        }

    classified_ids = set()
    zone_breakdown = []

    for b in boundaries:
        if b.get("type") != "bnd-classification":
            continue
        contained = b.get("contained_nodes", [])
        zone_entities = [eid for eid in contained if _is_entity(ntypes.get(eid, ""))]
        classified_ids.update(zone_entities)
        zone_breakdown.append(
            {
                "zone_id": b["id"],
                "zone_label": b.get("label", b["id"]),
                "entity_count": len(zone_entities),
                "entities": [labels.get(eid, eid) for eid in zone_entities],
            }
        )

    classified_count = len(classified_ids)
    unclassified_ids = [ent["id"] for ent in entities if ent["id"] not in classified_ids]

    return {
        "total_entities": total,
        "classified": classified_count,
        "unclassified": total - classified_count,
        "coverage_pct": round((classified_count / total) * 100, 1),
        "zones": zone_breakdown,
        "unclassified_entities": [labels.get(eid, eid) for eid in unclassified_ids],
    }


# ── Gap Detection ────────────────────────────────────────────────────────────


def detect_data_gaps(assessment_result):
    """Identify missing controls and gaps from an assessment result.

    Args:
        assessment_result: Dict returned by assess_data_design().

    Returns:
        List of gap dicts with gap_type, severity, description, affected,
        and recommended_control.
    """
    findings = assessment_result.get("findings", [])
    gaps = []
    seen_rules = set()

    # Map rule IDs to recommended controls
    _RULE_RECOMMENDATIONS = {
        "DDC-ENC-001": ("ctrl-encryption", "Add encryption control (TDE/KMS) to CUI entities"),
        "DDC-ENC-002": ("ctrl-encryption", "Add NSA Type 1 / Suite B encryption to SECRET entities"),
        "DDC-ENC-003": ("flow-cross-domain", "Replace direct flows with cross-domain solution (CDS/guard)"),
        "DDC-ACC-001": ("ctrl-masking", "Add data masking/tokenization control for PII entities"),
        "DDC-ACC-002": ("ctrl-rbac", "Add RBAC control to restrict data access"),
        "DDC-AUD-001": ("ctrl-audit-log", "Add audit logging control for classified entities"),
        "DDC-AUD-002": ("col-audit", "Add audit trail columns (created_at, updated_by)"),
        "DDC-RET-001": ("ctrl-retention", "Add retention policy control with purge schedule"),
        "DDC-BAK-001": ("ctrl-backup-policy", "Add backup policy control with RTO/RPO targets"),
        "DDC-CLS-001": ("bnd-classification", "Place entity inside a classification zone boundary"),
        "DDC-SOV-001": ("bnd-region", "Place entity inside a data residency boundary"),
        "DDC-DLP-001": ("ctrl-dlp", "Add DLP policy control for egress data flows"),
    }

    for f in findings:
        rule_id = f["rule_id"]
        if rule_id in seen_rules:
            continue
        seen_rules.add(rule_id)
        rec = _RULE_RECOMMENDATIONS.get(rule_id, ("", ""))
        gaps.append(
            {
                "gap_type": "missing_control" if rec[0].startswith("ctrl-") else "missing_boundary",
                "rule_id": rule_id,
                "severity": f["severity"],
                "description": f.get("description", f["title"]),
                "affected": f["affected_entity"],
                "recommended_control": rec[0],
                "recommendation": rec[1],
            }
        )

    return gaps


# ── NIST Coverage ────────────────────────────────────────────────────────────


def compute_nist_coverage(graph_data):
    """Compute NIST 800-53 control family coverage for data design.

    Args:
        graph_data: Dict with "nodes", "edges", and "boundaries".

    Returns:
        Dict with families, overall_coverage_pct, total_families, covered_families.
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    ntypes = _node_types(nodes)
    type_set = set(ntypes.values())

    # Map control types to NIST families they cover
    _CTRL_FAMILY_MAP = {
        "ctrl-encryption": [("SC", 40), ("MP", 20)],
        "ctrl-masking": [("PT", 40), ("MP", 20)],
        "ctrl-dlp": [("MP", 40), ("SI", 15)],
        "ctrl-rbac": [("AC", 50), ("IA", 15)],
        "ctrl-audit-log": [("AU", 50)],
        "ctrl-retention": [("MP", 20), ("SI", 15)],
        "ctrl-classification": [("MP", 30), ("AC", 10)],
        "ctrl-backup-policy": [("CP", 50)],
    }

    family_coverage = {}
    for fam_code in DATA_NIST_FAMILIES:
        family_coverage[fam_code] = {"pct": 0, "controls": []}

    for ctrl_type, mappings in _CTRL_FAMILY_MAP.items():
        if ctrl_type in type_set:
            for fam_code, pct_add in mappings:
                if fam_code in family_coverage:
                    family_coverage[fam_code]["pct"] = min(100, family_coverage[fam_code]["pct"] + pct_add)
                    family_coverage[fam_code]["controls"].append(ctrl_type)

    # Edges contribute: authenticated flows boost IA, encrypted edges boost SC
    has_authenticated = any(e.get("authenticated", False) for e in edges)
    has_encrypted = any(e.get("encrypted", False) for e in edges)
    if has_authenticated:
        family_coverage["IA"]["pct"] = min(100, family_coverage["IA"]["pct"] + 30)
    if has_encrypted:
        family_coverage["SC"]["pct"] = min(100, family_coverage["SC"]["pct"] + 20)

    # Boundaries contribute
    boundary_types = {ntypes.get(n["id"], "") for n in nodes if _is_boundary(ntypes.get(n["id"], ""))}
    if "bnd-classification" in boundary_types:
        family_coverage["MP"]["pct"] = min(100, family_coverage["MP"]["pct"] + 20)
    if "bnd-region" in boundary_types:
        family_coverage["PT"]["pct"] = min(100, family_coverage["PT"]["pct"] + 20)

    covered = sum(1 for v in family_coverage.values() if v["pct"] > 0)
    total_pct = sum(v["pct"] for v in family_coverage.values())
    total_families = len(DATA_NIST_FAMILIES)

    families = []
    for fam_code, fam_name in DATA_NIST_FAMILIES.items():
        fc = family_coverage.get(fam_code, {"pct": 0, "controls": []})
        families.append(
            {
                "code": fam_code,
                "name": fam_name,
                "coverage_pct": fc["pct"],
                "controls": fc["controls"],
            }
        )

    return {
        "families": families,
        "overall_coverage_pct": round(total_pct / total_families, 1) if total_families else 0,
        "total_families": total_families,
        "covered_families": covered,
    }


# ── Column-Level Lineage Analysis ─────────────────────────────────────────────


def analyze_column_lineage(
    design_id: str,
    graph_data: dict,
    lineage_records: list[dict],
) -> dict:
    """Run full column-level lineage analysis for a data design.

    Combines the lineage DAG, summary metrics, and data contract assertions
    into a single result dict suitable for API responses and impact reports.

    Args:
        design_id: UUID of the data design.
        graph_data: Dict with "nodes", "edges", and "boundaries".
        lineage_records: Rows from the ``dd_lineage`` table for this design.

    Returns:
        Dict with:
            ``design_id``           — echoed for correlation
            ``summary``             — output of :func:`summarize_lineage`
            ``dag``                 — output of :func:`build_column_lineage_dag`
            ``contract_assertions`` — list from :func:`generate_contract_assertions`
            ``assertion_count``     — total number of assertions
            ``cat1_assertions``     — CAT1 (blocking) assertion count
            ``analyzed_at``         — ISO-8601 timestamp
    """
    dag = build_column_lineage_dag(lineage_records)
    summary = summarize_lineage(lineage_records, graph_data)
    assertions = generate_contract_assertions(lineage_records, graph_data)

    cat1_count = sum(1 for a in assertions if a.get("severity") == "CAT1")

    return {
        "design_id": design_id,
        "summary": summary,
        "dag": dag,
        "contract_assertions": assertions,
        "assertion_count": len(assertions),
        "cat1_assertions": cat1_count,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }


def get_column_impact(
    design_id: str,
    source_entity_id: str,
    column_name: str,
    lineage_records: list[dict],
    direction: str = "downstream",
    max_depth: int = 20,
) -> dict:
    """Return downstream or upstream impact for a single column.

    Thin wrapper over :func:`compute_downstream_impact` and
    :func:`compute_upstream_provenance` that adds design metadata and validates
    inputs before traversal.

    Args:
        design_id: UUID of the data design.
        source_entity_id: Entity node ID that owns the column.
        column_name: Column name to trace.
        lineage_records: Rows from ``dd_lineage`` for this design.
        direction: ``"downstream"`` (default) or ``"upstream"``.
        max_depth: Maximum hops to traverse.

    Returns:
        Impact result dict with ``design_id`` and ``direction`` added.
    """
    if direction == "upstream":
        result = compute_upstream_provenance(source_entity_id, column_name, lineage_records, max_depth)
    else:
        result = compute_downstream_impact(source_entity_id, column_name, lineage_records, max_depth)

    result["design_id"] = design_id
    result["direction"] = direction
    return result


# ── Schema Introspection Integration ─────────────────────────────────────────


def generate_graph_from_schema(
    dsn: str,
    db_type: str = "auto",
    schema_filter: str | None = None,
) -> dict:
    """Introspect a live database and return a Data Canvas graph dict.

    Thin wrapper over :mod:`tools.data_canvas.introspector` that keeps
    callers within the data_engine API surface.  The returned dict is
    ready to be stored as ``graph_json`` in a ``data_designs`` row.

    Parameters
    ----------
    dsn:
        Database connection string.  Examples:

        * ``/path/to/file.db``  (SQLite)
        * ``sqlite:///path/to/file.db``  (SQLite, explicit prefix)
        * ``postgresql://user:pw@host:5432/dbname``  (PostgreSQL)
        * ``mysql://user:pw@host:3306/dbname``  (MySQL / MariaDB)
        * ``mssql://user:pw@host/dbname``  (SQL Server)
    db_type:
        ``"auto"`` (default, infer from DSN), or one of ``"sqlite"``,
        ``"postgresql"``, ``"mysql"``, ``"sqlserver"``.
    schema_filter:
        Restrict introspection to a specific schema/database (e.g.
        ``"public"`` for PostgreSQL, ``"dbo"`` for SQL Server).

    Returns
    -------
    dict
        Keys: ``nodes``, ``edges``, ``boundaries`` (graph_json-compatible),
        plus ``_meta`` with ``db_type``, ``db_name``, ``table_count``,
        ``column_count``, ``fk_count``.

    Raises
    ------
    ValueError
        For unsupported ``db_type`` values or malformed DSNs.
    RuntimeError
        If the required driver library is not installed.
    """
    from tools.data_canvas.introspector import introspect as _introspect

    return _introspect(dsn, db_type=db_type, schema_filter=schema_filter)


# Re-export lineage primitives so callers only need to import data_engine.
__all__ = [
    # assessment
    "assess_data_design",
    "compute_classification_coverage",
    "detect_data_gaps",
    "compute_nist_coverage",
    # schema introspection
    "generate_graph_from_schema",
    # column lineage
    "analyze_column_lineage",
    "get_column_impact",
    "build_column_lineage_dag",
    "compute_downstream_impact",
    "compute_upstream_provenance",
    "validate_lineage_edge",
    "summarize_lineage",
    "generate_contract_assertions",
    # constants re-exported for convenience
    "EDGE_TYPE_COLUMN_LINEAGE",
]
