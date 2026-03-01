# CUI // SP-CTI
# ICDEV GovProposal — CDRL Generator (Phase 60, D-CPMP-5)
# Dispatches CDRL auto-generation to existing ICDEV tools.

"""
CDRL Generator — Maps CDRL types to ICDEV tools for automated deliverable generation.

Dispatches generation requests to existing ICDEV platform tools:
    ssp → ssp_generator.py
    sbom → sbom_generator.py
    poam → poam_generator.py
    stig_checklist → stig_checker.py
    evm_report → evm_engine.py
    icd → icd_generator.py
    tsp → tsp_generator.py
    test_report → test_orchestrator.py
    security_scan → sast_runner.py

Usage:
    python tools/govcon/cdrl_generator.py --generate --deliverable-id <id> --json
    python tools/govcon/cdrl_generator.py --generate-due --contract-id <id> --json
    python tools/govcon/cdrl_generator.py --list-generations --contract-id <id> --json
    python tools/govcon/cdrl_generator.py --tool-mapping --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


def _load_config():
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            cfg = yaml.safe_load(f)
            return cfg.get("cpmp", {}).get("cdrl", {})
    return {}


_CFG = _load_config()

OUTPUT_DIR = _ROOT / _CFG.get("output_dir", "data/cdrl_output")
AUTO_GENERATE_DAYS = _CFG.get("auto_generate_days_before_due", 14)

TOOL_MAPPING = _CFG.get("tool_mapping", {
    "ssp": "tools/compliance/ssp_generator.py",
    "sbom": "tools/compliance/sbom_generator.py",
    "poam": "tools/compliance/poam_generator.py",
    "stig_checklist": "tools/compliance/stig_checker.py",
    "evm_report": "tools/govcon/evm_engine.py",
    "icd": "tools/mosa/icd_generator.py",
    "tsp": "tools/mosa/tsp_generator.py",
    "test_report": "tools/testing/test_orchestrator.py",
    "security_scan": "tools/security/sast_runner.py",
})


def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="cdrl_generator"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "cpmp.cdrl_generator", actor, action, details, "cpmp"),
        )
    except Exception:
        pass


def _file_hash(filepath):
    """SHA-256 hash of file contents."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def generate_cdrl(deliverable_id, project_id=None):
    """Generate a CDRL by dispatching to the appropriate ICDEV tool.

    Steps:
    1. Look up deliverable and its cdrl_type / deliverable_type
    2. Find matching tool from TOOL_MAPPING
    3. Execute tool via subprocess
    4. Record generation in cpmp_cdrl_generations (append-only)
    5. Update deliverable with generated_by_tool and output_path
    """
    conn = _get_db()
    deliv = conn.execute(
        "SELECT * FROM cpmp_deliverables WHERE id = ?", (deliverable_id,)
    ).fetchone()
    if not deliv:
        conn.close()
        return {"status": "error", "message": f"Deliverable {deliverable_id} not found"}

    contract_id = deliv["contract_id"]

    # Determine CDRL type from deliverable cdrl_type or type column
    cdrl_type = deliv["cdrl_type"] or deliv["type"]

    tool_path = TOOL_MAPPING.get(cdrl_type)
    if not tool_path:
        conn.close()
        return {
            "status": "error",
            "message": f"No tool mapping for CDRL type '{cdrl_type}'. Available: {list(TOOL_MAPPING.keys())}",
        }

    tool_full_path = _ROOT / tool_path
    if not tool_full_path.exists():
        conn.close()
        return {"status": "error", "message": f"Tool not found: {tool_path}"}

    # Prepare output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_filename = f"{cdrl_type}_{deliv['cdrl_number'] or deliverable_id[:8]}_{datetime.now().strftime('%Y%m%d')}"
    output_path = OUTPUT_DIR / output_filename

    # Build tool arguments
    tool_args = [sys.executable, str(tool_full_path)]
    pid = project_id
    if pid:
        tool_args.extend(["--project-id", pid])
    tool_args.append("--json")

    gen_id = _uuid()
    error_message = None
    status = "generated"
    output_hash = None

    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith("_")}
        result = subprocess.run(
            tool_args,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode == 0:
            # Write output
            out_file = str(output_path) + ".json"
            with open(out_file, "w") as f:
                f.write(result.stdout)
            output_hash = _file_hash(out_file)
            output_path = out_file
        else:
            status = "failed"
            error_message = result.stderr[:500] if result.stderr else f"Exit code {result.returncode}"
            output_path = None

    except subprocess.TimeoutExpired:
        status = "failed"
        error_message = "Tool execution timed out (300s)"
        output_path = None
    except Exception as e:
        status = "failed"
        error_message = str(e)[:500]
        output_path = None

    # Record generation (append-only)
    conn.execute(
        "INSERT INTO cpmp_cdrl_generations "
        "(id, deliverable_id, contract_id, cdrl_type, generation_tool, "
        "output_path, output_hash, status, error_message, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            gen_id, deliverable_id, contract_id, cdrl_type, tool_path,
            str(output_path) if output_path else None,
            output_hash, status, error_message, _now(),
        ),
    )

    # Update deliverable if successful
    if status == "generated":
        conn.execute(
            "UPDATE cpmp_deliverables SET generated_by_tool = ?, updated_at = ? WHERE id = ?",
            (tool_path, _now(), deliverable_id),
        )

    _audit(conn, "generate_cdrl",
           f"Generated {cdrl_type} for deliverable {deliverable_id}: {status}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "generation_id": gen_id,
        "cdrl_type": cdrl_type,
        "tool": tool_path,
        "generation_status": status,
        "output_path": str(output_path) if output_path else None,
        "output_hash": output_hash,
        "error": error_message,
    }


