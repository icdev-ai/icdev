#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""TSP Generator -- Technical Standard Profile for DoD MOSA compliance.

Auto-detects standards from project tech stack, generates a CUI-marked
markdown TSP document, and stores it in the tsp_documents table.

Usage:
    python tools/mosa/tsp_generator.py --project-id proj-123 --json
    python tools/mosa/tsp_generator.py --project-id proj-123 --output-dir /tmp
    python tools/mosa/tsp_generator.py --project-id proj-123 --human
"""
import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "mosa_config.yaml"

# (indicator_files, standard_name, version, category)
RULES = [
    (["requirements.txt","setup.py","pyproject.toml","Pipfile"], "PEP 8", "2001", "programming_language"),
    (["requirements.txt","setup.py","pyproject.toml","Pipfile"], "PEP 257", "2001", "programming_language"),
    (["pom.xml","build.gradle","build.gradle.kts"], "JSR 330 Dependency Injection", "1.0", "programming_language"),
    (["pom.xml","build.gradle","build.gradle.kts"], "JSR 370 JAX-RS 2.1", "2.1", "programming_language"),
    (["package.json","tsconfig.json"], "ECMAScript Specification", "ES2023", "programming_language"),
    (["go.mod"], "Effective Go Conventions", "1.21+", "programming_language"),
    (["Cargo.toml"], "Rust Edition Guide", "2021", "programming_language"),
    (["*.csproj"], ".NET Design Guidelines", "8.0", "programming_language"),
    (["openapi.yaml","openapi.json","swagger.yaml","swagger.json"], "OpenAPI Specification", "3.1.0", "api_specification"),
    (["*.proto"], "Protocol Buffers (gRPC)", "3", "api_specification"),
    (["Dockerfile","docker-compose.yml","docker-compose.yaml"], "OCI Image Specification", "1.0", "containerization"),
    (["Dockerfile","docker-compose.yml","docker-compose.yaml"], "OCI Runtime Specification", "1.0", "containerization"),
    ([], "Kubernetes API", "1.28+", "containerization"),  # detected via content
    ([], "OAuth 2.0 (RFC 6749)", "2.0", "authentication"),  # detected via content
    ([], "OpenID Connect Core 1.0", "1.0", "authentication"),  # detected via content
    ([], "TLS 1.3 (RFC 8446)", "1.3", "communication_protocol"),  # always for DoD
    ([], "FIPS 140-2 Cryptographic Validation", "140-2", "security"),  # always for DoD
    ([], "JSON Schema", "2020-12", "data_format"),  # detected if *.json present
    (["*.xsd","*.xml"], "XML Schema (W3C)", "1.1", "data_format"),
]
AUTH_KW = ["oauth", "oidc", "openid", "authorization_code", "jwt"]
K8S_KW = ["apiVersion:", "kind: Deployment", "kind: Service"]
ALWAYS_CATS = {"communication_protocol", "security"}


def _conn(db_path=None):
    p = db_path or DB_PATH
    if not Path(p).exists():
        raise FileNotFoundError(f"DB not found: {p}. Run: python tools/db/init_icdev_db.py")
    c = sqlite3.connect(str(p)); c.row_factory = sqlite3.Row; return c

def _project(conn, pid):
    r = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if not r: raise ValueError(f"Project '{pid}' not found")
    return dict(r)

def _config():
    defaults = {"max_age_days": 180, "standard_categories": [
        "api_specification","data_format","communication_protocol",
        "authentication","containerization","programming_language","security"]}
    if not CONFIG_PATH.exists(): return defaults
    try:
        import yaml
        tsp = yaml.safe_load(CONFIG_PATH.open()).get("mosa",{}).get("tsp",{})
        return {"max_age_days": tsp.get("max_age_days", 180),
                "standard_categories": tsp.get("standard_categories", defaults["standard_categories"])}
    except Exception: return defaults


def detect_standards(project_dir):
    """Auto-detect standards from project tech stack."""
    pp = Path(project_dir)
    if not pp.is_dir(): return []
    files = {p.name for pat in ("*","*/*","*/*/*") for p in pp.glob(pat) if p.is_file()}
    has_auth = has_k8s = False
    for ext in ("*.py","*.java","*.ts","*.js","*.yaml","*.yml"):
        for fp in pp.rglob(ext):
            try:
                txt = fp.read_text(encoding="utf-8", errors="ignore")[:8192]
                if any(k in txt.lower() for k in AUTH_KW): has_auth = True
                if any(k in txt for k in K8S_KW): has_k8s = True
                if has_auth and has_k8s: break
            except Exception: continue
        if has_auth and has_k8s: break
    detected, seen = [], set()
    def _add(name, ver, cat):
        if name not in seen:
            seen.add(name)
            detected.append({"category": cat, "standard": name, "version": ver,
                             "conformance": "full", "deviation_rationale": None})
    for inds, name, ver, cat in RULES:
        if name in seen: continue
        if cat in ALWAYS_CATS: _add(name, ver, cat); continue
        if cat == "authentication":
            if has_auth: _add(name, ver, cat); continue
            else: continue
        if "Kubernetes" in name:
            if has_k8s: _add(name, ver, cat); continue
            else: continue
        if "JSON Schema" in name:
            if any(f.endswith(".json") for f in files): _add(name, ver, cat)
            continue
        if not inds: continue
        for ind in inds:
            if ("*" in ind and any(f.endswith(ind.lstrip("*")) for f in files)) \
               or ind in files:
                _add(name, ver, cat); break
    return sorted(detected, key=lambda s: (s["category"], s["standard"]))


def _build_md(proj, tid, now, stds, devs, cfg):
    """Build CUI-marked TSP markdown."""
    L = ["CUI // SP-CTI\n\n# Technical Standard Profile (TSP)\n",
         "## Document Identification\n",
         "| Field | Value |", "|-------|-------|",
         f"| TSP ID | {tid} |",
         f"| Project | {proj.get('name', proj['id'])} ({proj['id']}) |",
         "| Version | 1.0.0 |", f"| Date | {now.strftime('%Y-%m-%d')} |",
         "| Classification | CUI // SP-CTI |",
         "| Authority | 10 U.S.C. Section 4401, DoDI 5000.87 |",
         "\n## Standards Inventory\n",
         "| # | Category | Standard | Version | Conformance |",
         "|---|----------|----------|---------|-------------|"]
    for i, s in enumerate(stds, 1):
        L.append(f"| {i} | {s['category']} | {s['standard']} | {s['version']} | {s['conformance']} |")
    full = sum(1 for s in stds if s["conformance"] == "full")
    partial = sum(1 for s in stds if s["conformance"] == "partial")
    planned = sum(1 for s in stds if s["conformance"] == "planned")
    cats = len(set(s["category"] for s in stds))
    L.extend(["\n## Conformance Summary\n",
              f"- **Total standards:** {len(stds)}", f"- **Full:** {full}",
              f"- **Partial:** {partial}", f"- **Planned:** {planned}",
              f"- **Categories:** {cats} of {len(cfg['standard_categories'])}"])
    L.append("\n## Deviations\n")
    if devs:
        L.extend(["| Standard | Conformance | Rationale |", "|----------|-------------|-----------|"])
        for d in devs:
            L.append(f"| {d['standard']} | {d['conformance']} | {d.get('deviation_rationale') or 'Pending'} |")
    else:
        L.append("No deviations recorded.")
    L.extend(["\n## Validation\n", "- Status: **draft**", "- Approval: **pending**",
              f"- Max age: {cfg['max_age_days']} days",
              f"- Next review: within {cfg['max_age_days']} days of approval",
              "\n---\nCUI // SP-CTI"])
    return "\n".join(L)


def generate_tsp(project_id, output_dir=None, db_path=None):
    """Generate a Technical Standard Profile and store in DB + filesystem."""
    conn = _conn(db_path)
    try:
        proj = _project(conn, project_id)
        cfg = _config()
        now = datetime.now(tz=timezone.utc)
        tid = f"tsp-{uuid.uuid4().hex[:12]}"
        pdir = proj.get("project_dir") or proj.get("source_path")
        stds = detect_standards(pdir) if pdir and Path(pdir).is_dir() else detect_standards(str(BASE_DIR))
        devs = [s for s in stds if s["conformance"] != "full"]
        content = _build_md(proj, tid, now, stds, devs, cfg)
        out = Path(output_dir) if output_dir else BASE_DIR / ".tmp" / "mosa" / "tsp"
        out.mkdir(parents=True, exist_ok=True)
        fp = out / f"{tid}.md"
        fp.write_text(content, encoding="utf-8")
        conn.execute(
            """INSERT INTO tsp_documents (id,project_id,version,standards,deviations,
            content,file_path,classification,status,approval_status,created_at,updated_at)
            VALUES (?,?,'1.0.0',?,?,?,?,'CUI // SP-CTI','draft','pending',?,?)""",
            (tid, project_id, json.dumps(stds), json.dumps(devs), content, str(fp),
             now.isoformat(), now.isoformat()))
        conn.execute(
            """INSERT INTO audit_trail (project_id,event_type,actor,action,details,classification)
            VALUES (?,'tsp_generated','icdev-mosa-engine',?,?,'CUI')""",
            (project_id, f"TSP generated: {tid} ({len(stds)} standards)",
             json.dumps({"tsp_id": tid, "standards_count": len(stds)})))
        conn.commit()
        return {"tsp_id": tid, "project_id": project_id, "version": "1.0.0",
                "standards_count": len(stds), "standards": stds,
                "deviations_count": len(devs), "deviations": devs,
                "file_path": str(fp), "classification": "CUI // SP-CTI",
                "status": "draft", "approval_status": "pending",
                "valid_categories": cfg["standard_categories"],
                "max_age_days": cfg["max_age_days"], "generated_at": now.isoformat()}
    finally:
        conn.close()


def _print_plain(r):
    print(f"Technical Standard Profile -- {r['project_id']}")
    print("=" * 60)
    for k in ("tsp_id","version","standards_count","deviations_count","status","approval_status","file_path"):
        print(f"  {k:20s} {r[k]}")
    print("\nStandards:\n" + "-" * 60)
    for s in r["standards"]:
        print(f"  [{s['conformance']:7s}] {s['category']:25s} {s['standard']}")
    if r["deviations"]:
        print(f"\nDeviations ({r['deviations_count']}):")
        for d in r["deviations"]:
            print(f"  - {d['standard']}: {d.get('deviation_rationale','N/A')}")


def main():
    ap = argparse.ArgumentParser(description="MOSA Technical Standard Profile (TSP) Generator")
    ap.add_argument("--project-id", required=True, help="Project ID")
    ap.add_argument("--output-dir", default=None, help="Output directory (default: .tmp/mosa/tsp/)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--human", action="store_true", help="Human-readable colored output")
    ap.add_argument("--db-path", type=Path, default=None)
    args = ap.parse_args()
    try:
        result = generate_tsp(args.project_id, output_dir=args.output_dir, db_path=args.db_path)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        elif args.human:
            try:
                sys.path.insert(0, str(BASE_DIR / "tools"))
                from cli_formatter import CLIOutput
                CLIOutput(json_mode=False).print(result)
            except ImportError:
                _print_plain(result)
        else:
            _print_plain(result)
    except (ValueError, FileNotFoundError) as e:
        if args.json: print(json.dumps({"error": str(e)}, indent=2))
        else: print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
