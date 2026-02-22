#!/usr/bin/env python3
# CUI // SP-CTI
"""Prompt Injection Detector — scan text and files for prompt injection attacks.

Detects 5 categories of prompt injection per REQ-37-021:
  1. Role hijacking — attempts to override system instructions
  2. Delimiter attacks — injecting chat/prompt delimiters
  3. Instruction injection — explicit override/bypass language
  4. Data exfiltration triggers — attempts to send data externally
  5. Encoded payloads — Base64, unicode escapes, Cyrillic homoglyphs

Pattern: tools/security/secret_detector.py (regex BUILTIN_PATTERNS, severity levels, CLI with --gate)
ADRs: D217 (regex+heuristic, air-gap safe), D218 (SHA-256 prompt hashing)

CLI:
    python tools/security/prompt_injection_detector.py --text "ignore previous instructions" --json
    python tools/security/prompt_injection_detector.py --file /path/to/file.md --json
    python tools/security/prompt_injection_detector.py --project-dir /path --gate --json
"""

import argparse
import base64
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ============================================================
# INJECTION PATTERNS — 5 categories per REQ-37-021
# ============================================================
INJECTION_PATTERNS: List[Dict] = [
    # ── Category 1: Role Hijacking ──
    {
        "name": "role_hijack_ignore_previous",
        "pattern": r"(?i)(ignore|disregard|forget|abandon|drop)\s+(all\s+)?(previous|prior|above|earlier|existing|system|original)\s+(instructions|prompts|rules|context|guidelines|directives|constraints)",
        "category": "role_hijacking",
        "severity": "critical",
        "confidence": 0.95,
        "description": "Attempts to discard prior system instructions",
    },
    {
        "name": "role_hijack_you_are_now",
        "pattern": r"(?i)you\s+are\s+now\s+(a\s+)?[a-z]+",
        "category": "role_hijacking",
        "severity": "critical",
        "confidence": 0.85,
        "description": "Attempts to reassign the AI's identity/role",
    },
    {
        "name": "role_hijack_new_instructions",
        "pattern": r"(?i)(new\s+instructions|updated\s+system\s+prompt|your\s+new\s+role|act\s+as\s+if|pretend\s+you\s+are|roleplay\s+as)",
        "category": "role_hijacking",
        "severity": "high",
        "confidence": 0.80,
        "description": "Attempts to inject new role or system prompt",
    },
    {
        "name": "role_hijack_jailbreak",
        "pattern": r"(?i)(DAN|do\s+anything\s+now|developer\s+mode|jailbreak|unrestricted\s+mode|god\s+mode|admin\s+mode)",
        "category": "role_hijacking",
        "severity": "critical",
        "confidence": 0.90,
        "description": "Known jailbreak prompt patterns",
    },
    # ── Category 2: Delimiter Attacks ──
    {
        "name": "delimiter_system_tag",
        "pattern": r"<\|?(?:im_start|im_end|system|user|assistant|endoftext)\|?>",
        "category": "delimiter_attack",
        "severity": "critical",
        "confidence": 0.95,
        "description": "Injecting chat template delimiters (OpenAI/HuggingFace format)",
    },
    {
        "name": "delimiter_xml_system",
        "pattern": r"</?system(?:\s+[^>]*)?>",
        "category": "delimiter_attack",
        "severity": "high",
        "confidence": 0.80,
        "description": "Injecting XML-style system tags",
    },
    {
        "name": "delimiter_inst",
        "pattern": r"\[/?INST\]|\[/?SYS\]",
        "category": "delimiter_attack",
        "severity": "critical",
        "confidence": 0.90,
        "description": "Injecting Llama/Mistral instruction delimiters",
    },
    {
        "name": "delimiter_markdown_system",
        "pattern": r"```\s*system\s*\n",
        "category": "delimiter_attack",
        "severity": "high",
        "confidence": 0.75,
        "description": "Markdown code block disguised as system prompt",
    },
    {
        "name": "delimiter_triple_backtick_injection",
        "pattern": r"```\s*(?:python|bash|sh|javascript)\s*\n.*(?:exec|eval|subprocess|os\.system|import\s+os)",
        "category": "delimiter_attack",
        "severity": "high",
        "confidence": 0.70,
        "description": "Code block with executable payload",
    },
    # ── Category 3: Instruction Injection ──
    {
        "name": "instruction_override",
        "pattern": r"(?i)(override|bypass|circumvent|ignore|disable|turn\s+off|deactivate|skip)\s+(your|the|all|my|any|every)\s+(\w+\s+)?(instructions|rules|restrictions|guidelines|filters|safety|guardrails|constraints|policies|limitations)",
        "category": "instruction_injection",
        "severity": "critical",
        "confidence": 0.90,
        "description": "Explicit instruction override attempts",
    },
    {
        "name": "instruction_do_not_follow",
        "pattern": r"(?i)do\s+not\s+follow\s+(your|the|any|previous)\s+(instructions|guidelines|rules|training|programming)",
        "category": "instruction_injection",
        "severity": "critical",
        "confidence": 0.92,
        "description": "Direct instruction to violate guidelines",
    },
    {
        "name": "instruction_secret_mode",
        "pattern": r"(?i)(enter|switch\s+to|enable|activate)\s+(secret|hidden|debug|test|admin|root|sudo|unrestricted|verbose|developer)\s+(mode|access|state|prompt)",
        "category": "instruction_injection",
        "severity": "high",
        "confidence": 0.85,
        "description": "Attempts to activate non-existent modes",
    },
    {
        "name": "instruction_system_prompt_reveal",
        "pattern": r"(?i)(reveal|show|display|print|output|repeat|recite|tell\s+me)\s+(me\s+)?(your|the)\s+(system\s+prompt|instructions|initial\s+prompt|original\s+prompt|hidden\s+prompt|internal\s+prompt|internal\s+instructions|configuration|rules)",
        "category": "instruction_injection",
        "severity": "high",
        "confidence": 0.88,
        "description": "System prompt extraction attempt (AML.T0056)",
    },
    # ── Category 4: Data Exfiltration Triggers ──
    {
        "name": "exfil_send_data",
        "pattern": r"(?i)(send|email|post|transmit|forward|upload|exfiltrate|transfer|relay)\s+(\w+\s+){0,4}(to|at|via)\s+(https?://|ftp://|mailto:|[a-zA-Z0-9._%+-]+@)",
        "category": "data_exfiltration",
        "severity": "critical",
        "confidence": 0.90,
        "description": "Attempts to exfiltrate data to external endpoints",
    },
    {
        "name": "exfil_curl_wget",
        "pattern": r"(?i)(curl|wget|fetch|http\.get|requests\.(?:get|post)|urllib)\s+['\"]?https?://",
        "category": "data_exfiltration",
        "severity": "high",
        "confidence": 0.80,
        "description": "HTTP request commands targeting external URLs",
    },
    {
        "name": "exfil_webhook_url",
        "pattern": r"(?i)(webhook|callback|notify)\s*[=:]\s*['\"]?https?://",
        "category": "data_exfiltration",
        "severity": "high",
        "confidence": 0.75,
        "description": "Webhook/callback URL injection",
    },
    {
        "name": "exfil_encode_and_send",
        "pattern": r"(?i)(encode|encrypt|compress|serialize)\s+(and|then|before)\s+(send|post|transmit|upload)",
        "category": "data_exfiltration",
        "severity": "high",
        "confidence": 0.80,
        "description": "Encode-then-exfiltrate pattern",
    },
    # ── Category 5: Encoded Payloads ──
    {
        "name": "encoded_base64_block",
        "pattern": r"(?:[A-Za-z0-9+/]{4}){10,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?",
        "category": "encoded_payload",
        "severity": "medium",
        "confidence": 0.60,
        "description": "Suspicious Base64-encoded block (40+ chars)",
    },
    {
        "name": "encoded_unicode_escape",
        "pattern": r"(?:\\u[0-9a-fA-F]{4}){4,}",
        "category": "encoded_payload",
        "severity": "medium",
        "confidence": 0.70,
        "description": "Unicode escape sequence chain (potential obfuscation)",
    },
    {
        "name": "encoded_hex_payload",
        "pattern": r"(?:\\x[0-9a-fA-F]{2}){8,}",
        "category": "encoded_payload",
        "severity": "medium",
        "confidence": 0.65,
        "description": "Hex-encoded payload (potential obfuscation)",
    },
    {
        "name": "encoded_cyrillic_homoglyph",
        "pattern": r"[\u0400-\u04FF]",
        "category": "encoded_payload",
        "severity": "high",
        "confidence": 0.60,
        "description": "Cyrillic characters (potential homoglyph attack)",
    },
    {
        "name": "encoded_invisible_chars",
        "pattern": r"[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff]",
        "category": "encoded_payload",
        "severity": "high",
        "confidence": 0.75,
        "description": "Invisible/zero-width Unicode characters (steganographic injection)",
    },
]

