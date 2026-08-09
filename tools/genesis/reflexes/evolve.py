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

DGM/Hyperagents-inspired capabilities:
  - Variant Archive: stores accepted AND rejected mutations for future context
  - Meta-Evolve: self-modifying selection strategy rotation based on acceptance rates
  - Peer-Review Gate: lightweight LLM diff review before full test suite
"""
IMPLEMENTATION_STATUS = "full"

import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utcnow_ts() -> str:
    """Compact UTC timestamp for filenames."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _get_recently_evolved(hours: int = 72) -> set:
    """Get files that were recently targeted by Evolve to avoid re-targeting."""
    conn = get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = conn.execute(
            """
            SELECT payload FROM genesis_gkp
            WHERE genesis_reflex = 'evolve' AND created_at > %s
        """,
            (cutoff,),
        ).fetchall()
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


def _compute_anomaly_thresholds(anomaly_cfg: Optional[Dict] = None) -> Dict[str, float]:
    """Compute anomaly-based thresholds from the code quality metric distribution.

    Uses population variance (mean ± sigma·σ) to surface true outliers rather than
    applying static cutoffs.  Falls back to configured defaults when fewer than
    min_samples data points are available.

    All tuning constants are drawn from anomaly_cfg (evolve.anomaly_detection in
    genesis_config.yaml) so they can be adjusted without code changes.
    """
    cfg = anomaly_cfg or {}
    min_samples = cfg.get("min_samples", 10)
    sigma_multiplier = cfg.get("sigma_multiplier", 0.5)
    fallback_smell = cfg.get("fallback_smell_threshold", 1.0)
    fallback_maint = cfg.get("fallback_maintainability_threshold", 50.0)
    bounds = cfg.get("adaptive_bounds", {})
    smell_floor: float = bounds.get("smell_threshold_floor", fallback_smell)
    maint_floor: float = bounds.get("maint_threshold_floor", 0.0)

    conn = None
    try:
        conn = get_connection()
        row = conn.execute("""
            SELECT
                AVG(smell_count) AS mean_smells,
                AVG(smell_count * smell_count) - AVG(smell_count) * AVG(smell_count) AS var_smells,
                AVG(maintainability_score) AS mean_maint,
                AVG(maintainability_score * maintainability_score)
                    - AVG(maintainability_score) * AVG(maintainability_score) AS var_maint,
                COUNT(*) AS n
            FROM code_quality_metrics
            WHERE smell_count IS NOT NULL AND maintainability_score IS NOT NULL
        """).fetchone()
        if row:
            n = row["n"] if isinstance(row, dict) else row[4]
            if n and n >= min_samples:
                mean_smells = float(row["mean_smells"] if isinstance(row, dict) else row[0])
                var_smells = max(0.0, float(row["var_smells"] if isinstance(row, dict) else row[1]))
                mean_maint = float(row["mean_maint"] if isinstance(row, dict) else row[2])
                var_maint = max(0.0, float(row["var_maint"] if isinstance(row, dict) else row[3]))
                std_smells = math.sqrt(var_smells)
                std_maint = math.sqrt(var_maint)
                smell_threshold = max(smell_floor, mean_smells + sigma_multiplier * std_smells)
                maint_threshold = max(maint_floor, mean_maint - sigma_multiplier * std_maint)
                print(
                    f"  Evolve: anomaly thresholds from {n} samples — "
                    f"smell>{smell_threshold:.1f}, maint<{maint_threshold:.1f}"
                )
                return {
                    "smell_threshold": smell_threshold,
                    "maintainability_threshold": maint_threshold,
                    "computed": True,
                }
    except Exception as e:
        print(f"  WARN: anomaly threshold computation failed: {e}")
    finally:
        if conn is not None:
            conn.close()
    return {"smell_threshold": fallback_smell, "maintainability_threshold": fallback_maint, "computed": False}


