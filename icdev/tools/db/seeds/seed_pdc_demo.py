#!/usr/bin/env python3
# CUI // SP-CTI
"""PDC Demo Seed -- populates pipeline_canvas.db with realistic demo data.

Replaces the 7 (copy) duplicate pipelines with 7 distinct, well-described
demo pipelines tied to real programs (BCAP, IL5, FedRAMP, Platform One, etc.)
and populates the related subsystems that are otherwise empty:

  pipelines             7 distinct demo pipelines (no (copy) suffixes)
  pc_projects           4 programs (BCAP, IL5 AI Demo, FedRAMP cATO, Platform One)
  pc_project_pipelines  many-to-many join rows
  pc_stages             ~24 named stages (3-4 per pipeline)
  pc_versions           15 version-history rows (2-3 per pipeline)
  pc_compliance_checks  10 (NIST SSDF, FedRAMP, SLSA checks)
  pc_compliance_findings ~30 findings (CAT1/CAT2/CAT3, mix of open/fixed)
  pc_change_requests    5 (pending + approved CRs)
  pc_boundaries         6 (cross-domain / classification zones)
  pc_collab_sessions    4 (active "users" editing)

Append-only invariant: pc_audit is NEVER touched.

Usage:
  python tools/db/seeds/seed_pdc_demo.py --reset --json
  python tools/db/seeds/seed_pdc_demo.py --verify --json
  python tools/db/seeds/seed_pdc_demo.py --reset
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

random.seed(42)

_NOW = datetime.now(timezone.utc)
_DB = _ROOT / "data" / "pipeline_canvas.db"

# Tables we manage (in FK-safe deletion order: child -> parent)
TABLES = [
    "pc_collab_sessions",
    "pc_change_requests",
    "pc_compliance_findings",
    "pc_compliance_checks",
    "pc_boundaries",
    "pc_versions",
    "pc_project_pipelines",
    "pc_stages",
    "pc_projects",
    "pipelines",
]
# Never touched (append-only / shared content)
PROTECTED = ["pc_audit", "pc_templates", "pc_snippets"]


def _ts(offset_hours: float = 0.0) -> str:
    return (_NOW + timedelta(hours=offset_hours)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _uid() -> str:
    return str(uuid.uuid4())


def _get_conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

# 7 distinct demo pipelines -- each maps to a real template_id in pc_templates
PIPELINES = [
    {
        "id": _uid(),
        "name": "BCAP IL5 -- Web App Boundary",
        "description": (
            "Boundary Cloud Access Point (BCAP) reference build for IL5 cloud-bound "
            "web applications. Implements zero-trust pipeline with signed commits, "
            "hermetic builds, SLSA L3 provenance, Cosign artifact signing, and "
            "Kyverno admission control. Classification: CUI//SP-CTI."
        ),
        "template_id": "tpl-zero-trust-pipeline",
        "classification": "CUI",
        "target_csp": "aws-il5",
    },
    {
        "id": _uid(),
        "name": "FedRAMP Moderate cATO Pipeline",
        "description": (
            "Continuous ATO evidence pipeline for a FedRAMP Moderate SaaS boundary. "
            "OSCAL artifact generation, STIG Manager integration, OpenSCAP scans, "
            "CycloneDX SBOMs, and automated evidence locker. ATO package builder "
            "emits SSP + SAP + SAR + POA&M on demand."
        ),
        "template_id": "tpl-fedramp-cato",
        "classification": "CUI",
        "target_csp": "aws-govcloud",
    },
    {
        "id": _uid(),
        "name": "Platform One -- DoD Software Factory",
        "description": (
            "Reference Platform One build chain for the DoD Software Factory: "
            "self-hosted GitLab, Iron Bank hardened base images, Big Bang K8s "
            "distribution, Twistlock runtime defense, OPA Gatekeeper policy, "
            "STIG Manager attestation, and FIPS-validated cryptography."
        ),
        "template_id": "tpl-airgap-dod",
        "classification": "CUI",
        "target_csp": "onprem-dod",
    },
    {
        "id": _uid(),
        "name": "GitOps Multi-Tenant SaaS",
        "description": (
            "ArgoCD-federated multi-cluster GitOps deployment for a commercial "
            "multi-tenant SaaS. Progressive delivery via Argo Rollouts, App-of-Apps "
            "pattern, sealed-secrets for credential distribution, and per-tenant "
            "ApplicationSets. Targets EKS + AKS + GKE from a single source of truth."
        ),
        "template_id": "tpl-gitops",
        "classification": "CUI",
        "target_csp": "multi-cloud",
    },
    {
        "id": _uid(),
        "name": "SLSA L3 Supply Chain",
        "description": (
            "End-to-end SLSA Level 3 supply chain: hermetic Tekton builds, in-toto "
            "provenance generation, Cosign keyless signing with Fulcio, Rekor "
            "transparency log, Syft SBOM, Grype vulnerability gating, and "
            "admission-time SLSA verifier in Kyverno."
        ),
        "template_id": "tpl-slsa-l3",
        "classification": "CUI",
        "target_csp": "aws",
    },
    {
        "id": _uid(),
        "name": "Hybrid NDC Bridge -- NIPR <-> SIPR",
        "description": (
            "Cross-domain deployment pipeline spanning NIPR (unclass) and SIPR "
            "(secret) via the NDC topology bridge. CDS guard + data diode, separate "
            "Harbor registries per domain, two-person manual approval at the "
            "classification boundary, and STIG-hardened RKE2 clusters on both sides."
        ),
        "template_id": "tpl-hybrid-multicloud",
        "classification": "SECRET",
        "target_csp": "onprem-dod",
    },
    {
        "id": _uid(),
        "name": "NDC Zero-Trust DevSecOps",
        "description": (
            "Flagship ICDEV demo pipeline for the NDC customer briefing: complete "
            "11-stage DevSecOps with SAST/DAST/SCA, container image signing, "
            "supply chain provenance, progressive delivery, runtime monitoring, "
            "and full compliance evidence generation. End-to-end in <18 minutes."
        ),
        "template_id": "tpl-full-devsecops",
        "classification": "CUI",
        "target_csp": "aws",
    },
]

# 4 programs
PROJECTS = [
    {
        "id": _uid(),
        "name": "BCAP Reference Implementation",
        "description": (
            "Defense Information Systems Agency (DISA) Boundary Cloud Access Point "
            "reference implementation. IL5 cloud-bound web applications with "
            "zero-trust posture, quarterly ATO refresh."
        ),
        "status": "active",
        "owner": "DISA / BCAP PMO",
    },
    {
        "id": _uid(),
        "name": "IL5 AI Capabilities Demo",
        "description": (
            "Demonstration of AI/ML workloads on IL5 boundary for the IRAD "
            "AI Capabilities program. CUI//SP-CTI data, model versioning, "
            "and audit-ready inference logs."
        ),
        "status": "active",
        "owner": "ICDEV / IRAD Team",
    },
    {
        "id": _uid(),
        "name": "FedRAMP cATO Acceleration",
        "description": (
            "Continuous ATO program for a civilian agency SaaS platform. "
            "OSCAL-driven evidence collection, monthly continuous monitoring "
            "deliverables, automated POA&M tracking."
        ),
        "status": "active",
        "owner": "Agency CISO Office",
    },
    {
        "id": _uid(),
        "name": "Platform One Adoption",
        "description": (
            "DoD program office adopting Platform One Iron Bank + Big Bang K8s "
            "for mission applications. Multi-program rollout across 3 ILs."
        ),
        "status": "active",
        "owner": "DoD Program Office",
    },
]

# Stages -- 3-4 per pipeline, named and positioned
def _stages_for(pipeline_idx: int, pid: str) -> list[dict]:
    base_y = 80 + pipeline_idx * 40
    stage_defs = [
        ("source", "Source Control", "Signed commits, branch protection, signed tags", 60, base_y),
        ("build", "Build & Test", "Hermetic build, unit + integration tests, SBOM generation", 320, base_y),
        ("scan", "Security Scanning", "SAST, SCA, secrets, container image, IaC, DAST", 580, base_y),
        ("deploy", "Deploy & Verify", "Progressive rollout, policy enforcement, runtime hooks", 840, base_y),
    ]
    out = []
    for i, (stype, label, desc, x, y) in enumerate(stage_defs):
        out.append({
            "id": _uid(),
            "pipeline_id": pid,
            "parent_id": None,
            "stage_type": stype,
            "label": label,
            "description": desc,
            "auto_nodes_json": json.dumps([]),
            "pos_x": float(x),
            "pos_y": float(y),
            "width": 220.0,
            "height": 140.0,
            "color": ["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6"][i],
            "collapsed": 0,
            "parallel": 0,
        })
    return out


# Compliance checks -- 1-2 per major pipeline
COMPLIANCE_CHECKS = [
    # (pipeline_idx, check_type, passed, failed, findings, offset_hours_ago)
    (0, "NIST_SSDF", 14, 3, 3, 72),
    (0, "STIG", 38, 4, 4, 24),
    (1, "FEDRAMP_MOD", 21, 5, 5, 48),
    (1, "NIST_800_53", 47, 2, 2, 12),
    (2, "DISA_STIG", 33, 6, 6, 96),
    (2, "NIST_SSDF", 12, 1, 1, 8),
    (3, "SOC2_TYPE2", 18, 0, 0, 36),
    (4, "SLSA_L3", 8, 2, 2, 60),
    (5, "NIST_800_53", 22, 3, 3, 4),
    (6, "NIST_SSDF", 16, 4, 4, 16),
]

# Findings templates -- 2-4 per check, mix of severities + status
FINDING_TEMPLATES = [
    ("STIG-V-242415", "Container Running as Root (CAT I)", "Container image executes as UID 0; violates STIG V-242415.", "container", "CAT1", "open", "Set USER directive to non-root in Dockerfile"),
    ("CAT2-OAUTH-MISS", "OAuth Scope Too Broad", "OAuth scope includes write:admin which is not required by application.", "auth", "CAT2", "open", "Reduce to minimum required scopes per OAuth 2.0 RFC 6749"),
    ("CAT3-LOG-REDACT", "PII in Application Logs", "Email addresses appear in DEBUG log lines; PII leakage risk.", "logging", "CAT3", "fixed", "Add PII redaction middleware to logger"),
    ("SBOM-MISSING", "SBOM Not Generated for Build Artifact", "CycloneDX SBOM missing from one or more built artifacts.", "build", "CAT2", "open", "Add syft step to build pipeline; fail build if SBOM missing"),
    ("PROVENANCE-MISS", "SLSA Provenance Attestation Missing", "in-toto provenance not generated for one of three build targets.", "build", "CAT1", "open", "Enable slsa-provenance generator in Tekton task"),
    ("SECRET-ROTATE", "Hard-coded Service Account Token", "Long-lived service account token committed to Git history.", "secrets", "CAT1", "fixed", "Rotate token, add gitleaks pre-commit hook"),
    ("TLS-VERSION", "TLS 1.0 Still Permitted on Legacy Endpoint", "Two legacy endpoints permit TLS 1.0 connections; not FIPS 140-3 compliant.", "network", "CAT2", "open", "Restrict to TLS 1.2+ with FIPS cipher suites only"),
    ("MTLS-MISS", "Service-to-Service mTLS Not Enforced", "Internal microservice call uses plaintext HTTP on cluster-internal port.", "network", "CAT1", "open", "Enforce mTLS via Istio PeerAuthentication or Linkerd policy"),
    ("AUDIT-LOG-GAP", "Audit Logs Not Shipped to SIEM", "Container runtime audit events not forwarded to agency SIEM.", "logging", "CAT2", "open", "Configure fluent-bit to forward kubernetes audit events"),
    ("DEPENDENCY-VULN", "Critical CVE in Third-party Library", "log4j-core 2.14.1 contains CVE-2021-44228 (Log4Shell).", "dependencies", "CAT1", "fixed", "Upgrade to log4j 2.17.1+; add renovate-bot dependency update PRs"),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _delete_all(conn) -> None:
    """Delete all rows from the 10 target tables (FK-safe order)."""
    for t in TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


def _has_only_copy_pipelines(conn) -> bool:
    """True if pipelines table contains any non-(copy) row."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM pipelines WHERE name NOT LIKE '%(copy)%'"
    ).fetchone()
    return (row["n"] or 0) > 0


