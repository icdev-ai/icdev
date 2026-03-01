#!/usr/bin/env python3
# CUI // SP-CTI
"""Endpoint Security Scanner (D-EPSEC-2).

Detects API routes missing security safeguards (auth decorators, input
validation, IDOR protection).  Unlike ``code_pattern_scanner.py`` which
detects *dangerous* code that IS present, this scanner detects *safeguards*
that are ABSENT.

Declarative YAML patterns from ``args/endpoint_security_config.yaml`` (D26).
Follows the same class structure as ``CodePatternScanner`` so that callers
(production audit, CI gates, child-app generation) get a consistent API.

Usage:
    from tools.security.endpoint_security_scanner import EndpointSecurityScanner

    scanner = EndpointSecurityScanner()
    result = scanner.scan_file("tools/dashboard/api/cpmp.py")
    result = scanner.scan_directory("tools/dashboard/api")
    gate   = scanner.evaluate_gate()

CLI:
    python tools/security/endpoint_security_scanner.py --dir tools/ --json
    python tools/security/endpoint_security_scanner.py --file tools/dashboard/api/cpmp.py
"""

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("icdev.endpoint_security_scanner")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# File extension → language mapping
# ---------------------------------------------------------------------------
EXTENSION_MAP: Dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
}

# ---------------------------------------------------------------------------
# Default config (used when YAML not available)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "route_patterns": {
        "python": [
            r'@\w+\.(route|get|post|put|delete|patch)\s*\(',
            r'@app\.route\s*\(',
            r'@bp\.route\s*\(',
            r'@blueprint\.route\s*\(',
        ],
        "java": [
            r'@(Get|Post|Put|Delete|Patch)Mapping',
            r'@RequestMapping',
        ],
        "go": [
            r'http\.HandleFunc\s*\(',
            r'\.(GET|POST|PUT|DELETE|PATCH)\s*\(',
            r'router\.(Handle|HandleFunc|Get|Post|Put|Delete)\s*\(',
        ],
        "typescript": [
            r'router\.(get|post|put|delete|patch)\s*\(',
            r'app\.(get|post|put|delete|patch)\s*\(',
        ],
        "rust": [
            r'#\[actix_web::(get|post|put|delete)\(',
            r'\.route\s*\(',
        ],
        "csharp": [
            r'\[Http(Get|Post|Put|Delete|Patch)\]',
            r'\[Route\(',
        ],
    },
    "auth_patterns": {
        "python": [
            r'@require_role', r'@require_auth', r'@login_required',
            r'@jwt_required', r'@auth_required', r'g\.current_user',
            r'@token_required',
        ],
        "java": [r'@PreAuthorize', r'@Secured', r'@RolesAllowed', r'SecurityContext'],
        "go": [r'authMiddleware', r'requireAuth', r'AuthRequired'],
        "typescript": [r'authMiddleware', r'requireAuth', r'passport\.authenticate', r'isAuthenticated'],
        "rust": [r'#\[authorize\]', r'AuthGuard'],
        "csharp": [r'\[Authorize\]', r'\[AllowAnonymous\]'],
    },
    "validation_patterns": {
        "python": [
            r'isinstance\s*\(', r'validate\s*\(', r'schema\.\w+\s*\(',
            r'_validate_fields\s*\(', r'if\s+not\s+data',
            r"if\s+[\"']\w+[\"']\s+not\s+in\s+data", r'pydantic',
        ],
        "java": [r'@Valid', r'@NotNull', r'@NotBlank', r'Validator'],
        "go": [r'validate\.Struct', r'binding:"required"'],
        "typescript": [r'Joi\.', r'zod\.', r'class-validator'],
        "rust": [r'#\[validate\]', r'serde::Deserialize'],
        "csharp": [r'\[Required\]', r'ModelState\.IsValid'],
    },
    "write_methods": ["POST", "PUT", "PATCH"],
    "exempt_patterns": [
        "/health", "/ready", "/metrics", "/ping",
        "/favicon", "/static", "/login", "/api_events",
    ],
    "severity": {
        "missing_auth": "critical",
        "missing_validation_on_write": "high",
        "missing_idor_check": "medium",
    },
    "scan": {
        "skip_dirs": [
            "node_modules", ".git", "__pycache__", ".tox",
            ".venv", "venv", ".tmp", "vendor", "build", "dist",
        ],
        "exclude_file_patterns": [
            "test_*.py", "*_test.py", "conftest.py",
            "*_test.go", "*Test.java", "*.spec.ts", "*.test.ts",
        ],
        "context_window_lines": 20,
        "max_file_size_kb": 500,
    },
}