# File extensions to scan
SCANNABLE_EXTENSIONS = {
    ".md", ".yaml", ".yml", ".json", ".txt", ".csv",
    ".py", ".js", ".ts", ".java", ".go", ".rs", ".cs",
    ".html", ".xml", ".toml", ".cfg", ".ini", ".conf",
    ".sh", ".bash", ".bat", ".ps1",
    ".env", ".properties", ".feature", ".gherkin",
}

SKIP_DIRS = {
    "venv", "node_modules", ".git", "__pycache__", "build", "dist",
    ".eggs", ".tox", ".mypy_cache", ".pytest_cache", ".tmp",
}


class PromptInjectionDetector:
    """Detect prompt injection attacks in text and files.

    Uses regex + heuristic pattern matching (D217 — air-gap safe,
    no LLM dependency). Logs detections to append-only DB table.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._compiled_patterns = [
            {
                "name": p["name"],
                "regex": re.compile(p["pattern"]),
                "category": p["category"],
                "severity": p["severity"],
                "confidence": p["confidence"],
                "description": p["description"],
            }
            for p in INJECTION_PATTERNS
        ]

    # ---------------------------------------------------------------
    # Scanning
    # ---------------------------------------------------------------
    def scan_text(self, text: str, source: str = "unknown") -> Dict:
        """Scan text for prompt injection patterns.

        Args:
            text: The text to scan.
            source: Origin label (e.g. 'user_input', 'jira_issue', 'file:foo.md').

        Returns:
            Dict with keys: detected, confidence, findings, action, source,
            text_hash, scanned_at.
        """
        findings = []
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        for pat in self._compiled_patterns:
            for match in pat["regex"].finditer(text):
                # Context: surrounding text for human review
                start = max(0, match.start() - 30)
                end = min(len(text), match.end() + 30)
                context_snippet = text[start:end].replace("\n", " ")

                findings.append({
                    "pattern_name": pat["name"],
                    "category": pat["category"],
                    "severity": pat["severity"],
                    "confidence": pat["confidence"],
                    "match": match.group()[:100],
                    "position": match.start(),
                    "context": context_snippet[:200],
                    "description": pat["description"],
                })

        # Deduplicate overlapping matches of same category
        findings = self._deduplicate_findings(findings)

        confidence = self._compute_confidence(findings)
        action = self._determine_action(confidence)

        return {
            "detected": len(findings) > 0,
            "confidence": round(confidence, 4),
            "action": action,
            "findings": findings,
            "finding_count": len(findings),
            "source": source,
            "text_hash": text_hash,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    def scan_file(self, file_path: str, source: Optional[str] = None) -> Dict:
        """Scan a file for prompt injection patterns.

        Args:
            file_path: Path to the file.
            source: Override source label.

        Returns:
            Dict matching scan_text output, with added file_path key.
        """
        path = Path(file_path)
        if not path.exists():
            return {
                "detected": False,
                "confidence": 0.0,
                "action": "allow",
                "findings": [],
                "finding_count": 0,
                "source": source or f"file:{path.name}",
                "file_path": str(path),
                "error": "File not found",
                "scanned_at": datetime.now(timezone.utc).isoformat(),
            }

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except IOError as e:
            return {
                "detected": False,
                "confidence": 0.0,
                "action": "allow",
                "findings": [],
                "finding_count": 0,
                "source": source or f"file:{path.name}",
                "file_path": str(path),
                "error": str(e),
                "scanned_at": datetime.now(timezone.utc).isoformat(),
            }

        result = self.scan_text(content, source=source or f"file:{path.name}")
        result["file_path"] = str(path)
        return result

    def scan_project(self, project_dir: str) -> Dict:
        """Scan all scannable files in a project directory.

        Returns:
            Dict with project-level summary and per-file findings.
        """
        root = Path(project_dir)
        file_results = []
        total_findings = 0
        files_scanned = 0
        max_confidence = 0.0

        for fpath in self._walk_files(root):
            files_scanned += 1
            result = self.scan_file(str(fpath), source=f"project:{fpath.relative_to(root)}")
            if result["detected"]:
                file_results.append(result)
                total_findings += result["finding_count"]
                max_confidence = max(max_confidence, result["confidence"])

        overall_action = self._determine_action(max_confidence) if total_findings > 0 else "allow"

        return {
            "detected": total_findings > 0,
            "confidence": round(max_confidence, 4),
            "action": overall_action,
            "total_findings": total_findings,
            "files_with_findings": len(file_results),
            "files_scanned": files_scanned,
            "file_results": file_results,
            "project_dir": str(root),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    # ---------------------------------------------------------------
    # Confidence & action logic
    # ---------------------------------------------------------------
    def _compute_confidence(self, findings: List[Dict]) -> float:
        """Compute aggregate confidence from individual findings.

        Uses max-confidence approach weighted by severity. Multiple
        findings in the same category boost confidence slightly.
        """
        if not findings:
            return 0.0

        severity_weight = {
            "critical": 1.0,
            "high": 0.85,
            "medium": 0.65,
            "low": 0.40,
        }

        # Start with highest individual confidence
        max_conf = max(f["confidence"] for f in findings)

        # Boost for multiple categories detected
        categories = set(f["category"] for f in findings)
        category_boost = min(0.10, len(categories) * 0.03)

        # Boost for severity
        severities = [f["severity"] for f in findings]
        max_severity_weight = max(severity_weight.get(s, 0.5) for s in severities)

        # Final confidence: base * severity weight + category diversity boost
        confidence = min(1.0, (max_conf * max_severity_weight) + category_boost)
        return confidence

    @staticmethod
    def _determine_action(confidence: float) -> str:
        """Determine response action based on confidence level.

        Per REQ-37-022:
            >= 0.90: block  — reject input, log, alert
            0.70–0.89: flag — log, continue with warning, require review
            0.50–0.69: warn — log, continue normally with warning
            < 0.50: allow  — log only if detected, continue normally
        """
        if confidence >= 0.90:
            return "block"
        elif confidence >= 0.70:
            return "flag"
        elif confidence >= 0.50:
            return "warn"
        return "allow"

    def _deduplicate_findings(self, findings: List[Dict]) -> List[Dict]:
        """Remove duplicate findings at the same position for same category."""
        seen = set()
        deduped = []
        for f in findings:
            key = (f["category"], f["position"])
            if key not in seen:
                seen.add(key)
                deduped.append(f)
        return deduped

    # ---------------------------------------------------------------
    # Database logging (append-only per D6)
    # ---------------------------------------------------------------
    def log_detection(
        self,
        scan_result: Dict,
        project_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[str]:
        """Log a detection result to the prompt_injection_log table.

        Append-only per D6 — no UPDATE/DELETE.

        Returns:
            The log entry ID, or None if DB unavailable.
        """
        if not self._db_path.exists():
            return None

        entry_id = str(uuid.uuid4())
        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.execute(
                """INSERT INTO prompt_injection_log
                   (id, project_id, user_id, source, text_hash,
                    detected, confidence, action, finding_count,
                    findings_json, scanned_at, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry_id,
                    project_id,
                    user_id,
                    scan_result.get("source", "unknown"),
                    scan_result.get("text_hash", ""),
                    1 if scan_result.get("detected") else 0,
                    scan_result.get("confidence", 0.0),
                    scan_result.get("action", "allow"),
                    scan_result.get("finding_count", 0),
                    json.dumps(scan_result.get("findings", [])),
                    scan_result.get("scanned_at", datetime.now(timezone.utc).isoformat()),
                    "CUI",
                ),
            )
            conn.commit()
            conn.close()
            return entry_id
        except Exception:
            return None

    # ---------------------------------------------------------------
    # Gate evaluation
    # ---------------------------------------------------------------
    def evaluate_gate(self, project_id: str) -> Dict:
        """Evaluate prompt injection security gate for a project.

        Checks:
          - Any high-confidence (block-level) injections unresolved
          - Number of flag-level injections within threshold
          - Detection system active (this tool exists and patterns loaded)

        Returns:
            Dict with keys: passed, blocking_issues, warnings, details.
        """
        blocking_issues = []
        warnings = []

        # Check 1: Detection active
        if not self._compiled_patterns:
            blocking_issues.append("prompt_injection_defense_inactive")

        # Check 2: Query recent detections from DB
        blocked_count = 0
        flagged_count = 0

        if self._db_path.exists():
            try:
                conn = sqlite3.connect(str(self._db_path))
                cursor = conn.execute(
                    """SELECT action, COUNT(*) FROM prompt_injection_log
                       WHERE project_id = ? AND action IN ('block', 'flag')
                       GROUP BY action""",
                    (project_id,),
                )
                for row in cursor:
                    if row[0] == "block":
                        blocked_count = row[1]
                    elif row[0] == "flag":
                        flagged_count = row[1]
                conn.close()
            except Exception:
                pass

        if blocked_count > 0:
            blocking_issues.append(
                f"high_confidence_injection_unresolved: {blocked_count} blocked injection(s)"
            )

        if flagged_count > 5:
            warnings.append(
                f"flagged_injection_count_high: {flagged_count} flagged injection(s)"
            )

        return {
            "passed": len(blocking_issues) == 0,
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "details": {
                "detection_active": len(self._compiled_patterns) > 0,
                "pattern_count": len(self._compiled_patterns),
                "blocked_count": blocked_count,
                "flagged_count": flagged_count,
            },
        }

    # ---------------------------------------------------------------
    # File walking
    # ---------------------------------------------------------------
    @staticmethod
    def _walk_files(root: Path):
        """Walk scannable files, skipping binaries and ignored dirs."""
        for item in root.iterdir():
            if item.name in SKIP_DIRS:
                continue
            if item.is_dir():
                yield from PromptInjectionDetector._walk_files(item)
            elif item.is_file():
                if item.suffix.lower() in SCANNABLE_EXTENSIONS:
                    yield item

    # ---------------------------------------------------------------
    # Base64 deep inspection
    # ---------------------------------------------------------------
    def check_base64_payload(self, text: str) -> List[Dict]:
        """Decode Base64 blocks and scan decoded content for injection.

        Returns list of findings from decoded payloads.
        """
        b64_pattern = re.compile(
            r"(?:[A-Za-z0-9+/]{4}){10,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?"
        )
        deep_findings = []

        for match in b64_pattern.finditer(text):
            try:
                decoded = base64.b64decode(match.group()).decode("utf-8", errors="ignore")
                if len(decoded) > 10:
                    inner_result = self.scan_text(decoded, source="base64_decoded")
                    if inner_result["detected"]:
                        for f in inner_result["findings"]:
                            f["note"] = "Found inside Base64-encoded payload"
                            deep_findings.append(f)
            except Exception:
                continue

        return deep_findings