def _fetch_template_graph(conn, template_id: str) -> dict:
    """Load the graph_json from pc_templates to clone into a demo pipeline."""
    row = conn.execute(
        "SELECT graph_json FROM pc_templates WHERE id = ?", (template_id,)
    ).fetchone()
    if row is None:
        return {"nodes": [], "edges": []}
    return json.loads(row["graph_json"] or '{"nodes":[],"edges":[]}')


def _seed_pipelines(conn) -> list[str]:
    ids = []
    for p in PIPELINES:
        graph = _fetch_template_graph(conn, p["template_id"])
        # Refresh node/edge IDs so the pipeline owns its own graph (not shared with template)
        for n in graph.get("nodes", []):
            n["id"] = _uid()
        for e in graph.get("edges", []):
            e["id"] = _uid()
        conn.execute(
            "INSERT INTO pipelines (id, name, description, graph_json, template_id, "
            "classification, target_csp, created_at, updated_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p["id"],
                p["name"],
                p["description"],
                json.dumps(graph),
                p["template_id"],
                p["classification"],
                p["target_csp"],
                _ts(-random.randint(48, 720)),
                _ts(-random.randint(0, 24)),
            ),
        )
        ids.append(p["id"])
    conn.commit()
    return ids


def _seed_projects_and_links(conn, pipeline_ids: list[str]) -> tuple[list[str], int]:
    project_ids = []
    for prj in PROJECTS:
        conn.execute(
            "INSERT INTO pc_projects (id, name, description, status, owner, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                prj["id"],
                prj["name"],
                prj["description"],
                prj["status"],
                prj["owner"],
                _ts(-random.randint(168, 1440)),
                _ts(-random.randint(0, 24)),
            ),
        )
        project_ids.append(prj["id"])

    # Many-to-many: assign each project 2-4 pipelines
    link_count = 0
    for i, pid in enumerate(project_ids):
        chosen = random.sample(pipeline_ids, k=random.randint(2, 4))
        for plid in chosen:
            conn.execute(
                "INSERT OR IGNORE INTO pc_project_pipelines (project_id, pipeline_id) "
                "VALUES (?, ?)",
                (pid, plid),
            )
            link_count += 1
    conn.commit()
    return project_ids, link_count


