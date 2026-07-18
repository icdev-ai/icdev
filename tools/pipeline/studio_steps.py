#!/usr/bin/env python3
# CUI // SP-CTI — PDC Studio Workflow Steps (live-engine adapter)
"""Studio workflow adapter for the Pipeline Design Canvas (PDC).

Replaces the retired ``tools/pdc/*`` trio, which graded a hardcoded demo
pipeline and emitted fabricated gate/compliance results whenever their
non-existent backing tables (``pdc_designs`` / ``pipeline_designs``) were
absent. This module reads the *real* design from the live ``pipelines`` table
(``pipelines.graph_json``) via the canvas connection and runs the live
analysis engine:

    * scan / antipattern → tools.pipeline.antipattern_detector + premerge_runner
    * iac                → tools.pipeline.deploy_generator + iac_validator

Invocation contract (driven by ``tools/studio/workflow_runner.py``):

    python tools/pipeline/studio_steps.py --step <scan|antipattern|iac> \
        --project-id <pipeline-id> [--pipeline-id <id>] [--run-id <id>] --json

The workflow runner injects ``--project-id`` (the design/pipeline id),
``--run-id``, and ``--json`` automatically; the ``step`` value is supplied per
step via the template ``args:`` block. Exit code IS the gate: 0 = pass/warn,
non-zero = fail. stdout is a single JSON object with an ``artifacts`` list that
downstream shared executors (terraform_plan, ansible_executor, …) consume.

FAIL-LOUD contract: if the referenced pipeline id does not exist in the
``pipelines`` table, every step exits non-zero with an explicit error JSON.
It NEVER falls back to demo data and NEVER emits PASS on missing input.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.pipeline.antipattern_detector import detect_antipatterns  # noqa: E402
from tools.pipeline.deploy_generator import generate_deploy_bundle  # noqa: E402
from tools.pipeline.iac_validator import validate_bundle  # noqa: E402
from tools.pipeline.premerge_runner import run_premerge  # noqa: E402

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "pdc"

# Extension → downstream artifact type. Downstream executors filter on these:
#   terraform_plan → "tf", ansible_executor → "yml"/"ini", validation_runner → "py".
_EXT_TYPE = {
    ".tf": "tf",
    ".tfvars": "tf",
    ".yml": "yml",
    ".yaml": "yml",
    ".json": "json",
    ".sh": "sh",
    ".ini": "ini",
    ".md": "md",
    ".py": "py",
}


class PipelineNotFoundError(Exception):
    """Raised when the referenced pipeline id has no row in the pipelines table."""


# ── Design loading (live pipelines table only — no demo fallback) ─────────────


def _load_pipeline(pipeline_id: str) -> dict:
    """Load a single pipeline design from the live ``pipelines`` table.

    Uses the canvas connection (``tools.pipeline.db.init_db.get_connection``),
    which already disables RLS for the canvas-owned tables. Raises
    ``PipelineNotFoundError`` when the id is missing or empty — the caller
    turns that into a loud, non-zero-exit failure. There is NO demo fallback.
    """
    if not pipeline_id or pipeline_id == "default":
        raise PipelineNotFoundError(
            f"No pipeline id supplied (got {pipeline_id!r}). "
            "Set --pipeline-id / --project-id to a real pipelines.id."
        )

    from tools.pipeline.db.init_db import get_connection  # noqa: PLC0415

    conn = get_connection()
    # NOTE: canvas get_connection may return a cached/thread-local handle; this
    # is a one-shot CLI subprocess, so we read and let process exit release it
    # rather than close() (closing a cached handle poisons the thread).
    row = conn.execute(
        "SELECT id, name, graph_json FROM pipelines WHERE id = %s",
        (pipeline_id,),
    ).fetchone()

    if not row:
        raise PipelineNotFoundError(
            f"Pipeline id {pipeline_id!r} not found in the pipelines table."
        )

    try:
        graph = json.loads(row["graph_json"]) if row["graph_json"] else {}
    except (json.JSONDecodeError, TypeError) as exc:
        raise PipelineNotFoundError(
            f"Pipeline id {pipeline_id!r} has unparseable graph_json: {exc}"
        ) from None

    return {
        "id": row["id"],
        "name": row["name"] or row["id"],
        "nodes": graph.get("nodes", []) or [],
        "edges": graph.get("edges", []) or [],
    }


def _write_report(prefix: str, text: str) -> Path:
    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    fpath = _ARTIFACTS_DIR / f"{prefix}_{uuid.uuid4().hex[:8]}.md"
    fpath.write_text(text, encoding="utf-8")
    return fpath


def _rel(path: Path) -> str:
    return path.relative_to(_ROOT).as_posix()


# ── Step 1: Pipeline Scan ─────────────────────────────────────────────────────


def step_scan(pipeline_id: str) -> tuple[dict, int]:
    design = _load_pipeline(pipeline_id)
    nodes, edges = design["nodes"], design["edges"]

    findings = detect_antipatterns(nodes, edges)
    severities = [f.get("severity") for f in findings]
    critical = sum(1 for s in severities if s == "critical")
    high = sum(1 for s in severities if s == "high")
    medium = sum(1 for s in severities if s == "medium")
    gate = "FAIL" if critical else ("WARN" if high else "PASS")

    node_types = sorted({n.get("type", "") for n in nodes if n.get("type")})

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Pipeline Design Scan Report",
        f"**Generated:** {ts}  ",
        f"**Pipeline:** {design['name']} (`{design['id']}`)  ",
        f"**Nodes:** {len(nodes)}  **Edges:** {len(edges)}  ",
        f"**Gate:** {gate}",
        "",
        "## Node Types",
        "",
    ]
    lines += [f"- `{t}`" for t in node_types] or ["(none)"]
    lines += ["", "## Anti-Patterns Detected", ""]
    if findings:
        for f in findings:
            lines += [
                f"### [{f['id']}] {f['title']} ({f['severity'].upper()})",
                f"{f['description']}",
                f"**Recommendation:** {f['recommendation']}",
                "",
            ]
    else:
        lines.append("No anti-patterns detected.")
    report = _write_report("pipeline_scan", "\n".join(lines))

    output = {
        "status": "success",
        "gate": gate,
        "pipeline_id": design["id"],
        "node_count": len(nodes),
        "edge_count": len(edges),
        "antipatterns_detected": len(findings),
        "critical": critical,
        "high": high,
        "medium": medium,
        "node_types": node_types,
        "artifacts": [
            {"name": "Pipeline Scan Report", "path": _rel(report), "type": "md"}
        ],
    }
    # Scan/discovery step is informational: it inventories and reports the gate
    # but does not itself block the workflow (exit 0). The antipattern step gates.
    return output, 0


# ── Step 2: Anti-pattern Check ────────────────────────────────────────────────


def step_antipattern(pipeline_id: str) -> tuple[dict, int]:
    design = _load_pipeline(pipeline_id)
    nodes, edges = design["nodes"], design["edges"]

    result = run_premerge({"nodes": nodes, "edges": edges})
    verdict = result["gate"]  # "pass" | "warn" | "fail"
    hits = result["antipattern_hits"]
    gate = verdict.upper()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# DevSecOps Anti-Pattern Check Report",
        f"**Generated:** {ts}  ",
        f"**Pipeline:** {design['name']} (`{design['id']}`)  ",
        f"**SLSA Level:** {result['slsa_score']}  ",
        f"**Gate:** {gate}",
        "",
        "## Findings",
        "",
    ]
    if hits:
        for f in hits:
            lines += [
                f"### [{f['id']}] {f['title']} ({f['severity'].upper()})",
                f"**Frameworks:** {', '.join(f.get('frameworks', [])) or 'n/a'}",
                "",
                f"{f['description']}",
                f"**Recommendation:** {f['recommendation']}",
                "",
            ]
    else:
        lines.append("All anti-pattern checks passed.")
    report = _write_report("antipattern_report", "\n".join(lines))

    output = {
        "status": "success" if verdict != "fail" else "failed",
        "gate": gate,
        "pipeline_id": design["id"],
        "slsa_score": result["slsa_score"],
        "findings": len(hits),
        "critical": sum(1 for f in hits if f.get("severity") == "critical"),
        "high": sum(1 for f in hits if f.get("severity") == "high"),
        "medium": sum(1 for f in hits if f.get("severity") == "medium"),
        "artifacts": [
            {"name": "Antipattern Report", "path": _rel(report), "type": "md"}
        ],
    }
    # Gate the workflow: exit non-zero on a critical anti-pattern (verdict fail).
    return output, (1 if verdict == "fail" else 0)


# ── Step 3: Generate IaC ──────────────────────────────────────────────────────


def step_iac(pipeline_id: str) -> tuple[dict, int]:
    design = _load_pipeline(pipeline_id)
    nodes, edges = design["nodes"], design["edges"]

    graph = {"nodes": nodes, "edges": edges}
    bundle = generate_deploy_bundle(graph, design["name"], target_csp="auto")
    files = bundle.get("files", [])

    # Persist bundle files to disk so downstream shared executors can read them.
    uid = uuid.uuid4().hex[:8]
    out_dir = _ARTIFACTS_DIR / f"deploy_{uid}"
    out_dir.mkdir(parents=True, exist_ok=True)

    artifacts: list[dict] = []
    for f in files:
        rel_path = f.get("path", "")
        content = f.get("content", "")
        if not rel_path:
            continue
        dest = out_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        atype = _EXT_TYPE.get(dest.suffix.lower(), "txt")
        artifacts.append({"name": dest.name, "path": _rel(dest), "type": atype})

    # Validate the generated IaC (layers 1-3, air-gap safe).
    validation = validate_bundle(files, max_layer=3)
    val_gate = validation.get("gate", "fail")
    gate = "PASS" if val_gate == "pass" else "FAIL"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = validation.get("summary", {})
    lines = [
        "# IaC Generation & Validation Report",
        f"**Generated:** {ts}  ",
        f"**Pipeline:** {design['name']} (`{design['id']}`)  ",
        f"**Target CSP:** {bundle.get('manifest', {}).get('target_csp', 'n/a')}  ",
        f"**Files generated:** {len(files)}  ",
        f"**Validation gate:** {gate}",
        "",
        "## Validation Summary",
        "",
        f"- Total checks: {summary.get('total', 0)}",
        f"- Passed: {summary.get('passed', 0)}",
        f"- Failed: {summary.get('failed', 0)}",
        f"- Warned: {summary.get('warned', 0)}",
        f"- Skipped: {summary.get('skipped', 0)}",
        "",
        "## Deployment Summary",
        "",
        "```",
        bundle.get("summary", ""),
        "```",
    ]
    failed = [r for r in validation.get("results", []) if r.get("status") == "fail"]
    if failed:
        lines += ["", "## Failed Checks", ""]
        for r in failed:
            lines.append(f"- [L{r.get('layer')}] {r.get('check')}: {r.get('message')}")
    report = _write_report("iac_report", "\n".join(lines))
    artifacts.append({"name": "IaC Generation Report", "path": _rel(report), "type": "md"})

    output = {
        "status": "success" if gate == "PASS" else "failed",
        "gate": gate,
        "pipeline_id": design["id"],
        "target_csp": bundle.get("manifest", {}).get("target_csp"),
        "files_generated": len(files),
        "validation": summary,
        "artifacts": artifacts,
    }
    # Do not feed a broken bundle into human-approval + terraform apply.
    return output, (0 if gate == "PASS" else 1)


_STEPS = {
    "scan": step_scan,
    "antipattern": step_antipattern,
    "iac": step_iac,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PDC Studio workflow steps (live engine).")
    parser.add_argument("--step", required=True, choices=sorted(_STEPS))
    parser.add_argument("--project-id", default="default",
                        help="Pipeline id (injected by the workflow runner).")
    parser.add_argument("--pipeline-id", default="",
                        help="Explicit pipeline id; overrides --project-id when set.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="pdc")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    pipeline_id = args.pipeline_id or args.project_id
    handler = _STEPS[args.step]

    try:
        output, code = handler(pipeline_id)
    except PipelineNotFoundError as exc:
        # FAIL LOUD — never grade demo data, never emit PASS on missing input.
        print(json.dumps({
            "status": "failed",
            "gate": "FAIL",
            "step": args.step,
            "pipeline_id": pipeline_id,
            "error": str(exc),
            "artifacts": [],
        }))
        return 1
    except Exception as exc:  # noqa: BLE001 — surface any engine error loudly
        print(json.dumps({
            "status": "failed",
            "gate": "FAIL",
            "step": args.step,
            "pipeline_id": pipeline_id,
            "error": f"{type(exc).__name__}: {exc}",
            "artifacts": [],
        }))
        return 1

    print(json.dumps(output))
    return code


if __name__ == "__main__":
    sys.exit(main())
