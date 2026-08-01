"""Compliance Checker — AADC Workflow Step 2.

Graph-driven compliance verdict. Loads the AADC design graph and runs the real
rule engine (``agentic_engine.assess_design``); the 'AI Compliance Report'
PASS/FAIL gate and findings are derived from the actual design.

penta-aadc-02: this module previously computed its PASS/FAIL from a hardcoded
default checklist, never read the design graph, and failed open
(except: pass -> defaults), so the workflow verdict was decoupled from the
design. It is now a thin adapter over ``assess_design`` and reports
assessment-unavailable explicitly on any failure — it NEVER falls back to a
synthetic PASS.
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

# Severities that block the compliance gate (mirrors agentic_engine finding
# vocabulary: CRITICAL, CAT1, HIGH, MEDIUM, LOW).
_BLOCKING_SEVERITIES = frozenset({"CRITICAL", "CAT1", "HIGH"})


class AssessmentUnavailable(RuntimeError):
    """Raised when the design cannot be loaded or assessed.

    Callers must surface this as an explicit 'assessment unavailable' verdict —
    never substitute a fabricated PASS.
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


def run_compliance_checks(project_id: str) -> dict:
    """Run graph-driven compliance checks for an AADC design.

    Returns ``{findings, gate, scores, project_id, assessment_id}`` where the
    gate is FAIL when the design assessment produced any blocking-severity
    finding (CRITICAL/CAT1/HIGH), else PASS.

    Raises ``AssessmentUnavailable`` when the design cannot be loaded or the
    assessment cannot run. No fabricated fallback.
    """
    from tools.agentic_ai_canvas.agentic_engine import assess_design

    graph_json, meta = _load_design(project_id)
    try:
        assessment = assess_design(project_id, graph_json, meta)
    except Exception as exc:  # noqa: BLE001 — surface as explicit unavailable
        raise AssessmentUnavailable(f"assessment failed: {exc}") from exc

    findings = json.loads(assessment.get("findings_json") or "[]")
    blocking = [f for f in findings if f.get("severity") in _BLOCKING_SEVERITIES]
    gate = "FAIL" if blocking else "PASS"

    return {
        "findings": findings,
        "gate": gate,
        "scores": {
            "overall": assessment.get("score"),
            "nist_rmf": assessment.get("nist_rmf_score"),
            "owasp": assessment.get("owasp_score"),
            "omb_compliant": assessment.get("omb_compliant"),
            "autonomy_max": assessment.get("autonomy_max"),
        },
        "assessment_id": assessment.get("id"),
        "project_id": project_id,
    }


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    findings = result["findings"]
    gate = result["gate"]
    scores = result.get("scores", {})
    project_id = result["project_id"]

    blocking = [f for f in findings if f.get("severity") in _BLOCKING_SEVERITIES]
    warnings = [f for f in findings if f.get("severity") not in _BLOCKING_SEVERITIES]

    lines = [
        "# AI Compliance Report — AADC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}  ",
        f"**Assessment:** {result.get('assessment_id', 'n/a')}  ",
        f"**Compliance Gate:** {gate}",
        "",
        "> Derived from the design graph via the AADC rule engine "
        "(agentic_engine.assess_design). Not a default checklist.",
        "",
        "## Assessment Scores",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Overall Score | {scores.get('overall')} |",
        f"| NIST AI RMF | {scores.get('nist_rmf')} |",
        f"| OWASP LLM | {scores.get('owasp')} |",
        f"| Max Autonomy | L{scores.get('autonomy_max')} |",
        f"| OMB M-25-21 Compliant | {'yes' if scores.get('omb_compliant') else 'no'} |",
        "",
    ]

    if blocking:
        lines += ["## Blocking Findings"]
        for f in blocking:
            lines.append(
                f"- FAIL [{f.get('severity')}] {f.get('title')} "
                f"({f.get('framework')}): {f.get('detail')}"
            )
        lines.append("")
    if warnings:
        lines += ["## Warnings"]
        for f in warnings:
            lines.append(
                f"- WARN [{f.get('severity')}] {f.get('title')} "
                f"({f.get('framework')}): {f.get('detail')}"
            )
        lines.append("")
    if not findings:
        lines += ["## No Findings", "",
                  "The rule engine surfaced no compliance findings for this design."]

    return "\n".join(lines)


def _write_artifact(report_md: str) -> Path:
    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex[:8]
    fpath = _ARTIFACTS_DIR / f"compliance_report_{uid}.md"
    fpath.write_text(report_md, encoding="utf-8")
    return fpath


def main():
    parser = argparse.ArgumentParser(description="Compliance Checker — AADC Step 2")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="aadc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = run_compliance_checks(args.project_id)
    except AssessmentUnavailable as exc:
        # Explicit unavailable verdict — never a fabricated PASS.
        print(json.dumps({
            "status": "unavailable",
            "reason": str(exc),
            "project_id": args.project_id,
            "gate": "UNAVAILABLE",
        }))
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)

    report_md = build_report(result)
    fpath = _write_artifact(report_md)
    blocking = [f for f in result["findings"]
                if f.get("severity") in _BLOCKING_SEVERITIES]
    output = {
        "status": "success" if not blocking else "failed",
        "gate": result["gate"],
        "findings": len(result["findings"]),
        "failures": len(blocking),
        "artifacts": [
            {"name": "Compliance Report",
             "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
        ],
    }
    print(json.dumps(output))
    sys.exit(0 if not blocking else 1)


if __name__ == "__main__":
    main()
