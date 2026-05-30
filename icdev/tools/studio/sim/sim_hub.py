# CUI // SP-CTI
"""Studio Simulation Hub — per-canvas status reader and run launcher.

Reads data/studio_artifacts/<canvas>/training/*/training_pair.json to surface
the last simulation result for each canvas without running anything.

Usage (standalone):
    python tools/studio/sim/sim_hub.py --status --json
    python tools/studio/sim/sim_hub.py --canvas ndc --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_BASE = _ROOT / "data" / "studio_artifacts"
_GNS3_SIM = _ROOT / "tools" / "studio" / "executors" / "gns3_sim.py"

_CANVASES = [
    "ndc", "sdc", "bdc", "pdc", "odc", "idc",
    "qdc", "mdc", "aadc", "aimc", "ohc", "ddc",
]

_CANVAS_LABELS = {
    "ndc":  "Network (NDC)",
    "sdc":  "Security (SDC)",
    "bdc":  "Boundary (BDC)",
    "pdc":  "Pipeline (PDC)",
    "odc":  "Observability (ODC)",
    "idc":  "Infrastructure (IDC)",
    "qdc":  "Quality (QDC)",
    "mdc":  "Migration (MDC)",
    "aadc": "Agentic AI (AADC)",
    "aimc": "AI Model (AIMC)",
    "ohc":  "Ops Health (OHC)",
    "ddc":  "Data (DDC)",
}


def get_canvas_sim_status(canvas: str) -> dict[str, Any]:
    """Return last simulation result dict for a single canvas."""
    base = _ARTIFACTS_BASE / canvas / "training"
    result: dict[str, Any] = {
        "canvas": canvas,
        "label": _CANVAS_LABELS.get(canvas, canvas.upper()),
        "gate": "NEVER",
        "last_run": None,
        "nodes_deployed": 0,
        "links_deployed": 0,
        "probes_passed": 0,
        "probes_total": 0,
        "traffic_flows_tested": 0,
        "traffic_flows_reachable": 0,
        "training_examples": 0,
        "mode": None,
    }

    if not base.exists():
        return result

    pairs = sorted(base.rglob("training_pair.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pairs:
        return result

    try:
        data = json.loads(pairs[0].read_text(encoding="utf-8"))
    except Exception:
        return result

    out = data.get("output", {})
    mtime = pairs[0].stat().st_mtime
    result.update({
        "gate":                  out.get("gate", data.get("gate", "UNKNOWN")),
        "last_run":              datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "nodes_deployed":        out.get("nodes_deployed", 0),
        "links_deployed":        out.get("links_deployed", 0),
        "probes_passed":         out.get("probes_passed", 0),
        "probes_total":          out.get("probes_total", 0),
        "traffic_flows_tested":  out.get("traffic_flows_tested",
                                          out.get("traffic", {}).get("flows_tested", 0)),
        "traffic_flows_reachable": out.get("traffic_flows_reachable",
                                            out.get("traffic", {}).get("reachable", 0)),
        "mode":                  out.get("mode"),
    })
    # count total training examples on disk
    result["training_examples"] = len(pairs)
    return result


def get_all_canvas_statuses() -> list[dict[str, Any]]:
    """Return status for all 12 canvases."""
    return [get_canvas_sim_status(c) for c in _CANVASES]


def get_ft_dataset_counts() -> dict[str, int]:
    """Return {canvas: example_count} from ft_datasets (best-effort)."""
    counts: dict[str, int] = {}
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT name, example_count FROM ft_datasets WHERE name LIKE ?",
            ("icdev-%-sim",),
        ).fetchall()
        conn.close()
        for row in rows:
            canvas = row["name"].replace("icdev-", "").replace("-sim", "")
            counts[canvas] = row["example_count"] or 0
    except Exception:
        pass
    return counts


def run_canvas_sim(canvas: str, dry_run: bool = False) -> dict[str, Any]:
    """Spawn gns3_sim.py --canvas <canvas> as a background subprocess.

    Returns immediately with pid; caller polls /api/studio/sim/status to track.
    """
    if canvas not in _CANVASES:
        return {"error": f"unknown canvas '{canvas}'"}
    cmd = [sys.executable, str(_GNS3_SIM), "--canvas", canvas, "--json"]
    if dry_run:
        cmd.append("--dry-run")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"status": "started", "canvas": canvas, "pid": proc.pid}
    except Exception as exc:
        return {"status": "error", "canvas": canvas, "error": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Studio Simulation Hub")
    parser.add_argument("--status", action="store_true", help="Show all canvas statuses")
    parser.add_argument("--canvas", default="", help="Single canvas status")
    parser.add_argument("--run", action="store_true", help="Run simulation for --canvas")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run mode")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.run and args.canvas:
        result = run_canvas_sim(args.canvas, dry_run=args.dry_run)
        print(json.dumps(result))
        return

    if args.canvas:
        result = get_canvas_sim_status(args.canvas)
        print(json.dumps(result, indent=2))
        return

    statuses = get_all_canvas_statuses()
    ft_counts = get_ft_dataset_counts()
    for s in statuses:
        s["ft_examples"] = ft_counts.get(s["canvas"], 0)

    if args.json or args.status:
        print(json.dumps(statuses, indent=2))
        return

    # human-readable table
    print(f"{'Canvas':<20} {'Gate':<8} {'Last Run':<22} {'Probes':>8} {'Traffic':>10} {'Train':>7}")
    print("-" * 80)
    for s in statuses:
        probes = f"{s['probes_passed']}/{s['probes_total']}"
        traffic = f"{s['traffic_flows_reachable']}/{s['traffic_flows_tested']}"
        print(f"{s['label']:<20} {s['gate']:<8} {(s['last_run'] or 'Never'):<22} {probes:>8} {traffic:>10} {s['training_examples']:>7}")


if __name__ == "__main__":
    main()
# CUI // SP-CTI
