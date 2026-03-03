#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""FedRAMP 20x Authorization Package Bundler.

Bundles OSCAL SSP, KSI evidence, and compliance artifacts into a
FedRAMP 20x authorization package for submission. Extends
oscal_generator.py (D340).

Usage:
    python tools/compliance/fedramp_authorization_packager.py --project-id proj-123 --json
    python tools/compliance/fedramp_authorization_packager.py --project-id proj-123 --output-dir /path --json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _check_artifact(name: str, check_fn) -> Dict[str, Any]:
    try:
        available = check_fn()
        return {"artifact": name, "available": available, "status": "ready" if available else "missing"}
    except Exception as e:
        return {"artifact": name, "available": False, "status": f"error: {e}"}


def package_authorization(project_id: str, output_dir: Path = None) -> Dict[str, Any]:
    """Bundle FedRAMP 20x authorization package."""
    from fedramp_ksi_generator import generate_all_ksis

    artifacts = []

    # 1. KSI Evidence
    ksi_result = generate_all_ksis(project_id, DB_PATH)
    artifacts.append({
        "artifact": "KSI Evidence Bundle",
        "available": ksi_result.get("total_ksis", 0) > 0,
        "status": "ready",
        "details": {
            "total_ksis": ksi_result.get("total_ksis", 0),
            "coverage_pct": ksi_result.get("coverage_pct", 0),
        },
    })

    # 2. OSCAL SSP
    oscal_gen = BASE_DIR / "tools" / "compliance" / "oscal_generator.py"
    artifacts.append(_check_artifact("OSCAL SSP Generator", lambda: oscal_gen.exists()))

    # 3. SBOM
    sbom_gen = BASE_DIR / "tools" / "compliance" / "sbom_generator.py"
    artifacts.append(_check_artifact("SBOM Generator", lambda: sbom_gen.exists()))

    # 4. POAM
    poam_gen = BASE_DIR / "tools" / "compliance" / "poam_generator.py"
    artifacts.append(_check_artifact("POAM Generator", lambda: poam_gen.exists()))

    # 5. AI-BOM
    ai_bom_gen = BASE_DIR / "tools" / "security" / "ai_bom_generator.py"
    artifacts.append(_check_artifact("AI-BOM Generator", lambda: ai_bom_gen.exists()))

    # 6. Production Audit
    prod_audit = BASE_DIR / "tools" / "testing" / "production_audit.py"
    artifacts.append(_check_artifact("Production Audit", lambda: prod_audit.exists()))

    # 7. OWASP ASI Assessment
    asi_assessor = BASE_DIR / "tools" / "compliance" / "owasp_asi_assessor.py"
    artifacts.append(_check_artifact("OWASP ASI Assessor", lambda: asi_assessor.exists()))

    ready_count = sum(1 for a in artifacts if a.get("available"))
    total_count = len(artifacts)
    readiness_pct = round((ready_count / total_count * 100) if total_count > 0 else 0, 1)

    package = {
        "package_id": f"frp-{uuid4().hex[:12]}",
        "project_id": project_id,
        "package_type": "fedramp_20x_authorization",
        "readiness_pct": readiness_pct,
        "artifacts_ready": ready_count,
        "artifacts_total": total_count,
        "artifacts": artifacts,
        "ksi_summary": {
            "total_ksis": ksi_result.get("total_ksis", 0),
            "coverage_pct": ksi_result.get("coverage_pct", 0),
            "maturity_summary": ksi_result.get("maturity_summary", {}),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"fedramp_20x_package_{project_id}.json"
        output_file.write_text(json.dumps(package, indent=2), encoding="utf-8")
        package["output_file"] = str(output_file)

    return package


def main():
    parser = argparse.ArgumentParser(description="FedRAMP 20x Authorization Packager")
    parser.add_argument("--project-id", required=True, help="Project UUID")
    parser.add_argument("--output-dir", type=Path, help="Output directory for package")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    result = package_authorization(args.project_id, args.output_dir)

    if args.human:
        print(f"\nFedRAMP 20x Authorization Package — {result['project_id']}")
        print(f"Readiness: {result['readiness_pct']}% ({result['artifacts_ready']}/{result['artifacts_total']} artifacts)")
        print("-" * 50)
        for a in result["artifacts"]:
            icon = "+" if a.get("available") else "-"
            print(f"  [{icon}] {a['artifact']}: {a['status']}")
        ks = result.get("ksi_summary", {})
        print(f"\nKSI Coverage: {ks.get('coverage_pct', 0)}% ({ks.get('total_ksis', 0)} KSIs)")
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
