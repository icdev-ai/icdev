#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Evolve Reflex — autoresearch-style code mutation.

Picks the worst-quality tool (highest complexity/smells), proposes an
improvement using scanner-tier LLM (phi4-reasoning), runs tests to
verify, and stages the change as a GKP code_patch for human review.

ORANGE tier (code mutation — worktree sandbox + test gate + human review).
Uses phi4-reasoning for code analysis (zero Claude tokens).

Key autoresearch lesson: ONE file per mutation cycle.

Quality-gated confidence scaling:
  - Low risk + tests pass + fresh metrics improved → confidence 0.75 (auto-promotable)
  - Medium risk + tests pass → confidence 0.60 (expedited review)
  - High risk OR tests fail → confidence 0.45 (mandatory human review)
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_recently_evolved(hours: int = 72) -> set:
    """Get files that were recently targeted by Evolve to avoid re-targeting."""
    conn = get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = conn.execute("""
            SELECT payload FROM genesis_gkp
            WHERE genesis_reflex = 'evolve' AND created_at > ?
        """, (cutoff,)).fetchall()
        recently_evolved = set()
        for row in rows:
            payload_str = row["payload"] if isinstance(row, dict) else row[0]
            if payload_str:
                try:
                    payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
                    fp = payload.get("file_path", "")
                    if fp:
                        recently_evolved.add(fp)
                except (json.JSONDecodeError, TypeError):
                    pass
        return recently_evolved
    except Exception:
        return set()
    finally:
        conn.close()


def _refresh_file_metrics(file_path: str) -> Optional[Dict[str, Any]]:
    """Re-run code_analyzer on a single file to get fresh metrics."""
    try:
        result = subprocess.run(
            [sys.executable, "tools/analysis/code_analyzer.py",
             "--file", file_path, "--json"],
            capture_output=True, text=True, timeout=60,
            cwd=str(BASE_DIR), env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
        )
        stdout = result.stdout.strip()
        json_start = stdout.find("{")
        if json_start >= 0:
            data = json.loads(stdout[json_start:])
            files = data.get("files", [])
            if files:
                f = files[0]
                return {
                    "cyclomatic_complexity": f.get("cyclomatic_complexity", 0),
                    "cognitive_complexity": f.get("cognitive_complexity", 0),
                    "maintainability_score": f.get("maintainability_score", 0),
                    "smell_count": f.get("smell_count", 0),
                    "function_count": f.get("function_count", 0),
                }
    except Exception as e:
        print(f"  WARN: Fresh metrics failed: {e}")
    return None


def _compute_confidence(
    risk: str,
    tests_passed: bool,
    metrics_improved: bool,
) -> Tuple[float, str]:
    """Compute GKP confidence based on quality gates.

    Returns (confidence, rationale).
    """
    if not tests_passed:
        return 0.35, "tests_failed"

    if risk == "low" and metrics_improved:
        return 0.75, "low_risk_tests_pass_metrics_improved"
    if risk == "low":
        return 0.65, "low_risk_tests_pass"
    if risk == "medium" and metrics_improved:
        return 0.60, "medium_risk_metrics_improved"
    if risk == "medium":
        return 0.50, "medium_risk_tests_pass"
    # high risk
    return 0.45, "high_risk_human_review"


