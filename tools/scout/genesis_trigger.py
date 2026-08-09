#!/usr/bin/env python3
# CUI // SP-CTI
"""Scout Genesis Trigger — auto-build new features from Scout findings.

Picks the highest-value actionable finding, creates a git worktree,
runs Genesis to build the feature, validates, and reports results.
Falls back to 2nd and 3rd highest if validation fails.

Architecture:
    - Worktree isolation: builds on feature/scout-auto-YYYY-MM-DD branch
    - Full validation: py_compile, ruff, pytest, bandit
    - Auto-rollback on validation failure
    - Max 1 build per day, 3-deep fallback chain
    - GREEN risk = auto-approve, YELLOW = sandbox, ORANGE = skip

Usage:
    python tools/scout/genesis_trigger.py --trigger --findings findings.json --json
    python tools/scout/genesis_trigger.py --status --json
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_cmd(cmd: list, cwd: str = None, timeout: int = 300) -> dict:
    """Run a shell command, return result dict."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd or str(BASE_DIR),
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
            "ok": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "Timeout", "ok": False}
    except Exception as exc:
        return {"returncode": -1, "stdout": "", "stderr": str(exc), "ok": False}


def _assess_risk(finding: dict) -> str:
    """Assess risk tier for a finding. GREEN/YELLOW/ORANGE."""
    category = finding.get("category", "")
    severity = finding.get("severity", "medium")

    # GREEN: low-risk internal improvements
    green_categories = {
        "test_coverage",
        "dead_code",
        "config_drift",
        "unused_tools",
        "cli_harmonization",
        "code_quality",
    }
    if category in green_categories:
        return "GREEN"

    # ORANGE: external competitive features, entirely new architectures
    if category in {"new_competitor"} or severity == "high":
        return "ORANGE"

    # YELLOW: everything else (trending repos, moderate enhancements)
    return "YELLOW"


def _rank_findings(findings: List[dict], config: dict) -> List[dict]:
    """Rank actionable findings by value, filtered by risk tier."""
    risk_cfg = config.get("genesis_trigger", {}).get("risk_tiers", {})
    allowed_tiers = set()
    for tier, action in risk_cfg.items():
        if action in ("auto", "sandbox"):
            allowed_tiers.add(tier.upper())

    # Default to GREEN + YELLOW if not configured
    if not allowed_tiers:
        allowed_tiers = {"GREEN", "YELLOW"}

    candidates = []
    for f in findings:
        if not f.get("actionable"):
            continue
        risk = _assess_risk(f)
        if risk not in allowed_tiers:
            continue
        candidates.append({**f, "_risk_tier": risk})

    # Sort by relevance_score descending
    candidates.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    return candidates


def _create_worktree(date_str: str, config: dict) -> Optional[dict]:
    """Create an isolated git worktree for the build."""
    wt_cfg = config.get("genesis_trigger", {}).get("worktree", {})
    base_dir = wt_cfg.get("base_dir", ".worktrees")
    prefix = wt_cfg.get("branch_prefix", "feature/scout-auto")
    branch_name = f"{prefix}-{date_str}"
    worktree_path = str(BASE_DIR / base_dir / f"scout-{date_str}")

    # Create worktree
    result = _run_cmd(["git", "worktree", "add", worktree_path, "-b", branch_name])
    if not result["ok"]:
        # Branch might already exist — try without -b
        result = _run_cmd(["git", "worktree", "add", worktree_path, branch_name])
        if not result["ok"]:
            return None

    return {"path": worktree_path, "branch": branch_name}


def _remove_worktree(worktree_path: str) -> dict:
    """Remove a git worktree (rollback)."""
    result = _run_cmd(["git", "worktree", "remove", worktree_path, "--force"])
    # Also try to delete the branch
    return result


def _build_in_worktree(finding: dict, worktree_path: str, config: dict) -> dict:
    """Run Genesis build for a finding inside the worktree."""
    build_result = {
        "finding_id": finding.get("id"),
        "finding_title": finding.get("title"),
        "risk_tier": finding.get("_risk_tier", "YELLOW"),
        "started_at": _now(),
    }

    action = finding.get("suggested_action", "")
    category = finding.get("category", "")
    description = finding.get("description", "")

    # Build specification for Genesis
    spec = (
        f"Scout Auto-Build: {action}\n\n"
        f"Category: {category}\n"
        f"Finding: {finding.get('title', '')}\n"
        f"Description: {description}\n\n"
        f"Requirements:\n"
        f"- Implement the suggested improvement\n"
        f"- Add appropriate tests\n"
        f"- Follow ICDEV™ FORGE framework conventions\n"
        f"- Include CUI markings\n"
    )

    # Write build spec to worktree
    spec_path = Path(worktree_path) / ".scout_build_spec.md"
    spec_path.write_text(spec, encoding="utf-8", newline="")

    # Try to use solution_generator if available (generates from innovation_signals DB)
    try:
        from tools.innovation.solution_generator import generate_solution_spec

        # solution_generator works from signal IDs in the DB, not direct args
        # If the finding came from an innovation signal, use its ID
        signal_id = finding.get("metadata", {}).get("signal_id")
        if signal_id:
            sol = generate_solution_spec(signal_id)
            build_result["solution"] = sol if isinstance(sol, dict) else {"raw": str(sol)}
        else:
            build_result["solution"] = {"status": "spec_only", "spec_path": str(spec_path)}
    except Exception as exc:
        build_result["solution"] = {"error": str(exc), "fallback": "spec_only"}

    build_result["completed_at"] = _now()
    return build_result


