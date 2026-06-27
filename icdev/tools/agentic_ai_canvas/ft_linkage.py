# CUI // SP-CTI
"""AADC → Fine-Tuning Dataset Linkage.

Exports the latest assessment scores for a design as a fine-tuning signal
row in fine_tuning_datasets, enabling the fine-tuning dashboard to surface
AADC compliance gaps as training examples.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def export_assessment_as_ft_signal(design_id: str) -> Dict[str, Any]:
    """Create or upsert a fine_tuning_datasets row for the design's latest assessment.

    Returns {"datasets_created": N, "dataset_id": int|None, "error": str|None}.
    """
    try:
        from tools.agentic_ai_canvas.db.init_db import get_connection as _aadc_conn
        from tools.db.storage import get_connection as _main_conn
    except ImportError as exc:
        return {"datasets_created": 0, "dataset_id": None, "error": f"import failed: {exc}"}

    # 1. Load latest assessment
    conn = None
    assessment: Optional[dict] = None
    design_name: str = design_id
    try:
        conn = _aadc_conn()
        a_row = conn.execute(
            "SELECT score, nist_rmf_score, owasp_score, findings_json "
            "FROM aadc_assessments WHERE design_id=%s ORDER BY created_at DESC LIMIT 1",
            (design_id,),
        ).fetchone()
        d_row = conn.execute(
            "SELECT name FROM aadc_designs WHERE id=%s", (design_id,)
        ).fetchone()
        if a_row:
            assessment = dict(a_row)
        if d_row:
            design_name = d_row["name"]
    except Exception as exc:
        return {"datasets_created": 0, "dataset_id": None, "error": f"assessment load failed: {exc}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # If no prior assessment run an inline score
    if assessment is None:
        try:
            from tools.agentic_ai_canvas.agentic_engine import assess_design
            conn2 = None
            try:
                conn2 = _aadc_conn()
                row = conn2.execute(
                    "SELECT graph_json, domain, classification FROM aadc_designs WHERE id=%s",
                    (design_id,),
                ).fetchone()
            finally:
                if conn2 is not None:
                    try:
                        conn2.close()
                    except Exception:
                        pass
            if row:
                result = assess_design(design_id, json.loads(row["graph_json"] or "{}"),
                                       {"domain": row.get("domain", ""),
                                        "classification": row.get("classification", "CUI")})
                assessment = {
                    "score": result.get("score", 0),
                    "nist_rmf_score": result.get("nist_rmf_score", 0),
                    "owasp_score": result.get("owasp_score", 0),
                    "findings_json": result.get("findings_json", "[]"),
                }
        except Exception:
            assessment = {"score": 0.0, "nist_rmf_score": 0.0, "owasp_score": 0.0, "findings_json": "[]"}

    # 2. Build gap list for expected_output
    findings: List[dict] = []
    try:
        findings = json.loads(assessment.get("findings_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        findings = []

    score = float(assessment.get("score") or 0.0)
    nist = float(assessment.get("nist_rmf_score") or 0.0)
    owasp = float(assessment.get("owasp_score") or 0.0)

    gaps: List[str] = [f.get("title", f.get("text", "")) for f in findings
                       if f.get("severity", "").upper() in ("HIGH", "CRITICAL")]
    if not gaps:
        # Derive from raw scores
        if nist < 50:
            gaps.append(f"Add NIST AI RMF governance nodes (current: {nist:.0f}%)")
        if owasp < 50:
            gaps.append(f"Add OWASP LLM security nodes (current: {owasp:.0f}%)")

    input_text = (
        f"Design: {design_id} | Name: {design_name} | "
        f"NIST AI RMF: {nist:.0f}% | OWASP LLM: {owasp:.0f}% | Overall: {score:.0f}%"
    )
    expected_output = (
        "Improve coverage for: " + "; ".join(gaps)
        if gaps else "Coverage looks good — no critical gaps detected."
    )
    quality_signal = round(score / 100.0, 4)
    now = datetime.now(timezone.utc).isoformat()

    # 3. Upsert into fine_tuning_datasets
    mconn = None
    dataset_id: Optional[int] = None
    try:
        mconn = _main_conn()
        # Check if a row already exists for this design
        existing = mconn.execute(
            "SELECT id FROM fine_tuning_datasets WHERE source='aadc' AND source_id=%s LIMIT 1",
            (design_id,),
        ).fetchone()
        if existing:
            mconn.execute(
                "UPDATE fine_tuning_datasets SET input_text=%s, expected_output=%s, "
                "quality_signal=%s, updated_at=%s WHERE id=%s",
                (input_text, expected_output, quality_signal, now, existing[0]),
            )
            dataset_id = existing[0]
            created = 0
        else:
            cur = mconn.execute(
                "INSERT INTO fine_tuning_datasets "
                "(source, source_id, input_text, expected_output, quality_signal, created_at, updated_at) "
                "VALUES ('aadc', %s, %s, %s, %s, %s, %s) RETURNING id",
                (design_id, input_text, expected_output, quality_signal, now, now),
            )
            row_back = cur.fetchone()
            dataset_id = row_back[0] if row_back else None
            created = 1
        mconn.commit()
    except Exception as exc:
        return {"datasets_created": 0, "dataset_id": None, "error": f"dataset write failed: {exc}"}
    finally:
        if mconn is not None:
            try:
                mconn.close()
            except Exception:
                pass

    return {
        "datasets_created": created,
        "dataset_id": dataset_id,
        "design_id": design_id,
        "quality_signal": quality_signal,
        "error": None,
    }