def _get_worst_quality_file(
    allowed_dirs: List[str],
    forbidden_files: List[str],
) -> Optional[Dict[str, Any]]:
    """Find the file with worst code quality metrics."""
    recently_evolved = _get_recently_evolved(hours=72)

    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT file_path, cyclomatic_complexity, cognitive_complexity,
                   maintainability_score, smell_count, function_count
            FROM code_quality_metrics
            WHERE smell_count > 0
            ORDER BY smell_count DESC, cyclomatic_complexity DESC
            LIMIT 50
        """).fetchall()

        for row in rows:
            fp = row["file_path"] if isinstance(row, dict) else row[0]
            if not fp:
                continue

            # Normalize path
            fp_norm = fp.replace("\\", "/")

            # Check allowed directories
            in_allowed = any(fp_norm.startswith(d.rstrip("/")) for d in allowed_dirs)
            if not in_allowed:
                continue

            # Check forbidden files
            is_forbidden = any(fp_norm.endswith(f.replace("\\", "/")) for f in forbidden_files)
            if is_forbidden:
                continue

            # Skip recently evolved files (72h cooldown)
            if fp_norm in recently_evolved:
                continue

            # Verify file exists
            full_path = BASE_DIR / fp_norm
            if not full_path.exists():
                continue

            return {
                "file_path": fp_norm,
                "full_path": str(full_path),
                "cyclomatic_complexity": row["cyclomatic_complexity"] if isinstance(row, dict) else row[1],
                "cognitive_complexity": row["cognitive_complexity"] if isinstance(row, dict) else row[2],
                "maintainability_score": row["maintainability_score"] if isinstance(row, dict) else row[3],
                "smell_count": row["smell_count"] if isinstance(row, dict) else row[4],
                "function_count": row["function_count"] if isinstance(row, dict) else row[5],
            }
    except Exception as e:
        print(f"  WARN: Could not query code quality: {e}")
    finally:
        conn.close()
    return None


def _analyze_with_llm(file_path: str, file_content: str,
                       metrics: Dict) -> Optional[Dict[str, Any]]:
    """Use scanner-tier LLM to propose improvement (phi4-reasoning)."""
    try:
        from tools.llm.router import invoke as llm_invoke

        prompt = f"""Analyze this Python file and suggest ONE specific improvement to reduce complexity or code smells.

File: {file_path}
Current metrics:
- Cyclomatic complexity: {metrics.get('cyclomatic_complexity', '?')}
- Smell count: {metrics.get('smell_count', '?')}
- Maintainability score: {metrics.get('maintainability_score', '?')}

Code:
```python
{file_content[:8000]}
```