def _seed_stages(conn, pipeline_ids: list[str]) -> int:
    n = 0
    for idx, pid in enumerate(pipeline_ids):
        for stage in _stages_for(idx, pid):
            conn.execute(
                "INSERT INTO pc_stages (id, pipeline_id, parent_id, stage_type, label, "
                "description, auto_nodes_json, pos_x, pos_y, width, height, color, "
                "collapsed, parallel, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    stage["id"], stage["pipeline_id"], stage["parent_id"],
                    stage["stage_type"], stage["label"], stage["description"],
                    stage["auto_nodes_json"], stage["pos_x"], stage["pos_y"],
                    stage["width"], stage["height"], stage["color"],
                    stage["collapsed"], stage["parallel"], _ts(-random.randint(24, 720)),
                ),
            )
            n += 1
    conn.commit()
    return n


def _seed_versions(conn, pipeline_ids: list[str]) -> int:
    n = 0
    version_notes = [
        "Initial release from template; baseline graph.",
        "Added SAST stage; incorporated CodeQL scan results.",
        "Updated container base image to patched version; rotated signing keys.",
        "Enabled progressive delivery; added canary stage.",
        "Compliance review pass; added NIST 800-53 evidence generation.",
    ]
    for pid in pipeline_ids:
        # version_num=1 is the initial (mirrors current pipeline.graph_json), then 2 revs
        for v in (1, 2, 3):
            conn.execute(
                "INSERT INTO pc_versions (id, pipeline_id, version_num, label, "
                "graph_json, created_by, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _uid(),
                    pid,
                    v,
                    f"v{v}.0",
                    json.dumps({"nodes": [], "edges": [], "_version": v}),
                    random.choice(["alice@icdev.local", "bob@icdev.local", "carol@icdev.local"]),
                    random.choice(version_notes),
                    _ts(-random.randint(0, 720) - v * 24),
                ),
            )
            n += 1
    conn.commit()
    return n


