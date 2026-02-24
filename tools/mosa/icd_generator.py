#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Interface Control Document (ICD) generator for DoD MOSA compliance.

Auto-discovers interfaces by scanning for OpenAPI/Swagger spec files,
generates CUI-marked ICD markdown documents, stores them in the
icd_documents database table, and writes to the filesystem.

Authority: 10 U.S.C. Section 4401, DoDI 5000.87

Usage:
    python tools/mosa/icd_generator.py --project-id proj-123 --all
    python tools/mosa/icd_generator.py --project-id proj-123 --interface-id ifc-abc
    python tools/mosa/icd_generator.py --project-id proj-123 --all --output-dir .tmp/icd
    python tools/mosa/icd_generator.py --project-id proj-123 --all --json
    python tools/mosa/icd_generator.py --project-id proj-123 --all --human
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
MOSA_CONFIG_PATH = BASE_DIR / "args" / "mosa_config.yaml"
DEFAULT_OUTPUT_DIR = BASE_DIR / ".tmp" / "mosa" / "icd"

SPEC_PATTERNS = [
    "openapi.yaml", "openapi.yml", "openapi.json",
    "swagger.yaml", "swagger.yml", "swagger.json",
    "**/openapi.yaml", "**/openapi.yml", "**/openapi.json",
    "**/swagger.yaml", "**/swagger.yml", "**/swagger.json",
]

CUI_BANNER = ("CUI // SP-CTI\nControlled by: Department of Defense\n"
              "CUI Category: CTI | Distribution: D\n"
              "Destruction Notice: Destroy when no longer needed.\n")


def _load_mosa_config():
    """Load allowed protocols and data formats from args/mosa_config.yaml."""
    defaults = {"allowed_protocols": ["REST", "gRPC", "AMQP", "Kafka",
                "SFTP", "JDBC", "GraphQL", "WebSocket"],
                "allowed_data_formats": ["JSON", "XML", "Protobuf", "CSV",
                "Avro", "Parquet"]}
    if not MOSA_CONFIG_PATH.exists():
        return defaults
    try:
        import yaml
        with open(MOSA_CONFIG_PATH, "r", encoding="utf-8") as f:
            icd = (yaml.safe_load(f) or {}).get("mosa", {}).get("icd", {})
        return {"allowed_protocols": icd.get("allowed_protocols", defaults["allowed_protocols"]),
                "allowed_data_formats": icd.get("allowed_data_formats", defaults["allowed_data_formats"])}
    except Exception:
        return defaults