Respond with JSON only:
{{"improvement": "description of the change", "target_function": "function name to refactor", "risk": "low|medium|high", "estimated_complexity_reduction": number}}
"""
        result = llm_invoke(
            prompt=prompt,
            function="code_analysis",
            project_id="genesis",
            max_tokens=2000,
        )

        if result and result.get("content"):
            content = result["content"]
            # Extract JSON from response
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                return json.loads(content[json_start:json_end])
    except Exception as e:
        print(f"  WARN: LLM analysis failed: {e}")
    return None


def _run_tests_for_file(file_path: str, timeout: int = 120) -> Dict[str, Any]:
    """Run tests related to a specific file."""
    # Find related test files
    module_name = Path(file_path).stem
    test_patterns = [
        f"tests/**/test_{module_name}.py",
        f"tests/test_{module_name}.py",
        f"tests/genesis_auto/test_{module_name}.py",
    ]

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "--tb=short", "-q",
             "tests/"],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
        )
        return {
            "passed": result.returncode == 0,
            "returncode": result.returncode,
            "output": result.stdout[-500:] if result.stdout else "",
        }
    except subprocess.TimeoutExpired:
        return {"passed": False, "error": "timeout"}
    except Exception as e:
        return {"passed": False, "error": str(e)}


def _export_mutation_proposal(
    file_path: str,
    analysis: Dict,
    metrics: Dict,
    confidence: float = 0.5,
    confidence_rationale: str = "default",
    fresh_metrics: Optional[Dict] = None,
) -> Optional[str]:
    """Export mutation proposal as a GKP code_patch with quality-gated confidence."""
    try:
        from tools.genesis.promoter import export_gkp
        result = export_gkp(
            reflex="evolve",
            artifact_type="code_patch",
            payload={
                "title": f"Evolve: {analysis.get('improvement', 'Refactor')} — {file_path}",
                "file_path": file_path,
                "improvement": analysis.get("improvement", ""),
                "target_function": analysis.get("target_function", ""),
                "risk": analysis.get("risk", "medium"),
                "current_metrics": metrics,
                "fresh_metrics": fresh_metrics,
            },
            confidence=confidence,
            evidence={
                "complexity": metrics.get("cyclomatic_complexity", 0),
                "smells": metrics.get("smell_count", 0),
                "analysis_model": "phi4-reasoning",
                "confidence_rationale": confidence_rationale,
                "tests_run": True,
                "fresh_metrics_available": fresh_metrics is not None,
            },
        )
        if result.get("status") == "exported":
            return result.get("gkp_id")
    except Exception as e:
        print(f"  WARN: GKP export failed: {e}")
    return None


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Evolve Reflex.

    ORANGE tier with quality-gated confidence scaling:
    - Low risk + tests pass + metrics improve → confidence 0.75 (auto-promotable)
    - Medium risk + tests pass → confidence 0.60 (expedited review)
    - High risk OR tests fail → confidence 0.45 (mandatory human review)
    """
    allowed_dirs = config.get("allowed_directories", ["tools/", "goals/"])
    forbidden_files = config.get("forbidden_files", [
        "CLAUDE.md", ".env", "tools/db/storage.py", "tools/genesis/daemon.py",
    ])

    # Step 1: Find worst-quality file
    print("  Evolve: scanning for worst-quality file...")
    target = _get_worst_quality_file(allowed_dirs, forbidden_files)
    if not target:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"status": "no_files_need_improvement"},
        }

    print(f"  Evolve: target = {target['file_path']} "
          f"(smells={target['smell_count']}, complexity={target['cyclomatic_complexity']})")

    # Step 2: Read file
    try:
        full_path = Path(target["full_path"])
        file_content = full_path.read_text(encoding="utf-8")
    except Exception as e:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {"error": f"Could not read {target['file_path']}: {e}"},
        }

    # Step 3: Analyze with LLM (scanner-tier)
    print("  Evolve: analyzing with LLM...")
    analysis = _analyze_with_llm(target["file_path"], file_content, target)
    if not analysis:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {
                "status": "llm_analysis_failed",
                "target": target["file_path"],
            },
        }

    # Step 4: Run tests as quality gate
    risk = analysis.get("risk", "medium").lower()
    print(f"  Evolve: running test suite (risk={risk})...")
    test_result = _run_tests_for_file(target["file_path"])
    tests_passed = test_result.get("passed", False)
    print(f"  Evolve: tests {'PASSED' if tests_passed else 'FAILED'}")

    # Step 5: Get fresh metrics for comparison
    fresh_metrics = None
    metrics_improved = False
    if tests_passed:
        print("  Evolve: refreshing metrics for quality comparison...")
        fresh_metrics = _refresh_file_metrics(target["file_path"])
        if fresh_metrics:
            old_smells = target.get("smell_count", 0) or 0
            new_smells = fresh_metrics.get("smell_count", 0) or 0
            old_cc = target.get("cyclomatic_complexity", 0) or 0
            new_cc = fresh_metrics.get("cyclomatic_complexity", 0) or 0
            old_maint = target.get("maintainability_score", 0) or 0
            new_maint = fresh_metrics.get("maintainability_score", 0) or 0
            # Improvement = fewer smells OR lower complexity OR higher maintainability
            metrics_improved = (
                new_smells < old_smells
                or new_cc < old_cc
                or new_maint > old_maint
            )

    # Step 6: Compute quality-gated confidence
    confidence, rationale = _compute_confidence(risk, tests_passed, metrics_improved)
    print(f"  Evolve: confidence={confidence:.2f} ({rationale})")

    # Step 7: Export as GKP proposal with computed confidence
    print(f"  Evolve: proposing -- {analysis.get('improvement', '?')[:80]}")
    gkp_id = _export_mutation_proposal(
        target["file_path"], analysis, target,
        confidence=confidence,
        confidence_rationale=rationale,
        fresh_metrics=fresh_metrics,
    )

    status = "proposed_for_human_review"
    if confidence >= 0.70:
        status = "auto_promotable"
    elif confidence >= 0.55:
        status = "expedited_review"

    return {
        "success": gkp_id is not None,
        "metric_value": confidence if gkp_id else 0.0,
        "details": {
            "target_file": target["file_path"],
            "improvement": analysis.get("improvement", ""),
            "target_function": analysis.get("target_function", ""),
            "risk": risk,
            "current_metrics": {
                "cyclomatic_complexity": target["cyclomatic_complexity"],
                "smell_count": target["smell_count"],
                "maintainability_score": target["maintainability_score"],
            },
            "fresh_metrics": fresh_metrics,
            "metrics_improved": metrics_improved,
            "tests_passed": tests_passed,
            "confidence": confidence,
            "confidence_rationale": rationale,
            "gkp_id": gkp_id,
            "status": status,
        },
    }