def _seed_compliance(conn, pipeline_ids: list[str]) -> tuple[int, int]:
    check_count = 0
    finding_count = 0
    for p_idx, check_type, passed, failed, n_findings, hours_ago in COMPLIANCE_CHECKS:
        if p_idx >= len(pipeline_ids):
            continue
        pid = pipeline_ids[p_idx]
        check_id = _uid()
        # Build a findings_json blob of synthetic findings metadata
        findings_blob = json.dumps([
            {
                "rule_id": f"R-{random.randint(1000,9999)}",
                "framework": check_type,
                "severity": random.choice(["CAT1", "CAT2", "CAT3"]),
                "title": f"Sample finding {i+1} for {check_type}",
                "status": random.choice(["open", "fixed", "suppressed"]),
            }
            for i in range(n_findings)
        ])
        conn.execute(
            "INSERT INTO pc_compliance_checks (id, pipeline_id, check_type, passed, "
            "failed, findings_json, ran_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (check_id, pid, check_type, passed, failed, findings_blob, _ts(-hours_ago)),
        )
        check_count += 1

        # Insert individual pc_compliance_findings rows
        for i in range(n_findings):
            tmpl = random.choice(FINDING_TEMPLATES)
            rule_id, title, desc, affected_type, sev, status, fix = tmpl
            conn.execute(
                "INSERT INTO pc_compliance_findings (id, pipeline_id, audit_id, "
                "rule_id, framework, severity, title, description, affected_entity, "
                "affected_type, status, fix_action, remediated_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _uid(),
                    pid,
                    check_id,
                    f"{rule_id}-{i+1}",
                    check_type,
                    sev,
                    f"{title} ({check_type})",
                    desc,
                    affected_type,
                    affected_type,
                    status,
                    fix,
                    _ts(-random.randint(0, hours_ago)) if status == "fixed" else None,
                    _ts(-hours_ago - random.randint(0, 24)),
                ),
            )
            finding_count += 1
    conn.commit()
    return check_count, finding_count


