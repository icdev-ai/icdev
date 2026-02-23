#!/usr/bin/env python3
# CUI // SP-CTI
"""Unified dangerous code pattern detection (Phase 44 — D278).

Consolidates marketplace Gate 9 patterns + language-specific detection
across 6 languages. Callable from marketplace, translation, child app
generation, and security scanning. Declarative YAML patterns (D26).

Usage:
    from tools.security.code_pattern_scanner import CodePatternScanner

    scanner = CodePatternScanner()
    result = scanner.scan_file("/path/to/file.py", "python")
    result = scanner.scan_directory("/path/to/project", "python")
    result = scanner.scan_content("eval(input())", "python", "inline")
    gate = scanner.evaluate_gate("proj-123")
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("icdev.code_pattern_scanner")

BASE_DIR = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Default patterns (used when YAML config not available)
# ---------------------------------------------------------------------------

DEFAULT_PATTERNS = {
    "universal": [
        {"pattern": r"\beval\s*\(", "severity": "critical", "name": "eval_usage",
         "description": "Dynamic code evaluation — potential code injection"},
        {"pattern": r"\bexec\s*\(", "severity": "critical", "name": "exec_usage",
         "description": "Dynamic code execution — potential code injection"},
        {"pattern": r"subprocess\.(call|run|Popen|check_output)\s*\(", "severity": "high",
         "name": "subprocess_spawn", "description": "Process spawning — potential command injection"},
    ],
    "python": [
        {"pattern": r"\bos\.system\s*\(", "severity": "critical", "name": "os_system",
         "description": "Shell command execution via os.system"},
        {"pattern": r"\bpickle\.(loads?|dumps?)\s*\(", "severity": "high", "name": "pickle_usage",
         "description": "Pickle deserialization — potential arbitrary code execution"},
        {"pattern": r"\b__import__\s*\(", "severity": "high", "name": "dunder_import",
         "description": "Dynamic import — potential code injection"},
        {"pattern": r"\bimportlib\.import_module\s*\(", "severity": "medium", "name": "importlib_usage",
         "description": "Dynamic module import via importlib"},
        {"pattern": r"\bos\.popen\s*\(", "severity": "critical", "name": "os_popen",
         "description": "Shell command via os.popen"},
        {"pattern": r"\bcompile\s*\(", "severity": "medium", "name": "compile_usage",
         "description": "Dynamic code compilation"},
    ],
    "java": [
        {"pattern": r"Runtime\.getRuntime\(\)\.exec\s*\(", "severity": "critical",
         "name": "runtime_exec", "description": "Shell execution via Runtime.exec"},
        {"pattern": r"ObjectInputStream", "severity": "high", "name": "deserialization",
         "description": "Java deserialization — potential RCE"},
        {"pattern": r"(InitialContext|lookup)\s*\(.*[\"']ldap", "severity": "critical",
         "name": "jndi_injection", "description": "JNDI lookup — potential Log4Shell"},
        {"pattern": r"ProcessBuilder\s*\(", "severity": "high", "name": "process_builder",
         "description": "Process execution via ProcessBuilder"},
    ],
    "go": [
        {"pattern": r"os/exec", "severity": "high", "name": "os_exec_import",
         "description": "Process execution package import"},
        {"pattern": r'exec\.Command\s*\(', "severity": "high", "name": "exec_command",
         "description": "Shell command execution via exec.Command"},
        {"pattern": r"\bunsafe\.", "severity": "medium", "name": "unsafe_usage",
         "description": "Unsafe pointer operations"},
    ],
    "rust": [
        {"pattern": r"\bunsafe\s*\{", "severity": "medium", "name": "unsafe_block",
         "description": "Unsafe code block — bypasses borrow checker"},
        {"pattern": r"std::process::Command", "severity": "high", "name": "process_command",
         "description": "Shell command execution via std::process::Command"},
        {"pattern": r"std::mem::transmute", "severity": "high", "name": "mem_transmute",
         "description": "Unsafe memory transmutation"},
    ],
    "csharp": [
        {"pattern": r"Process\.Start\s*\(", "severity": "high", "name": "process_start",
         "description": "Process execution via Process.Start"},
        {"pattern": r"Assembly\.Load", "severity": "high", "name": "assembly_load",
         "description": "Dynamic assembly loading — potential code injection"},
        {"pattern": r"SqlCommand\s*\(.*\+", "severity": "high", "name": "sql_concat",
         "description": "SQL string concatenation — potential SQL injection"},
    ],
    "typescript": [
        {"pattern": r"child_process", "severity": "high", "name": "child_process",
         "description": "Child process module — command execution"},
        {"pattern": r"Deno\.run\s*\(", "severity": "high", "name": "deno_run",
         "description": "Deno process execution"},
        {"pattern": r"\beval\s*\(", "severity": "critical", "name": "eval_usage",
         "description": "Dynamic code evaluation"},
        {"pattern": r"process\.exit\s*\(", "severity": "medium", "name": "process_exit",
         "description": "Process termination"},
        {"pattern": r"Function\s*\(", "severity": "high", "name": "function_constructor",
         "description": "Dynamic function creation via Function constructor"},
    ],
}

# File extension → language mapping
EXTENSION_MAP = {
    ".py": "python",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",  # JS uses same patterns
    ".jsx": "typescript",
}

# Directories to skip
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".tox", ".venv", "venv",
    "target", "build", "dist", ".tmp", "vendor",
}


class CodePatternScanner:
    """Unified dangerous code pattern scanner across 6 languages.

    Loads patterns from args/code_pattern_config.yaml or defaults.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._patterns = dict(DEFAULT_PATTERNS)
        self._compiled: Dict[str, List[Tuple[re.Pattern, dict]]] = {}

        # Try loading YAML config
        config_file = config_path or (BASE_DIR / "args" / "code_pattern_config.yaml")
        if config_file.exists():
            self._load_config(config_file)

        self._compile_patterns()

    def _load_config(self, path: Path) -> None:
        """Load patterns from YAML config."""
        try:
            import yaml
            with open(path) as f:
                config = yaml.safe_load(f) or {}

            patterns = config.get("patterns", {})
            for lang, lang_patterns in patterns.items():
                self._patterns[lang] = lang_patterns
        except (ImportError, Exception) as exc:
            logger.debug("YAML config load failed: %s — using defaults", exc)

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns for performance."""
        self._compiled = {}
        for lang, patterns in self._patterns.items():
            compiled = []
            for p in patterns:
                try:
                    regex = re.compile(p["pattern"])
                    compiled.append((regex, p))
                except re.error as exc:
                    logger.warning("Invalid regex pattern '%s': %s", p["pattern"], exc)
            self._compiled[lang] = compiled

    def scan_file(self, file_path: str, language: str = "") -> dict:
        """Scan a single file for dangerous patterns.

        Args:
            file_path: Path to the file to scan.
            language: Language override. Auto-detected from extension if empty.

        Returns:
            SecurityScanResult-compatible dict.
        """
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        if not language:
            language = self._detect_language(path)
        if not language:
            return {"scan_type": "code_patterns", "status": "skipped",
                    "findings_count": 0, "findings": [], "language": "unknown"}

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {"error": f"Cannot read file: {exc}"}

        return self.scan_content(content, language, source=str(file_path))

    def scan_content(self, content: str, language: str, source: str = "inline") -> dict:
        """Scan content string for dangerous patterns.

        Args:
            content: Code content to scan.
            language: Programming language.
            source: Source identifier for findings.

        Returns:
            SecurityScanResult-compatible dict.
        """
        findings = []

        # Get patterns: universal + language-specific
        patterns = self._compiled.get("universal", []) + self._compiled.get(language, [])

        lines = content.split("\n")
        for line_num, line in enumerate(lines, start=1):
            for regex, pattern_meta in patterns:
                if regex.search(line):
                    findings.append({
                        "name": pattern_meta["name"],
                        "severity": pattern_meta["severity"],
                        "description": pattern_meta["description"],
                        "file": source,
                        "line": line_num,
                        "line_content": line.strip()[:200],
                        "language": language,
                    })

        # Categorize by severity
        critical = sum(1 for f in findings if f["severity"] == "critical")
        high = sum(1 for f in findings if f["severity"] == "high")
        medium = sum(1 for f in findings if f["severity"] == "medium")
        low = sum(1 for f in findings if f["severity"] == "low")

        return {
            "scan_type": "code_patterns",
            "status": "completed",
            "findings_count": len(findings),
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
            "findings": findings,
            "scanned_at": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
            "language": language,
            "source": source,
            "gate_passed": critical == 0 and high == 0,
        }

    def scan_directory(
        self,
        dir_path: str,
        language: str = "",
        recursive: bool = True,
    ) -> dict:
        """Scan a directory for dangerous patterns.

        Args:
            dir_path: Directory path.
            language: Language filter. Empty = scan all detected languages.
            recursive: Whether to scan subdirectories.

        Returns:
            Aggregated scan results.
        """
        path = Path(dir_path)
        if not path.is_dir():
            return {"error": f"Not a directory: {dir_path}"}

        all_findings = []
        scanned_files = 0
        severity_totals = {"critical": 0, "high": 0, "medium": 0, "low": 0}

        glob_pattern = "**/*" if recursive else "*"
        for file_path in path.glob(glob_pattern):
            if not file_path.is_file():
                continue
            if any(skip in file_path.parts for skip in SKIP_DIRS):
                continue

            file_lang = language or self._detect_language(file_path)
            if not file_lang:
                continue

            result = self.scan_file(str(file_path), file_lang)
            if "error" in result:
                continue

            all_findings.extend(result.get("findings", []))
            scanned_files += 1
            for sev in severity_totals:
                severity_totals[sev] += result.get(sev, 0)

        return {
            "scan_type": "code_patterns",
            "status": "completed",
            "directory": str(dir_path),
            "scanned_files": scanned_files,
            "findings_count": len(all_findings),
            "critical": severity_totals["critical"],
            "high": severity_totals["high"],
            "medium": severity_totals["medium"],
            "low": severity_totals["low"],
            "findings": all_findings,
            "gate_passed": severity_totals["critical"] == 0 and severity_totals["high"] == 0,
        }

    def evaluate_gate(self, project_id: str = "") -> dict:
        """Evaluate security gate for code patterns.

        Args:
            project_id: Optional project ID for context.

        Returns:
            {passed, blocking_issues, warnings, summary}
        """
        # Load gate config
        gate_config = {"max_critical": 0, "max_high": 0, "max_medium": 10}
        try:
            import yaml
            gates_path = BASE_DIR / "args" / "security_gates.yaml"
            if gates_path.exists():
                with open(gates_path) as f:
                    gates = yaml.safe_load(f) or {}
                cp = gates.get("code_patterns", {})
                gate_config.update(cp)
        except (ImportError, Exception):
            pass

        return {
            "gate": "code_patterns",
            "config": gate_config,
            "note": "Run scan_file() or scan_directory() first, then check findings against gate thresholds",
        }

    @staticmethod
    def _detect_language(file_path: Path) -> str:
        """Detect language from file extension."""
        return EXTENSION_MAP.get(file_path.suffix.lower(), "")

    @property
    def supported_languages(self) -> List[str]:
        """List of supported languages."""
        return [k for k in self._patterns.keys() if k != "universal"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Code Pattern Scanner")
    parser.add_argument("--file", help="Scan a single file")
    parser.add_argument("--dir", help="Scan a directory")
    parser.add_argument("--content", help="Scan inline content")
    parser.add_argument("--language", default="", help="Language override")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    scanner = CodePatternScanner()

    if args.file:
        result = scanner.scan_file(args.file, args.language)
    elif args.dir:
        result = scanner.scan_directory(args.dir, args.language)
    elif args.content:
        result = scanner.scan_content(args.content, args.language or "python", "cli")
    else:
        result = {"error": "Provide --file, --dir, or --content"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Findings: {result.get('findings_count', 0)}")
        print(f"Critical: {result.get('critical', 0)} | High: {result.get('high', 0)} | Medium: {result.get('medium', 0)}")
        print(f"Gate: {'PASSED' if result.get('gate_passed') else 'FAILED'}")
        for f in result.get("findings", [])[:10]:
            print(f"  [{f['severity'].upper()}] {f['name']} at {f.get('file', 'unknown')}:{f.get('line', 0)} — {f['description']}")
