#!/usr/bin/env python3
# CUI // SP-CTI
"""NDC Migration Document Generator.

Generates a comprehensive migration runbook for network device replacement
by assembling data from the NDC database, replacement recommender, COA planner,
config alignment analyzer, and existing migration canvas tables.

Usage:
    python tools/ndc/migration_document_generator.py --device-id <id> --coa 3 --json
    python tools/ndc/migration_document_generator.py --device-id <id> --format markdown --output runbook.md
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_NC_DB = BASE_DIR / "data" / "network_canvas.db"
_TEMPLATE_DIR = BASE_DIR / "tools" / "ndc" / "templates" / "migration_runbook"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nc_conn():
    # PG-primary via the Network Canvas helper (NC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.network.db.init_db import get_connection

    return get_connection()


def _get_device(conn: sqlite3.Connection, device_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """SELECT id, vendor, model, device_type, firmware_version,
                  site, rack_location, eol_date, eos_date,
                  replacement_cost, criticality_score, downstream_count, label
           FROM ni_devices WHERE id = %s""",
        (device_id,),
    ).fetchone()
    return dict(row) if row else None


def _get_config_text(device_id: str) -> str:
    conn = _nc_conn()
    try:
        row = conn.execute(
            """SELECT config_text FROM ni_device_configs
               WHERE device_id = %s
               ORDER BY CASE config_type
                 WHEN 'running' THEN 1
                 WHEN 'startup' THEN 2
                 ELSE 3
               END, created_at DESC LIMIT 1""",
            (device_id,),
        ).fetchone()
        return row["config_text"] if row and row["config_text"] else ""
    finally:
        conn.close()


def _get_topology_for_device(device_id: str) -> Dict[str, Any]:
    conn = _nc_conn()
    try:
        row = conn.execute(
            "SELECT topology_id FROM ni_devices WHERE id = %s", (device_id,)
        ).fetchone()
        topo_id = row["topology_id"] if row else None
        if not topo_id:
            return {"nodes": [], "edges": []}
        topo = conn.execute(
            "SELECT graph_json FROM topologies WHERE id = %s", (topo_id,)
        ).fetchone()
        if topo and topo["graph_json"]:
            try:
                return json.loads(topo["graph_json"])
            except Exception:
                pass
        return {"nodes": [], "edges": []}
    finally:
        conn.close()


def _get_replacement(device_id: str) -> Optional[Dict[str, Any]]:
    try:
        from tools.ndc.replacement_recommender import recommend
        result = recommend(device_id=device_id, top_k=1, include_rag_sops=False)
        recs = result.get("recommendations", [])
        if recs:
            return recs[0]
    except Exception:
        pass
    return None


def _get_coas(src: Dict[str, Any], tgt: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from tools.migration_canvas.network_migration import generate_coas
        return generate_coas(src, tgt)
    except Exception:
        return {}


def _get_alignment(device_id: str) -> Dict[str, Any]:
    try:
        from tools.ndc.config_alignment_analyzer import analyze_device
        return analyze_device(device_id, use_llm=False)
    except Exception:
        return {"overall_status": "UNKNOWN", "overall_score": 0, "sections": [], "recommendations": []}


def _build_port_map(src: Dict[str, Any], tgt: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        from tools.migration_canvas.network_migration import (
            parse_source_config,
            generate_port_map,
            fetch_hardware_profiles,
        )
        config_text = _get_config_text(src["id"])
        if not config_text:
            return []
        parsed = parse_source_config(config_text)
        hw = fetch_hardware_profiles(src["model"], tgt["model"])
        port_map_result = generate_port_map(parsed.get("interfaces", []), hw.get("target", {}))
        return port_map_result.get("mappings", [])
    except Exception:
        return []


def _build_config_diff(src: Dict[str, Any], tgt: Dict[str, Any]) -> str:
    try:
        from tools.migration_canvas.network_migration import parse_source_config, convert_config
        config_text = _get_config_text(src["id"])
        if not config_text:
            return ""
        parsed = parse_source_config(config_text)
        port_map = _build_port_map(src, tgt)
        converted = convert_config(parsed, port_map)
        unmapped = converted.get("unmapped", [])
        lines = [f"Port map applied: {len(converted.get('port_map_applied', {}))} interfaces"]
        if unmapped:
            lines.append(f"Unmapped interfaces: {', '.join(unmapped[:5])}")
        return "\n".join(lines)
    except Exception:
        return ""


def _render_markdown(context: Dict[str, Any]) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=False)  # nosec: B701 — output is Markdown (.md.j2), not HTML; XSS not applicable
        template = env.get_template("runbook.md.j2")
        return template.render(**context)
    except Exception as exc:
        return f"# Render Error\n\n{exc}"


def _render_drawio_xml(context: Dict[str, Any]) -> str:
    """Generate a basic DrawIO XML with old + new device nodes."""
    src = context["source_device"]
    tgt = context["target_device"]
    migration_id = context["migration_id"]
    return f"""&lt;mxfile host="icdev" modified="{_now()}" agent="ICDEV" version="1.0" etag="{migration_id}"&gt;
  &lt;diagram name="Migration Topology"&gt;
    &lt;mxGraphModel dx="800" dy="400" grid="1"&gt;
      &lt;root&gt;
        &lt;mxCell id="0" /&gt;
        &lt;mxCell id="1" parent="0" /&gt;
        &lt;mxCell id="src" value="{src['vendor']} {src['model']}" style="ellipse;fillColor=#dae8fc;" vertex="1" parent="1"&gt;
          &lt;mxGeometry x="120" y="80" width="120" height="60" /&gt;
        &lt;/mxCell&gt;
        &lt;mxCell id="tgt" value="{tgt['vendor']} {tgt['model']}" style="ellipse;fillColor=#d5e8d4;" vertex="1" parent="1"&gt;
          &lt;mxGeometry x="360" y="80" width="120" height="60" /&gt;
        &lt;/mxCell&gt;
        &lt;mxCell id="arrow" value="migrate" edge="1" parent="1" source="src" target="tgt"&gt;
          &lt;mxGeometry relative="1" /&gt;
        &lt;/mxCell&gt;
      &lt;/root&gt;
    &lt;/mxGraphModel&gt;
  &lt;/diagram&gt;