def _seed_change_requests(conn, pipeline_ids: list[str]) -> int:
    cr_types = ["add_stage", "modify", "remove_node", "policy_change", "approval_gate"]
    statuses = ["pending", "approved", "approved", "in_review", "rejected"]
    creators = ["alice@icdev.local", "bob@icdev.local", "carol@icdev.local", "dave@icdev.local"]
    approvers = ["isso@agency.gov", "isso@agency.gov", "ciso@agency.gov", "pm@program.gov"]
    n = 0
    for i in range(5):
        pid = random.choice(pipeline_ids)
        cr_num = f"CR-2026-{1000+i}"
        cr_type = random.choice(cr_types)
        status = statuses[i]
        markup = json.dumps([
            {"node_id": _uid(), "change": "rename", "old": "Old Label", "new": "New Label"},
            {"node_id": _uid(), "change": "add", "new": f"new {cr_type} node"},
        ])
        conn.execute(
            "INSERT INTO pc_change_requests (id, pipeline_id, cr_number, cr_type, "
            "status, markup_json, created_by, approved_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _uid(),
                pid,
                cr_num,
                cr_type,
                status,
                markup,
                random.choice(creators),
                random.choice(approvers) if status in ("approved", "rejected") else None,
                _ts(-random.randint(1, 240)),
                _ts(-random.randint(0, 24)),
            ),
        )
        n += 1
    conn.commit()
    return n


def _seed_boundaries(conn, pipeline_ids: list[str]) -> int:
    """Cross-domain / classification zones -- mostly on the Hybrid NDC pipeline."""
    boundary_defs = [
        ("NIPR Zone", "CUI", "#10b981", 0.10, "security_zone", 40, 40, 540, 320),
        ("CDS Guard", "SECRET", "#f59e0b", 0.15, "cross_domain", 600, 40, 120, 320),
        ("SIPR Zone", "SECRET", "#ef4444", 0.12, "security_zone", 740, 40, 540, 320),
        ("IL5 Boundary", "CUI", "#3b82f6", 0.08, "classification", 40, 400, 700, 240),
        ("Build Cluster", "CUI", "#8b5cf6", 0.06, "trust_zone", 760, 400, 280, 240),
        ("Runtime Monitoring", "CUI", "#06b6d4", 0.06, "observability", 1060, 400, 220, 240),
    ]
    n = 0
    for label, classification, color, opacity, btype, x, y, w, h in boundary_defs:
        # Attach to a random pipeline; the Hybrid NDC pipeline (index 5) gets a couple
        pid = pipeline_ids[5] if len(pipeline_ids) > 5 and random.random() < 0.6 else random.choice(pipeline_ids)
        node_ids = json.dumps([_uid() for _ in range(random.randint(2, 5))])
        conn.execute(
            "INSERT INTO pc_boundaries (id, pipeline_id, label, classification, color, "
            "fill_opacity, node_ids, boundary_type, pos_x, pos_y, width, height, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _uid(),
                pid,
                label,
                classification,
                color,
                opacity,
                node_ids,
                btype,
                float(x), float(y), float(w), float(h),
                _ts(-random.randint(24, 720)),
            ),
        )
        n += 1
    conn.commit()
    return n