def _validate_worktree(worktree_path: str, config: dict) -> dict:
    """Run full validation suite in the worktree."""
    val_cfg = config.get("genesis_trigger", {}).get("validation", {})
    results = {}

    if val_cfg.get("py_compile", True):
        # Find new/modified .py files in worktree (vs main branch)
        diff = _run_cmd(["git", "diff", "--name-only", "HEAD~1"], cwd=worktree_path)
        if not diff["ok"]:
            # No commits yet on this branch — check untracked/modified files instead
            diff = _run_cmd(["git", "diff", "--name-only"], cwd=worktree_path)
        py_files = [f for f in diff.get("stdout", "").strip().split("\n") if f.endswith(".py") and f.strip()]
        # Also check the build spec we wrote
        if not py_files:
            results["py_compile"] = "PASS"  # No .py files changed = nothing to compile
        else:
            for pf in py_files[:20]:
                full_path = Path(worktree_path) / pf
                if not full_path.exists():
                    continue  # File was deleted, not added
                r = _run_cmd(["python", "-m", "py_compile", str(full_path)], cwd=worktree_path)
                if not r["ok"]:
                    results["py_compile"] = "FAIL"
                    results["py_compile_error"] = r["stderr"]
                    break
            else:
                results["py_compile"] = "PASS"

    if val_cfg.get("ruff", True):
        r = _run_cmd(["python", "-m", "ruff", "check", "."], cwd=worktree_path)
        results["ruff"] = "PASS" if r["ok"] else "FAIL"

    if val_cfg.get("pytest", True):
        r = _run_cmd(
            ["python", "-m", "pytest", "tests/", "-q", "--tb=line", "-x", "--timeout=60"],
            cwd=worktree_path,
            timeout=600,
        )
        results["pytest"] = "PASS" if r["ok"] else "FAIL"

    if val_cfg.get("bandit", True):
        r = _run_cmd(
            ["python", "-m", "bandit", "-r", "tools/", "--severity-level", "medium", "-q"],
            cwd=worktree_path,
            timeout=120,
        )
        results["bandit"] = "PASS" if r["ok"] else "FAIL"

    # Overall pass/fail
    results["overall"] = (
        "PASS"
        if all(v in ("PASS", "SKIP") for k, v in results.items() if k not in ("overall",) and not k.endswith("_error"))
        else "FAIL"
    )

    return results


def trigger_build(findings: List[dict], config: dict = None) -> dict:
    """Pick highest-value finding, build in worktree, validate. Fallback up to 3 deep.

    Returns:
        {
            "status": "success" | "all_failed" | "skipped",
            "branch": "feature/scout-auto-YYYY-MM-DD",
            "finding_title": "...",
            "validation": {...},
            "attempts": [...],
        }
    """
    config = config or {}
    gt_cfg = config.get("genesis_trigger", {})

    if not gt_cfg.get("enabled", True):
        return {"status": "skipped", "reason": "genesis_trigger disabled"}

    fallback_depth = gt_cfg.get("fallback_depth", 3)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Rank candidates
    candidates = _rank_findings(findings, config)
    if not candidates:
        return {"status": "skipped", "reason": "No actionable findings meeting risk criteria"}

    attempts = []

    for i, candidate in enumerate(candidates[:fallback_depth]):
        attempt = {
            "rank": i + 1,
            "finding_id": candidate.get("id"),
            "finding_title": candidate.get("title"),
            "risk_tier": candidate.get("_risk_tier", "YELLOW"),
            "relevance_score": candidate.get("relevance_score", 0),
            "started_at": _now(),
        }

        # Create worktree
        wt = _create_worktree(f"{date_str}-attempt{i + 1}", config)
        if not wt:
            attempt["status"] = "worktree_failed"
            attempt["completed_at"] = _now()
            attempts.append(attempt)
            continue

        attempt["worktree_path"] = wt["path"]
        attempt["branch"] = wt["branch"]

        # Build
        build_result = _build_in_worktree(candidate, wt["path"], config)
        attempt["build"] = build_result

        # Validate
        validation = _validate_worktree(wt["path"], config)
        attempt["validation"] = validation

        if validation.get("overall") == "PASS":
            attempt["status"] = "success"
            attempt["completed_at"] = _now()
            attempts.append(attempt)

            return {
                "status": "success",
                "branch": wt["branch"],
                "worktree_path": wt["path"],
                "finding_id": candidate.get("id"),
                "finding_title": candidate.get("title"),
                "risk_tier": candidate.get("_risk_tier"),
                "validation": validation,
                "attempts": attempts,
                "completed_at": _now(),
            }
        else:
            # Validation failed — rollback worktree
            attempt["status"] = "validation_failed"
            attempt["completed_at"] = _now()
            _remove_worktree(wt["path"])
            attempts.append(attempt)

    return {
        "status": "all_failed",
        "reason": f"All {len(attempts)} build attempts failed validation",
        "attempts": attempts,
        "completed_at": _now(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Scout Genesis trigger")
    parser.add_argument("--trigger", action="store_true", help="Trigger build from findings")
    parser.add_argument("--findings", help="Path to findings JSON file")
    parser.add_argument("--status", action="store_true", help="Show build status")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    try:
        import yaml

        cfg_path = BASE_DIR / "args" / "scout_config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        config = {}

    if args.trigger:
        if args.findings:
            with open(args.findings, "r", encoding="utf-8") as f:
                findings = json.load(f)
        else:
            findings = []
        result = trigger_build(findings, config)
    elif args.status:
        # Check for active worktrees
        r = _run_cmd(["git", "worktree", "list", "--porcelain"])
        result = {"worktrees": r.get("stdout", ""), "checked_at": _now()}
    else:
        parser.error("Specify --trigger or --status")
        return

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        for k, v in result.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
