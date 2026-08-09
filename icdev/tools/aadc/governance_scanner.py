"""Governance Scanner — AADC Workflow Step 1.

Graph-driven AI governance posture scan. Loads the AADC design graph and runs
the real rule engine (``agentic_engine.assess_design``); the AI Risk Score and
the missing-control findings are derived from the actual design, not from a
fabricated default posture.

penta-aadc-02: this module previously computed the 'AI Risk Score' from a
hardcoded default posture (a fixed agent count and model list), never read the
design graph, and failed open (except: pass -> defaults). It is now a thin
adapter over ``assess_design`` and reports assessment-unavailable explicitly on
any failure — it NEVER falls back to a synthetic verdict.
Outputs JSON with artifact paths to stdout.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "aadc"


class AssessmentUnavailable(RuntimeError):
    """Raised when the design cannot be loaded or assessed.

    Callers must surface this as an explicit 'assessment unavailable' verdict —
    never substitute a fabricated PASS/score.
    """


def _load_design(project_id: str) -> tuple[str, dict]:
    """Load ``(graph_json, meta)`` for an AADC design.

    Uses the canvas connection factory
    (``tools.agentic_ai_canvas.db.init_db.get_connection``) which wraps
    ``get_canvas_connection()`` on PostgreSQL: the ``aadc_*`` tables carry no
    ``tenant_id``/``classification`` columns, so the RLS-enforcing
    ``get_connection()`` would raise ``UndefinedColumn`` on PG. On SQLite it
    points at the canvas ``.db`` file where the designs actually live.
    """
    from tools.agentic_ai_canvas.db.init_db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, domain, classification, graph_json, "
            "safety_impacting, rights_impacting FROM aadc_designs WHERE id=%s",
            (project_id,),
        ).fetchone()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not row:
        raise AssessmentUnavailable(f"AADC design not found: {project_id}")

    d = dict(row)
    meta = {
        "domain": d.get("domain") or "",
        "classification": d.get("classification") or "CUI",
        "safety_impacting": bool(d.get("safety_impacting")),
        "rights_impacting": bool(d.get("rights_impacting")),
        "name": d.get("name") or "",
    }
    return d.get("graph_json") or '{"nodes":[],"edges":[]}', meta


def scan_governance(project_id: str) -> dict:
    """Run a graph-driven governance scan for an AADC design.

    Returns a dict with a design-derived posture inventory, ``missing_controls``
    (the real rule-engine findings), and ``ai_risk_score`` (the assessment's
    aggregate score, 0 = max risk .. 100 = no risk).

    Raises ``AssessmentUnavailable`` when the design cannot be loaded or the
    assessment cannot run. No fabricated fallback.
    """
    from tools.agentic_ai_canvas.agentic_engine import _parse_graph, assess_design
    from tools.agentic_ai_canvas.constants import AGENT_NODES, MODEL_NODES

    graph_json, meta = _load_design(project_id)
    try:
        assessment = assess_design(project_id, graph_json, meta)
    except Exception as exc:  # noqa: BLE001 — surface as explicit unavailable
        raise AssessmentUnavailable(f"assessment failed: {exc}") from exc

    findings = json.loads(assessment.get("findings_json") or "[]")
    nodes, edges = _parse_graph(graph_json)

    ai_risk_score = round(float(assessment.get("score", 0.0)), 1)

    missing_controls = [
        {
            "key": f.get("id", ""),
            "label": f.get("title", ""),
            "severity": f.get("severity", ""),
            "framework": f.get("framework", ""),
            "description": f.get("detail", ""),
        }
        for f in findings
    ]

    model_types = sorted(
        {(n.get("label") or n.get("type") or "") for n in nodes
         if n.get("type") in MODEL_NODES}
    )
    posture = {
        "agent_count": sum(1 for n in nodes if n.get("type") in AGENT_NODES),
        "model_types": model_types,
        "data_classification": meta.get("classification", "CUI"),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "autonomy_max": assessment.get("autonomy_max", 0),
        "safety_impacting": bool(assessment.get("safety_impacting")),
        "rights_impacting": bool(assessment.get("rights_impacting")),
        "nist_rmf_score": assessment.get("nist_rmf_score"),
        "owasp_score": assessment.get("owasp_score"),
    }

    return {
        "posture": posture,
        "missing_controls": missing_controls,
        "ai_risk_score": ai_risk_score,
        "assessment_id": assessment.get("id"),
        "project_id": project_id,
    }


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    posture = result["posture"]
    missing = result["missing_controls"]
    risk_score = result["ai_risk_score"]
    project_id = result["project_id"]

    risk_label = "LOW" if risk_score >= 80 else ("MEDIUM" if risk_score >= 50 else "HIGH")
    model_types = posture.get("model_types", []) or ["(none)"]

    lines = [
        "# AI Governance Scan Report — AADC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Assessment:** {result.get('assessment_id', 'n/a')}  ",
        f"**AI Risk Score:** {risk_score}/100 ({risk_label} RISK)",
        "",
        "> Derived from the design graph via the AADC rule engine "
        "(agentic_engine.assess_design). Not a default posture.",
        "",
        "## AI System Inventory (from design graph)",
        "| Attribute | Value |",
        "|-----------|-------|",
        f"| Agent Count | {posture.get('agent_count', 0)} |",
        f"| Model Nodes | {', '.join(model_types)} |",
        f"| Total Nodes | {posture.get('node_count', 0)} |",
        f"| Max Autonomy | L{posture.get('autonomy_max', 0)} |",
        f"| Data Classification | {posture.get('data_classification', 'Unknown')} |",
        f"| Safety-Impacting | {'YES' if posture.get('safety_impacting') else 'no'} |",
        f"| Rights-Impacting | {'YES' if posture.get('rights_impacting') else 'no'} |",
        f"| NIST AI RMF Score | {posture.get('nist_rmf_score')} |",
        f"| OWASP LLM Score | {posture.get('owasp_score')} |",
        "",
    ]

    if missing:
        lines += [
            "## Governance Findings",
            f"**{len(missing)} finding(s) from the design assessment:**",
            "",
        ]
        for ctrl in missing:
            sev = ctrl.get("severity", "")
            fw = ctrl.get("framework", "")
            lines.append(f"- **[{sev}] {ctrl['label']}** ({fw}) — {ctrl['description']}")
        lines.append("")
    else:
        lines += ["## No Governance Findings", "",
                  "The rule engine surfaced no governance findings for this design.", ""]

    return "\n".join(lines)


def _write_artifact(report_md: str) -> Path:
    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    fpath = _ARTIFACTS_DIR / f"governance_scan_{uid}.md"
    fpath.write_text(report_md, encoding="utf-8", newline="")
    return fpath


def main():
    parser = argparse.ArgumentParser(description="Governance Scanner — AADC Step 1")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="aadc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = scan_governance(args.project_id)
    except AssessmentUnavailable as exc:
        # Explicit unavailable verdict — never a fabricated PASS/score.
        print(json.dumps({
            "status": "unavailable",
            "reason": str(exc),
            "project_id": args.project_id,
            "ai_risk_score": None,
        }))
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)

    report_md = build_report(result)
    fpath = _write_artifact(report_md)
    output = {
        "status": "success",
        "ai_risk_score": result["ai_risk_score"],
        "missing_controls": len(result["missing_controls"]),
        "artifacts": [
            {"name": "Governance Scan Report",
             "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
        ],
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
