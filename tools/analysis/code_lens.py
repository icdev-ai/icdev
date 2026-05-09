#!/usr/bin/env python3
# CUI // SP-CTI
"""CodeLens — single-file static analysis gate for ICDEV™ V&V.

Delegates to code_analyzer for metrics, then applies pass/fail gate rules:
  - PASS if no critical smells and maintainability >= 0.4
  - WARN if maintainability < 0.6 or complexity > 10 avg
  - FAIL if import errors or parse errors detected

Usage:
    python tools/analysis/code_lens.py --file tools/data_canvas/anomaly_detector.py --json
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    p.add_argument("--json", dest="json_output", action="store_true")
    return p.parse_args()


def _check_importability(file_path: Path) -> dict:
    """Try to compile-check the file for syntax errors."""
    try:
        source = file_path.read_text(encoding="utf-8")
        compile(source, str(file_path), "exec")
        return {"ok": True}
    except SyntaxError as e:
        return {"ok": False, "error": f"SyntaxError: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def run_lens(file_path: Path) -> dict:
    syntax = _check_importability(file_path)
    if not syntax["ok"]:
        return {
            "file": str(file_path),
            "gate": "FAIL",
            "reason": syntax["error"],
            "metrics": {},
        }

    # Run code_analyzer
    from tools.analysis.code_analyzer import CodeAnalyzer

    analyzer = CodeAnalyzer(project_dir=str(file_path.parent))
    try:
        metrics_list = analyzer.analyze_python_file(file_path)
    except Exception as e:
        return {
            "file": str(file_path),
            "gate": "FAIL",
            "reason": f"Analyzer error: {e}",
            "metrics": {},
        }

    if not metrics_list:
        return {
            "file": str(file_path),
            "gate": "PASS",
            "reason": "No functions found (module-level only)",
            "metrics": {"function_count": 0},
        }

    total = len(metrics_list)
    avg_cc = sum(m.get("cyclomatic_complexity", 1) for m in metrics_list) / total
    avg_maint = sum(m.get("maintainability_score", 1.0) for m in metrics_list) / total
    critical_smells = sum(
        1
        for m in metrics_list
        for s in m.get("smells", [])
        if s.get("severity") == "critical"
    )

    gate = "PASS"
    reasons = []
    if critical_smells > 0:
        gate = "FAIL"
        reasons.append(f"{critical_smells} critical smell(s)")
    if avg_maint < 0.4:
        gate = "FAIL"
        reasons.append(f"avg maintainability {avg_maint:.2f} < 0.4")
    elif avg_maint < 0.6 and gate == "PASS":
        gate = "WARN"
        reasons.append(f"avg maintainability {avg_maint:.2f} < 0.6")
    if avg_cc > 15 and gate == "PASS":
        gate = "WARN"
        reasons.append(f"avg cyclomatic complexity {avg_cc:.1f} > 15")

    return {
        "file": str(file_path),
        "gate": gate,
        "reason": "; ".join(reasons) if reasons else "ok",
        "metrics": {
            "function_count": total,
            "avg_cyclomatic_complexity": round(avg_cc, 2),
            "avg_maintainability": round(avg_maint, 4),
            "critical_smells": critical_smells,
        },
    }


def main():
    args = _parse_args()
    fp = Path(args.file)
    if not fp.is_absolute():
        fp = BASE_DIR / fp
    if not fp.exists():
        result = {"file": str(args.file), "gate": "FAIL", "reason": "file not found"}
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"FAIL: {result['reason']}")
        sys.exit(1)

    result = run_lens(fp)
    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(f"{result['gate']}: {result['file']} — {result['reason']}")
        if result.get("metrics"):
            m = result["metrics"]
            print(f"  functions={m.get('function_count',0)}  "
                  f"avg_cc={m.get('avg_cyclomatic_complexity',0)}  "
                  f"avg_maint={m.get('avg_maintainability',0)}")

    sys.exit(0 if result["gate"] in ("PASS", "WARN") else 1)


if __name__ == "__main__":
    main()