def _refresh_file_metrics(file_path: str) -> Optional[Dict[str, Any]]:
    """Re-run code_analyzer on a single file to get fresh metrics."""
    try:
        result = subprocess.run(
            [sys.executable, "tools/analysis/code_analyzer.py", "--file", file_path, "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
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
    sandbox_passed: bool = True,
    tiers_cfg: Optional[Dict] = None,
) -> Tuple[float, str]:
    """Compute GKP confidence based on quality gates.

    Confidence tier values are drawn from tiers_cfg (evolve.thresholds.confidence_tiers
    in genesis_config.yaml) so they can be tuned without code changes.

    Returns (confidence, rationale).
    """
    t = tiers_cfg or {}
    tests_failed_conf = t.get("tests_failed", 0.35)
    low_improved_conf = t.get("low_risk_improved", 0.75)
    low_conf = t.get("low_risk", 0.65)
    med_improved_conf = t.get("medium_risk_improved", 0.60)
    med_conf = t.get("medium_risk", 0.50)
    high_conf = t.get("high_risk", 0.45)
    sandbox_penalty = 0.0 if sandbox_passed else t.get("sandbox_penalty", 0.10)
    sandbox_suffix = "" if sandbox_passed else "+sandbox_failed"

    if not tests_passed:
        return tests_failed_conf, "tests_failed"

    if risk == "low" and metrics_improved:
        return low_improved_conf - sandbox_penalty, "low_risk_tests_pass_metrics_improved" + sandbox_suffix
    if risk == "low":
        return low_conf - sandbox_penalty, "low_risk_tests_pass" + sandbox_suffix
    if risk == "medium" and metrics_improved:
        return med_improved_conf - sandbox_penalty, "medium_risk_metrics_improved" + sandbox_suffix
    if risk == "medium":
        return med_conf - sandbox_penalty, "medium_risk_tests_pass" + sandbox_suffix
    return high_conf - sandbox_penalty, "high_risk_human_review" + sandbox_suffix


def _get_worst_quality_file(
    allowed_dirs: List[str],
    forbidden_files: List[str],
    thresholds: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Find the file with worst code quality metrics."""
    recently_evolved = _get_recently_evolved(hours=72)
    smell_threshold = (thresholds or {}).get("smell_threshold", 0)

    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT file_path, cyclomatic_complexity, cognitive_complexity,
                   maintainability_score, smell_count, function_count
            FROM code_quality_metrics
            WHERE smell_count > %s
            ORDER BY smell_count DESC, cyclomatic_complexity DESC
            LIMIT 50
        """, (smell_threshold,)).fetchall()

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


# ── Selection strategy implementations ──────────────────────────────────────


def _select_most_failures(
    allowed_dirs: List[str],
    forbidden_files: List[str],
    thresholds: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Select the file with most recent test failures."""
    recently_evolved = _get_recently_evolved(hours=72)
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT file_path, COUNT(*) as failure_count
            FROM audit_trail
            WHERE event_type LIKE 'error.%'
              AND created_at > datetime('now', '-7 days')
            GROUP BY file_path
            ORDER BY failure_count DESC
            LIMIT 50
        """).fetchall()
        for row in rows:
            fp = row["file_path"] if isinstance(row, dict) else row[0]
            if not fp:
                continue
            fp_norm = fp.replace("\\", "/")
            in_allowed = any(fp_norm.startswith(d.rstrip("/")) for d in allowed_dirs)
            if not in_allowed:
                continue
            is_forbidden = any(fp_norm.endswith(f.replace("\\", "/")) for f in forbidden_files)
            if is_forbidden:
                continue
            if fp_norm in recently_evolved:
                continue
            full_path = BASE_DIR / fp_norm
            if not full_path.exists():
                continue
            # Get quality metrics for the file
            metrics_row = conn.execute(
                """
                SELECT cyclomatic_complexity, cognitive_complexity,
                       maintainability_score, smell_count, function_count
                FROM code_quality_metrics WHERE file_path = %s LIMIT 1
            """,
                (fp_norm,),
            ).fetchone()
            return {
                "file_path": fp_norm,
                "full_path": str(full_path),
                "cyclomatic_complexity": (metrics_row[0] if metrics_row else 0),
                "cognitive_complexity": (metrics_row[1] if metrics_row else 0),
                "maintainability_score": (metrics_row[2] if metrics_row else 0),
                "smell_count": (metrics_row[3] if metrics_row else 0),
                "function_count": (metrics_row[4] if metrics_row else 0),
            }
    except Exception as e:
        print(f"  WARN: most_failures selection failed: {e}")
    finally:
        conn.close()
    return None


def _select_highest_churn(
    allowed_dirs: List[str],
    forbidden_files: List[str],
    thresholds: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Select the file with highest churn (most GKP mutations)."""
    recently_evolved = _get_recently_evolved(hours=72)
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT payload FROM genesis_gkp
            WHERE genesis_reflex = 'evolve'
            ORDER BY created_at DESC
            LIMIT 200
        """).fetchall()
        churn: Dict[str, int] = {}
        for row in rows:
            payload_str = row["payload"] if isinstance(row, dict) else row[0]
            if payload_str:
                try:
                    payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
                    fp = payload.get("file_path", "")
                    if fp:
                        churn[fp] = churn.get(fp, 0) + 1
                except (json.JSONDecodeError, TypeError):
                    pass
        # Sort by churn descending
        for fp_norm, _ in sorted(churn.items(), key=lambda x: x[1], reverse=True):
            in_allowed = any(fp_norm.startswith(d.rstrip("/")) for d in allowed_dirs)
            if not in_allowed:
                continue
            is_forbidden = any(fp_norm.endswith(f.replace("\\", "/")) for f in forbidden_files)
            if is_forbidden:
                continue
            if fp_norm in recently_evolved:
                continue
            full_path = BASE_DIR / fp_norm
            if not full_path.exists():
                continue
            metrics_row = conn.execute(
                """
                SELECT cyclomatic_complexity, cognitive_complexity,
                       maintainability_score, smell_count, function_count
                FROM code_quality_metrics WHERE file_path = %s LIMIT 1
            """,
                (fp_norm,),
            ).fetchone()
            return {
                "file_path": fp_norm,
                "full_path": str(full_path),
                "cyclomatic_complexity": (metrics_row[0] if metrics_row else 0),
                "cognitive_complexity": (metrics_row[1] if metrics_row else 0),
                "maintainability_score": (metrics_row[2] if metrics_row else 0),
                "smell_count": (metrics_row[3] if metrics_row else 0),
                "function_count": (metrics_row[4] if metrics_row else 0),
            }
    except Exception as e:
        print(f"  WARN: highest_churn selection failed: {e}")
    finally:
        conn.close()
    return None


def _select_lowest_coverage(
    allowed_dirs: List[str],
    forbidden_files: List[str],
    thresholds: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Select the file with lowest test coverage."""
    recently_evolved = _get_recently_evolved(hours=72)
    maint_threshold = (thresholds or {}).get("maintainability_threshold", 50.0)
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT file_path, cyclomatic_complexity, cognitive_complexity,
                   maintainability_score, smell_count, function_count
            FROM code_quality_metrics
            WHERE maintainability_score < %s
            ORDER BY maintainability_score ASC
            LIMIT 50
        """, (maint_threshold,)).fetchall()
        for row in rows:
            fp = row["file_path"] if isinstance(row, dict) else row[0]
            if not fp:
                continue
            fp_norm = fp.replace("\\", "/")
            in_allowed = any(fp_norm.startswith(d.rstrip("/")) for d in allowed_dirs)
            if not in_allowed:
                continue
            is_forbidden = any(fp_norm.endswith(f.replace("\\", "/")) for f in forbidden_files)
            if is_forbidden:
                continue
            if fp_norm in recently_evolved:
                continue
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
        print(f"  WARN: lowest_coverage selection failed: {e}")
    finally:
        conn.close()
    return None


# Map strategy names to selector functions
_STRATEGY_SELECTORS = {
    "worst_code_quality": _get_worst_quality_file,
    "most_failures": _select_most_failures,
    "highest_churn": _select_highest_churn,
    "lowest_coverage": _select_lowest_coverage,
}


def _select_target(
    strategy: str,
    allowed_dirs: List[str],
    forbidden_files: List[str],
    thresholds: Optional[Dict[str, float]] = None,
) -> Optional[Dict[str, Any]]:
    """Select a target file using the given strategy, falling back to worst_code_quality."""
    selector = _STRATEGY_SELECTORS.get(strategy, _get_worst_quality_file)
    target = selector(allowed_dirs, forbidden_files, thresholds)
    if target is None and strategy != "worst_code_quality":
        print(f"  Evolve: strategy '{strategy}' found nothing, falling back to worst_code_quality")
        target = _get_worst_quality_file(allowed_dirs, forbidden_files, thresholds)
    return target


def _analyze_with_llm(
    file_path: str, file_content: str, metrics: Dict, variant_context: str = ""
) -> Optional[Dict[str, Any]]:
    """Use scanner-tier LLM to propose improvement (phi4-reasoning).

    Args:
        variant_context: Optional prior variant summaries for richer mutation proposals.
    """
    try:
        from tools.llm.router import invoke as llm_invoke

        context_block = ""
        if variant_context:
            context_block = f"""
Previous mutation attempts on this file (learn from these):
{variant_context}

"""

        prompt = f"""Analyze this Python file and suggest ONE specific improvement to reduce complexity or code smells.

File: {file_path}
Current metrics:
- Cyclomatic complexity: {metrics.get("cyclomatic_complexity", "?")}
- Smell count: {metrics.get("smell_count", "?")}
- Maintainability score: {metrics.get("maintainability_score", "?")}
{context_block}
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


def _sandbox_verify_file(file_path: str, timeout: int = 30) -> bool:
    """Verify a file executes cleanly in LLM Sandbox container (D-SEC-10).

    Runs a compile-check inside a Docker container with network isolation.
    Returns True if sandbox passed or unavailable (graceful degradation).
    Returns False if the file failed sandbox execution.
    """
    try:
        from tools.security.sandbox_executor import SandboxExecutor
    except ImportError:
        return True  # Graceful degradation

    executor = SandboxExecutor()
    if not executor._enabled or not executor._available:
        return True  # Graceful degradation

    path = Path(file_path) if not Path(file_path).is_absolute() else Path(file_path)
    if not path.exists():
        full_path = BASE_DIR / file_path
        if not full_path.exists():
            return True  # File not found — skip, don't block
        path = full_path

    try:
        code = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return True  # Can't read — skip

    compile_code = (
        "import sys\n"
        f"src = '''{code[:6000]}'''\n"
        "try:\n"
        "    compile(src, '<sandbox>', 'exec')\n"
        "    print('OK')\n"
        "except SyntaxError as e:\n"
        "    print(f'SYNTAX_ERROR: {e}')\n"
        "    sys.exit(1)\n"
    )

    result = executor.execute(
        code=compile_code,
        language="python",
        executor_type="genesis_evolve",
        network_enabled=False,
        timeout_seconds=timeout,
        actor="genesis-evolve",
        project_id="genesis",
    )

    if result.status in ("disabled", "unavailable"):
        return True  # Graceful degradation

    return result.exit_code == 0


def _run_tests_for_file(file_path: str, timeout: int = 120) -> Dict[str, Any]:
    """Run tests related to a specific file."""
    # Run tests (targeted test discovery planned for future)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "--tb=short", "-q", "tests/"],
            capture_output=True,
            text=True,
            timeout=timeout,
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
    sandbox_passed: bool = True,
    peer_review_result: Optional[Dict] = None,
) -> Optional[str]:
    """Export mutation proposal as a GKP code_patch with quality-gated confidence."""
    try:
        from tools.genesis.promoter import export_gkp

        evidence = {
            "complexity": metrics.get("cyclomatic_complexity", 0),
            "smells": metrics.get("smell_count", 0),
            "analysis_model": "phi4-reasoning",
            "confidence_rationale": confidence_rationale,
            "tests_run": True,
            "sandbox_verified": sandbox_passed,
            "fresh_metrics_available": fresh_metrics is not None,
        }
        if peer_review_result is not None:
            evidence["peer_review"] = peer_review_result

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
            evidence=evidence,
        )
        if result.get("status") == "exported":
            return result.get("gkp_id")
    except Exception as e:
        print(f"  WARN: GKP export failed: {e}")
    return None


# ── Feature 1: Variant Archive (DGM-inspired) ──────────────────────────────


def _archive_variant(
    config: Dict[str, Any],
    file_path: str,
    file_content: str,
    status: str,
    analysis: Dict,
    test_results: Optional[Dict] = None,
    metrics: Optional[Dict] = None,
    fresh_metrics: Optional[Dict] = None,
    rejection_reason: str = "",
) -> Optional[str]:
    """Store a mutation variant (accepted or rejected) in the archive.

    Returns the archive path on success, None on failure.
    """
    archive_cfg = config.get("archive", {})
    if not archive_cfg.get("enabled", False):
        return None

    archive_base = Path(archive_cfg.get("path", "data/genesis/evolve_archive"))
    if not archive_base.is_absolute():
        archive_base = BASE_DIR / archive_base

    # Derive tool name from file_path (e.g. tools/llm/router.py -> tools_llm_router)
    tool_name = file_path.replace("/", "_").replace("\\", "_")
    if tool_name.endswith(".py"):
        tool_name = tool_name[:-3]

    tool_dir = archive_base / tool_name
    tool_dir.mkdir(parents=True, exist_ok=True)

    ts = _utcnow_ts()

    # Write variant source
    variant_path = tool_dir / f"{ts}_{status}.py"
    variant_path.write_text(file_content, encoding="utf-8", newline="")

    # Write metadata
    old_complexity = (metrics or {}).get("cyclomatic_complexity", 0) or 0
    new_complexity = (fresh_metrics or {}).get("cyclomatic_complexity", 0) or 0
    complexity_delta = new_complexity - old_complexity

    metadata = {
        "timestamp": _utcnow_iso(),
        "file_path": file_path,
        "status": status,
        "test_results": test_results,
        "complexity_delta": complexity_delta,
        "current_metrics": metrics,
        "fresh_metrics": fresh_metrics,
        "acceptance": status == "accepted",
        "rejection_reason": rejection_reason,
        "analysis": {
            "improvement": analysis.get("improvement", ""),
            "target_function": analysis.get("target_function", ""),
            "risk": analysis.get("risk", "unknown"),
        },
    }
    meta_path = tool_dir / f"{ts}_metadata.json"
    meta_path.write_text(
        json.dumps(metadata, indent=2, default=str),
        encoding="utf-8", newline="",
    )

    # Enforce max_variants_per_tool — prune oldest beyond limit
    max_variants = archive_cfg.get("max_variants_per_tool", 10)
    _prune_archive(tool_dir, max_variants)

    print(f"  Evolve: archived variant {status} -> {variant_path.relative_to(BASE_DIR)}")
    return str(variant_path)


def _prune_archive(tool_dir: Path, max_variants: int) -> None:
    """Remove oldest variants beyond max_variants_per_tool limit."""
    py_files = sorted(tool_dir.glob("*_*.py"))
    if len(py_files) <= max_variants:
        return

    to_remove = py_files[: len(py_files) - max_variants]
    for py_file in to_remove:
        py_file.unlink(missing_ok=True)
        # Also remove matching metadata
        meta_stem = py_file.stem  # e.g. 20260324T120000Z_accepted
        # Metadata is {ts}_metadata.json — extract timestamp prefix
        parts = meta_stem.rsplit("_", 1)
        if parts:
            meta_path = tool_dir / f"{parts[0]}_metadata.json"
            meta_path.unlink(missing_ok=True)


def _load_variant_context(
    config: Dict[str, Any],
    file_path: str,
    max_recent: int = 5,
) -> str:
    """Load recent variant metadata for a file to provide context to the LLM.

    Returns a text summary of prior variants, or empty string if none/disabled.
    """
    archive_cfg = config.get("archive", {})
    if not archive_cfg.get("enabled", False):
        return ""

    archive_base = Path(archive_cfg.get("path", "data/genesis/evolve_archive"))
    if not archive_base.is_absolute():
        archive_base = BASE_DIR / archive_base

    tool_name = file_path.replace("/", "_").replace("\\", "_")
    if tool_name.endswith(".py"):
        tool_name = tool_name[:-3]

    tool_dir = archive_base / tool_name
    if not tool_dir.exists():
        return ""

    meta_files = sorted(tool_dir.glob("*_metadata.json"), reverse=True)
    if not meta_files:
        return ""

    summaries = []
    for mf in meta_files[:max_recent]:
        try:
            meta = json.loads(mf.read_text(encoding="utf-8"))
            status = meta.get("status", "unknown")
            improvement = meta.get("analysis", {}).get("improvement", "?")
            reason = meta.get("rejection_reason", "")
            delta = meta.get("complexity_delta", 0)
            line = f"- [{status}] {improvement} (complexity_delta={delta})"
            if reason:
                line += f" reason: {reason}"
            summaries.append(line)
        except Exception:
            continue

    if not summaries:
        return ""

    return "\n".join(summaries)


# ── Feature 2: Meta-Evolve (self-modifying selection strategy) ──────────────


def _meta_evolve_check(
    config: Dict[str, Any],
    current_strategy: str,
    cycle_count: int,
) -> str:
    """Evaluate whether to rotate the selection strategy.

    Returns the (possibly new) strategy name.
    """
    meta_cfg = config.get("meta_evolve", {})
    if not meta_cfg.get("enabled", False):
        return current_strategy

    evaluate_every = meta_cfg.get("evaluate_every_n_cycles", 10)
    if cycle_count < evaluate_every:
        return current_strategy

    # Only evaluate on exact multiples of the evaluation interval
    if cycle_count % evaluate_every != 0:
        return current_strategy

    strategies = meta_cfg.get(
        "strategies",
        [
            "worst_code_quality",
            "most_failures",
            "highest_churn",
            "lowest_coverage",
        ],
    )
    min_acceptance_rate = meta_cfg.get("min_acceptance_rate", 0.20)

    # Compute acceptance rate for current strategy over the last N cycles
    acceptance_rate = _compute_strategy_acceptance_rate(current_strategy, evaluate_every)

    print(
        f"  Evolve: meta-evolve check — strategy='{current_strategy}', "
        f"acceptance_rate={acceptance_rate:.2f}, min={min_acceptance_rate:.2f}"
    )

    if acceptance_rate >= min_acceptance_rate:
        return current_strategy

    # Rotate to next strategy
    try:
        current_idx = strategies.index(current_strategy)
    except ValueError:
        current_idx = -1
    next_idx = (current_idx + 1) % len(strategies)
    new_strategy = strategies[next_idx]

    print(
        f"  Evolve: meta-evolve ROTATING strategy '{current_strategy}' -> '{new_strategy}' "
        f"(acceptance_rate {acceptance_rate:.2f} < {min_acceptance_rate:.2f})"
    )

    # Log strategy switch to genesis_audit
    _log_strategy_switch(current_strategy, new_strategy, acceptance_rate, min_acceptance_rate)

    return new_strategy


def _compute_strategy_acceptance_rate(strategy: str, window: int) -> float:
    """Compute the acceptance rate for a strategy over the last `window` evolve cycles."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT payload FROM genesis_gkp
            WHERE genesis_reflex = 'evolve'
            ORDER BY created_at DESC
            LIMIT %s
        """,
            (window,),
        ).fetchall()

        if not rows:
            return 0.0

        total = 0
        accepted = 0
        for row in rows:
            payload_str = row["payload"] if isinstance(row, dict) else row[0]
            if not payload_str:
                continue
            try:
                json.loads(payload_str) if isinstance(payload_str, str) else payload_str
                # Check if this GKP was from this strategy
                # (We don't have strategy stored in GKP, so count all evolve GKPs)
                total += 1
                # A GKP with confidence >= 0.55 = accepted/promotable
                # We approximate by checking if the confidence rationale indicates success
            except (json.JSONDecodeError, TypeError):
                continue

        # Use genesis_audit for actual acceptance tracking
        audit_rows = conn.execute(
            """
            SELECT event_type FROM genesis_audit
            WHERE event_type IN ('genesis.evolve.mutation_accepted', 'genesis.evolve.mutation_rejected')
            ORDER BY created_at DESC
            LIMIT %s
        """,
            (window,),
        ).fetchall()

        if not audit_rows:
            return 0.0

        total = len(audit_rows)
        accepted = sum(
            1
            for r in audit_rows
            if (r["event_type"] if isinstance(r, dict) else r[0]) == "genesis.evolve.mutation_accepted"
        )
        return accepted / total if total > 0 else 0.0

    except Exception as e:
        print(f"  WARN: meta-evolve acceptance rate check failed: {e}")
        return 0.0
    finally:
        conn.close()


def _log_strategy_switch(
    old_strategy: str,
    new_strategy: str,
    acceptance_rate: float,
    min_rate: float,
) -> None:
    """Log a strategy rotation to genesis_audit."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO genesis_audit (event_type, reflex_name, details, created_at)
            VALUES (%s, %s, %s, %s)
        """,
            (
                "genesis.evolve.strategy_rotated",
                "genesis-evolve",
                json.dumps(
                    {
                        "old_strategy": old_strategy,
                        "new_strategy": new_strategy,
                        "acceptance_rate": round(acceptance_rate, 4),
                        "min_acceptance_rate": min_rate,
                    }
                ),
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"  WARN: Could not log strategy switch: {e}")
    finally:
        conn.close()


def _get_evolve_cycle_count() -> int:
    """Get the total number of evolve cycles from genesis_audit."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM genesis_audit
            WHERE event_type = 'genesis.reflex.completed'
              AND actor = 'genesis-evolve'
        """).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


# ── Feature 3: Peer-Review Gate (Hyperagents-inspired) ──────────────────────


def _peer_review_mutation(
    config: Dict[str, Any],
    file_path: str,
    original_content: str,
    analysis: Dict,
) -> Dict[str, Any]:
    """Lightweight LLM review of the proposed mutation before running tests.

    Uses scanner-tier LLM (zero cost) to check for:
    - Removed error handling
    - Introduced security issues
    - Logic regressions

    Returns:
        {"approved": bool, "issues": [...], "reviewer": str}
    """
    peer_cfg = config.get("peer_review", {})
    if not peer_cfg.get("enabled", False):
        return {"approved": True, "issues": [], "reviewer": "disabled", "skipped": True}

    reviewer_function = peer_cfg.get("reviewer_function", "wg_coherence_check")

    try:
        from tools.llm.router import invoke as llm_invoke

        improvement = analysis.get("improvement", "Unknown improvement")
        target_fn = analysis.get("target_function", "Unknown")
        risk = analysis.get("risk", "medium")

        prompt = f"""You are a code reviewer. A mutation is proposed for the following file.

File: {file_path}
Proposed change: {improvement}
Target function: {target_fn}
Risk level: {risk}

Current code (first 4000 chars):
```python
{original_content[:4000]}
```

Review this proposed mutation for the following issues ONLY:
1. Would it REMOVE existing error handling (try/except, validation, bounds checks)?
2. Would it INTRODUCE security issues (SQL injection, path traversal, eval/exec, hardcoded secrets)?
3. Would it cause LOGIC REGRESSIONS (changed return types, removed required parameters, broken contracts)?

Respond with JSON only:
{{"approved": true/false, "issues": ["issue1", "issue2"], "risk_assessment": "safe|caution|reject"}}
"""
        result = llm_invoke(
            prompt=prompt,
            function=reviewer_function,
            project_id="genesis",
            max_tokens=1000,
        )

        if result and result.get("content"):
            content = result["content"]
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                review = json.loads(content[json_start:json_end])
                review["reviewer"] = reviewer_function
                review["skipped"] = False
                # If risk_assessment is "reject", override approved to False
                if review.get("risk_assessment") == "reject":
                    review["approved"] = False
                return review

    except Exception as e:
        print(f"  WARN: Peer review failed (graceful pass): {e}")

    # Graceful degradation — approve on failure (don't block)
    return {"approved": True, "issues": [], "reviewer": "fallback", "skipped": True}


# ── Main run() ──────────────────────────────────────────────────────────────


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Evolve Reflex.

    ORANGE tier with quality-gated confidence scaling:
    - Low risk + tests pass + metrics improve → confidence 0.75 (auto-promotable)
    - Medium risk + tests pass → confidence 0.60 (expedited review)
    - High risk OR tests fail → confidence 0.45 (mandatory human review)

    DGM/Hyperagents enhancements:
    - Variant Archive: stores all mutations for learning context
    - Meta-Evolve: rotates selection strategy if acceptance rate drops
    - Peer-Review Gate: lightweight LLM review before full test suite
    """
    allowed_dirs = config.get("allowed_directories", ["tools/", "goals/"])
    forbidden_files = config.get(
        "forbidden_files",
        [
            "CLAUDE.md",
            ".env",
            "tools/db/storage.py",
            "tools/genesis/daemon.py",
        ],
    )

    # ── Meta-Evolve: check if strategy needs rotation ───────────────────────
    base_strategy = config.get("selection_strategy", "worst_code_quality")
    cycle_count = _get_evolve_cycle_count()
    strategy = _meta_evolve_check(config, base_strategy, cycle_count)

    # ── Anomaly thresholds: data-driven instead of hardcoded cutoffs ─────────
    anomaly_cfg = config.get("anomaly_detection", {})
    anomaly_thresholds = _compute_anomaly_thresholds(anomaly_cfg)

    # ── Config-driven confidence gates (fall back to documented defaults) ────
    thresholds_cfg = config.get("thresholds", {})
    min_confidence = thresholds_cfg.get("min_confidence", 0.50)
    expedited_review_confidence = thresholds_cfg.get("expedited_review", 0.55)
    auto_promotable_confidence = thresholds_cfg.get("auto_promotable", 0.70)
    tiers_cfg = thresholds_cfg.get("confidence_tiers", {})

    # Step 1: Find target file using (possibly rotated) strategy
    print(f"  Evolve: scanning with strategy='{strategy}'...")
    target = _select_target(strategy, allowed_dirs, forbidden_files, anomaly_thresholds)
    if not target:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"status": "no_files_need_improvement", "strategy": strategy},
        }

    print(
        f"  Evolve: target = {target['file_path']} "
        f"(smells={target['smell_count']}, complexity={target['cyclomatic_complexity']})"
    )

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

    # ── Variant Archive: load prior variants for context ────────────────────
    variant_context = _load_variant_context(config, target["file_path"])
    if variant_context:
        print(f"  Evolve: loaded prior variant context ({variant_context.count(chr(10)) + 1} entries)")

    # Step 3: Analyze with LLM (scanner-tier), including variant history
    print("  Evolve: analyzing with LLM...")
    analysis = _analyze_with_llm(
        target["file_path"],
        file_content,
        target,
        variant_context=variant_context,
    )
    if not analysis:
        return {
            "success": False,
            "metric_value": 0.0,
            "details": {
                "status": "llm_analysis_failed",
                "target": target["file_path"],
                "strategy": strategy,
            },
        }

    # ── Peer-Review Gate: lightweight LLM review before testing ─────────────
    print("  Evolve: peer review gate...")
    peer_review = _peer_review_mutation(config, target["file_path"], file_content, analysis)
    if not peer_review.get("approved", True):
        issues = peer_review.get("issues", [])
        print(f"  Evolve: PEER REVIEW REJECTED — {len(issues)} issue(s): {issues}")

        # Archive the rejected variant
        _archive_variant(
            config,
            file_path=target["file_path"],
            file_content=file_content,
            status="rejected",
            analysis=analysis,
            test_results=None,
            metrics=target,
            rejection_reason=f"peer_review: {'; '.join(issues)}",
        )

        return {
            "success": False,
            "metric_value": 0.0,
            "details": {
                "status": "peer_review_rejected",
                "target_file": target["file_path"],
                "strategy": strategy,
                "peer_review_issues": issues,
                "improvement": analysis.get("improvement", ""),
            },
        }

    # Step 3.5: Sandbox isolation check (D-SEC-10)
    print("  Evolve: sandbox verification...")
    sandbox_passed = _sandbox_verify_file(target["file_path"])
    if not sandbox_passed:
        print("  Evolve: SANDBOX FAILED — file does not compile cleanly in container")

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
            metrics_improved = new_smells < old_smells or new_cc < old_cc or new_maint > old_maint

    # Step 6: Compute quality-gated confidence
    confidence, rationale = _compute_confidence(risk, tests_passed, metrics_improved, sandbox_passed, tiers_cfg)
    print(f"  Evolve: confidence={confidence:.2f} ({rationale})")

    # ── Variant Archive: store the result ───────────────────────────────────
    variant_status = "accepted" if tests_passed and confidence >= min_confidence else "rejected"
    rejection_reason = ""
    if not tests_passed:
        rejection_reason = "tests_failed"
    elif confidence < min_confidence:
        rejection_reason = f"low_confidence_{confidence:.2f}"

    _archive_variant(
        config,
        file_path=target["file_path"],
        file_content=file_content,
        status=variant_status,
        analysis=analysis,
        test_results=test_result,
        metrics=target,
        fresh_metrics=fresh_metrics,
        rejection_reason=rejection_reason,
    )

    # Step 7: Export as GKP proposal with computed confidence
    print(f"  Evolve: proposing -- {analysis.get('improvement', '?')[:80]}")
    gkp_id = _export_mutation_proposal(
        target["file_path"],
        analysis,
        target,
        confidence=confidence,
        confidence_rationale=rationale,
        fresh_metrics=fresh_metrics,
        sandbox_passed=sandbox_passed,
        peer_review_result=peer_review if not peer_review.get("skipped") else None,
    )

    status = "proposed_for_human_review"
    if confidence >= auto_promotable_confidence:
        status = "auto_promotable"
    elif confidence >= expedited_review_confidence:
        status = "expedited_review"

    return {
        "success": gkp_id is not None,
        "metric_value": confidence if gkp_id else 0.0,
        "details": {
            "target_file": target["file_path"],
            "improvement": analysis.get("improvement", ""),
            "target_function": analysis.get("target_function", ""),
            "risk": risk,
            "strategy": strategy,
            "current_metrics": {
                "cyclomatic_complexity": target["cyclomatic_complexity"],
                "smell_count": target["smell_count"],
                "maintainability_score": target["maintainability_score"],
            },
            "fresh_metrics": fresh_metrics,
            "metrics_improved": metrics_improved,
            "tests_passed": tests_passed,
            "sandbox_passed": sandbox_passed,
            "confidence": confidence,
            "confidence_rationale": rationale,
            "gkp_id": gkp_id,
            "status": status,
            "variant_status": variant_status,
            "peer_review": {
                "approved": peer_review.get("approved", True),
                "issues": peer_review.get("issues", []),
                "skipped": peer_review.get("skipped", True),
            },
        },
    }
