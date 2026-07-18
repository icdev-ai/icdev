# CUI // SP-CTI
"""AIMC — Governance Assessor.

Runs DoD RAI 5 Principles + OMB M-25-21 + IL suitability checks
against a canvas design graph. Wraps existing compliance assessors
(nist_ai_rmf, atlas, owasp_llm) for orchestrated multi-framework scoring.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from tools.aiml_canvas.constants import DOD_RAI_PRINCIPLES, IL_REQUIREMENTS, FOUNDATION_MODELS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_type(graph: dict, node_type: str) -> bool:
    return any(n.get("type") == node_type for n in graph.get("nodes", []))


def _model_nodes(graph: dict) -> list[dict]:
    return [n for n in graph.get("nodes", []) if n.get("type", "").startswith("model-")]


def assess_dod_rai(graph: dict, design: dict) -> dict:
    """Evaluate DoD Responsible AI 5 Principles against the canvas design."""
    findings: list[dict] = []
    principle_map = {
        "rai-responsible": {
            "checks": [
                ("HITL gate or Output Validator present (human oversight)",
                 _has_type(graph, "safety-output-validator") or _has_type(graph, "safety-guardrail")),
                ("Governance block (Model Card or NIST RMF) connected",
                 _has_type(graph, "gov-model-card") or _has_type(graph, "gov-nist-ai-rmf")),
                ("Deployment node present (system is actually deployable)",
                 any(n.get("type", "").startswith("deploy-") for n in graph.get("nodes", []))),
            ]
        },
        "rai-equitable": {
            "checks": [
                ("Evaluation set present (fairness/accuracy measured)",
                 any(n.get("type", "").startswith("eval-") for n in graph.get("nodes", []))),
                ("LLM-as-judge rubric (multi-dimension scoring)",
                 _has_type(graph, "eval-rubric")),
                ("Red team set present (bias/adversarial testing)",
                 _has_type(graph, "eval-red-team")),
            ]
        },
        "rai-traceable": {
            "checks": [
                ("Model Card present (training provenance documented)",
                 _has_type(graph, "gov-model-card")),
                ("AI BOM present (model supply chain traceable)",
                 _has_type(graph, "gov-ai-bom")),
                ("Audit edge or logging node in graph",
                 any(e.get("type") == "audit-trail" for e in graph.get("edges", []))),
            ]
        },
        "rai-reliable": {
            "checks": [
                ("Benchmark evaluation node present",
                 _has_type(graph, "eval-benchmark")),
                ("Input Guardrail present (input validation)",
                 _has_type(graph, "safety-guardrail")),
                ("Output Validator present (output safety check)",
                 _has_type(graph, "safety-output-validator")),
            ]
        },
        "rai-governable": {
            "checks": [
                ("NIST AI RMF node present (formal governance framework)",
                 _has_type(graph, "gov-nist-ai-rmf")),
                ("DoD RAI node present (DoD-specific governance)",
                 _has_type(graph, "gov-dod-rai")),
                ("System Card or use case registration present",
                 _has_type(graph, "gov-system-card")),
            ]
        },
    }

    total_checks = 0
    passed_checks = 0

    for principle in DOD_RAI_PRINCIPLES:
        pid = principle["id"]
        checks = principle_map.get(pid, {}).get("checks", [])
        p_passed = sum(1 for _, ok in checks if ok)
        p_total = len(checks)
        p_score = round((p_passed / p_total) * 100, 1) if p_total else 0.0
        total_checks += p_total
        passed_checks += p_passed
        findings.append({
            "principle_id": pid,
            "principle": principle["principle"],
            "description": principle["description"],
            "score": p_score,
            "passed": p_passed,
            "total": p_total,
            "checks": [{"label": label, "ok": ok} for label, ok in checks],
            "nist_controls": principle["nist_controls"],
            "severity": "CAT1" if p_score < 50 else ("CAT2" if p_score < 80 else "PASS"),
        })

    overall = round((passed_checks / total_checks) * 100, 1) if total_checks else 0.0
    return {
        "framework": "DoD Responsible AI 5 Principles",
        "framework_id": "dod-rai",
        "score": overall,
        "passed": overall >= 70,
        "assessed_at": _now(),
        "findings": findings,
        "summary": {"total": total_checks, "passed": passed_checks, "failed": total_checks - passed_checks},
    }


def assess_il_suitability(graph: dict, design: dict) -> dict:
    """Score IL suitability of the design — checks each model node against zone requirements."""
    il_level = design.get("il_level", "IL4")
    il_int = int(il_level.replace("IL", "")) if il_level.startswith("IL") else 4
    il_req = IL_REQUIREMENTS.get(il_level, {})

    model_findings: list[dict] = []
    for node in _model_nodes(graph):
        props = node.get("properties_json", {})
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except Exception:
                props = {}
        model_id = props.get("model_id", "")
        m = next((fm for fm in FOUNDATION_MODELS if fm["id"] == model_id), None)
        if not m:
            model_findings.append({
                "node_id": node["id"],
                "label": node.get("label", node["id"]),
                "model_id": model_id,
                "status": "UNKNOWN",
                "issues": ["Model not found in catalog — add model_id to node properties"],
            })
            continue

        issues: list[str] = []
        if il_int not in m.get("il_suitability", []):
            issues.append(f"Model '{m['name']}' does not support {il_level} (supports: IL{', IL'.join(str(x) for x in m.get('il_suitability', []))})")
        if il_req.get("must_be_air_gap") and not m.get("air_gap_ready"):
            issues.append(f"Air-gap required for {il_level} but '{m['name']}' is cloud-connected")
        allowed = il_req.get("allowed_providers", [])
        if allowed and not any(p in m.get("provider", "") for p in allowed):
            issues.append(f"Provider '{m['provider']}' not on approved list for {il_level}: {allowed}")

        model_findings.append({
            "node_id": node["id"],
            "label": node.get("label", node["id"]),
            "model_id": model_id,
            "model_name": m.get("name", ""),
            "provider": m.get("provider", ""),
            "air_gap_ready": m.get("air_gap_ready", False),
            "il_suitability": m.get("il_suitability", []),
            "status": "PASS" if not issues else "FAIL",
            "issues": issues,
        })

    passed = sum(1 for f in model_findings if f["status"] == "PASS")
    total = len(model_findings)
    score = round((passed / total) * 100, 1) if total else 100.0

    return {
        "framework": f"IL Suitability — {il_level}",
        "framework_id": "il-suitability",
        "il_level": il_level,
        "il_requirements": il_req,
        "score": score,
        "passed": score >= 100,
        "assessed_at": _now(),
        "model_findings": model_findings,
        "summary": {"total_models": total, "compliant": passed, "non_compliant": total - passed},
    }


def assess_omm_m25_21(graph: dict, design: dict) -> dict:
    """Check OMB M-25-21 AI use case registration readiness."""
    checks = [
        ("System Card present (OMB M-25-21 §3.a)",
         _has_type(graph, "gov-system-card"),
         "Create a System Card governance node and connect to LLM nodes"),
        ("Model Card present (OMB M-25-21 §3.b)",
         _has_type(graph, "gov-model-card"),
         "Create a Model Card governance node for each foundation model"),
        ("AI BOM present (EO 14110 §4.2)",
         _has_type(graph, "gov-ai-bom"),
         "Create an AI BOM governance node tracking model provenance"),
        ("NIST AI RMF assessment node present",
         _has_type(graph, "gov-nist-ai-rmf"),
         "Add NIST AI RMF governance node (GOVERN/MAP/MEASURE/MANAGE)"),
        ("Evaluation suite present (OMB M-25-21 performance testing)",
         any(n.get("type", "").startswith("eval-") for n in graph.get("nodes", [])),
         "Add at least one Evaluation node (Benchmark, Rubric, or Red Team)"),
        ("Safety controls present (OMB M-25-21 §5 safety measures)",
         _has_type(graph, "safety-guardrail") or _has_type(graph, "safety-output-validator"),
         "Add Input Guardrail and/or Output Validator safety nodes"),
    ]

    findings = [
        {"label": label, "ok": ok, "remediation": rem if not ok else ""}
        for label, ok, rem in checks
    ]
    passed = sum(1 for f in findings if f["ok"])
    total = len(findings)
    score = round((passed / total) * 100, 1) if total else 0.0

    return {
        "framework": "OMB M-25-21 AI Use Case Registration",
        "framework_id": "omb-m25-21",
        "score": score,
        "passed": score >= 80,
        "assessed_at": _now(),
        "findings": findings,
        "summary": {"total": total, "passed": passed, "failed": total - passed},
        "next_steps": [f["remediation"] for f in findings if f["remediation"]],
    }


def run_all(graph: dict, design: dict) -> dict:
    """Run DoD RAI + IL Suitability + OMB M-25-21 and return combined report."""
    dod_rai = assess_dod_rai(graph, design)
    il_suit = assess_il_suitability(graph, design)
    omm = assess_omm_m25_21(graph, design)

    # Try external assessors. On import/call failure, surface an explicit
    # {"status": "unavailable"} entry so the UI can distinguish a framework that
    # genuinely passed/failed from one that could not be run (penta-aimc-04).
    external: list[dict] = []
    for module_name, fn_name, fw_id, fw_name in [
        ("tools.compliance.nist_ai_rmf_assessor", "assess", "nist-ai-rmf", "NIST AI RMF"),
        ("tools.compliance.owasp_llm_assessor",   "assess", "owasp-llm",   "OWASP Top 10 for LLM"),
        ("tools.compliance.atlas_assessor",        "assess", "mitre-atlas", "MITRE ATLAS"),
    ]:
        try:
            import importlib
            mod = importlib.import_module(module_name)
            result = getattr(mod, fn_name)(project_id=design.get("id", "aimc"), gate=False)
            if isinstance(result, dict):
                external.append({"framework_id": fw_id, "status": "assessed", **result})
            else:
                external.append({
                    "framework_id": fw_id, "framework": fw_name,
                    "status": "unavailable",
                    "reason": f"{module_name}.{fn_name} returned {type(result).__name__}, expected dict",
                })
        except Exception as exc:
            external.append({
                "framework_id": fw_id, "framework": fw_name,
                "status": "unavailable", "reason": str(exc),
            })

    overall_scores = [dod_rai["score"], il_suit["score"], omm["score"]]
    overall = round(sum(overall_scores) / len(overall_scores), 1)

    return {
        "design_id": design.get("id"),
        "overall_score": overall,
        "overall_passed": overall >= 75,
        "assessed_at": _now(),
        "dod_rai": dod_rai,
        "il_suitability": il_suit,
        "omm_m25_21": omm,
        "external_assessments": external,
    }
