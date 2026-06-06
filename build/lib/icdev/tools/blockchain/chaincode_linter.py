#!/usr/bin/env python3
# CUI // SP-CTI
"""GovChain Chaincode Security Linter (D-GC-4).

Scans Go/Java/Node chaincode source files against vulnerability patterns
defined in args/chaincode_security_config.yaml. Reports findings by severity
with NIST 800-53 control mappings and enforces gate thresholds.

Usage:
    python tools/blockchain/chaincode_linter.py --scan --json
    python tools/blockchain/chaincode_linter.py --scan --gate --json
    python tools/blockchain/chaincode_linter.py --scan --chaincode-dir path/to/cc --json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DEFAULT_CHAINCODE_DIR = BASE_DIR / "tools" / "blockchain" / "chaincode"
DEFAULT_CONFIG = BASE_DIR / "args" / "chaincode_security_config.yaml"

# Language -> file extensions
LANG_EXTENSIONS: Dict[str, List[str]] = {
    "go": [".go"],
    "java": [".java"],
    "node": [".js", ".ts"],
}


def _load_config(config_path: Path = None) -> Dict[str, Any]:
    path = config_path or DEFAULT_CONFIG
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # Fallback: minimal inline config if yaml unavailable
        return {
            "vulnerability_patterns": {
                "go": {
                    "critical": [
                        {"pattern": "unsafe\\.", "id": "CC-GO-001", "title": "Unsafe package usage", "nist_controls": ["SI-16"]},
                        {"pattern": "exec\\.Command\\(", "id": "CC-GO-002", "title": "OS command execution", "nist_controls": ["SI-3"]},
                    ]
                }
            },
            "gate": {"max_critical": 0, "max_high": 0, "max_medium": 10, "max_low": 50},
        }
    except Exception as e:
        return {"_error": str(e), "gate": {"max_critical": 0, "max_high": 0}}


def _collect_files(chaincode_dir: Path) -> Dict[str, List[Path]]:
    """Return {language: [file_paths]} for all chaincode source files."""
    result: Dict[str, List[Path]] = {lang: [] for lang in LANG_EXTENSIONS}
    if not chaincode_dir.exists():
        return result
    for file_path in chaincode_dir.rglob("*"):
        if not file_path.is_file():
            continue
        suffix = file_path.suffix.lower()
        for lang, exts in LANG_EXTENSIONS.items():
            if suffix in exts:
                result[lang].append(file_path)
    return result


def _scan_file(file_path: Path, patterns: List[Dict[str, Any]], severity: str) -> List[Dict[str, Any]]:
    """Scan a single file against a list of pattern dicts. Returns findings."""
    findings = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
    except Exception as e:
        return [{"severity": severity, "id": "CC-READ-ERR", "file": str(file_path), "error": str(e)}]

    for pat_def in patterns:
        pattern = pat_def.get("pattern", "")
        if not pattern:
            continue
        try:
            compiled = re.compile(pattern)
        except re.error:
            continue

        for lineno, line in enumerate(lines, start=1):
            if compiled.search(line):
                findings.append(
                    {
                        "severity": severity,
                        "id": pat_def.get("id", "CC-UNKNOWN"),
                        "title": pat_def.get("title", "Unknown"),
                        "description": pat_def.get("description", ""),
                        "nist_controls": pat_def.get("nist_controls", []),
                        "file": str(file_path.relative_to(BASE_DIR)),
                        "line": lineno,
                        "match": line.strip()[:120],
                    }
                )

    return findings


def scan(chaincode_dir: Path = None, config_path: Path = None) -> Dict[str, Any]:
    """Run the full chaincode security lint scan.

    Returns:
        dict with findings by severity, counts, gate result, and per-file details.
    """
    chaincode_dir = chaincode_dir or DEFAULT_CHAINCODE_DIR
    cfg = _load_config(config_path)

    patterns_cfg = cfg.get("vulnerability_patterns", {})
    gate_cfg = cfg.get("gate", {})
    severity_weights = cfg.get("severity_weights", {"critical": 10.0, "high": 5.0, "medium": 2.0, "low": 1.0})

    files_by_lang = _collect_files(chaincode_dir)
    total_files = sum(len(v) for v in files_by_lang.values())

    all_findings: List[Dict[str, Any]] = []

    for lang, lang_patterns in patterns_cfg.items():
        files = files_by_lang.get(lang, [])
        if not files:
            continue
        for severity, pat_list in lang_patterns.items():
            for file_path in files:
                findings = _scan_file(file_path, pat_list, severity)
                all_findings.extend(findings)

    # Deduplicate: same (id, file, line) shouldn't appear twice
    seen = set()
    deduped = []
    for f in all_findings:
        key = (f.get("id"), f.get("file"), f.get("line"))
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    all_findings = deduped

    # Count by severity
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in all_findings:
        sev = f.get("severity", "low")
        counts[sev] = counts.get(sev, 0) + 1

    # Compute risk score
    risk_score = sum(
        counts.get(sev, 0) * severity_weights.get(sev, 1.0)
        for sev in counts
    )

    # Gate evaluation
    gate_pass = (
        counts["critical"] <= gate_cfg.get("max_critical", 0)
        and counts["high"] <= gate_cfg.get("max_high", 0)
        and counts["medium"] <= gate_cfg.get("max_medium", 10)
        and counts.get("low", 0) <= gate_cfg.get("max_low", 50)
    )

    return {
        "chaincode_dir": str(chaincode_dir),
        "total_files_scanned": total_files,
        "total_findings": len(all_findings),
        "counts": counts,
        "risk_score": round(risk_score, 1),
        "gate": {
            "passed": gate_pass,
            "thresholds": gate_cfg,
            "actual": counts,
        },
        "findings": all_findings,
    }


def main():
    parser = argparse.ArgumentParser(description="GovChain Chaincode Security Linter (D-GC-4)")
    parser.add_argument("--scan", action="store_true", help="Run security scan against chaincode directory")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if gate thresholds are exceeded")
    parser.add_argument("--chaincode-dir", help="Path to chaincode directory (default: tools/blockchain/chaincode)")
    parser.add_argument("--config", help="Path to chaincode_security_config.yaml")
    parser.add_argument("--severity", choices=["critical", "high", "medium", "low"], help="Filter findings by severity")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not args.scan:
        parser.print_help()
        return

    chaincode_dir = Path(args.chaincode_dir) if args.chaincode_dir else None
    config_path = Path(args.config) if args.config else None

    result = scan(chaincode_dir=chaincode_dir, config_path=config_path)

    # Filter by severity if requested
    if args.severity:
        result["findings"] = [f for f in result["findings"] if f.get("severity") == args.severity]

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Files scanned: {result['total_files_scanned']}")
        print(f"Findings: {result['total_findings']} (critical={result['counts']['critical']}, high={result['counts']['high']}, medium={result['counts']['medium']}, low={result['counts'].get('low', 0)})")
        print(f"Risk score: {result['risk_score']}")
        print(f"Gate: {'PASS' if result['gate']['passed'] else 'FAIL'}")
        if result["findings"]:
            print("\nFindings:")
            for f in result["findings"]:
                print(f"  [{f['severity'].upper()}] {f['id']}: {f['title']}")
                print(f"    {f['file']}:{f['line']} — {f['match']}")
                if f.get("nist_controls"):
                    print(f"    NIST: {', '.join(f['nist_controls'])}")

    if args.gate and not result["gate"]["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
