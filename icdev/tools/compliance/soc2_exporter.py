#!/usr/bin/env python3
# CUI // SP-CTI
"""SOC 2 report exporter — renders evidence coverage as HTML or JSON.

Usage:
    python tools/compliance/soc2_exporter.py --tenant-id <tid> --output report.html
    python tools/compliance/soc2_exporter.py --tenant-id <tid> --format json --output report.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


_CONTROLS_YAML = Path(__file__).parent / "soc2_controls.yaml"
_TEMPLATE_PATH = Path(__file__).parent / "templates" / "soc2_report.html.j2"


def _load_controls() -> dict[str, Any]:
    with open(_CONTROLS_YAML, encoding="utf-8") as fh:
        return yaml.safe_load(fh).get("controls", {})


def _collect_coverage(tenant_id: str, framework: str = "soc2") -> dict[str, Any]:
    """Query evidence_items to build per-control coverage data."""
    from tools.db.storage import get_connection

    controls = _load_controls()
    coverage: dict[str, dict[str, Any]] = {}

    for ctrl_id, ctrl in controls.items():
        coverage[ctrl_id] = {
            "control_id": ctrl_id,
            "description": ctrl.get("description", ""),
            "category": ctrl.get("category", "CC"),
            "evidence_count": 0,
            "last_collected": None,
            "status": "not_evidenced",
        }

    with get_connection() as conn:
        try:
            rows = conn.execute(
                "SELECT control_id, COUNT(*) as cnt, MAX(collected_at) as last_at "
                "FROM evidence_items "
                "WHERE tenant_id = %s AND framework = %s "
                "GROUP BY control_id",
                (tenant_id, framework),
            ).fetchall()
        except Exception:
            rows = []

    for row in rows:
        ctrl_id, cnt, last_at = row[0], row[1], row[2]
        if ctrl_id in coverage:
            coverage[ctrl_id]["evidence_count"] = cnt
            coverage[ctrl_id]["last_collected"] = last_at
            coverage[ctrl_id]["status"] = "evidenced"

    total = len(coverage)
    evidenced = sum(1 for c in coverage.values() if c["status"] == "evidenced")
    pct = round(evidenced / total * 100, 1) if total else 0.0

    return {
        "tenant_id": tenant_id,
        "framework": framework,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"total_controls": total, "evidenced": evidenced, "coverage_pct": pct},
        "controls": list(coverage.values()),
    }


def export_report(
    tenant_id: str,
    output_path: str | Path,
    fmt: str = "html",
    framework: str = "soc2",
) -> dict[str, Any]:
    """Generate SOC 2 evidence report.

    Returns the coverage data dict (useful in tests).
    """
    output_path = Path(output_path)
    data = _collect_coverage(tenant_id, framework)

    if fmt == "json":
        output_path.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="")
    else:
        try:
            from jinja2 import Environment, FileSystemLoader, select_autoescape
            env = Environment(
                loader=FileSystemLoader(str(_TEMPLATE_PATH.parent)),
                autoescape=select_autoescape(["html"]),
            )
            tmpl = env.get_template(_TEMPLATE_PATH.name)
            html = tmpl.render(**data)
        except Exception:
            # Fallback: minimal HTML without Jinja2
            rows_html = "".join(
                f"<tr><td>{c['control_id']}</td><td>{c['description'][:60]}</td>"
                f"<td>{c['status']}</td><td>{c['evidence_count']}</td>"
                f"<td>{c['last_collected'] or ''}</td></tr>"
                for c in data["controls"]
            )
            html = (
                f"<!DOCTYPE html><html><body>"
                f"<h1>SOC 2 Evidence Report — {tenant_id}</h1>"
                f"<p>Generated: {data['generated_at']}</p>"
                f"<p>Coverage: {data['summary']['coverage_pct']}% "
                f"({data['summary']['evidenced']}/{data['summary']['total_controls']} controls)</p>"
                f"<table border=1><tr><th>Control</th><th>Description</th>"
                f"<th>Status</th><th>Evidence</th><th>Last Collected</th></tr>"
                f"{rows_html}</table></body></html>"
            )
        output_path.write_text(html, encoding="utf-8", newline="")

    return data


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="SOC 2 report exporter")
    p.add_argument("--tenant-id", default="default")
    p.add_argument("--framework", default="soc2")
    p.add_argument("--format", dest="fmt", choices=["html", "json"], default="html")
    p.add_argument("--output", default="soc2_report.html")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Print coverage summary as JSON to stdout")
    args = p.parse_args(argv)

    data = export_report(args.tenant_id, args.output, fmt=args.fmt, framework=args.framework)

    if args.as_json:
        print(json.dumps(data["summary"], indent=2))
    else:
        s = data["summary"]
        print(f"Report written to {args.output}")
        print(f"Coverage: {s['coverage_pct']}% ({s['evidenced']}/{s['total_controls']} controls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
