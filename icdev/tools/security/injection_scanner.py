#!/usr/bin/env python3
# CUI // SP-CTI
"""Prompt Injection Scanner — scans context/evidence files for threats.

Cherry-picked from Hermes Agent's prompt_builder.py injection scanning.
Adapted for ICDEV's deterministic tool pattern.

Detects:
    - Invisible Unicode (zero-width chars, RTL overrides, homoglyphs)
    - Exfiltration patterns (fetch/curl/webhook URLs in prompts)
    - Instruction override ("ignore previous instructions", etc.)
    - CSS/HTML injection (display:none, hidden elements)
    - Base64 encoded payloads

Usage:
    python tools/security/injection_scanner.py --scan-file <path>
    python tools/security/injection_scanner.py --scan-dir context/
    python tools/security/injection_scanner.py --scan-dir hardprompts/
    python tools/security/injection_scanner.py --json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ── Threat Patterns ──────────────────────────────────────────────

# Invisible Unicode characters
INVISIBLE_CHARS = {
    "\u200b": "zero-width space",
    "\u200c": "zero-width non-joiner",
    "\u200d": "zero-width joiner",
    "\u2060": "word joiner",
    "\ufeff": "BOM / zero-width no-break",
    "\u200e": "left-to-right mark",
    "\u200f": "right-to-left mark",
    "\u202a": "left-to-right embedding",
    "\u202b": "right-to-left embedding",
    "\u202c": "pop directional formatting",
    "\u202d": "left-to-right override",
    "\u202e": "right-to-left override",
    "\u2066": "left-to-right isolate",
    "\u2067": "right-to-left isolate",
    "\u2068": "first strong isolate",
    "\u2069": "pop directional isolate",
    "\u00ad": "soft hyphen",
    "\u034f": "combining grapheme joiner",
    "\u061c": "Arabic letter mark",
    "\u115f": "Hangul choseong filler",
    "\u1160": "Hangul jungseong filler",
    "\u17b4": "Khmer vowel inherent AQ",
    "\u17b5": "Khmer vowel inherent AA",
    "\u180e": "Mongolian vowel separator",
}

# Instruction override patterns
OVERRIDE_PATTERNS = [
    (
        r"ignore\s+(all\s+)?previous\s+instructions",
        "instruction_override",
    ),
    (
        r"disregard\s+(all\s+)?(prior|previous|above)",
        "instruction_override",
    ),
    (
        r"forget\s+(everything|all)\s+(you|that)",
        "instruction_override",
    ),
    (
        r"you\s+are\s+now\s+(a|an)\s+",
        "role_hijacking",
    ),
    (
        r"new\s+instructions?\s*:",
        "instruction_override",
    ),
    (
        r"system\s*:\s*you\s+are",
        "system_prompt_injection",
    ),
    (
        r"<\|?system\|?>",
        "system_prompt_injection",
    ),
    (
        r"IMPORTANT:\s*ignore",
        "instruction_override",
    ),
    (
        r"override\s+(all\s+)?previous",
        "instruction_override",
    ),
]

# Exfiltration patterns
EXFIL_PATTERNS = [
    (
        r"https?://[^\s]+\.(ngrok|burp|requestbin|webhook\.site"
        r"|pipedream|hookbin)",
        "exfiltration_url",
    ),
    (
        r"curl\s+-[sS]*\s+https?://",
        "exfiltration_curl",
    ),
    (
        r"fetch\s*\(\s*['\"]https?://",
        "exfiltration_fetch",
    ),
    (
        r"\.postMessage\s*\(",
        "exfiltration_postmessage",
    ),
    (
        r"<img\s+src=['\"]https?://[^'\"]+\?",
        "exfiltration_img_beacon",
    ),
]

# HTML/CSS injection
HTML_PATTERNS = [
    (r"display\s*:\s*none", "css_hidden"),
    (r"visibility\s*:\s*hidden", "css_hidden"),
    (r"font-size\s*:\s*0", "css_hidden"),
    (r"color\s*:\s*transparent", "css_hidden"),
    (r"opacity\s*:\s*0\b", "css_hidden"),
    (r"<script[^>]*>", "script_injection"),
    (r"javascript\s*:", "script_injection"),
    (r"on\w+\s*=\s*['\"]", "event_handler_injection"),
]


def scan_text(text: str, source: str = "") -> List[Dict[str, Any]]:
    """Scan text for injection threats.

    Returns list of findings, each with:
        category, pattern, line, char_pos, snippet, source
    """
    findings: List[Dict[str, Any]] = []

    # 1. Invisible Unicode
    for i, ch in enumerate(text):
        if ch in INVISIBLE_CHARS:
            # Find line number
            line_num = text[:i].count("\n") + 1
            context = text[max(0, i - 20) : i + 20]
            findings.append(
                {
                    "category": "invisible_unicode",
                    "pattern": INVISIBLE_CHARS[ch],
                    "line": line_num,
                    "char_pos": i,
                    "snippet": repr(context),
                    "source": source,
                    "severity": "high",
                }
            )

    # 2. Instruction override
    for pattern, category in OVERRIDE_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            line_num = text[: m.start()].count("\n") + 1
            findings.append(
                {
                    "category": category,
                    "pattern": pattern,
                    "line": line_num,
                    "char_pos": m.start(),
                    "snippet": m.group()[:80],
                    "source": source,
                    "severity": "critical",
                }
            )

    # 3. Exfiltration
    for pattern, category in EXFIL_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            line_num = text[: m.start()].count("\n") + 1
            findings.append(
                {
                    "category": category,
                    "pattern": pattern,
                    "line": line_num,
                    "char_pos": m.start(),
                    "snippet": m.group()[:80],
                    "source": source,
                    "severity": "critical",
                }
            )

    # 4. HTML/CSS injection
    for pattern, category in HTML_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            line_num = text[: m.start()].count("\n") + 1
            findings.append(
                {
                    "category": category,
                    "pattern": pattern,
                    "line": line_num,
                    "char_pos": m.start(),
                    "snippet": m.group()[:80],
                    "source": source,
                    "severity": "medium",
                }
            )

    # 5. Base64 payloads (suspiciously long)
    for m in re.finditer(r"[A-Za-z0-9+/]{100,}={0,2}", text):
        line_num = text[: m.start()].count("\n") + 1
        findings.append(
            {
                "category": "base64_payload",
                "pattern": "long_base64_string",
                "line": line_num,
                "char_pos": m.start(),
                "snippet": m.group()[:40] + "...",
                "source": source,
                "severity": "medium",
            }
        )

    return findings


def scan_file(filepath: Path) -> List[Dict[str, Any]]:
    """Scan a single file."""
    try:
        text = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return []
    return scan_text(text, source=str(filepath))


def scan_directory(dirpath: Path, extensions: set = None) -> List[Dict[str, Any]]:
    """Scan all files in a directory."""
    if extensions is None:
        extensions = {
            ".md",
            ".yaml",
            ".yml",
            ".json",
            ".txt",
            ".html",
            ".py",
        }

    findings = []
    for f in sorted(dirpath.rglob("*")):
        if f.is_file() and f.suffix in extensions:
            findings.extend(scan_file(f))
    return findings


def scan_icdev_context() -> Dict[str, Any]:
    """Scan all ICDEV context/evidence paths."""
    dirs_to_scan = [
        BASE_DIR / "context",
        BASE_DIR / "hardprompts",
        BASE_DIR / "args",
    ]

    all_findings = []
    for d in dirs_to_scan:
        if d.exists():
            all_findings.extend(scan_directory(d))

    by_severity = {}
    for f in all_findings:
        sev = f["severity"]
        by_severity.setdefault(sev, []).append(f)

    return {
        "total_findings": len(all_findings),
        "critical": len(by_severity.get("critical", [])),
        "high": len(by_severity.get("high", [])),
        "medium": len(by_severity.get("medium", [])),
        "findings": all_findings,
        "scanned_dirs": [str(d) for d in dirs_to_scan],
        "status": ("clean" if len(all_findings) == 0 else "threats_found"),
    }


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prompt Injection Scanner (CUI // SP-CTI)")
    parser.add_argument(
        "--scan-file",
        metavar="PATH",
        help="Scan a single file",
    )
    parser.add_argument(
        "--scan-dir",
        metavar="DIR",
        help="Scan a directory",
    )
    parser.add_argument(
        "--scan-all",
        action="store_true",
        help="Scan all context/hardprompts/args",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.scan_file:
        findings = scan_file(Path(args.scan_file))
    elif args.scan_dir:
        findings = scan_directory(Path(args.scan_dir))
    elif args.scan_all:
        result = scan_icdev_context()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Scanned: {', '.join(result['scanned_dirs'])}")
            print(
                f"Findings: {result['total_findings']} "
                f"(critical={result['critical']}, "
                f"high={result['high']}, "
                f"medium={result['medium']})"
            )
            for f in result["findings"]:
                print(f"  [{f['severity']:8}] {f['source']}:{f['line']} — {f['category']}: {f['snippet'][:60]}")
        sys.exit(0)
    else:
        parser.print_help()
        sys.exit(1)

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        for f in findings:
            print(f"  [{f['severity']:8}] {f.get('source', '?')}:{f['line']} — {f['category']}: {f['snippet'][:60]}")
        print(f"\nTotal: {len(findings)} findings")