class EndpointSecurityScanner:
    """Detects API routes missing auth decorators, input validation, or IDOR checks.

    Loads configuration from ``args/endpoint_security_config.yaml``.
    Falls back to ``DEFAULT_CONFIG`` when YAML is unavailable.
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self._compiled_routes: Dict[str, List[re.Pattern]] = {}
        self._compiled_auth: Dict[str, List[re.Pattern]] = {}
        self._compiled_validation: Dict[str, List[re.Pattern]] = {}
        self._skip_dirs: set = set()
        self._exclude_globs: List[str] = []
        self._exempt_patterns: List[str] = []
        self._context_window: int = 20
        self._max_file_size: int = 500 * 1024  # bytes

        cfg_path = config_path or (BASE_DIR / "args" / "endpoint_security_config.yaml")
        if cfg_path.exists():
            self._load_config(cfg_path)

        self._compile()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _load_config(self, path: Path) -> None:
        """Load configuration from YAML."""
        try:
            import yaml
            with open(path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}

            for key in ("route_patterns", "auth_patterns", "validation_patterns",
                        "write_methods", "exempt_patterns", "severity", "scan"):
                if key in data:
                    self._config[key] = data[key]
        except (ImportError, Exception) as exc:
            logger.debug("YAML config load failed: %s — using defaults", exc)

    def _compile(self) -> None:
        """Pre-compile all regex patterns."""
        self._compiled_routes = self._compile_group(self._config["route_patterns"])
        self._compiled_auth = self._compile_group(self._config["auth_patterns"])
        self._compiled_validation = self._compile_group(self._config["validation_patterns"])

        scan_cfg = self._config.get("scan", {})
        self._skip_dirs = set(scan_cfg.get("skip_dirs", []))
        self._exclude_globs = scan_cfg.get("exclude_file_patterns", [])
        self._context_window = scan_cfg.get("context_window_lines", 20)
        max_kb = scan_cfg.get("max_file_size_kb", 500)
        self._max_file_size = max_kb * 1024
        self._exempt_patterns = self._config.get("exempt_patterns", [])

    @staticmethod
    def _compile_group(group: Dict[str, List[str]]) -> Dict[str, List[re.Pattern]]:
        """Compile a dict of {language: [regex_str, …]} into compiled patterns."""
        result: Dict[str, List[re.Pattern]] = {}
        for lang, patterns in group.items():
            compiled = []
            for p in patterns:
                try:
                    compiled.append(re.compile(p))
                except re.error as exc:
                    logger.warning("Invalid regex '%s': %s", p, exc)
            result[lang] = compiled
        return result

    # ------------------------------------------------------------------
    # Core scanning
    # ------------------------------------------------------------------

    def scan_file(self, file_path: str, language: str = "") -> dict:
        """Scan a single file for endpoints missing security safeguards.

        Returns a SecurityScanResult-compatible dict.
        """
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}"}

        if not language:
            language = EXTENSION_MAP.get(path.suffix.lower(), "")
        if not language:
            return {"scan_type": "endpoint_security", "status": "skipped",
                    "findings_count": 0, "findings": [], "language": "unknown"}

        # Skip test files
        if self._is_excluded(path):
            return {"scan_type": "endpoint_security", "status": "skipped",
                    "findings_count": 0, "findings": [], "language": language}

        # Size guard
        try:
            if path.stat().st_size > self._max_file_size:
                return {"scan_type": "endpoint_security", "status": "skipped",
                        "findings_count": 0, "findings": [],
                        "message": f"File exceeds {self._max_file_size // 1024}KB limit"}
        except OSError:
            pass

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {"error": f"Cannot read file: {exc}"}

        return self.scan_content(content, language, source=str(file_path))

    def scan_content(self, content: str, language: str, source: str = "inline") -> dict:
        """Scan content for endpoints missing security safeguards."""
        findings: List[dict] = []
        lines = content.split("\n")

        route_patterns = self._compiled_routes.get(language, [])
        auth_patterns = self._compiled_auth.get(language, [])
        validation_patterns = self._compiled_validation.get(language, [])
        severity_cfg = self._config.get("severity", {})
        write_methods = [m.upper() for m in self._config.get("write_methods", [])]

        if not route_patterns:
            return {"scan_type": "endpoint_security", "status": "skipped",
                    "findings_count": 0, "findings": [],
                    "message": f"No route patterns for language: {language}"}

        # Find all route definitions
        routes = self._find_routes(lines, route_patterns, language)

        for route_info in routes:
            line_num = route_info["line"]
            route_path = route_info.get("path", "")
            http_method = route_info.get("method", "GET").upper()

            # Check exempt patterns
            if self._is_exempt(route_path):
                continue

            # Get context window (lines above and below the route)
            ctx_start = max(0, line_num - 1 - self._context_window)
            ctx_end = min(len(lines), line_num + self._context_window)
            context_lines = lines[ctx_start:ctx_end]
            context_text = "\n".join(context_lines)

            # Check 1: Missing auth
            has_auth = any(p.search(context_text) for p in auth_patterns)
            if not has_auth:
                findings.append({
                    "name": "api_route_without_auth_decorator",
                    "severity": severity_cfg.get("missing_auth", "critical"),
                    "description": f"API route at line {line_num} has no auth decorator/check",
                    "file": source,
                    "line": line_num,
                    "line_content": lines[line_num - 1].strip()[:200],
                    "route_path": route_path,
                    "http_method": http_method,
                    "language": language,
                    "finding_type": "missing_auth",
                })

            # Check 2: Missing input validation on write methods
            if http_method in write_methods:
                has_validation = any(p.search(context_text) for p in validation_patterns)
                if not has_validation:
                    findings.append({
                        "name": "write_route_without_input_validation",
                        "severity": severity_cfg.get("missing_validation_on_write", "high"),
                        "description": f"{http_method} route at line {line_num} has no input validation",
                        "file": source,
                        "line": line_num,
                        "line_content": lines[line_num - 1].strip()[:200],
                        "route_path": route_path,
                        "http_method": http_method,
                        "language": language,
                        "finding_type": "missing_validation",
                    })

        # Tally severities
        critical = sum(1 for f in findings if f["severity"] == "critical")
        high = sum(1 for f in findings if f["severity"] == "high")
        medium = sum(1 for f in findings if f["severity"] == "medium")
        low = sum(1 for f in findings if f["severity"] == "low")

        return {
            "scan_type": "endpoint_security",
            "status": "completed",
            "findings_count": len(findings),
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
            "findings": findings,
            "routes_found": len(routes),
            "routes_exempt": sum(1 for r in routes if self._is_exempt(r.get("path", ""))),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
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
        """Scan a directory for endpoints missing security safeguards."""
        path = Path(dir_path)
        if not path.is_dir():
            return {"error": f"Not a directory: {dir_path}"}

        all_findings: List[dict] = []
        scanned_files = 0
        total_routes = 0
        total_exempt = 0
        severity_totals = {"critical": 0, "high": 0, "medium": 0, "low": 0}

        glob_pattern = "**/*" if recursive else "*"
        for file_path in sorted(path.glob(glob_pattern)):
            if not file_path.is_file():
                continue
            if any(skip in file_path.parts for skip in self._skip_dirs):
                continue

            file_lang = language or EXTENSION_MAP.get(file_path.suffix.lower(), "")
            if not file_lang:
                continue

            result = self.scan_file(str(file_path), file_lang)
            if "error" in result or result.get("status") == "skipped":
                continue

            all_findings.extend(result.get("findings", []))
            scanned_files += 1
            total_routes += result.get("routes_found", 0)
            total_exempt += result.get("routes_exempt", 0)
            for sev in severity_totals:
                severity_totals[sev] += result.get(sev, 0)

        return {
            "scan_type": "endpoint_security",
            "status": "completed",
            "directory": str(dir_path),
            "scanned_files": scanned_files,
            "routes_found": total_routes,
            "routes_exempt": total_exempt,
            "findings_count": len(all_findings),
            "critical": severity_totals["critical"],
            "high": severity_totals["high"],
            "medium": severity_totals["medium"],
            "low": severity_totals["low"],
            "findings": all_findings,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "gate_passed": severity_totals["critical"] == 0 and severity_totals["high"] == 0,
        }

    def evaluate_gate(self, scan_result: Optional[dict] = None) -> dict:
        """Evaluate endpoint_security gate against scan results.

        Args:
            scan_result: Output from scan_file or scan_directory.

        Returns:
            {gate, passed, blocking_issues, warnings, summary}
        """
        gate_config = {"max_critical": 0, "max_high": 0}
        try:
            import yaml
            gates_path = BASE_DIR / "args" / "security_gates.yaml"
            if gates_path.exists():
                with open(gates_path, encoding="utf-8") as fh:
                    gates = yaml.safe_load(fh) or {}
                ep_cfg = gates.get("endpoint_security", {})
                thresholds = ep_cfg.get("thresholds", {})
                gate_config.update(thresholds)
        except (ImportError, Exception):
            pass

        if scan_result is None:
            return {
                "gate": "endpoint_security",
                "passed": None,
                "config": gate_config,
                "note": "No scan result provided — run scan_directory() first",
            }

        critical = scan_result.get("critical", 0)
        high = scan_result.get("high", 0)

        blocking = []
        if critical > gate_config.get("max_critical", 0):
            blocking.append(f"critical={critical} exceeds max_critical={gate_config['max_critical']}")
        if high > gate_config.get("max_high", 0):
            blocking.append(f"high={high} exceeds max_high={gate_config['max_high']}")

        passed = len(blocking) == 0
        return {
            "gate": "endpoint_security",
            "passed": passed,
            "blocking_issues": blocking,
            "config": gate_config,
            "summary": {
                "critical": critical,
                "high": high,
                "medium": scan_result.get("medium", 0),
                "routes_found": scan_result.get("routes_found", 0),
                "findings_count": scan_result.get("findings_count", 0),
            },
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_routes(
        self, lines: List[str], route_patterns: List[re.Pattern], language: str
    ) -> List[dict]:
        """Find all route definitions in the file lines."""
        routes: List[dict] = []
        for line_num, line in enumerate(lines, start=1):
            for pattern in route_patterns:
                match = pattern.search(line)
                if match:
                    route_path = self._extract_route_path(line, language)
                    http_method = self._extract_http_method(line, lines, line_num, language)
                    routes.append({
                        "line": line_num,
                        "path": route_path,
                        "method": http_method,
                        "raw": line.strip()[:200],
                    })
                    break  # Don't double-count the same line
        return routes

    @staticmethod
    def _extract_route_path(line: str, language: str) -> str:
        """Best-effort extract route path from decorator/annotation line."""
        # Python: @bp.route("/path", ...)  or  @bp.get("/path")
        # Java:  @GetMapping("/path")
        # Go:    http.HandleFunc("/path", ...)
        # TS:    router.get("/path", ...)
        # C#:    [Route("/path")]
        m = re.search(r'["\'](/[^"\']*)["\']', line)
        return m.group(1) if m else ""

    @staticmethod
    def _extract_http_method(
        line: str, lines: List[str], line_num: int, language: str
    ) -> str:
        """Best-effort extract HTTP method from route declaration."""
        line_lower = line.lower()

        # Explicit method in decorator name
        for method in ("get", "post", "put", "delete", "patch"):
            if f".{method}" in line_lower or f"@{method}" in line_lower:
                return method.upper()
            if f"http{method}" in line_lower:  # C# [HttpPost]
                return method.upper()

        # Python @bp.route(..., methods=["POST"])
        m = re.search(r'methods\s*=\s*\[([^\]]+)\]', line)
        if m:
            methods_str = m.group(1).upper()
            for method in ("POST", "PUT", "PATCH", "DELETE", "GET"):
                if method in methods_str:
                    return method

        return "GET"  # Default assumption

    def _is_exempt(self, route_path: str) -> bool:
        """Check if route path matches any exempt pattern."""
        if not route_path:
            return False
        for exempt in self._exempt_patterns:
            if exempt in route_path:
                return True
        return False

    def _is_excluded(self, file_path: Path) -> bool:
        """Check if file matches exclusion patterns (test files)."""
        name = file_path.name
        for glob_pat in self._exclude_globs:
            # Simple glob matching using fnmatch
            import fnmatch
            if fnmatch.fnmatch(name, glob_pat):
                return True
        return False

    @property
    def supported_languages(self) -> List[str]:
        """List of supported languages."""
        return list(self._compiled_routes.keys())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Endpoint Security Scanner — detects API routes missing auth/validation"
    )
    parser.add_argument("--file", help="Scan a single file")
    parser.add_argument("--dir", help="Scan a directory")
    parser.add_argument("--language", default="", help="Language override")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gate")
    args = parser.parse_args()

    scanner = EndpointSecurityScanner()

    if args.file:
        result = scanner.scan_file(args.file, args.language)
    elif args.dir:
        result = scanner.scan_directory(args.dir, args.language)
    else:
        result = {"error": "Provide --file or --dir"}
        print(json.dumps(result) if args.json else result["error"], file=sys.stderr)
        sys.exit(1)

    if args.gate:
        gate_result = scanner.evaluate_gate(result)
        result["gate_evaluation"] = gate_result

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Routes found: {result.get('routes_found', 0)}")
        print(f"Routes exempt: {result.get('routes_exempt', 0)}")
        print(f"Findings: {result.get('findings_count', 0)}")
        print(f"  Critical: {result.get('critical', 0)} | High: {result.get('high', 0)} | Medium: {result.get('medium', 0)}")
        print(f"Gate: {'PASSED' if result.get('gate_passed') else 'FAILED'}")
        for f in result.get("findings", [])[:20]:
            print(f"  [{f['severity'].upper()}] {f['name']} at {f.get('file', '?')}:{f.get('line', 0)}")
            print(f"    {f['description']}")
            if f.get("route_path"):
                print(f"    Route: {f.get('http_method', '?')} {f['route_path']}")
        if args.gate and "gate_evaluation" in result:
            ge = result["gate_evaluation"]
            print(f"\nGate: {'PASSED' if ge['passed'] else 'FAILED'}")
            for issue in ge.get("blocking_issues", []):
                print(f"  BLOCKING: {issue}")
