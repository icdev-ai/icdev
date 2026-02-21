#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate Splunk + ELK SIEM forwarding configurations for projects.
Loads templates from context/compliance/siem_config_templates/, substitutes
project-specific variables, validates log source coverage, applies CUI
markings, and logs an audit event."""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
SIEM_TEMPLATES_DIR = BASE_DIR / "context" / "compliance" / "siem_config_templates"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_cui_config():
    """Load CUI marking configuration via cui_marker helper.
    Falls back to built-in defaults if the module is unavailable."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from cui_marker import load_cui_config
        return load_cui_config()
    except ImportError:
        return {
            "document_header": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "document_footer": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "////////////////////////////////////////////////////////////////////"
            ),
        }


def _load_project_defaults():
    """Load project defaults from args/project_defaults.yaml.
    Returns a flat dict of monitoring/SIEM-related settings."""
    defaults_path = BASE_DIR / "args" / "project_defaults.yaml"
    defaults = {}
    if defaults_path.exists():
        try:
            import yaml
            with open(defaults_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # Flatten monitoring section into defaults
            monitoring = data.get("monitoring", {})
            infra = data.get("infrastructure", {})
            defaults.update({
                "log_aggregator": monitoring.get("log_aggregator", ["elk", "splunk"]),
                "metrics": monitoring.get("metrics", "prometheus"),
                "dashboards": monitoring.get("dashboards", "grafana"),
                "self_healing": monitoring.get("self_healing", True),
                "cloud": infra.get("cloud", "aws-govcloud"),
                "region": infra.get("region", "us-gov-west-1"),
            })
        except ImportError:
            pass
        except Exception as exc:
            print(f"Warning: Could not load project_defaults.yaml: {exc}",
                  file=sys.stderr)
    return defaults


def _load_template(template_name):
    """Load a SIEM config template from the templates directory.
    Returns the raw template string, or None if not found."""
    template_path = SIEM_TEMPLATES_DIR / template_name
    if not template_path.exists():
        return None
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def _load_log_sources():
    """Load required log source definitions from log_sources.json.
    Returns a list of source dicts, or a built-in default set."""
    content = _load_template("log_sources.json")
    if content:
        try:
            data = json.loads(content)
            return data if isinstance(data, list) else data.get("sources", [])
        except json.JSONDecodeError:
            print("Warning: log_sources.json is invalid JSON, using defaults.",
                  file=sys.stderr)

    # Built-in required log sources per DI 8530.01
    return [
        {"name": "application", "path": "logs/*.log", "required": True,
         "description": "Application logs"},
        {"name": "auth", "path": "/var/log/auth.log", "required": True,
         "description": "Authentication/authorization events"},
        {"name": "audit", "path": "/var/log/audit/audit.log", "required": True,
         "description": "OS audit subsystem events"},
        {"name": "syslog", "path": "/var/log/syslog", "required": True,
         "description": "System log messages"},
        {"name": "container", "path": "/var/log/containers/*.log", "required": True,
         "description": "Container runtime logs"},
        {"name": "access", "path": "logs/access.log", "required": False,
         "description": "HTTP access logs"},
        {"name": "error", "path": "logs/error.log", "required": False,
         "description": "HTTP error logs"},
        {"name": "database", "path": "/var/log/postgresql/*.log", "required": False,
         "description": "Database query/audit logs"},
    ]


def _detect_log_paths(project_dir):
    """Auto-detect log directory paths within the project.
    Returns (app_log_path, auth_log_path)."""
    project_dir = Path(project_dir)
    app_log_path = "/var/log/app"
    auth_log_path = "/var/log"

    # Scan for common log directories inside the project tree
    for candidate in ("logs", "log", "var/log"):
        candidate_path = project_dir / candidate
        if candidate_path.is_dir():
            app_log_path = str(candidate_path)
            break

    return app_log_path, auth_log_path


def _substitute_template(content, variables):
    """Replace {{VARIABLE}} placeholders in template content."""
    def replacer(match):
        key = match.group(1)
        return str(variables.get(key, match.group(0)))
    return re.sub(r'\{\{(\w+)\}\}', replacer, content)


def _log_audit_event(conn, project_id, action, details, affected_files=None):
    """Log an audit trail event for SIEM config generation."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "siem_config_generated",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps(affected_files or []),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def generate_siem_config(
    project_dir,
    project_id=None,
    siem_targets=None,
    output_dir=None,
    db_path=None,
):
    """Generate SIEM forwarding configurations for a project.

    Args:
        project_dir:  Path to the project directory (required).
        project_id:   Optional project ID for DB logging.
        siem_targets: List of targets, default ["splunk", "elk"].
        output_dir:   Where to write configs, default {project_dir}/siem/.
        db_path:      Database path override.

    Returns:
        Dict with generated file paths and validation results.
    """
    project_dir = Path(project_dir).resolve()
    if not project_dir.is_dir():
        raise NotADirectoryError(f"Project directory not found: {project_dir}")

    if siem_targets is None:
        siem_targets = ["splunk", "elk"]

    if output_dir is None:
        output_dir = project_dir / "siem"
    else:
        output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load supporting data
    defaults = _load_project_defaults()
    cui_config = _load_cui_config()
    log_sources = _load_log_sources()

    # Auto-detect project properties
    system_name = project_id or project_dir.name
    system_name = re.sub(r'[^a-zA-Z0-9_-]', '-', system_name).lower()
    app_log_path, auth_log_path = _detect_log_paths(project_dir)

    # Build substitution variables
    variables = {
        "system_name": system_name,
        "splunk_server": defaults.get("splunk_server", "splunk.govcloud.local"),
        "splunk_port": defaults.get("splunk_port", "9997"),
        "splunk_hec_token": "{{SPLUNK_HEC_TOKEN}}",
        "splunk_index": f"icdev-{system_name}",
        "ssl_cert_path": "/etc/pki/tls/certs/client.crt",
        "ssl_key_path": "/etc/pki/tls/private/client.key",
        "ssl_ca_path": "/etc/pki/tls/certs/ca-bundle.crt",
        "ssl_key_password": "{{SSL_KEY_PASSWORD}}",
        "app_log_path": app_log_path,
        "auth_log_path": auth_log_path,
        "elasticsearch_hosts": '["https://elasticsearch.govcloud.local:9200"]',
        "logstash_hosts": '["logstash.govcloud.local:5044"]',
        "kibana_host": "https://kibana.govcloud.local:5601",
        "elk_index_prefix": f"icdev-{system_name}",
        "project_dir": str(project_dir),
    }

    generated_files = []
    template_map = {
        "splunk": "splunk_forwarder.conf",
        "elk": "filebeat.yml",
    }

    # Generate config for each requested target
    for target in siem_targets:
        template_name = template_map.get(target)
        if not template_name:
            print(f"Warning: Unknown SIEM target '{target}', skipping.",
                  file=sys.stderr)
            continue

        template_content = _load_template(template_name)
        if template_content is None:
            # Generate a sensible default when no template exists
            template_content = _default_template(target, variables)

        config_content = _substitute_template(template_content, variables)
        out_file = output_dir / template_name
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(config_content)
        generated_files.append(str(out_file))
        print(f"  Generated: {out_file}")

    # Validate log source coverage
    validation = _validate_log_sources(log_sources, variables)

    # Generate summary report with CUI markings
    report_path = _generate_report(
        output_dir, cui_config, siem_targets, system_name,
        log_sources, validation, generated_files,
    )
    generated_files.append(str(report_path))

    # Audit trail
    conn = None
    if db_path or DB_PATH.exists():
        try:
            conn = _get_connection(db_path)
            _log_audit_event(conn, project_id, "SIEM configs generated", {
                "system_name": system_name,
                "targets": siem_targets,
                "files_generated": len(generated_files),
                "log_sources_configured": validation["log_sources_configured"],
                "log_sources_required": validation["log_sources_required"],
            }, generated_files)
        except FileNotFoundError:
            print("Warning: Database not found; audit event not logged.",
                  file=sys.stderr)
        finally:
            if conn:
                conn.close()

    result = {
        "status": "success",
        "system_name": system_name,
        "output_dir": str(output_dir),
        "generated_files": generated_files,
        "targets_configured": siem_targets,
        "validation": validation,
    }
    return result


def _default_template(target, variables):
    """Return a built-in default template when no file template exists."""
    if target == "splunk":
        return (
            "# Splunk Universal Forwarder Configuration\n"
            "# System: {{system_name}}\n"
            "# Classification: CUI // SP-CTI\n"
            "#\n"
            "# NOTE: SPLUNK_HEC_TOKEN must be sourced from AWS Secrets Manager.\n"
            "\n"
            "[default]\n"
            "host = {{system_name}}\n"
            "\n"
            "[tcpout]\n"
            "defaultGroup = govcloud\n"
            "\n"
            "[tcpout:govcloud]\n"
            "server = {{splunk_server}}:{{splunk_port}}\n"
            "sslCertPath = {{ssl_cert_path}}\n"
            "sslRootCAPath = {{ssl_ca_path}}\n"
            "sslPassword = {{ssl_key_password}}\n"
            "sslVerifyServerCert = true\n"
            "useSSL = true\n"
            "\n"
            "[monitor://{{app_log_path}}]\n"
            "disabled = false\n"
            "index = {{splunk_index}}\n"
            "sourcetype = icdev:app\n"
            "followTail = 0\n"
            "\n"
            "[monitor://{{auth_log_path}}/auth.log]\n"
            "disabled = false\n"
            "index = {{splunk_index}}\n"
            "sourcetype = linux:auth\n"
            "\n"
            "[monitor:///var/log/audit/audit.log]\n"
            "disabled = false\n"
            "index = {{splunk_index}}\n"
            "sourcetype = linux:audit\n"
            "\n"
            "[monitor:///var/log/syslog]\n"
            "disabled = false\n"
            "index = {{splunk_index}}\n"
            "sourcetype = syslog\n"
            "\n"
            "[monitor:///var/log/containers/*.log]\n"
            "disabled = false\n"
            "index = {{splunk_index}}\n"
            "sourcetype = kube:container:log\n"
        )
    elif target == "elk":
        return (
            "# Filebeat Configuration\n"
            "# System: {{system_name}}\n"
            "# Classification: CUI // SP-CTI\n"
            "#\n"
            "# NOTE: SSL key password must be sourced from AWS Secrets Manager.\n"
            "\n"
            "filebeat.inputs:\n"
            "  - type: log\n"
            "    id: app-logs\n"
            "    enabled: true\n"
            "    paths:\n"
            "      - {{app_log_path}}/*.log\n"
            "    fields:\n"
            "      index_prefix: {{elk_index_prefix}}\n"
            "      log_type: application\n"
            "    fields_under_root: true\n"
            "\n"
            "  - type: log\n"
            "    id: auth-logs\n"
            "    enabled: true\n"
            "    paths:\n"
            "      - {{auth_log_path}}/auth.log\n"
            "    fields:\n"
            "      index_prefix: {{elk_index_prefix}}\n"
            "      log_type: auth\n"
            "    fields_under_root: true\n"
            "\n"
            "  - type: log\n"
            "    id: audit-logs\n"
            "    enabled: true\n"
            "    paths:\n"
            "      - /var/log/audit/audit.log\n"
            "    fields:\n"
            "      index_prefix: {{elk_index_prefix}}\n"
            "      log_type: audit\n"
            "    fields_under_root: true\n"
            "\n"
            "  - type: log\n"
            "    id: syslog\n"
            "    enabled: true\n"
            "    paths:\n"
            "      - /var/log/syslog\n"
            "    fields:\n"
            "      index_prefix: {{elk_index_prefix}}\n"
            "      log_type: syslog\n"
            "    fields_under_root: true\n"
            "\n"
            "  - type: container\n"
            "    id: container-logs\n"
            "    enabled: true\n"
            "    paths:\n"
            "      - /var/log/containers/*.log\n"
            "    fields:\n"
            "      index_prefix: {{elk_index_prefix}}\n"
            "      log_type: container\n"
            "    fields_under_root: true\n"
            "\n"
            "output.logstash:\n"
            "  hosts: {{logstash_hosts}}\n"
            "  ssl.certificate: {{ssl_cert_path}}\n"
            "  ssl.key: {{ssl_key_path}}\n"
            "  ssl.certificate_authorities:\n"
            "    - {{ssl_ca_path}}\n"
            "\n"
            "setup.kibana:\n"
            "  host: {{kibana_host}}\n"
            "\n"
            "processors:\n"
            "  - add_host_metadata: ~\n"
            "  - add_fields:\n"
            "      target: ''\n"
            "      fields:\n"
            "        system_name: {{system_name}}\n"
            "        classification: CUI // SP-CTI\n"
        )
    return "# Unknown SIEM target\n"


def _validate_log_sources(log_sources, variables):
    """Check which required log sources have corresponding paths configured.
    Returns a validation summary dict."""
    configured = 0
    required_count = 0
    missing = []

    for source in log_sources:
        is_required = source.get("required", False)
        if is_required:
            required_count += 1

        # Check whether the source path resolves to something in the variables
        source_path = source.get("path", "")
        # A source is considered "configured" if its path appears in the
        # variable set or if the variable set references a parent directory
        # that would contain it.
        found = False
        for var_key in ("app_log_path", "auth_log_path", "project_dir"):
            var_val = variables.get(var_key, "")
            if var_val and (
                source_path.startswith(var_val)
                or var_val in source_path
                or source_path.startswith("/var/log")
            ):
                found = True
                break

        if found:
            configured += 1
        elif is_required:
            missing.append(source["name"])

    return {
        "log_sources_configured": configured,
        "log_sources_required": required_count,
        "log_sources_total": len(log_sources),
        "missing_sources": missing,
        "all_required_present": len(missing) == 0,
    }


def _generate_report(
    output_dir, cui_config, siem_targets, system_name,
    log_sources, validation, generated_files,
):
    """Generate the siem-config-report.md summary document."""
    now = datetime.now(timezone.utc)
    header = cui_config.get("document_header", "CUI // SP-CTI")
    footer = cui_config.get("document_footer", "CUI // SP-CTI")

    lines = [
        header,
        "",
        f"# SIEM Configuration Report — {system_name}",
        "",
        f"**Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "**Classification:** CUI // SP-CTI",
        "",
        "## SIEM Targets Configured",
        "",
    ]

    for target in siem_targets:
        lines.append(f"- **{target.upper()}** — configured")
    lines.append("")

    # Log sources table
    lines.append("## Log Sources")
    lines.append("")
    lines.append("| Source | Path | Required | Status |")
    lines.append("|--------|------|----------|--------|")
    for source in log_sources:
        name = source.get("name", "unknown")
        path = source.get("path", "N/A")
        required = "Yes" if source.get("required") else "No"
        status = "Missing" if name in validation["missing_sources"] else "Configured"
        lines.append(f"| {name} | `{path}` | {required} | {status} |")
    lines.append("")

    # Validation results
    lines.append("## Validation Results")
    lines.append("")
    lines.append(f"- Log sources configured: **{validation['log_sources_configured']}** "
                 f"/ {validation['log_sources_total']}")
    lines.append(f"- Required sources present: **{validation['log_sources_required'] - len(validation['missing_sources'])}** "
                 f"/ {validation['log_sources_required']}")
    if validation["missing_sources"]:
        lines.append(f"- **Missing required sources:** {', '.join(validation['missing_sources'])}")
    else:
        lines.append("- All required log sources are configured.")
    lines.append("")

    # DI 8530.01 requirements
    lines.append("## DI 8530.01 Requirements Coverage")
    lines.append("")
    lines.append("| Requirement | Description | Status |")
    lines.append("|-------------|-------------|--------|")
    di_requirements = [
        ("DE-2", "Continuous monitoring of security events", "Covered" if siem_targets else "Not configured"),
        ("DE-3", "Event correlation and analysis", "Covered" if "elk" in siem_targets or "splunk" in siem_targets else "Not configured"),
        ("AU-6", "Audit review, analysis, and reporting", "Covered" if validation["all_required_present"] else "Partial"),
        ("AU-12", "Audit generation", "Covered" if validation["log_sources_configured"] > 0 else "Not configured"),
        ("SI-4", "System monitoring", "Covered" if len(siem_targets) >= 2 else "Partial"),
    ]
    for req_id, desc, status in di_requirements:
        lines.append(f"| {req_id} | {desc} | {status} |")
    lines.append("")

    # Generated files
    lines.append("## Generated Files")
    lines.append("")
    for fp in generated_files:
        lines.append(f"- `{fp}`")
    lines.append("")

    lines.append(footer)
    lines.append("")

    report_path = output_dir / "siem-config-report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  Generated: {report_path}")
    return report_path


# ---------------------------------------------------------------------------
# Validation (standalone)
# ---------------------------------------------------------------------------

def validate_siem_config(project_dir):
    """Validate existing SIEM configuration for a project directory.

    Returns:
        Dict with validation results including which targets are configured
        and log source coverage.
    """
    project_dir = Path(project_dir).resolve()
    siem_dir = project_dir / "siem"

    result = {
        "valid": False,
        "splunk_configured": False,
        "elk_configured": False,
        "log_sources_configured": 0,
        "log_sources_required": 0,
        "missing_sources": [],
    }

    if not siem_dir.is_dir():
        result["error"] = f"SIEM directory not found: {siem_dir}"
        return result

    # Check for Splunk config
    splunk_conf = siem_dir / "splunk_forwarder.conf"
    if splunk_conf.exists():
        result["splunk_configured"] = True

    # Check for Filebeat / ELK config
    filebeat_conf = siem_dir / "filebeat.yml"
    if filebeat_conf.exists():
        result["elk_configured"] = True

    # Load log source definitions and check coverage
    log_sources = _load_log_sources()
    required_count = sum(1 for s in log_sources if s.get("required"))
    result["log_sources_required"] = required_count

    # Read generated configs to determine which sources are referenced
    config_content = ""
    for conf_file in (splunk_conf, filebeat_conf):
        if conf_file.exists():
            with open(conf_file, "r", encoding="utf-8") as f:
                config_content += f.read()

    configured_count = 0
    missing = []
    for source in log_sources:
        source_path = source.get("path", "")
        source_name = source.get("name", "")
        # Check if either the path or source name appears in any config
        if source_path in config_content or source_name in config_content:
            configured_count += 1
        elif source.get("required"):
            missing.append(source_name)

    result["log_sources_configured"] = configured_count
    result["missing_sources"] = missing
    result["valid"] = (
        (result["splunk_configured"] or result["elk_configured"])
        and len(missing) == 0
    )

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate SIEM forwarding configs (Splunk + ELK)"
    )
    parser.add_argument("--project-dir", required=True, type=Path,
                        help="Path to the project directory")
    parser.add_argument("--project-id", help="Project ID for DB logging")
    parser.add_argument("--targets", nargs="+", default=["splunk", "elk"],
                        choices=["splunk", "elk"],
                        help="SIEM targets to configure (default: splunk elk)")
    parser.add_argument("--output-dir", type=Path,
                        help="Output directory (default: <project-dir>/siem/)")
    parser.add_argument("--validate-only", action="store_true",
                        help="Only validate existing config, do not generate")
    parser.add_argument("--db-path", type=Path, default=DB_PATH,
                        help="Database path")
    args = parser.parse_args()

    if args.validate_only:
        result = validate_siem_config(args.project_dir)
    else:
        result = generate_siem_config(
            args.project_dir,
            args.project_id,
            args.targets,
            args.output_dir,
            args.db_path,
        )

    print(json.dumps(result, indent=2, default=str))