# ===============================================================
# CLI
# ===============================================================
def main():
    parser = argparse.ArgumentParser(
        description="Prompt Injection Detector — scan text and files for injection attacks"
    )
    parser.add_argument("--text", help="Text string to scan")
    parser.add_argument("--file", help="File path to scan")
    parser.add_argument("--project-dir", help="Project directory to scan (all scannable files)")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gate for project")
    parser.add_argument("--project-id", help="Project ID for gate evaluation and logging")
    parser.add_argument("--deep", action="store_true", help="Enable deep Base64 payload inspection")
    parser.add_argument("--log", action="store_true", help="Log results to database")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    detector = PromptInjectionDetector()
    result = None

    if args.text:
        result = detector.scan_text(args.text, source="cli_text")
        if args.deep:
            deep = detector.check_base64_payload(args.text)
            if deep:
                result["deep_findings"] = deep
                result["finding_count"] += len(deep)
                result["detected"] = True

    elif args.file:
        result = detector.scan_file(args.file)
        if args.deep:
            try:
                content = Path(args.file).read_text(encoding="utf-8", errors="ignore")
                deep = detector.check_base64_payload(content)
                if deep:
                    result["deep_findings"] = deep
                    result["finding_count"] += len(deep)
                    result["detected"] = True
            except IOError:
                pass

    elif args.project_dir:
        result = detector.scan_project(args.project_dir)

    elif args.gate:
        if not args.project_id:
            print("Error: --project-id required for --gate evaluation", file=__import__("sys").stderr)
            __import__("sys").exit(1)
        result = detector.evaluate_gate(args.project_id)

    else:
        parser.print_help()
        return

    # Log to DB if requested
    if args.log and result and not args.gate:
        entry_id = detector.log_detection(result, project_id=args.project_id)
        if entry_id:
            result["log_entry_id"] = entry_id

    # Gate evaluation can be combined with scan
    if args.gate and args.project_id and not (not args.text and not args.file and not args.project_dir):
        gate_result = detector.evaluate_gate(args.project_id)
        if result:
            result["gate"] = gate_result

    # Output
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result, is_gate=args.gate and not args.text and not args.file and not args.project_dir)