def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project record from database."""
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _ensure_table(conn):
    """Create icd_documents table if it does not already exist."""
    conn.execute("""CREATE TABLE IF NOT EXISTS icd_documents (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, interface_id TEXT,
        interface_name TEXT NOT NULL, version TEXT DEFAULT '1.0.0',
        source_system TEXT, target_system TEXT, protocol TEXT, data_format TEXT,
        content TEXT, file_path TEXT, classification TEXT DEFAULT 'CUI // SP-CTI',
        status TEXT DEFAULT 'draft', approval_status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()


def _log_audit_event(conn, project_id, action, details, file_path):
    """Log an append-only audit trail event for ICD generation."""
    try:
        conn.execute(
            """INSERT INTO audit_trail (project_id, event_type, actor, action,
               details, affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, "compliance_check", "icdev-mosa-engine", action,
             json.dumps(details), json.dumps([str(file_path)]), "CUI"))
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


def _discover_interfaces(project_dir):
    """Scan project directory for OpenAPI/Swagger spec files."""
    interfaces = []
    if not project_dir or not Path(project_dir).is_dir():
        return interfaces
    root = Path(project_dir)
    seen = set()
    for pattern in SPEC_PATTERNS:
        for match in root.glob(pattern):
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                with open(resolved, "r", encoding="utf-8") as f:
                    if resolved.suffix == ".json":
                        spec = json.load(f)
                    else:
                        try:
                            import yaml
                            spec = yaml.safe_load(f)
                        except ImportError:
                            spec = {}
                name = spec.get("info", {}).get("title", resolved.stem)
                interfaces.append({"name": name, "path": str(resolved), "spec": spec})
            except Exception:
                continue
    return interfaces


def _build_icd_content(interface, project, config):
    """Generate a CUI-marked markdown ICD document for one interface."""
    spec = interface.get("spec", {})
    info = spec.get("info", {})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    name = info.get("title", interface.get("name", "Unknown Interface"))
    version = info.get("version", "1.0.0")
    description = info.get("description", "No description provided.")
    source = project.get("name", "Source System")
    target = info.get("contact", {}).get("name", "External Consumer")
    servers = spec.get("servers", [])
    base_url = servers[0].get("url", "TBD") if servers else "TBD"

    # Detect protocol and data format
    protocol, data_format = "REST", "JSON"
    if any("grpc" in str(s).lower() for s in servers):
        protocol, data_format = "gRPC", "Protobuf"
    elif spec.get("asyncapi"):
        protocol = "AMQP"
    for key, vals in [("allowed_protocols", protocol), ("allowed_data_formats", data_format)]:
        if vals not in config.get(key, []) and config.get(key):
            pass  # keep detected value, mark in template if needed

    # Build endpoint summary
    endpoints = []
    for path_str, methods in spec.get("paths", {}).items():
        for verb in ("get", "post", "put", "patch", "delete"):
            if verb in methods:
                op = methods[verb]
                endpoints.append(f"| `{verb.upper()}` | `{path_str}` "
                                 f"| {op.get('summary', op.get('operationId', ''))} |")
    ep_table = ("| Method | Path | Description |\n|--------|------|-------------|\n"
                + "\n".join(endpoints)) if endpoints else "*No endpoints discovered.*"

    content = f"""{CUI_BANNER}
---
# Interface Control Document (ICD)

## 1. Document Identification
| Field | Value |
|-------|-------|
| Interface Name | {name} |
| ICD Version | {version} |
| Source System | {source} |
| Target System | {target} |
| Date Generated | {now} |
| Classification | CUI // SP-CTI |
| Status | Draft |
| Spec File | `{interface.get('path', 'N/A')}` |

## 2. Interface Description
{description}

## 3. Protocol & Data Format
| Attribute | Value |
|-----------|-------|
| Protocol | {protocol} |
| Data Format | {data_format} |
| Transport Security | TLS 1.3 (FIPS 140-2 validated) |
| Base URL | {base_url} |

## 4. Authentication Requirements
- OAuth 2.0 / OIDC bearer token, mTLS with DoD certs, or CAC/PIV client cert.
- API keys alone are NOT sufficient for CUI data.
- Credentials MUST be stored in AWS Secrets Manager or K8s Secrets.

## 5. Endpoints / Operations
{ep_table}

## 6. Error Handling
| HTTP Status | Meaning | Action |
|-------------|---------|--------|
| 400 | Bad Request | Validate against schema |
| 401 | Unauthorized | Re-authenticate |
| 403 | Forbidden | Check RBAC roles |
| 404 | Not Found | Verify resource ID |
| 429 | Rate Limited | Exponential backoff |
| 500 | Server Error | Retry; escalate if persistent |
| 503 | Unavailable | Check health; retry after delay |

## 7. SLA & Performance
| Metric | Target |
|--------|--------|
| Availability | 99.9% |
| Response p95 | < 500 ms |
| Response p99 | < 2000 ms |
| Max Payload | 10 MB |
| Rate Limit | Per consumer tier |

## 8. Change Control & Versioning
- SemVer required (`MAJOR.MINOR.PATCH`). Breaking changes = new MAJOR + 90-day notice.
- All changes recorded in audit trail. ISSO review required for boundary/classification changes.

## 9. Approval
| Role | Name | Date | Signature |
|------|------|------|-----------|
| System Owner | ________________ | ________ | ________ |
| ISSO | ________________ | ________ | ________ |
| Interface Owner | ________________ | ________ | ________ |

---
*Generated by ICDEV MOSA Engine | {now}*
{CUI_BANNER}"""

    return {"content": content, "name": name, "version": version,
            "source_system": source, "target_system": target,
            "protocol": protocol, "data_format": data_format}


def generate_icd(conn, project_id, interface, output_dir, config):
    """Generate and persist a single ICD document."""
    _ensure_table(conn)
    project = _get_project(conn, project_id)
    result = _build_icd_content(interface, project, config)
    icd_id = f"icd-{uuid.uuid4().hex[:12]}"

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = result["name"].replace(" ", "_").replace("/", "_")[:80]
    file_path = out_dir / f"{safe_name}_icd.md"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(result["content"])

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO icd_documents (id, project_id, interface_id, interface_name,
           version, source_system, target_system, protocol, data_format, content,
           file_path, classification, status, approval_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (icd_id, project_id, interface.get("id"), result["name"], result["version"],
         result["source_system"], result["target_system"], result["protocol"],
         result["data_format"], result["content"], str(file_path),
         "CUI // SP-CTI", "draft", "pending", now, now))
    conn.commit()

    _log_audit_event(conn, project_id, f"ICD generated: {result['name']}", {
        "icd_id": icd_id, "interface_name": result["name"],
        "version": result["version"], "protocol": result["protocol"],
        "file_path": str(file_path)}, file_path)

    return {"id": icd_id, "interface_name": result["name"], "version": result["version"],
            "protocol": result["protocol"], "data_format": result["data_format"],
            "file_path": str(file_path), "status": "draft", "approval_status": "pending"}


def _print_human(output):
    """Plain-text summary for terminal output."""
    print("ICD Generation Complete")
    print(f"  Project:    {output['project_id']}")
    print(f"  Generated:  {output['icds_generated']} document(s)")
    print(f"  Output Dir: {output['output_dir']}")
    for doc in output.get("documents", []):
        print(f"\n  [{doc['id']}] {doc['interface_name']} v{doc['version']}")
        print(f"    Protocol: {doc['protocol']}  Format: {doc['data_format']}")
        print(f"    File:     {doc['file_path']}")
        print(f"    Status:   {doc['status']} / {doc['approval_status']}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate Interface Control Documents (ICDs) for MOSA compliance")
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--interface-id", help="Specific interface to generate ICD for")
    parser.add_argument("--all", action="store_true", dest="gen_all",
                        help="Generate ICDs for all discovered interfaces")
    parser.add_argument("--output-dir", help="Output directory (default: .tmp/mosa/icd/)")
    parser.add_argument("--json", action="store_true", dest="json_mode", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Colored terminal output")
    args = parser.parse_args()

    if not args.interface_id and not args.gen_all:
        parser.error("Specify --interface-id or --all")

    output_dir = args.output_dir or str(DEFAULT_OUTPUT_DIR / args.project_id)
    config = _load_mosa_config()
    try:
        conn = _get_connection()
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        project = _get_project(conn, args.project_id)
        project_dir = project.get("directory_path", "")
        results = []

        if args.gen_all:
            interfaces = _discover_interfaces(project_dir)
            if not interfaces:
                msg = (f"No OpenAPI/Swagger specs found in "
                       f"'{project_dir or '(no directory_path)'}'. "
                       "Add spec files or use --interface-id.")
                if args.json_mode:
                    print(json.dumps({"status": "warning", "message": msg}))
                else:
                    print(f"WARNING: {msg}", file=sys.stderr)
                sys.exit(0)
            for ifc in interfaces:
                results.append(generate_icd(conn, args.project_id, ifc, output_dir, config))
        else:
            row = conn.execute(
                "SELECT * FROM icd_documents WHERE id = ? AND project_id = ?",
                (args.interface_id, args.project_id)).fetchone()
            ifc = ({"name": row["interface_name"], "spec": {}, "path": ""} if row
                   else {"name": args.interface_id, "spec": {}, "path": ""})
            results.append(generate_icd(conn, args.project_id, ifc, output_dir, config))

        output = {"status": "success", "project_id": args.project_id,
                  "icds_generated": len(results), "output_dir": output_dir,
                  "documents": results}
        if args.json_mode:
            print(json.dumps(output, indent=2))
        elif args.human:
            try:
                from tools.cli_formatter import CLIOutput
                CLIOutput(json_mode=False).print(output)
            except ImportError:
                _print_human(output)
        else:
            _print_human(output)
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
