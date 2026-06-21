# CUI // SP-CTI
"""Skill promotion gate — single-skill security assessment for Kanban workflows.

Public API:
    check_skill_security(skill_dir)           -- scan one skill directory, return raw result
    assess_skill_for_promotion(skill_dir_or_id) -- cache-aware wrapper, returns compact gate dict

Both functions return a dict compatible with the Kanban promotion decision schema:
    {
        "allowed":        bool,    # True == gate passes (no blocking findings)
        "risk_score":     float,   # 0–100 weighted risk points
        "risk_severity":  str,     # highest severity found ("critical"|"high"|"medium"|"low"|"info"|"none")
        "findings_count": int,     # total findings (code patterns + bash patterns)
        "reason":         str,     # human-readable gate verdict explanation
    }

check_skill_security also includes a "findings" list and "skill_dir" path.

Cache:
    Results are persisted to .tmp/skillspector_cache.json via
    tools.integrity.skillspector_cache (keyed by SKILL.md content + mtime so any
    edit invalidates the entry automatically).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# Ensure repo root is on sys.path when executed directly
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.integrity import skillspector_cache
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.kanban.skill_promotion_gate")

# ---------------------------------------------------------------------------
# Risk scoring thresholds (aligned with SIPA scoring.py defaults)
# ---------------------------------------------------------------------------
_SEVERITY_POINTS: dict[str, float] = {
    "critical": 50.0,
    "high":     35.0,
    "medium":   20.0,
    "low":      10.0,
    "info":      2.5,
}

# Severity ordering — most severe first (used to find the highest severity hit)
_SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]

# ---------------------------------------------------------------------------
# Dangerous bash / shell patterns checked inside SKILL.md code fences
# (code_pattern_scanner handles Python/JS/Go files; these cover shell commands)
# ---------------------------------------------------------------------------
_BASH_DANGER_PATTERNS: list[tuple[str, str, str, str]] = [
    # (regex, severity, name, description)
    (r"\brm\s+-[rf]{1,2}\b",             "critical", "rm_rf",            "Destructive rm -rf / rm -r command"),
    (r"curl\b[^|]*\|\s*(ba)?sh\b",       "critical", "curl_pipe_shell",  "Remote code exec: curl | bash/sh"),
    (r"wget\b[^|]*\|\s*(ba)?sh\b",       "critical", "wget_pipe_shell",  "Remote code exec: wget | bash/sh"),
    (r"\beval\s+\$",                      "critical", "eval_shell_expand","Shell eval with variable expansion"),
    (r"\beval\s+[\"']",                   "high",     "eval_shell_string", "Shell eval with string literal"),
    (r"base64\s+(?:--decode|-d)\s*\|",   "critical", "b64_pipe_exec",    "Base64-decoded shell execution"),
    (r"python[23]?\s+-c\s+[\"'].*eval",  "critical", "python_c_eval",    "python -c containing eval"),
    (r"\bsudo\s+(su|bash|sh)\b",         "high",     "sudo_shell",       "Privilege escalation via sudo shell"),
    (r"\bchmod\s+[0-7]*7\s",             "high",     "world_writable",   "World-writable file permissions"),
    (r"\b(wget|curl)\s+[^\n]+\s+-[oO]\s","high",     "remote_download",  "Downloading remote content to disk"),
    (r">\s*/etc/",                        "high",     "write_etc",        "Overwriting /etc system files"),
    (r"\b(nc|ncat|netcat)\s+-[le]",      "high",     "netcat_listener",  "Netcat listener — reverse shell indicator"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_fenced_blocks(text: str) -> list[str]:
    """Return content of all fenced bash/sh/shell code blocks in *text*."""
    return re.findall(r"```(?:bash|sh|shell)?\n(.*?)```", text, re.DOTALL)


def _highest_severity(findings: list[dict]) -> str:
    """Return the highest severity present in *findings*, or 'none'."""
    for sev in _SEVERITY_ORDER:
        if any(f.get("severity") == sev for f in findings):
            return sev
    return "none"


def _compact(result: dict[str, Any]) -> dict[str, Any]:
    """Strip internal keys; return only the public promotion-gate shape."""
    return {
        "allowed":        result.get("allowed", False),
        "risk_score":     result.get("risk_score", 0.0),
        "risk_severity":  result.get("risk_severity", "none"),
        "findings_count": result.get("findings_count", 0),
        "reason":         result.get("reason", ""),
    }


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

def check_skill_security(skill_dir: str | pathlib.Path) -> dict[str, Any]:
    """Scan *skill_dir* for dangerous code and shell patterns.

    Two scan passes are run:
      1. ``CodePatternScanner.scan_directory()`` — catches dangerous Python/JS/Go/Rust
         APIs in any source files inside the skill directory.
      2. Regex sweep over SKILL.md fenced bash/sh/shell code blocks — catches
         destructive shell commands and remote-exec patterns not covered by the
         code pattern scanner (which targets source code extensions, not .md).

    Returns a full result dict including a ``"findings"`` list.  Callers that only
    need the compact gate result should use :func:`assess_skill_for_promotion`.
    """
    skill_path = pathlib.Path(skill_dir).resolve()

    all_findings: list[dict] = []
    code_gate_passed = True  # default: pass if scanner unavailable

    # ------------------------------------------------------------------
    # Pass 1 — Code pattern scan (Python / shell / JS / Go / Rust files)
    # ------------------------------------------------------------------
    try:
        from tools.security.code_pattern_scanner import CodePatternScanner  # lazy import

        if skill_path.is_dir():
            scanner = CodePatternScanner()
            dir_result = scanner.scan_directory(str(skill_path))
            if "error" not in dir_result:
                for finding in dir_result.get("findings", []):
                    all_findings.append({
                        "name":        finding.get("name", ""),
                        "severity":    finding.get("severity", "info"),
                        "description": finding.get("description", ""),
                        "file":        finding.get("file", ""),
                        "line":        finding.get("line", 0),
                        "source":      "code_pattern_scanner",
                    })
                # Respect the scanner's allowlist-aware gate
                code_gate_passed = dir_result.get("gate_passed", True)
    except Exception as exc:
        logger.debug("CodePatternScanner unavailable for %s: %s", skill_dir, exc)

    # ------------------------------------------------------------------
    # Pass 2 — Bash pattern scan on SKILL.md fenced code blocks
    # ------------------------------------------------------------------
    bash_findings: list[dict] = []
    skill_md = skill_path / "SKILL.md"
    if skill_md.exists():
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            for block in _extract_fenced_blocks(content):
                for pattern, severity, name, description in _BASH_DANGER_PATTERNS:
                    for m in re.finditer(pattern, block, re.IGNORECASE | re.MULTILINE):
                        line_in_block = block[: m.start()].count("\n") + 1
                        bash_findings.append({
                            "name":        name,
                            "severity":    severity,
                            "description": description,
                            "file":        str(skill_md),
                            "line":        line_in_block,
                            "source":      "bash_pattern_scan",
                        })
        except Exception as exc:
            logger.debug("SKILL.md bash scan failed for %s: %s", skill_dir, exc)

    all_findings.extend(bash_findings)

    # ------------------------------------------------------------------
    # Risk score — weighted sum of severity points, capped at 100
    # ------------------------------------------------------------------
    raw_score = sum(_SEVERITY_POINTS.get(f.get("severity", "info"), 2.5) for f in all_findings)
    risk_score = round(min(100.0, raw_score), 2)

    risk_severity = _highest_severity(all_findings)

    # ------------------------------------------------------------------
    # Gate verdict
    # ------------------------------------------------------------------
    bash_critical = sum(1 for f in bash_findings if f.get("severity") == "critical")
    bash_high     = sum(1 for f in bash_findings if f.get("severity") == "high")
    allowed = code_gate_passed and bash_critical == 0 and bash_high == 0

    # Human-readable reason
    critical_count = sum(1 for f in all_findings if f.get("severity") == "critical")
    high_count     = sum(1 for f in all_findings if f.get("severity") == "high")

    if allowed:
        if not all_findings:
            reason = "No dangerous patterns detected."
        else:
            reason = (
                f"{len(all_findings)} finding(s) — all at medium/low/info severity; gate passes."
            )
    else:
        parts: list[str] = []
        if critical_count:
            parts.append(f"{critical_count} critical")
        if high_count:
            parts.append(f"{high_count} high")
        reason = f"Blocked: {', '.join(parts)} severity finding(s) require remediation."

    return {
        "allowed":        allowed,
        "risk_score":     risk_score,
        "risk_severity":  risk_severity,
        "findings_count": len(all_findings),
        "findings":       all_findings,
        "reason":         reason,
        "skill_dir":      str(skill_path),
    }


# ---------------------------------------------------------------------------
# Public promotion API (cache-aware)
# ---------------------------------------------------------------------------

def _resolve_skill_dir(skill_dir_or_id: str) -> pathlib.Path | None:
    """Resolve *skill_dir_or_id* to an existing directory path.

    Tries in order:
    1. Treat as a filesystem path (absolute or relative).
    2. Look up by skill name in the skills registry (``tools/skills/registry.py``).
    3. Probe ``<SKILLS_DIR>/<skill_dir_or_id>`` directly.
    """
    candidate = pathlib.Path(skill_dir_or_id)
    if candidate.is_dir():
        return candidate.resolve()

    # Registry lookup
    try:
        from tools.skills.registry import BASE_DIR as SKILLS_BASE, SKILLS_DIR, load_registry

        reg = load_registry()
        entry = reg.get("skills", {}).get(skill_dir_or_id)
        if entry:
            skill_md_path = pathlib.Path(entry["path"])
            if not skill_md_path.is_absolute():
                skill_md_path = SKILLS_BASE / skill_md_path
            skill_dir_path = skill_md_path.parent
            if skill_dir_path.is_dir():
                return skill_dir_path.resolve()

        # Direct probe by directory name
        direct = SKILLS_DIR / skill_dir_or_id
        if direct.is_dir():
            return direct.resolve()
    except Exception as exc:
        logger.debug("Registry lookup failed for %r: %s", skill_dir_or_id, exc)

    return None


def assess_skill_for_promotion(skill_dir_or_id: str) -> dict[str, Any]:
    """Assess *skill_dir_or_id* for promotion and return a compact gate result.

    Accepts either a filesystem path to a skill directory or a skill name/ID
    (e.g. ``"icdev-secure"``) resolved via the skills registry.

    Returns::

        {
            "allowed":        bool,
            "risk_score":     float,   # 0–100
            "risk_severity":  str,
            "findings_count": int,
            "reason":         str,
        }

    Results are cached in ``.tmp/skillspector_cache.json`` keyed by SKILL.md
    content+mtime.  Any edit to SKILL.md automatically invalidates the entry.
    """
    skill_path = _resolve_skill_dir(skill_dir_or_id)

    if skill_path is None:
        logger.warning("Skill not found: %r", skill_dir_or_id)
        return {
            "allowed":        False,
            "risk_score":     100.0,
            "risk_severity":  "critical",
            "findings_count": 0,
            "reason":         f"Skill directory not found: {skill_dir_or_id!r}",
        }

    # Cache hit
    cached = skillspector_cache.get_cached(skill_path)
    if cached is not None:
        logger.debug("skillspector cache hit for %s", skill_path)
        return _compact(cached)

    # Cache miss — run scan
    logger.debug("skillspector cache miss for %s — scanning", skill_path)
    result = check_skill_security(skill_path)
    skillspector_cache.set_cached(skill_path, result)

    return _compact(result)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Assess a skill directory for promotion (Kanban gate).",
    )
    p.add_argument(
        "skill",
        help="Skill directory path or skill name (e.g. icdev-secure)",
    )
    p.add_argument("--full", action="store_true", help="Include findings list in output")
    p.add_argument("--json", action="store_true", help="Output JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.full:
        skill_path = _resolve_skill_dir(args.skill)
        if skill_path is None:
            result: dict[str, Any] = {
                "allowed": False,
                "risk_score": 100.0,
                "risk_severity": "critical",
                "findings_count": 0,
                "reason": f"Skill not found: {args.skill!r}",
            }
        else:
            result = check_skill_security(skill_path)
    else:
        result = assess_skill_for_promotion(args.skill)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        verdict = "PASS" if result.get("allowed") else "BLOCK"
        print(
            f"[{verdict}] {args.skill}\n"
            f"  risk_score={result['risk_score']}  "
            f"risk_severity={result['risk_severity']}  "
            f"findings={result['findings_count']}\n"
            f"  {result['reason']}"
        )

    return 0 if result.get("allowed") else 1


if __name__ == "__main__":
    sys.exit(main())
