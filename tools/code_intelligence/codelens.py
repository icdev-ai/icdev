#!/usr/bin/env python3
# CUI // SP-CTI
"""CodeLens — Phase D exit validation wrapper (Phase 52 / D331-D337).

Thin aggregator that delegates to tools/analysis/code_analyzer.py for
AST self-analysis and returns a combined JSON report.  Accepts --all
to scan the full tools/ directory (the default target for gate checks).

Usage:
    python tools/code_intelligence/codelens.py --all --json
    python tools/code_intelligence/codelens.py --project-dir tools/ --json
    python tools/code_intelligence/codelens.py --file tools/analysis/code_analyzer.py --json
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_ANALYZER = BASE_DIR / "tools" / "analysis" / "code_analyzer.py"


def _subprocess_env() -> dict:
    """Build env with BASE_DIR prepended to PYTHONPATH so child imports work."""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(BASE_DIR) + (os.pathsep + existing if existing else "")
    return env


def _run_analyzer(project_dir: Path, *, json_out: bool = True) -> dict:
    """Invoke code_analyzer.py and return parsed result."""
    cmd = [sys.executable, str(_ANALYZER), "--project-dir", str(project_dir)]
    if json_out:
        cmd.append("--json")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                            cwd=str(BASE_DIR), env=_subprocess_env())
    if result.returncode != 0:
        return {
            "status": "error",
            "exit_code": result.returncode,
            "stderr": result.stderr.strip()[:500],
        }
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        # analyzer may emit non-JSON preamble; return raw
        return {"status": "ok", "raw": result.stdout.strip()[:2000]}


# Discipline → (file patterns, keywords) for addyosmani engineering disciplines
_DISCIPLINE_CHECKS: dict[str, dict] = {
    "spec":          {"patterns": ["context/specs", "docs/", "SPEC.md", "PRD.md"], "keywords": ["acceptance criteria", "PRD", "spec"]},
    "tdd":           {"patterns": ["tests/", "test_", "_test.py", "features/"], "keywords": ["pytest", "behave", "unittest", "assert"]},
    "security":      {"patterns": ["tools/integrity", "tools/security", "bandit"], "keywords": ["bandit", "OWASP", "SQL injection", "XSS"]},
    "performance":   {"patterns": ["tools/analysis/performance", "benchmark"], "keywords": ["benchmark", "latency", "throughput", "cache"]},
    "ci-cd":         {"patterns": [".github/workflows", ".gitlab-ci.yml", "Dockerfile"], "keywords": ["CI", "CD", "pipeline", "gate"]},
    "documentation": {"patterns": ["docs/", "*.md", "ADR", "changelog"], "keywords": ["ADR", "architecture decision", "changelog"]},
    "observability": {"patterns": ["tools/monitoring", "tools/awareness", "logging"], "keywords": ["logging", "metrics", "trace", "OTel"]},
    "review":        {"patterns": ["tools/workflow/coherence", ".claude/hooks"], "keywords": ["code review", "linting", "coherence"]},
    "api-design":    {"patterns": ["tools/dashboard/app.py", "blueprint.py"], "keywords": ["@app.route", "@bp.route", "REST", "OpenAPI"]},
    "git":           {"patterns": [".git/", "COMMIT_EDITMSG", "pre-commit"], "keywords": ["commit", "branch", "merge", "rebase"]},
}


def _discipline_report(disciplines: list[str], target: Path) -> dict:
    """Return discipline-specific findings for the requested disciplines."""
    from itertools import islice

    def _find(root: Path, pat: str, limit: int = 5) -> list[Path]:
        if "*" in pat:
            return list(islice(root.rglob(pat), limit))
        # Direct path check first (fast)
        direct = root / pat
        if direct.exists():
            return [direct]
        # rglob fallback — cap at limit to avoid scanning the whole tree
        return list(islice(root.rglob(pat.rstrip("/").split("/")[-1]), limit))

    results = {}
    for disc in disciplines:
        if disc not in _DISCIPLINE_CHECKS:
            results[disc] = {"status": "unknown", "detail": f"Unknown discipline '{disc}'. Known: {list(_DISCIPLINE_CHECKS)}"}
            continue
        cfg = _DISCIPLINE_CHECKS[disc]
        hits: list[str] = []
        for pat in cfg["patterns"]:
            found = _find(target, pat) or _find(BASE_DIR, pat)
            hits.extend(str(p.relative_to(BASE_DIR)) for p in found if p.exists())
        results[disc] = {
            "status": "found" if hits else "missing",
            "patterns_checked": cfg["patterns"],
            "hits": hits[:10],
            "keywords": cfg["keywords"],
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="CodeLens — ICDEV™ code intelligence gate (Phase 52)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan the full tools/ directory (gate default)",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="Directory to scan (overrides --all)",
    )
    parser.add_argument("--file", default=None, help="Analyze a single file")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--discipline",
        nargs="+",
        metavar="NAME",
        default=None,
        help=(
            "Filter output by addyosmani engineering discipline(s). "
            f"Choices: {', '.join(_DISCIPLINE_CHECKS)}. "
            "Example: --discipline tdd security"
        ),
    )
    args = parser.parse_args()

    if args.file:
        cmd = [sys.executable, str(_ANALYZER), "--file", args.file]
        if args.json:
            cmd.append("--json")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                           cwd=str(BASE_DIR), env=_subprocess_env())
        if args.json:
            try:
                data = json.loads(r.stdout)
            except json.JSONDecodeError:
                data = {"status": "ok", "raw": r.stdout.strip()}
            print(json.dumps(data, indent=2))
        else:
            print(r.stdout)
        return r.returncode

    # Determine scan target
    if args.project_dir:
        target = Path(args.project_dir)
    elif args.all or True:  # --all is default for gate checks
        target = BASE_DIR / "tools"

    report = _run_analyzer(target, json_out=True)

    # Compute pass/fail for gate
    status = report.get("status", "ok")
    if status == "error":
        gate_pass = False
        gate_reason = report.get("stderr", "analyzer returned non-zero exit code")
    else:
        gate_pass = True
        gate_reason = "code intelligence scan completed"

    output = {
        "gate": "codelens",
        "status": "pass" if gate_pass else "fail",
        "reason": gate_reason,
        "target": str(target),
        "analysis": report,
    }

    if args.discipline:
        output["discipline_filter"] = _discipline_report(args.discipline, target)
        # Gate fails if any requested discipline is missing
        missing = [d for d, v in output["discipline_filter"].items() if v.get("status") == "missing"]
        if missing:
            output["status"] = "warn"
            output["discipline_warnings"] = missing

    if args.json:
        print(json.dumps(output, indent=2))
    else:
        print(f"CodeLens gate: {'PASS' if gate_pass else 'FAIL'}")
        print(f"Reason: {gate_reason}")
        if args.discipline:
            for disc, info in output.get("discipline_filter", {}).items():
                status_icon = "✓" if info.get("status") == "found" else "✗"
                print(f"  [{status_icon}] {disc}: {len(info.get('hits', []))} hits")

    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
