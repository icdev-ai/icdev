#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Evolve Reflex — autoresearch-style code mutation.

Picks the worst-quality tool (highest complexity/smells), proposes an
improvement using scanner-tier LLM (phi4-reasoning), runs tests to
verify, and stages the change as a GKP code_patch for human review.

ORANGE tier (code mutation — worktree sandbox + test gate + human review).
Uses phi4-reasoning for code analysis (zero Claude tokens).

Key autoresearch lesson: ONE file per mutation cycle.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_worst_quality_file(
    allowed_dirs: List[str],
    forbidden_files: List[str],
) -> Optional[Dict[str, Any]]:
    """Find the file with worst code quality metrics."""
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
             f"tests/"],
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
) -> Optional[str]:
    """Export mutation proposal as a GKP code_patch for human review."""
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
            },
            confidence=0.5,  # Always low confidence → forces human review
            evidence={
                "complexity": metrics.get("cyclomatic_complexity", 0),
                "smells": metrics.get("smell_count", 0),
                "analysis_model": "phi4-reasoning",
            },
        )
        if result.get("status") == "exported":
            return result.get("gkp_id")
    except Exception as e:
        print(f"  WARN: GKP export failed: {e}")
    return None


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Evolve Reflex.

    ORANGE tier: requires human approval for any actual code changes.
    This reflex only PROPOSES changes as GKP code_patches.
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

    # Step 4: Export as GKP proposal (NEVER auto-apply — ORANGE tier)
    print(f"  Evolve: proposing — {analysis.get('improvement', '?')[:80]}")
    gkp_id = _export_mutation_proposal(target["file_path"], analysis, target)

    return {
        "success": gkp_id is not None,
        "metric_value": 1.0 if gkp_id else 0.0,
        "details": {
            "target_file": target["file_path"],
            "improvement": analysis.get("improvement", ""),
            "target_function": analysis.get("target_function", ""),
            "risk": analysis.get("risk", "medium"),
            "current_metrics": {
                "cyclomatic_complexity": target["cyclomatic_complexity"],
                "smell_count": target["smell_count"],
                "maintainability_score": target["maintainability_score"],
            },
            "gkp_id": gkp_id,
            "status": "proposed_for_human_review",
        },
    }