def _print_human(result: Dict, is_gate: bool = False):
    """Print human-readable output."""
    if is_gate:
        status = "PASSED" if result.get("passed") else "FAILED"
        print(f"Prompt Injection Security Gate: {status}")
        details = result.get("details", {})
        print(f"  Detection active: {details.get('detection_active', False)}")
        print(f"  Patterns loaded: {details.get('pattern_count', 0)}")
        print(f"  Blocked detections: {details.get('blocked_count', 0)}")
        print(f"  Flagged detections: {details.get('flagged_count', 0)}")
        for issue in result.get("blocking_issues", []):
            print(f"  BLOCKING: {issue}")
        for warn in result.get("warnings", []):
            print(f"  WARNING: {warn}")
        return

    detected = result.get("detected", False)
    confidence = result.get("confidence", 0.0)
    action = result.get("action", "allow")
    source = result.get("source", "unknown")

    print(f"Prompt Injection Scan ({source})")
    print(f"  Detected: {detected}")
    print(f"  Confidence: {confidence:.2%}")
    print(f"  Action: {action.upper()}")

    findings = result.get("findings", [])
    if findings:
        print(f"  Findings: {len(findings)}")
        for f in findings[:20]:
            sev = f.get("severity", "?")
            cat = f.get("category", "?")
            name = f.get("pattern_name", "?")
            print(f"    [{sev}] {cat}: {name}")
            print(f"           {f.get('description', '')}")
        if len(findings) > 20:
            print(f"    ... and {len(findings) - 20} more")
    else:
        print("  No injection patterns detected.")

    # Project-level summary
    if "files_scanned" in result:
        print(f"\n  Files scanned: {result['files_scanned']}")
        print(f"  Files with findings: {result['files_with_findings']}")
        print(f"  Total findings: {result['total_findings']}")

    gate = result.get("gate")
    if gate:
        status = "PASSED" if gate["passed"] else "FAILED"
        print(f"\n  Security Gate: {status}")
        for issue in gate.get("blocking_issues", []):
            print(f"    BLOCKING: {issue}")


if __name__ == "__main__":
    main()