&lt;/mxfile&gt;"""


def _render_ansible(context: Dict[str, Any]) -> str:
    src = context["source_device"]
    tgt = context["target_device"]
    return f"""---
# Ansible playbook generated by ICDEV Migration Document Generator
# Source: {src['vendor']} {src['model']} → Target: {tgt['vendor']} {tgt['model']}
- name: Deploy target device base configuration
  hosts: network_targets
  gather_facts: false
  tasks:
    - name: Set hostname
      ansible.netcommon.cli_config:
        config: "hostname {{ context['migration_id'] }}"
      when: false  # Placeholder — replace with actual device connection
"""


def _render_terraform(context: Dict[str, Any]) -> str:
    return """# Terraform HCL placeholder for cloud connectivity changes
# Generated by ICDEV Migration Document Generator
# Add AWS/Azure/GCP resource blocks here as needed.
"""


def generate_runbook(
    device_id: str,
    coa_choice: int = 3,
    output_format: str = "markdown",
    output_path: str = "",
) -> Dict[str, Any]:
    """Generate a complete migration runbook for a device."""
    conn = _nc_conn()
    try:
        src = _get_device(conn, device_id)
    finally:
        conn.close()

    if not src:
        return {"error": f"Device not found: {device_id}"}

    tgt_rec = _get_replacement(device_id)
    if not tgt_rec:
        return {"error": "No replacement recommendation found. Run replacement_recommender first."}

    tgt = {
        "vendor": tgt_rec["vendor"],
        "model": tgt_rec["model"],
        "throughput_gbps": tgt_rec.get("throughput_gbps", 0),
        "rack_units": tgt_rec.get("rack_units", 0),
        "replacement_cost": tgt_rec.get("replacement_cost", 0),
    }

    coas = _get_coas(src, tgt)
    selected_coa = coas.get(f"coa_{coa_choice}", coas.get("coa_3", {}))

    alignment = _get_alignment(device_id)
    port_map = _build_port_map(src, tgt)
    topology = _get_topology_for_device(device_id)
    config_diff = _build_config_diff(src, tgt)
    config_text = _get_config_text(device_id)

    migration_id = f"mig-{uuid.uuid4().hex[:10]}"

    context = {
        "classification": "CUI // SP-CTI",
        "migration_id": migration_id,
        "generated_at": _now(),
        "site": src.get("site", ""),
        "source_device": src,
        "target_device": tgt,
        "selected_coa": selected_coa,
        "coa_justification": coas.get("recommendation", ""),
        "executive_summary": {
            "why": f"Source device {src['vendor']} {src['model']} is approaching EOL ({src.get('eol_date','unknown')}). Replacement is required to maintain supportability and compliance.",
            "what": f"Migrate all services from {src['vendor']} {src['model']} to {tgt['vendor']} {tgt['model']}.",
            "when": "Scheduled during approved maintenance window.",
            "who": "Network Engineering Team / ICDEV Migration Canvas",
        },
        "topology_json": topology,
        "config_summary": config_text[:1000] if config_text else "No config available.",
        "config_diff": config_diff,
        "port_map": port_map,
        "validation": {
            "pre": [
                "Capture baseline traffic counters",
                "Snapshot routing table",
                "Verify BGP neighbor states",
                "Backup running config",
                "Confirm management reachability",
            ],
            "cutover": [
                "Verify link up on migrated ports",
                "Confirm BGP re-establishment",
                "Ping all BGP peers",
                "Verify OSPF/ISIS adjacency",
                "Traceroute path validation",
            ],
            "post": [
                "Zero CRC errors after 1h",
                "Traffic within ±10% baseline",
                "No critical alarms",
                "NOC sign-off",
                "Update CMDB/NetBox",
            ],
        },
        "alignment": alignment,
        "risks": [
            {
                "description": "BGP session failure during cutover",
                "likelihood": "Medium",
                "impact": "High",
                "mitigation": "Pre-stage BGP config; use passive mode; verify prefix counts.",
                "owner": "Network Engineering",
            },
            {
                "description": "Optic incompatibility on target",
                "likelihood": "Low",
                "impact": "Medium",
                "mitigation": "Verify ports_json against source optics before ordering.",
                "owner": "Hardware Team",
            },
            {
                "description": "Insufficient target FIB capacity",
                "likelihood": "Low",
                "impact": "High",
                "mitigation": "Compare routing_table_size in hardware profiles.",
                "owner": "Architecture Team",
            },
        ],
        "erb_qa": [
            {
                "question": "Why this device model?",
                "answer": f"The {tgt['vendor']} {tgt['model']} was selected by the replacement recommender based on hardware parity ({tgt_rec.get('scores',{}).get('hardware_parity','N/A')}), feature parity ({tgt_rec.get('scores',{}).get('feature_parity','N/A')}), and cost score ({tgt_rec.get('scores',{}).get('cost','N/A')}).",
            },
            {
                "question": "What if the new device fails during cutover?",
                "answer": "Rollback procedures are defined per phase. The source device remains racked and powered for 30 days as an emergency fallback.",
            },
            {
                "question": "How do we verify traffic is flowing correctly?",
                "answer": "Pre-, during-, and post-cutover checklists include ping tests, route-table comparison, traffic counter validation, and NOC sign-off.",
            },
            {
                "question": "What is the impact on existing SLAs?",
                "answer": f"Selected COA ({selected_coa.get('name','Unknown')}) estimates downtime of {selected_coa.get('estimated_downtime','TBD')}. SLA impact is minimized by phased or side-by-side strategies.",
            },
            {
                "question": "How does this affect cross-domain traffic?",
                "answer": "Upstream/downstream devices maintain existing routing adjacencies. Traffic shift is controlled via routing metrics or HSRP/VRRP priority.",
            },
            {
                "question": "What STIG/cATO changes are needed?",
                "answer": "Post-migration STIG scan required. Any new CAT1 findings from the alignment analyzer must be remediated before cATO re-authorization.",
            },
        ],
        "rollback_contact": "NOC Lead / On-call Network Architect",
    }

    if output_format == "markdown":
        body = _render_markdown(context)
    elif output_format == "drawio":
        body = _render_drawio_xml(context)
    elif output_format == "ansible":
        body = _render_ansible(context)
    elif output_format == "terraform":
        body = _render_terraform(context)
    else:
        body = json.dumps(context, indent=2)

    if output_path:
        Path(output_path).write_text(body, encoding="utf-8")

    return {
        "classification": "CUI // SP-CTI",
        "migration_id": migration_id,
        "device_id": device_id,
        "format": output_format,
        "output_path": output_path,
        "body": body,
        "context": context,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="NDC Migration Document Generator")
    parser.add_argument("--device-id", type=str, required=True, help="Source device ID")
    parser.add_argument("--coa", type=int, default=3, choices=[1, 2, 3], help="COA selection (1=Rip, 2=Phased, 3=Side-by-Side)")
    parser.add_argument("--format", type=str, default="markdown", choices=["markdown", "json", "drawio", "ansible", "terraform"], help="Output format")
    parser.add_argument("--output", type=str, default="", help="Output file path")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    result = generate_runbook(
        device_id=args.device_id,
        coa_choice=args.coa,
        output_format=args.format,
        output_path=args.output,
    )

    if args.json_output:
        # Strip large body from JSON to avoid overflow
        out = {k: v for k, v in result.items() if k != "body"}
        out["body_length"] = len(result.get("body", ""))
        sys.stdout.buffer.write(json.dumps(out, indent=2).encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
    else:
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            if args.output:
                print(f"Runbook written to {args.output}")
            else:
                sys.stdout.buffer.write(result["body"].encode("utf-8"))
                sys.stdout.buffer.write(b"\n")


if __name__ == "__main__":
    main()
