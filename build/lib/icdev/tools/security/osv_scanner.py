# CUI // SP-CTI
"""OSV-Scanner wrapper for Software Composition Analysis (SCA) vulnerability detection.

Wraps the `osv-scanner` binary (https://github.com/google/osv-scanner).
Degrades gracefully when the binary is absent — returns status='unavailable'
rather than raising, so it can be wired into health_check.py without
blocking air-gap environments that haven't installed the binary.

Usage:
    python tools/security/osv_scanner.py --target requirements.txt --json
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class OsvResult:
    status: str  # 'clean' | 'vulnerabilities_found' | 'error' | 'unavailable'
    vuln_count: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    vulns: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    raw: Optional[Dict] = None


class OsvScanner:
    """Thin subprocess wrapper around the osv-scanner CLI binary."""

    def __init__(self, binary: Optional[str] = None) -> None:
        self._binary = binary or shutil.which("osv-scanner")
        if not self._binary:
            import warnings
            warnings.warn(
                "osv-scanner binary not found in PATH. "
                "Install from https://github.com/google/osv-scanner/releases "
                "or via 'go install github.com/google/osv-scanner/cmd/osv-scanner@latest'. "
                "OsvScanner will return status='unavailable' until installed.",
                ImportWarning,
                stacklevel=2,
            )

    @property
    def available(self) -> bool:
        return bool(self._binary)

    def run(self, target: str = "requirements.txt", extra_args: Optional[List[str]] = None) -> OsvResult:
        """Run osv-scanner against *target* and return a structured result.

        Parameters
        ----------
        target:
            Path to the file or directory to scan (e.g. requirements.txt,
            go.sum, package-lock.json, or a project directory).
        extra_args:
            Additional CLI flags forwarded verbatim to osv-scanner.
        """
        if not self._binary:
            return OsvResult(status="unavailable", error="osv-scanner binary not found in PATH")

        target_path = Path(target)
        if not target_path.exists():
            return OsvResult(status="error", error=f"Target not found: {target}")

        cmd = [self._binary, "--format", "json"]
        if target_path.is_dir():
            cmd += ["--recursive", str(target_path)]
        else:
            cmd += ["--lockfile", str(target_path)]
        if extra_args:
            cmd.extend(extra_args)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return OsvResult(status="error", error="osv-scanner timed out after 120s")
        except Exception as exc:
            return OsvResult(status="error", error=str(exc))

        # osv-scanner exits 1 when vulns are found — that is not an error condition.
        stdout = proc.stdout.strip()
        if not stdout:
            if proc.returncode not in (0, 1):
                return OsvResult(status="error", error=proc.stderr.strip() or "no output")
            return OsvResult(status="clean")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            return OsvResult(status="error", error=f"non-JSON output: {stdout[:200]}")

        vulns = _parse_vulns(data)
        if not vulns:
            return OsvResult(status="clean", raw=data)

        counts: Dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for v in vulns:
            sev = (v.get("severity") or "").upper()
            if sev in counts:
                counts[sev] += 1

        return OsvResult(
            status="vulnerabilities_found",
            vuln_count=len(vulns),
            critical=counts["CRITICAL"],
            high=counts["HIGH"],
            medium=counts["MEDIUM"],
            low=counts["LOW"],
            vulns=vulns,
            raw=data,
        )


def _parse_vulns(data: Dict) -> List[Dict[str, Any]]:
    """Extract a flat list of vuln dicts from osv-scanner JSON output."""
    vulns: List[Dict[str, Any]] = []
    for result in data.get("results", []):
        for pkg in result.get("packages", []):
            pkg_name = pkg.get("package", {}).get("name", "unknown")
            for v in pkg.get("vulnerabilities", []):
                severity = "UNKNOWN"
                for sev_entry in v.get("severity", []):
                    score = sev_entry.get("score", "")
                    if score.startswith("CRITICAL"):
                        severity = "CRITICAL"
                        break
                    elif score.startswith("HIGH"):
                        severity = "HIGH"
                    elif score.startswith("MEDIUM") and severity not in ("HIGH",):
                        severity = "MEDIUM"
                    elif score.startswith("LOW") and severity not in ("HIGH", "MEDIUM"):
                        severity = "LOW"
                vulns.append({
                    "id": v.get("id", ""),
                    "package": pkg_name,
                    "severity": severity,
                    "summary": v.get("summary", ""),
                })
    return vulns


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OSV-Scanner SCA wrapper")
    parser.add_argument("--target", default="requirements.txt", help="File or directory to scan")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output JSON")
    args = parser.parse_args()

    scanner = OsvScanner()
    result = scanner.run(args.target)

    if args.as_json:
        print(json.dumps({
            "status": result.status,
            "vuln_count": result.vuln_count,
            "critical": result.critical,
            "high": result.high,
            "medium": result.medium,
            "low": result.low,
            "error": result.error,
        }, indent=2))
    else:
        print(f"Status:  {result.status}")
        if result.error:
            print(f"Error:   {result.error}")
        else:
            print(f"Vulns:   {result.vuln_count} (crit={result.critical} high={result.high} med={result.medium} low={result.low})")