def _seed_collab_sessions(conn, pipeline_ids: list[str]) -> int:
    users = [
        ("alice@icdev.local", "Alice Chen", "#3498db"),
        ("bob@icdev.local", "Bob Singh", "#e74c3c"),
        ("carol@icdev.local", "Carol Martinez", "#2ecc71"),
        ("dave@icdev.local", "Dave Park", "#9b59b6"),
    ]
    n = 0
    for uid, uname, color in users:
        design_id = random.choice(pipeline_ids)
        joined = _ts(-random.uniform(0.05, 2.0))
        last_seen = _ts(-random.uniform(0.0, 0.02))
        conn.execute(
            "INSERT INTO pc_collab_sessions (id, design_id, user_id, user_name, color, "
            "joined_at, last_seen, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_uid(), design_id, uid, uname, color, joined, last_seen, 1),
        )
        n += 1
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _row_counts(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in TABLES + PROTECTED}


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed pipeline_canvas.db with demo data")
    ap.add_argument("--reset", action="store_true", help="Delete existing rows in target tables before seeding")
    ap.add_argument("--verify", action="store_true", help="Print row counts and exit")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()

    if not _DB.exists():
        print(f"ERROR: database not found at {_DB}", file=sys.stderr)
        return 1

    conn = _get_conn()
    try:
        if args.verify:
            counts = _row_counts(conn)
            sample = conn.execute(
                "SELECT name, classification, target_csp FROM pipelines ORDER BY name"
            ).fetchall()
            result = {"counts": counts, "sample_pipelines": [dict(r) for r in sample]}
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print("=== pipeline_canvas.db row counts ===")
                for t, n in counts.items():
                    marker = "  [protected]" if t in PROTECTED else ""
                    print(f"  {t:<28} {n:>5}{marker}")
                print("\n=== pipelines ===")
                for r in sample:
                    print(f"  {r['name']:<48}  {r['classification']:<8}  {r['target_csp']}")
            return 0

        # Decide whether to reset
        do_reset = args.reset
        if not do_reset and _has_only_copy_pipelines(conn):
            msg = (
                "pipelines table already contains non-(copy) rows. "
                "Re-run with --reset to overwrite, or use --verify to inspect."
            )
            if args.json:
                print(json.dumps({"skipped": True, "reason": msg}))
            else:
                print(msg)
            return 0

        if do_reset:
            _delete_all(conn)
            if not args.json:
                print("[reset] cleared 10 target tables (audit/templates/snippets preserved)")

        # Run all seeds
        pipeline_ids = _seed_pipelines(conn)
        project_ids, link_count = _seed_projects_and_links(conn, pipeline_ids)
        stage_n = _seed_stages(conn, pipeline_ids)
        version_n = _seed_versions(conn, pipeline_ids)
        check_n, finding_n = _seed_compliance(conn, pipeline_ids)
        cr_n = _seed_change_requests(conn, pipeline_ids)
        boundary_n = _seed_boundaries(conn, pipeline_ids)
        collab_n = _seed_collab_sessions(conn, pipeline_ids)

        counts = _row_counts(conn)
        result = {
            "seeded": {
                "pipelines": len(pipeline_ids),
                "pc_projects": len(project_ids),
                "pc_project_pipelines": link_count,
                "pc_stages": stage_n,
                "pc_versions": version_n,
                "pc_compliance_checks": check_n,
                "pc_compliance_findings": finding_n,
                "pc_change_requests": cr_n,
                "pc_boundaries": boundary_n,
                "pc_collab_sessions": collab_n,
            },
            "current_row_counts": counts,
        }
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("\n=== Seed complete ===")
            for k, v in result["seeded"].items():
                print(f"  {k:<32} +{v}")
            print("\n=== Current row counts ===")
            for t, n in counts.items():
                marker = "  [protected]" if t in PROTECTED else ""
                print(f"  {t:<28} {n:>5}{marker}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