def generate_all_due(contract_id=None, days_ahead=None):
    """Generate all CDRLs due within the configured window."""
    days = days_ahead or AUTO_GENERATE_DAYS
    conn = _get_db()

    query = (
        "SELECT id, contract_id FROM cpmp_deliverables "
        "WHERE due_date BETWEEN date('now') AND date('now', ? || ' days') "
        "AND status IN ('not_started', 'in_progress') "
        "AND generated_by_tool IS NULL"
    )
    params = [str(days)]
    if contract_id:
        query += " AND contract_id = ?"
        params.append(contract_id)

    deliverables = conn.execute(query, params).fetchall()
    conn.close()

    results = []
    for d in deliverables:
        r = generate_cdrl(d["id"])
        results.append(r)

    return {
        "status": "ok",
        "generated": len([r for r in results if r.get("generation_status") == "generated"]),
        "failed": len([r for r in results if r.get("generation_status") == "failed"]),
        "total": len(results),
        "results": results,
    }


def list_generations(contract_id=None, deliverable_id=None, status_filter=None):
    """List CDRL generation records."""
    conn = _get_db()
    query = "SELECT * FROM cpmp_cdrl_generations WHERE 1=1"
    params = []
    if contract_id:
        query += " AND contract_id = ?"
        params.append(contract_id)
    if deliverable_id:
        query += " AND deliverable_id = ?"
        params.append(deliverable_id)
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    query += " ORDER BY created_at DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "generations": [dict(r) for r in rows]}


def get_tool_mapping():
    """Return the current CDRL type → tool mapping."""
    return {
        "status": "ok",
        "tool_mapping": TOOL_MAPPING,
        "output_dir": str(OUTPUT_DIR),
        "auto_generate_days": AUTO_GENERATE_DAYS,
    }


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovProposal CDRL Generator (Phase 60)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true", help="Generate CDRL for a deliverable")
    group.add_argument("--generate-due", action="store_true", help="Generate all due CDRLs")
    group.add_argument("--list-generations", action="store_true", help="List generation records")
    group.add_argument("--tool-mapping", action="store_true", help="Show tool mapping")

    parser.add_argument("--deliverable-id")
    parser.add_argument("--contract-id")
    parser.add_argument("--project-id")
    parser.add_argument("--days-ahead", type=int)
    parser.add_argument("--status-filter")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.generate:
        if not args.deliverable_id:
            print("Error: --deliverable-id required", file=sys.stderr)
            sys.exit(1)
        result = generate_cdrl(args.deliverable_id, args.project_id)
    elif args.generate_due:
        result = generate_all_due(args.contract_id, args.days_ahead)
    elif args.list_generations:
        result = list_generations(args.contract_id, args.deliverable_id, args.status_filter)
    elif args.tool_mapping:
        result = get_tool_mapping()
    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
