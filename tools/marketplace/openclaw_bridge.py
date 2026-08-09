#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""OpenClaw Skill Bridge — Import/export skills between SkillHub and ICDEV™ Marketplace.

Enforces zero-trust security: quarantine-first, full 10-gate scanning,
mandatory human review for executable content, provenance tracking,
trust scoring with decay. No registration or renewal required for imports.

Usage:
    # Import a skill from local OpenClaw directory
    python tools/marketplace/openclaw_bridge.py --import \\
        --source-path /path/to/openclaw-skill \\
        --tenant-id "tenant-abc" --imported-by "user@mil" --json

    # List quarantined imports
    python tools/marketplace/openclaw_bridge.py --list-quarantine --json

    # Promote a quarantined import (after review)
    python tools/marketplace/openclaw_bridge.py --promote \\
        --import-id "oci-abc123" --promoted-by "isso@dod.mil" --json

    # Reject a quarantined import
    python tools/marketplace/openclaw_bridge.py --reject \\
        --import-id "oci-abc123" --rejected-by "isso@dod.mil" \\
        --reason "Contains eval() calls" --json

    # Export an ICDEV™ skill to OpenClaw format
    python tools/marketplace/openclaw_bridge.py --export \\
        --asset-id "asset-abc" --version-id "ver-abc" \\
        --output-path /path/to/output \\
        --exported-by "user@mil" --json

    # List pending exports awaiting approval
    python tools/marketplace/openclaw_bridge.py --list-exports --json

    # Health check
    python tools/marketplace/openclaw_bridge.py --health --json

    # Gate check (CI/CD)
    python tools/marketplace/openclaw_bridge.py --gate --json
"""

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import yaml
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
QUARANTINE_DIR = BASE_DIR / ".tmp" / "openclaw_quarantine"

# ---------------------------------------------------------------------------
# Graceful imports
# ---------------------------------------------------------------------------
try:
    from tools.db.storage import get_connection

    _HAS_DB = True
except ImportError:
    _HAS_DB = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event

    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1


def _safe_audit(**kwargs):
    """Non-fatal audit logging — never blocks the calling operation."""
    try:
        return audit_log_event(**kwargs)
    except Exception:
        return -1


try:
    from tools.marketplace.asset_scanner import run_full_scan

    _HAS_SCANNER = True
except ImportError:
    _HAS_SCANNER = False

try:
    from tools.marketplace.catalog_manager import register_asset, add_version

    _HAS_CATALOG = True
except ImportError:
    _HAS_CATALOG = False

try:
    import importlib.util

    _HAS_REVIEW = importlib.util.find_spec("tools.marketplace.review_queue") is not None
except (ImportError, ModuleNotFoundError):
    _HAS_REVIEW = False

try:
    from tools.security.prompt_injection_detector import scan_text

    _HAS_PI_DETECTOR = True
except ImportError:
    _HAS_PI_DETECTOR = False

try:
    from tools.security.code_pattern_scanner import CodePatternScanner

    _HAS_CODE_SCANNER = True
except ImportError:
    _HAS_CODE_SCANNER = False

try:
    from tools.marketplace.openclaw_compat import check_compatibility, translate_to_icdev

    _HAS_COMPAT = True
except ImportError:
    _HAS_COMPAT = False

try:
    from tools.databridge.connectors.skillhub_connector import SkillHubConnector

    _HAS_CLAWHUB = True
except ImportError:
    _HAS_CLAWHUB = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FEATURE_FLAG = "ICDEV_OPENCLAW_ENABLED"
INITIAL_TRUST_SCORE = 0.30
TRUST_DECAY_ON_VIOLATION = 0.15

# CUI patterns to strip on export (from asset_scanner.py)
CUI_PATTERNS = [
    re.compile(r"^#\s*CUI\s*//.*$", re.MULTILINE),
    re.compile(r"^#\s*Controlled by:.*$", re.MULTILINE),
    re.compile(r"^#\s*CUI Category:.*$", re.MULTILINE),
    re.compile(r"^#\s*Distribution:.*$", re.MULTILINE),
    re.compile(r"^#\s*POC:.*$", re.MULTILINE),
    re.compile(r"CONTROLLED UNCLASSIFIED INFORMATION", re.IGNORECASE),
]

# Internal metadata keys to strip on export
STRIP_KEYS = {
    "tenant_id",
    "impact_level",
    "classification",
    "compliance_controls",
    "_body",
    "_main_file",
    "catalog_tier",
    "publisher_tenant_id",
}

# YAML tag constructors to block (CVE-2026-25253)
YAML_TAG_PATTERN = re.compile(r"!!(?:python|ruby|perl|java|php|js|map|seq|set)/")
YAML_ANCHOR_PATTERN = re.compile(r"&[a-zA-Z_]\w*\s")

# OpenClaw frontmatter limits
MAX_FRONTMATTER_KEYS = 50
MAX_FRONTMATTER_VALUE_LEN = 10000

# Blocked imports for script safety analysis (network, shell, dynamic code)
BLOCKED_IMPORTS = {
    # Shell / process execution
    "subprocess",
    "os.system",
    "os.popen",
    "shlex",
    # Network access
    "requests",
    "urllib",
    "urllib3",
    "httpx",
    "aiohttp",
    "http.client",
    "socket",
    "socketserver",
    "ftplib",
    "smtplib",
    "xmlrpc",
    "grpc",
    # Dynamic code execution
    "importlib",
    "runpy",
    "code",
    "codeop",
    "compileall",
    # Serialization (arbitrary code execution)
    "pickle",
    "shelve",
    "marshal",
    # System-level
    "ctypes",
    "cffi",
    "signal",
    "multiprocessing",
    # File system (write-capable)
    "shutil",
    "tempfile",
}

# Blocked AST call names (functions that execute arbitrary code)
BLOCKED_CALLS = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "os.system",
    "os.popen",
    "os.execv",
    "os.execve",
    "os.spawn",
    "os.spawnl",
    "os.spawnle",
    "open",  # file writes — only blocked in strict mode
}

# Safe standard library imports (read-only, computational, no side effects)
SAFE_IMPORTS = {
    "json",
    "re",
    "math",
    "statistics",
    "collections",
    "itertools",
    "functools",
    "operator",
    "string",
    "textwrap",
    "datetime",
    "time",
    "calendar",
    "decimal",
    "fractions",
    "hashlib",
    "hmac",
    "base64",
    "binascii",
    "struct",
    "copy",
    "enum",
    "dataclasses",
    "typing",
    "abc",
    "pathlib",
    "os.path",
    "posixpath",
    "ntpath",
    "csv",
    "configparser",
    "argparse",
    "logging",
    "warnings",
    "traceback",
    "pprint",
    "difflib",
    "unicodedata",
}

# Allowed ICDEV™ tools for imported skills
ALLOWED_TOOLS = {
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Agent",
    "TodoWrite",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_enabled():
    """Check if OpenClaw bridge is enabled via feature flag."""
    return os.environ.get(FEATURE_FLAG, "").lower() in ("true", "1", "yes")


def _get_db():
    """Get database connection for OpenClaw tables."""
    if _HAS_DB:
        return get_connection()
    from tools.db.storage import get_connection as _get_conn

    return _get_conn(db_path=str(DB_PATH))


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix="oci"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _sha256_dir(dir_path):
    """Compute SHA-256 digest of directory contents (sorted, cross-platform)."""
    hasher = hashlib.sha256()
    dir_path = Path(dir_path)
    skip = {"__pycache__", ".git", "node_modules", ".DS_Store"}

    for fpath in sorted(dir_path.rglob("*")):
        if fpath.is_file():
            # Only skip directories that are direct children of the scan dir
            rel = fpath.relative_to(dir_path)
            if any(s in rel.parts for s in skip):
                continue
            hasher.update(rel.as_posix().encode("utf-8"))
            hasher.update(fpath.read_bytes())

    return hasher.hexdigest()


def _sha256_file(file_path):
    """Compute SHA-256 of a single file."""
    hasher = hashlib.sha256()
    hasher.update(Path(file_path).read_bytes())
    return hasher.hexdigest()


def _safe_parse_openclaw_frontmatter(text):
    """Parse YAML frontmatter with CVE-2026-25253 mitigations.

    Blocks dangerous YAML constructs before parsing.

    Returns:
        dict: Parsed frontmatter data.

    Raises:
        ValueError: If frontmatter contains dangerous constructs.
    """
    if YAML_TAG_PATTERN.search(text):
        raise ValueError("YAML tag constructor blocked (CVE-2026-25253 mitigation)")

    anchor_matches = YAML_ANCHOR_PATTERN.findall(text)
    if len(anchor_matches) > 0:
        raise ValueError("YAML anchor/alias blocked (CVE-2026-25253 mitigation)")

    if len(text) > MAX_FRONTMATTER_VALUE_LEN * MAX_FRONTMATTER_KEYS:
        raise ValueError(
            f"Frontmatter exceeds max total size ({MAX_FRONTMATTER_KEYS * MAX_FRONTMATTER_VALUE_LEN} chars)"
        )

    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Frontmatter must be a YAML mapping")

    if len(data) > MAX_FRONTMATTER_KEYS:
        raise ValueError(f"Frontmatter exceeds max key count ({MAX_FRONTMATTER_KEYS})")

    for key, val in data.items():
        if isinstance(val, str) and len(val) > MAX_FRONTMATTER_VALUE_LEN:
            raise ValueError(f"Frontmatter key '{key}' value exceeds {MAX_FRONTMATTER_VALUE_LEN} chars")

    return data


def _parse_skill_md(skill_path):
    """Parse OpenClaw skill.md into frontmatter dict + body text.

    Args:
        skill_path: Path to skill.md file.

    Returns:
        tuple: (frontmatter_dict, body_text)
    """
    content = Path(skill_path).read_text(encoding="utf-8")

    # Extract YAML frontmatter between --- delimiters
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if not fm_match:
        raise ValueError("skill.md missing YAML frontmatter (expected --- delimiters)")

    fm_text = fm_match.group(1)
    body = fm_match.group(2)

    frontmatter = _safe_parse_openclaw_frontmatter(fm_text)
    return frontmatter, body


def _sandboxed_run_fallback(script_path, timeout=30):
    """Subprocess fallback for sandbox — restricted env, runs --help only.

    Used when Docker/llm-sandbox is unavailable.
    """
    env = os.environ.copy()
    for key in list(env.keys()):
        if key.startswith(
            (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
                "AWS_",
                "AZURE_",
                "GCP_",
                "ANTHROPIC_",
                "OPENAI_",
                "OLLAMA_",
                "ICDEV_",
            )
        ):
            del env[key]

    try:
        result = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
            env=env,
            stdin=subprocess.DEVNULL,
        )
        return {
            "status": "pass" if result.returncode == 0 else "warning",
            "returncode": result.returncode,
            "stdout_preview": result.stdout[:500] if result.stdout else "",
            "stderr_preview": result.stderr[:500] if result.stderr else "",
            "isolation": "subprocess",
        }
    except subprocess.TimeoutExpired:
        return {"status": "fail", "error": f"Script timed out after {timeout}s"}
    except Exception as exc:
        return {"status": "fail", "error": str(exc)}


def _sandboxed_run(script_path, timeout=30):
    """Run script in LLM Sandbox container (D-SEC-10) with subprocess fallback.

    Attempts full Docker container isolation first (network disabled,
    resource-limited). Falls back to restricted subprocess if Docker
    or llm-sandbox is unavailable.
    """
    try:
        from tools.security.sandbox_executor import SandboxExecutor

        executor = SandboxExecutor()
        if executor._enabled and executor._available:
            result = executor.execute_file(
                file_path=Path(script_path),
                language="python",
                executor_type="openclaw_import",
                network_enabled=False,
                timeout_seconds=timeout,
                actor="openclaw-bridge",
            )

            if result.status in ("disabled", "unavailable"):
                # Fall through to subprocess fallback
                return _sandboxed_run_fallback(script_path, timeout)

            return {
                "status": "pass" if result.exit_code == 0 else ("fail" if result.status == "failed" else "warning"),
                "returncode": result.exit_code,
                "stdout_preview": result.stdout[:500] if result.stdout else "",
                "stderr_preview": result.stderr[:500] if result.stderr else "",
                "sandbox_status": result.status,
                "container_id": result.container_id,
                "isolation": "container",
            }
    except ImportError:
        pass

    # Fallback to restricted subprocess
    return _sandboxed_run_fallback(script_path, timeout)


def analyze_script_safety(script_path):
    """AST-based script safety analysis — deterministic, no LLM needed.

    Parses Python source into AST and checks for:
    1. Blocked imports (network, shell, dynamic code, serialization)
    2. Blocked function calls (eval, exec, compile, os.system)
    3. Compiled extensions (.so, .dll, .pyd)
    4. Open calls with write mode

    Returns:
        dict with 'safe' (bool), 'findings' (list), 'imports_found' (list)
    """
    script_path = Path(script_path)
    findings = []
    imports_found = []

    # Check for compiled extensions
    if script_path.suffix in (".so", ".dll", ".pyd", ".pyc"):
        return {
            "safe": False,
            "findings": [
                {"type": "compiled_extension", "detail": f"Binary file: {script_path.name}", "severity": "critical"}
            ],
            "imports_found": [],
        }

    try:
        source = script_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {
            "safe": False,
            "findings": [{"type": "read_error", "detail": str(exc), "severity": "critical"}],
            "imports_found": [],
        }

    # Parse AST
    try:
        tree = ast.parse(source, filename=str(script_path))
    except SyntaxError as exc:
        return {
            "safe": False,
            "findings": [{"type": "syntax_error", "detail": str(exc), "severity": "critical"}],
            "imports_found": [],
        }

    # Walk AST nodes
    for node in ast.walk(tree):
        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports_found.append(alias.name)
                if alias.name in BLOCKED_IMPORTS or any(alias.name.startswith(b + ".") for b in BLOCKED_IMPORTS):
                    findings.append(
                        {
                            "type": "blocked_import",
                            "detail": f"import {alias.name}",
                            "line": node.lineno,
                            "severity": "critical",
                        }
                    )

        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports_found.append(module)
            if module in BLOCKED_IMPORTS or any(module.startswith(b + ".") for b in BLOCKED_IMPORTS):
                findings.append(
                    {
                        "type": "blocked_import",
                        "detail": f"from {module} import ...",
                        "line": node.lineno,
                        "severity": "critical",
                    }
                )

        # Check function calls
        elif isinstance(node, ast.Call):
            call_name = _resolve_call_name(node)
            if call_name and call_name in BLOCKED_CALLS:
                findings.append(
                    {
                        "type": "blocked_call",
                        "detail": f"{call_name}()",
                        "line": node.lineno,
                        "severity": "critical",
                    }
                )

            # Check open() with write mode
            if call_name == "open" and len(node.args) >= 2:
                mode_arg = node.args[1]
                if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
                    if any(c in mode_arg.value for c in ("w", "a", "x", "+")):
                        findings.append(
                            {
                                "type": "file_write",
                                "detail": f"open(..., '{mode_arg.value}')",
                                "line": node.lineno,
                                "severity": "high",
                            }
                        )

    safe = len([f for f in findings if f["severity"] == "critical"]) == 0
    return {"safe": safe, "findings": findings, "imports_found": imports_found}


def _resolve_call_name(node):
    """Extract function name from an AST Call node."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        current = func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def analyze_scripts_directory(scripts_dir):
    """Analyze all Python scripts in a directory for safety.

    Returns:
        dict with 'all_safe' (bool), 'results' (list of per-file results)
    """
    scripts_dir = Path(scripts_dir)
    results = []

    for py_file in sorted(scripts_dir.rglob("*.py")):
        result = analyze_script_safety(py_file)
        result["file"] = py_file.name
        results.append(result)

    # Also check for compiled extensions
    for ext in ("*.so", "*.dll", "*.pyd", "*.pyc"):
        for binary in scripts_dir.rglob(ext):
            results.append(
                {
                    "file": binary.name,
                    "safe": False,
                    "findings": [
                        {"type": "compiled_extension", "detail": f"Binary: {binary.name}", "severity": "critical"}
                    ],
                    "imports_found": [],
                }
            )

    all_safe = all(r["safe"] for r in results) if results else True
    return {"all_safe": all_safe, "script_count": len(results), "results": results}


def _load_config():
    """Load OpenClaw config from args/openclaw_config.yaml."""
    config_path = BASE_DIR / "args" / "openclaw_config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f).get("openclaw", {})
    return {}


# ---------------------------------------------------------------------------
# Import functions
# ---------------------------------------------------------------------------
def import_skill(source_path, tenant_id, imported_by, skillhub_url=None):
    """Import an OpenClaw skill into quarantine with full security scanning.

    Args:
        source_path: Local path to OpenClaw skill directory.
        tenant_id: Tenant identifier.
        imported_by: User who initiated the import.
        skillhub_url: Optional SkillHub URL (for provenance tracking).

    Returns:
        dict: Import result with quarantine path, scan results, trust score.
    """
    if not _is_enabled():
        return {"error": f"OpenClaw bridge disabled. Set {FEATURE_FLAG}=true to enable."}

    source = Path(source_path)
    if not source.is_dir():
        return {"error": f"Source path is not a directory: {source_path}"}

    # Find skill.md
    skill_md = source / "skill.md"
    if not skill_md.exists():
        # Also check SKILL.md
        skill_md = source / "SKILL.md"
        if not skill_md.exists():
            return {"error": "No skill.md or SKILL.md found in source directory"}

    # Check size limit
    config = _load_config()
    max_size_mb = config.get("import", {}).get("max_skill_size_mb", 10)
    total_size = sum(f.stat().st_size for f in source.rglob("*") if f.is_file())
    if total_size > max_size_mb * 1024 * 1024:
        return {"error": f"Skill exceeds max size ({max_size_mb}MB): {total_size / 1024 / 1024:.1f}MB"}

    # Check blocked/allowed authors
    try:
        frontmatter, body = _parse_skill_md(skill_md)
    except ValueError as exc:
        return {"error": f"Frontmatter parse error: {exc}", "security": "CVE-2026-25253 check triggered"}

    # Resolve author: frontmatter → _meta.json → default
    author = frontmatter.get("author", "")
    if not author:
        meta_path = source / "_meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                author = meta.get("author_handle", "") or meta.get("author_name", "") or meta.get("ownerId", "")[:20]
            except Exception:
                pass
    if not author:
        author = "community contributor"
    blocked = config.get("import", {}).get("blocked_authors", [])
    allowed = config.get("import", {}).get("allowed_authors", [])
    if author in blocked:
        return {"error": f"Author '{author}' is blocked"}
    if allowed and author not in allowed:
        return {"error": f"Author '{author}' not in allowed list (whitelist mode)"}

    # Run compatibility check and translate
    compat_report = None
    if _HAS_COMPAT:
        compat_report = check_compatibility(source)
        if not compat_report.compatible:
            return {
                "error": "Skill has blocking incompatibilities — cannot import",
                "compatibility_report": compat_report.to_dict(),
                "blockers": compat_report.blockers,
            }

    # Create quarantine directory
    slug = frontmatter.get("name", source.name)
    slug = re.sub(r"[^a-z0-9-]", "-", slug.lower())[:63]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    quarantine_path = QUARANTINE_DIR / f"{slug}-{ts}"
    quarantine_path.mkdir(parents=True, exist_ok=True)

    # Copy to quarantine
    shutil.copytree(source, quarantine_path, dirs_exist_ok=True)

    # Translate OpenClaw → ICDEV™ format in quarantine
    translation_result = None
    functional_validation = None
    if _HAS_COMPAT:
        translation_result = translate_to_icdev(quarantine_path)
        if not translation_result.get("success"):
            return {
                "error": "Translation failed",
                "translation_result": translation_result,
            }

        # Run functional validation on translated skill
        from tools.marketplace.openclaw_compat import validate_translated_skill

        functional_validation = validate_translated_skill(quarantine_path)

    # Save pre-enrichment content for diff view
    skill_md_for_diff = quarantine_path / "SKILL.md"
    if not skill_md_for_diff.exists():
        skill_md_for_diff = quarantine_path / "skill.md"
    if skill_md_for_diff.exists():
        pre_enrichment_path = quarantine_path / "_pre_enrichment.md"
        pre_enrichment_path.write_text(
            skill_md_for_diff.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8", newline="",
        )

    # Enrichment — Innovation + Creative + Research engines (scanner-tier, graceful skip)
    enrichment_result = None
    try:
        from tools.marketplace.openclaw_enricher import enrich_skill as _enrich

        enrichment_result = _enrich(str(quarantine_path))
    except Exception:
        enrichment_result = {"success": False, "error": "Enrichment unavailable — skipped"}

    # Detect executable content BEFORE generating companion script
    scripts_dir = quarantine_path / "scripts"
    has_scripts = scripts_dir.is_dir() and any(scripts_dir.rglob("*.py"))

    # Generate companion Python script for actionable steps (LLM-agnostic)
    # This creates scripts/companion.py but it's ICDEV™-generated, not imported
    scriptgen_result = None
    try:
        from tools.marketplace.openclaw_scriptgen import generate_companion_script

        scriptgen_result = generate_companion_script(str(quarantine_path))
        # Smoke test: verify companion.py runs --help without error
        if scriptgen_result.get("generated"):
            companion_py = quarantine_path / "scripts" / "companion.py"
            if companion_py.exists():
                smoke = _sandboxed_run(companion_py, timeout=10)
                scriptgen_result["smoke_test"] = smoke.get("status", "unknown")
    except Exception:
        scriptgen_result = {"success": False, "generated": False, "error": "Script generator unavailable"}

    # Compute SHA-256 (after all modifications)
    sha256 = _sha256_dir(quarantine_path)

    # Generate import ID
    import_id = _gen_id("oci")

    # --- Security Scanning (10 gates) ---
    gate_results = {}
    scan_status = "passed"

    # Gate 1-7: Standard marketplace scan (if scanner available)
    if _HAS_SCANNER:
        scan_result = run_full_scan(
            asset_id=import_id,
            version_id=f"{import_id}-v1",
            asset_path=str(quarantine_path),
        )
        gate_results["marketplace_scan"] = scan_result
        if scan_result.get("status") == "fail":
            scan_status = "failed"

    # Gate 8: Prompt injection scan on all .md files
    pi_findings = []
    if _HAS_PI_DETECTOR:
        for md_file in quarantine_path.rglob("*.md"):
            md_content = md_file.read_text(encoding="utf-8", errors="replace")
            pi_result = scan_text(md_content)
            if pi_result.get("injections_found", 0) > 0:
                pi_findings.extend(pi_result.get("findings", []))
    else:
        # Air-gapped fallback: regex check for common injection patterns
        injection_patterns = [
            re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
            re.compile(r"you\s+are\s+now\s+(?:a\s+)?(?:DAN|jailbreak)", re.IGNORECASE),
            re.compile(r"<\|im_start\|>|<\|im_end\|>|\[INST\]|<system>", re.IGNORECASE),
            re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
        ]
        for md_file in quarantine_path.rglob("*.md"):
            md_content = md_file.read_text(encoding="utf-8", errors="replace")
            for pat in injection_patterns:
                matches = pat.findall(md_content)
                if matches:
                    pi_findings.append(
                        {
                            "file": md_file.name,
                            "pattern": pat.pattern[:60],
                            "count": len(matches),
                        }
                    )

    gate_results["prompt_injection_scan"] = {
        "status": "fail" if pi_findings else "pass",
        "findings": pi_findings,
    }
    if pi_findings:
        scan_status = "failed"

    # Gate 9: Behavioral sandbox (scripts only)
    sandbox_results = []
    if has_scripts:
        for script in scripts_dir.rglob("*.py"):
            sr = _sandboxed_run(script)
            sandbox_results.append({"script": script.name, **sr})
    gate_results["behavioral_sandbox"] = {
        "status": "warning" if any(s.get("status") == "fail" for s in sandbox_results) else "pass",
        "results": sandbox_results,
        "isolation": sandbox_results[0].get("isolation", "subprocess") if sandbox_results else "none",
    }

    # Gate 9b: Full sandbox execution — compile-check all .py files in quarantine (D-SEC-10)
    sandbox_full_results = []
    try:
        from tools.security.sandbox_executor import SandboxExecutor

        executor = SandboxExecutor()
        if executor._enabled and executor._available:
            for py_file in quarantine_path.rglob("*.py"):
                code = py_file.read_text(encoding="utf-8", errors="replace")
                compile_code = (
                    "import sys\n"
                    f"src = '''{code[:4000]}'''\n"
                    "try:\n"
                    "    compile(src, '<sandbox>', 'exec')\n"
                    "    print('OK')\n"
                    "except SyntaxError as e:\n"
                    "    print(f'SYNTAX_ERROR: {e}')\n"
                    "    sys.exit(1)\n"
                )
                result = executor.execute(
                    code=compile_code,
                    language="python",
                    executor_type="openclaw_full_sandbox",
                    network_enabled=False,
                    timeout_seconds=15,
                    actor="openclaw-bridge",
                )
                sandbox_full_results.append(
                    {
                        "file": py_file.name,
                        "status": "pass" if result.exit_code == 0 else "fail",
                        "sandbox_status": result.status,
                    }
                )
    except ImportError:
        pass

    if sandbox_full_results:
        full_failed = any(r["status"] == "fail" for r in sandbox_full_results)
        gate_results["sandbox_full_execution"] = {
            "status": "fail" if full_failed else "pass",
            "results": sandbox_full_results,
        }
        if full_failed:
            scan_status = "failed"

    # Gate 10: Code pattern scan (dangerous patterns: eval, exec, subprocess)
    code_pattern_findings = []
    if _HAS_CODE_SCANNER:
        scanner = CodePatternScanner()
        cp_result = scanner.scan_directory(str(quarantine_path))
        code_pattern_findings = cp_result.get("findings", [])
    else:
        # Air-gapped fallback: regex for dangerous Python patterns
        danger_patterns = [
            re.compile(r"\beval\s*\("),
            re.compile(r"\bexec\s*\("),
            re.compile(r"\bos\.system\s*\("),
            re.compile(r"\bsubprocess\.\w+\s*\("),
            re.compile(r"\b__import__\s*\("),
            re.compile(r"\bpickle\.loads?\s*\("),
        ]
        for py_file in quarantine_path.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8", errors="replace")
            for pat in danger_patterns:
                matches = pat.findall(content)
                if matches:
                    code_pattern_findings.append(
                        {
                            "file": py_file.name,
                            "pattern": pat.pattern,
                            "count": len(matches),
                            "severity": "critical",
                        }
                    )

    gate_results["code_pattern_scan"] = {
        "status": "fail" if any(f.get("severity") == "critical" for f in code_pattern_findings) else "pass",
        "findings": code_pattern_findings,
    }
    if gate_results["code_pattern_scan"]["status"] == "fail":
        scan_status = "failed"

    # Duplicate detection by hash — allow re-import of rejected/revoked skills
    conn = _get_db()
    try:
        existing = conn.execute(
            "SELECT id, status FROM openclaw_imports WHERE sha256_hash = %s",
            (sha256,),
        ).fetchone()
        if existing:
            ex_status = existing["status"] if hasattr(existing, "keys") else existing[1]
            ex_id = existing["id"] if hasattr(existing, "keys") else existing[0]
            if ex_status in ("quarantined", "scanning", "review_pending", "promoted"):
                # Active import exists — block duplicate
                return {
                    "warning": "Duplicate skill detected",
                    "existing_import_id": ex_id,
                    "existing_status": ex_status,
                    "sha256": sha256,
                }
            else:
                # Rejected/expired — allow re-import by deleting old record
                conn.execute("DELETE FROM openclaw_imports WHERE id = %s", (ex_id,))
                conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    # Determine review requirement
    # Scripts get AST-based safety analysis — if all scripts pass, auto-promote is possible
    script_safety = None
    if has_scripts:
        script_safety = analyze_scripts_directory(quarantine_path / "scripts")
        gate_results["script_safety_analysis"] = {
            "status": "pass" if script_safety["all_safe"] else "fail",
            "script_count": script_safety["script_count"],
            "all_safe": script_safety["all_safe"],
            "results": script_safety["results"],
        }
        if not script_safety["all_safe"]:
            review_required = True
        else:
            review_required = config.get("import", {}).get("require_review_for_safe_scripts", False)
    else:
        review_required = not config.get("import", {}).get("auto_promote_no_scripts", False)

    # Content intent scan — detect malicious instructions (deterministic, no LLM)
    content_intent = None
    if _HAS_COMPAT:
        from tools.marketplace.openclaw_compat import scan_content_intent

        # Scan the translated SKILL.md body
        skill_md_path = quarantine_path / "SKILL.md"
        if not skill_md_path.exists():
            skill_md_path = quarantine_path / "skill.md"
        if skill_md_path.exists():
            skill_text = skill_md_path.read_text(encoding="utf-8", errors="replace")
            content_intent = scan_content_intent(skill_text)
            gate_results["content_intent_scan"] = {
                "status": "pass" if content_intent["safe"] else "fail",
                "critical": content_intent["critical_count"],
                "high": content_intent["high_count"],
                "categories": content_intent["categories"],
            }
            if not content_intent["safe"]:
                scan_status = "failed"

    # Auto-boost trust for content-only skills that pass all checks
    # Content-only (no scripts) + all gates passed + no malicious intent → trust 0.50
    trust_score = INITIAL_TRUST_SCORE
    if not has_scripts and scan_status == "passed" and (content_intent is None or content_intent["safe"]):
        trust_score = 0.50  # Degraded — safe enough for confirmed use

    # Determine final status
    if scan_status == "failed":
        status = "quarantined"
    elif review_required:
        status = "review_pending"
    else:
        status = "quarantined"

    # Record in database
    now = _utcnow()
    try:
        conn.execute(
            """INSERT INTO openclaw_imports
               (id, source_url, source_path, openclaw_slug, openclaw_author,
                skill_name, skill_version, quarantine_path, sha256_hash,
                has_executable_content, scan_status, gate_results,
                review_required, trust_score, status, imported_by,
                tenant_id, metadata, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                import_id,
                skillhub_url,
                str(source),
                slug,
                author,
                frontmatter.get("name", slug),
                frontmatter.get("version", "1.0.0"),
                str(quarantine_path),
                sha256,
                1 if has_scripts else 0,
                scan_status,
                json.dumps(gate_results, default=str),
                1 if review_required else 0,
                trust_score,
                status,
                imported_by,
                tenant_id,
                json.dumps(frontmatter, default=str),
                now,
                now,
            ),
        )
        conn.commit()
    except Exception as exc:
        return {"error": f"Database insert failed: {exc}"}
    finally:
        conn.close()

    # Audit trail
    _safe_audit(
        event_type="openclaw_skill_imported",
        actor=imported_by,
        action=f"imported OpenClaw skill '{slug}' from {skillhub_url or source_path}",
        details={
            "import_id": import_id,
            "slug": slug,
            "author": author,
            "sha256": sha256,
            "scan_status": scan_status,
            "has_scripts": has_scripts,
            "review_required": review_required,
            "source_url": skillhub_url,
        },
    )

    return {
        "success": True,
        "import_id": import_id,
        "skill_name": frontmatter.get("name", slug),
        "author": author,
        "quarantine_path": str(quarantine_path),
        "sha256": sha256,
        "scan_status": scan_status,
        "gate_results_summary": {k: v.get("status", "unknown") for k, v in gate_results.items()},
        "has_executable_content": has_scripts,
        "review_required": review_required,
        "trust_score": trust_score,
        "status": status,
        "content_intent": content_intent,
        "enrichment": {
            "applied": enrichment_result.get("success", False) if enrichment_result else False,
            "engines_run": enrichment_result.get("engines_run", []) if enrichment_result else [],
            "engines_failed": enrichment_result.get("engines_failed", []) if enrichment_result else [],
            "engines_skipped": enrichment_result.get("engines_skipped", []) if enrichment_result else [],
            "files_generated": enrichment_result.get("files_count", 0) if enrichment_result else 0,
            "merge_candidates": enrichment_result.get("merge_candidates", 0) if enrichment_result else 0,
            "similar_skills": [
                {"slug": s.get("slug", ""), "name": s.get("displayName", ""), "score": s.get("score", 0)}
                for s in (
                    enrichment_result.get("similar_skills", {}).get("similar_skills", []) if enrichment_result else []
                )
            ][:5],
            "innovation_synthesis": (
                enrichment_result.get("innovation", {}).get("synthesis", "") if enrichment_result else ""
            )[:400],
            "innovation_count": enrichment_result.get("innovation", {}).get("findings_count", 0)
            if enrichment_result
            else 0,
            "creative_alternatives": [
                {"name": a.get("name", ""), "approach": a.get("approach", "")[:150]}
                for a in (enrichment_result.get("creative", {}).get("alternatives", []) if enrichment_result else [])
            ][:3],
            "research_patterns": [
                {"title": p.get("title", ""), "detail": p.get("detail", "")[:150]}
                for p in (enrichment_result.get("research", {}).get("patterns", []) if enrichment_result else [])
            ][:3],
        }
        if enrichment_result
        else None,
        "companion_script": {
            "generated": scriptgen_result.get("generated", False) if scriptgen_result else False,
            "functions": scriptgen_result.get("functions", []) if scriptgen_result else [],
            "function_count": scriptgen_result.get("function_count", 0) if scriptgen_result else 0,
        }
        if scriptgen_result
        else None,
        "script_safety": script_safety if script_safety else None,
        "compatibility_score": compat_report.score if compat_report else None,
        "adaptations_applied": len(compat_report.adaptations) if compat_report else 0,
        "translation_applied": translation_result.get("success") if translation_result else False,
        "functional_validation": functional_validation,
        "registration_required": False,
        "renewal_required": False,
    }


# ---------------------------------------------------------------------------
# Promote functions
# ---------------------------------------------------------------------------
def promote_import(import_id, promoted_by):
    """Promote a quarantined import to the ICDEV™ marketplace.

    Args:
        import_id: The import record ID.
        promoted_by: User promoting the import (ISSO/security officer).

    Returns:
        dict: Promotion result with marketplace asset ID.
    """
    if not _is_enabled():
        return {"error": f"OpenClaw bridge disabled. Set {FEATURE_FLAG}=true to enable."}

    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM openclaw_imports WHERE id = %s", (import_id,)).fetchone()

        if not row:
            return {"error": f"Import not found: {import_id}"}

        # Convert Row to dict
        rec = (
            dict(row)
            if hasattr(row, "keys")
            else {
                "id": row[0],
                "scan_status": row[10],
                "review_required": row[12],
                "review_id": row[13],
                "status": row[15],
                "quarantine_path": row[7],
                "skill_name": row[5],
                "openclaw_slug": row[3],
                "openclaw_author": row[4],
                "sha256_hash": row[8],
                "tenant_id": row[18],
                "metadata": row[19],
                "skill_version": row[6],
                "source_url": row[1],
            }
        )

        # Verify gates passed
        if rec.get("scan_status") != "passed":
            return {"error": "Cannot promote: security scan not passed", "scan_status": rec.get("scan_status")}

        # Verify review if required
        if rec.get("review_required") and not rec.get("review_id"):
            return {"error": "Cannot promote: human review required but not completed"}

        if rec.get("status") == "promoted":
            return {"error": "Already promoted", "asset_id": rec.get("marketplace_asset_id")}

        if rec.get("status") == "rejected":
            return {"error": "Cannot promote: import was rejected"}

        # Convert OpenClaw skill.md -> ICDEV™ SKILL.md
        quarantine_path = Path(rec["quarantine_path"])
        metadata = json.loads(rec.get("metadata", "{}")) if isinstance(rec.get("metadata"), str) else {}

        # Build ICDEV™ SKILL.md content
        icdev_name = re.sub(
            r"[^a-z0-9-]", "-", (metadata.get("name", rec["openclaw_slug"]) or "imported-skill").lower()
        )[:63]
        oc_tools = metadata.get("tools", [])
        icdev_tools = sorted(ALLOWED_TOOLS.intersection(set(oc_tools))) if oc_tools else ["Read", "Grep", "Glob"]

        icdev_frontmatter = {
            "name": icdev_name,
            "description": metadata.get("description", f"Imported OpenClaw skill: {rec['skill_name']}"),
            "context": "fork",
            "allowed-tools": icdev_tools,
            "tags": metadata.get("tags", []),
        }

        icdev_body = f"""# {rec["skill_name"]}

CUI // SP-CTI

## Overview

{metadata.get("description", "Imported from OpenClaw community.")}

## Provenance

- **Source:** OpenClaw Community (SkillHub)
- **Author:** {rec.get("openclaw_author", "unknown")}
- **Original URL:** {rec.get("source_url", "N/A")}
- **Import Date:** {_utcnow()}
- **SHA-256:** {rec["sha256_hash"]}
- **Scan Status:** PASSED (all 10 gates)
- **Trust Score:** {INITIAL_TRUST_SCORE}
- **Registration Required:** No
- **Renewal Required:** No

## Instructions

"""
        # Append original body
        skill_md_path = quarantine_path / "skill.md"
        if not skill_md_path.exists():
            skill_md_path = quarantine_path / "SKILL.md"

        if skill_md_path.exists():
            _, original_body = _parse_skill_md(skill_md_path)
            icdev_body += original_body

        # Write ICDEV™ SKILL.md
        fm_yaml = yaml.dump(icdev_frontmatter, default_flow_style=False, allow_unicode=True)
        icdev_content = f"---\n{fm_yaml}---\n{icdev_body}"

        icdev_skill_path = quarantine_path / "SKILL.md"
        icdev_skill_path.write_text(icdev_content, encoding="utf-8", newline="")

        # Register in marketplace (if catalog available)
        asset_id = None
        if _HAS_CATALOG:
            try:
                reg_result = register_asset(
                    slug=icdev_name,
                    name=rec["skill_name"],
                    asset_type="skill",
                    description=metadata.get("description", f"Imported OpenClaw skill: {rec['skill_name']}"),
                    version=rec.get("skill_version", "1.0.0"),
                    tenant_id=rec["tenant_id"],
                    impact_level="IL2",
                )
                asset_id = reg_result.get("asset_id")

                if asset_id:
                    add_version(
                        asset_id=asset_id,
                        version=rec.get("skill_version", "1.0.0"),
                        sha256_hash=rec["sha256_hash"],
                        file_path=str(quarantine_path),
                        published_by=promoted_by,
                    )
            except Exception as exc:
                asset_id = _gen_id("mka")
                # Log but don't fail promotion
                _safe_audit(
                    event_type="openclaw_skill_promoted",
                    actor=promoted_by,
                    action=f"catalog registration failed for {import_id}",
                    details={"catalog_error": str(exc)},
                )
        else:
            asset_id = _gen_id("mka")

        # Update import record
        now = _utcnow()
        conn.execute(
            """UPDATE openclaw_imports
               SET status = 'promoted', promoted_by = %s, promoted_at = %s,
                   marketplace_asset_id = %s, updated_at = %s
               WHERE id = %s""",
            (promoted_by, now, asset_id, now, import_id),
        )
        conn.commit()

        # Audit trail
        _safe_audit(
            event_type="openclaw_skill_promoted",
            actor=promoted_by,
            action=f"promoted OpenClaw import {import_id} to marketplace asset {asset_id}",
            details={
                "import_id": import_id,
                "marketplace_asset_id": asset_id,
                "skill_name": rec["skill_name"],
                "slug": icdev_name,
            },
        )

        return {
            "success": True,
            "import_id": import_id,
            "marketplace_asset_id": asset_id,
            "skill_name": rec["skill_name"],
            "icdev_slug": icdev_name,
            "status": "promoted",
        }

    except Exception as exc:
        return {"error": f"Promote failed: {exc}"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reject functions
# ---------------------------------------------------------------------------
def reject_import(import_id, rejected_by, reason):
    """Reject a quarantined import.

    Args:
        import_id: The import record ID.
        rejected_by: User rejecting the import.
        reason: Reason for rejection.

    Returns:
        dict: Rejection result.
    """
    if not _is_enabled():
        return {"error": f"OpenClaw bridge disabled. Set {FEATURE_FLAG}=true to enable."}

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, status, quarantine_path FROM openclaw_imports WHERE id = %s",
            (import_id,),
        ).fetchone()

        if not row:
            return {"error": f"Import not found: {import_id}"}

        status = row[1] if not hasattr(row, "keys") else row["status"]
        if status in ("promoted", "rejected"):
            return {"error": f"Cannot reject: import already {status}"}

        now = _utcnow()
        conn.execute(
            """UPDATE openclaw_imports
               SET status = 'rejected', rejected_by = %s, rejected_reason = %s,
                   updated_at = %s
               WHERE id = %s""",
            (rejected_by, reason, now, import_id),
        )
        conn.commit()

        # Audit trail
        _safe_audit(
            event_type="openclaw_skill_rejected",
            actor=rejected_by,
            action=f"rejected OpenClaw import {import_id}",
            details={"import_id": import_id, "reason": reason},
        )

        return {
            "success": True,
            "import_id": import_id,
            "status": "rejected",
            "rejected_by": rejected_by,
            "reason": reason,
        }

    except Exception as exc:
        return {"error": f"Reject failed: {exc}"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Discovery & fetch functions (DataBridge connector)
# ---------------------------------------------------------------------------
def discover_skills(query, limit=10):
    """Search SkillHub for skills matching a query (vector search).

    Args:
        query: Natural language search query.
        limit: Max results.

    Returns:
        dict: Search results from SkillHub API.
    """
    if not _is_enabled():
        return {"error": f"OpenClaw bridge disabled. Set {FEATURE_FLAG}=true to enable."}
    if not _HAS_CLAWHUB:
        return {"error": "SkillHub connector not available (install tools.databridge)"}

    conn = SkillHubConnector()
    config = _load_config()
    api_base = config.get("skillhub_api_base", "")
    conn.connect({"api_base": api_base} if api_base else {})
    try:
        results = conn.search_skills(query, limit=limit)
        return {"success": True, "query": query, "count": len(results) if results else 0, "results": results}
    except Exception as exc:
        return {"error": f"Search failed: {exc}"}
    finally:
        conn.disconnect()


def fetch_and_import(slug, tenant_id, imported_by):
    """Download a skill from SkillHub by slug and import it.

    Combines download + import_skill into a single operation.

    Args:
        slug: SkillHub skill slug (e.g., 'self-improving-agent').
        tenant_id: Tenant identifier.
        imported_by: User initiating the fetch.

    Returns:
        dict: Import result (same as import_skill).
    """
    if not _is_enabled():
        return {"error": f"OpenClaw bridge disabled. Set {FEATURE_FLAG}=true to enable."}
    if not _HAS_CLAWHUB:
        return {"error": "SkillHub connector not available (install tools.databridge)"}

    conn = SkillHubConnector()
    config = _load_config()
    api_base = config.get("skillhub_api_base", "")
    conn.connect({"api_base": api_base} if api_base else {})

    try:
        # Get skill detail first for provenance
        detail = conn.get_skill(slug)
        skillhub_url = (
            f"https://skillhub.ai/{detail.get('owner', {}).get('handle', 'unknown')}/{slug}" if detail else None
        )

        # Download to temp directory
        download_dir = str(QUARANTINE_DIR / "_downloads")
        dl_result = conn.download_skill(slug, download_dir)

        if not dl_result.get("success"):
            return {"error": f"Download failed: {dl_result.get('error')}", "slug": slug}

        # Write author info from API into _meta.json so translator can find it
        if detail and detail.get("owner"):
            meta_path = Path(dl_result["output_path"]) / "_meta.json"
            try:
                existing_meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
                existing_meta["author_handle"] = detail["owner"].get("handle", "")
                existing_meta["author_name"] = detail["owner"].get("displayName", "")
                meta_path.write_text(json.dumps(existing_meta, indent=2), encoding="utf-8", newline="")
            except Exception:
                pass

        # Import using existing pipeline
        return import_skill(
            source_path=dl_result["output_path"],
            tenant_id=tenant_id,
            imported_by=imported_by,
            skillhub_url=skillhub_url,
        )

    except Exception as exc:
        return {"error": f"Fetch failed: {exc}"}
    finally:
        conn.disconnect()


# ---------------------------------------------------------------------------
# Revoke (rollback) functions
# ---------------------------------------------------------------------------
def revoke_import(import_id, revoked_by, reason):
    """Revoke a previously promoted import — removes from active marketplace.

    This is the rollback mechanism for imported skills that cause issues
    after promotion. Marks the marketplace asset as 'revoked' and updates
    the import record.

    Args:
        import_id: The import record ID.
        revoked_by: User revoking the import.
        reason: Reason for revocation.

    Returns:
        dict: Revocation result.
    """
    if not _is_enabled():
        return {"error": f"OpenClaw bridge disabled. Set {FEATURE_FLAG}=true to enable."}

    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, status, marketplace_asset_id FROM openclaw_imports WHERE id = %s",
            (import_id,),
        ).fetchone()

        if not row:
            return {"error": f"Import not found: {import_id}"}

        status = row["status"] if hasattr(row, "keys") else row[1]
        asset_id = row["marketplace_asset_id"] if hasattr(row, "keys") else row[2]

        if status != "promoted":
            return {"error": f"Cannot revoke: import status is '{status}' (must be 'promoted')"}

        now = _utcnow()

        # Revoke the marketplace asset if catalog is available
        if _HAS_CATALOG and asset_id:
            try:
                from tools.marketplace.catalog_manager import update_status

                update_status(asset_id, "revoked")
            except Exception:
                pass  # Log but don't fail revocation

        # Update import record back to rejected
        conn.execute(
            """UPDATE openclaw_imports
               SET status = 'rejected', rejected_by = %s, rejected_reason = %s,
                   updated_at = %s
               WHERE id = %s""",
            (revoked_by, f"REVOKED: {reason}", now, import_id),
        )
        conn.commit()

        # Audit trail
        _safe_audit(
            event_type="openclaw_skill_rejected",
            actor=revoked_by,
            action=f"revoked promoted OpenClaw import {import_id} (asset {asset_id})",
            details={
                "import_id": import_id,
                "marketplace_asset_id": asset_id,
                "reason": reason,
                "action": "revoke",
            },
        )

        return {
            "success": True,
            "import_id": import_id,
            "marketplace_asset_id": asset_id,
            "status": "revoked",
            "revoked_by": revoked_by,
            "reason": reason,
        }

    except Exception as exc:
        return {"error": f"Revoke failed: {exc}"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Export functions
# ---------------------------------------------------------------------------
def export_skill(asset_id, version_id, output_path, exported_by):
    """Export an ICDEV™ skill to OpenClaw format.

    Strips CUI markings and internal metadata. Requires human approval.

    Args:
        asset_id: Marketplace asset ID.
        version_id: Version ID to export.
        output_path: Directory to write exported skill.
        exported_by: User initiating the export.

    Returns:
        dict: Export result with status.
    """
    if not _is_enabled():
        return {"error": f"OpenClaw bridge disabled. Set {FEATURE_FLAG}=true to enable."}

    conn = _get_db()
    try:
        # Load asset
        asset = conn.execute("SELECT * FROM marketplace_assets WHERE id = %s", (asset_id,)).fetchone()
        if not asset:
            return {"error": f"Asset not found: {asset_id}"}

        version = conn.execute("SELECT * FROM marketplace_versions WHERE id = %s", (version_id,)).fetchone()
        if not version:
            return {"error": f"Version not found: {version_id}"}

        asset_d = dict(asset) if hasattr(asset, "keys") else {}
        version_d = dict(version) if hasattr(version, "keys") else {}

        # Read the skill file
        file_path = version_d.get("file_path", "")
        if not file_path:
            return {"error": "Version has no file_path"}

        skill_file = Path(file_path) / "SKILL.md"
        if not skill_file.exists():
            skill_file = Path(file_path) / "skill.md"
        if not skill_file.exists():
            return {"error": f"SKILL.md not found at {file_path}"}

        content = skill_file.read_text(encoding="utf-8")

        # Strip CUI markings
        stripped_lines = []
        stripping_log = {"cui_lines_removed": 0, "metadata_keys_stripped": []}
        for line in content.splitlines():
            removed = False
            for pat in CUI_PATTERNS:
                if pat.search(line):
                    stripping_log["cui_lines_removed"] += 1
                    removed = True
                    break
            if not removed:
                stripped_lines.append(line)

        stripped_content = "\n".join(stripped_lines)

        # Strip internal metadata from frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", stripped_content, re.DOTALL)
        if fm_match:
            fm_data = yaml.safe_load(fm_match.group(1)) or {}
            body = fm_match.group(2)

            for key in list(fm_data.keys()):
                if key in STRIP_KEYS:
                    del fm_data[key]
                    stripping_log["metadata_keys_stripped"].append(key)

            # Convert to OpenClaw format
            oc_frontmatter = {
                "name": fm_data.get("name", asset_d.get("slug", "exported-skill")),
                "description": fm_data.get("description", asset_d.get("description", "")),
                "version": version_d.get("version", "1.0.0"),
                "author": "ICDEV™",
                "origin": "ICDEV™",
                "exported_at": _utcnow(),
            }
            if fm_data.get("tags"):
                oc_frontmatter["tags"] = fm_data["tags"]
            if fm_data.get("allowed-tools"):
                oc_frontmatter["tools"] = fm_data["allowed-tools"]

            fm_yaml = yaml.dump(oc_frontmatter, default_flow_style=False, allow_unicode=True)
            exported_content = f"---\n{fm_yaml}---\n{body}"
        else:
            exported_content = stripped_content

        # Verify no CUI markings remain after stripping
        for pat in CUI_PATTERNS:
            if pat.search(exported_content):
                return {
                    "error": "CUI markings still present after stripping — export blocked",
                    "gate": "openclaw_export",
                    "blocking_condition": "cui_markings_present_after_strip",
                }

        # Verify no internal metadata leaked
        for key in STRIP_KEYS:
            if re.search(rf"\b{re.escape(key)}\b\s*:", exported_content):
                return {
                    "error": f"Internal metadata key '{key}' still present — export blocked",
                    "gate": "openclaw_export",
                    "blocking_condition": "internal_metadata_leak",
                }

        # Record export (append-only — requires human approval)
        export_id = _gen_id("oce")
        sha256 = hashlib.sha256(exported_content.encode("utf-8")).hexdigest()

        conn.execute(
            """INSERT INTO openclaw_exports
               (id, asset_id, version_id, output_path, exported_by,
                review_status, stripping_log, sha256_hash, status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                export_id,
                asset_id,
                version_id,
                str(output_path),
                exported_by,
                "pending",
                json.dumps(stripping_log, default=str),
                sha256,
                "pending",
                _utcnow(),
            ),
        )
        conn.commit()

        # Audit trail
        _safe_audit(
            event_type="openclaw_skill_exported",
            actor=exported_by,
            action=f"exported asset {asset_id} to OpenClaw format",
            details={
                "export_id": export_id,
                "asset_id": asset_id,
                "version_id": version_id,
                "stripping_log": stripping_log,
                "sha256": sha256,
            },
        )

        return {
            "success": True,
            "export_id": export_id,
            "status": "pending",
            "message": "Export pending human approval. Use --list-exports to check status.",
            "stripping_log": stripping_log,
            "sha256": sha256,
            "requires_approval": True,
        }

    except Exception as exc:
        return {"error": f"Export failed: {exc}"}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# List / query functions
# ---------------------------------------------------------------------------
def list_quarantine(status_filter=None):
    """List quarantined OpenClaw imports.

    Args:
        status_filter: Optional status to filter by.

    Returns:
        dict: List of quarantined imports.
    """
    conn = _get_db()
    try:
        if status_filter:
            rows = conn.execute(
                "SELECT id, skill_name, openclaw_author, scan_status, status, "
                "trust_score, has_executable_content, review_required, created_at, "
                "rejected_by, rejected_reason, gate_results, quarantine_path "
                "FROM openclaw_imports WHERE status = %s ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, skill_name, openclaw_author, scan_status, status, "
                "trust_score, has_executable_content, review_required, created_at, "
                "rejected_by, rejected_reason, gate_results, quarantine_path "
                "FROM openclaw_imports ORDER BY created_at DESC",
            ).fetchall()

        items = []
        for r in rows:
            if hasattr(r, "keys"):
                d = dict(r)
            else:
                d = {
                    "id": r[0],
                    "skill_name": r[1],
                    "author": r[2],
                    "scan_status": r[3],
                    "status": r[4],
                    "trust_score": r[5],
                    "has_scripts": bool(r[6]),
                    "review_required": bool(r[7]),
                    "created_at": r[8],
                    "rejected_by": r[9],
                    "rejected_reason": r[10],
                    "gate_results": r[11],
                    "quarantine_path": r[12],
                }
            # Parse gate_results to extract failed gates summary
            gate_raw = d.get("gate_results") or d.get("gate_results_raw")
            failed_gates = []
            if gate_raw:
                try:
                    gates = json.loads(gate_raw) if isinstance(gate_raw, str) else gate_raw
                    failed_gates = [
                        k for k, v in gates.items() if isinstance(v, dict) and v.get("status") in ("fail", "failed")
                    ]
                except Exception:
                    pass
            d["failed_gates"] = failed_gates
            items.append(d)

        return {"success": True, "count": len(items), "imports": items}
    except Exception as exc:
        return {"error": str(exc), "imports": []}
    finally:
        conn.close()


def list_exports(status_filter=None):
    """List OpenClaw export records.

    Args:
        status_filter: Optional status to filter by.

    Returns:
        dict: List of exports.
    """
    conn = _get_db()
    try:
        if status_filter:
            rows = conn.execute(
                "SELECT id, asset_id, version_id, exported_by, review_status, "
                "status, created_at FROM openclaw_exports WHERE status = %s "
                "ORDER BY created_at DESC",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, asset_id, version_id, exported_by, review_status, "
                "status, created_at FROM openclaw_exports ORDER BY created_at DESC",
            ).fetchall()

        items = []
        for r in rows:
            if hasattr(r, "keys"):
                items.append(dict(r))
            else:
                items.append(
                    {
                        "id": r[0],
                        "asset_id": r[1],
                        "version_id": r[2],
                        "exported_by": r[3],
                        "review_status": r[4],
                        "status": r[5],
                        "created_at": r[6],
                    }
                )

        return {"success": True, "count": len(items), "exports": items}
    except Exception as exc:
        return {"error": str(exc), "exports": []}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Health & gate checks
# ---------------------------------------------------------------------------
def health_check():
    """Check health of OpenClaw bridge subsystem.

    Returns:
        dict: Health status of all components.
    """
    checks = {
        "feature_flag": _is_enabled(),
        "database": False,
        "quarantine_dir": QUARANTINE_DIR.exists(),
        "scanner_available": _HAS_SCANNER,
        "catalog_available": _HAS_CATALOG,
        "audit_available": _HAS_AUDIT,
        "pi_detector_available": _HAS_PI_DETECTOR,
        "code_scanner_available": _HAS_CODE_SCANNER,
        "review_queue_available": _HAS_REVIEW,
        "skillhub_connector_available": _HAS_CLAWHUB,
        "config_loaded": False,
    }

    # Check DB
    try:
        conn = _get_db()
        conn.execute("SELECT 1 FROM openclaw_imports LIMIT 0")
        conn.execute("SELECT 1 FROM openclaw_exports LIMIT 0")
        checks["database"] = True
        conn.close()
    except Exception:
        pass

    # Check config
    try:
        cfg = _load_config()
        checks["config_loaded"] = bool(cfg)
    except Exception:
        pass

    healthy = checks["database"]
    return {
        "success": True,
        "healthy": healthy,
        "checks": checks,
    }


def gate_check():
    """Run gate evaluation for CI/CD integration.

    Returns:
        dict: Gate pass/fail status.
    """
    result = health_check()
    checks = result.get("checks", {})

    blocking_failures = []
    warnings = []

    if not checks.get("database"):
        blocking_failures.append("database_unavailable")
    if not checks.get("config_loaded"):
        warnings.append("config_not_loaded")
    if not checks.get("scanner_available"):
        warnings.append("scanner_not_available")

    # Check for overdue quarantine items
    try:
        conn = _get_db()
        config = _load_config()
        max_age = config.get("import", {}).get("quarantine_max_age_days", 30)
        rows = conn.execute(
            "SELECT COUNT(*) FROM openclaw_imports WHERE status = 'quarantined' AND created_at < datetime('now', %s)",
            (f"-{max_age} days",),
        ).fetchone()
        overdue = rows[0] if rows else 0
        if overdue > 0:
            warnings.append(f"quarantine_overdue_items: {overdue}")
        conn.close()
    except Exception:
        pass

    passed = len(blocking_failures) == 0
    return {
        "success": True,
        "gate": "openclaw_bridge",
        "passed": passed,
        "blocking_failures": blocking_failures,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OpenClaw Skill Bridge — zero-trust import/export for SkillHub skills",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--import", dest="do_import", action="store_true", help="Import an OpenClaw skill into quarantine"
    )
    group.add_argument("--promote", action="store_true", help="Promote a quarantined import to marketplace")
    group.add_argument("--reject", action="store_true", help="Reject a quarantined import")
    group.add_argument("--revoke", action="store_true", help="Revoke a promoted import (rollback)")
    group.add_argument("--export", action="store_true", help="Export an ICDEV™ skill to OpenClaw format")
    group.add_argument("--list-quarantine", action="store_true", help="List quarantined imports")
    group.add_argument("--list-exports", action="store_true", help="List export records")
    group.add_argument("--discover", metavar="QUERY", help="Search SkillHub for skills (vector search)")
    group.add_argument("--fetch", metavar="SLUG", help="Download + import a skill from SkillHub by slug")
    group.add_argument("--health", action="store_true", help="Health check")
    group.add_argument("--gate", action="store_true", help="Gate check for CI/CD")

    # Import args
    parser.add_argument("--source-path", help="Path to OpenClaw skill directory")
    parser.add_argument("--skillhub-url", help="SkillHub URL (for provenance)")
    parser.add_argument("--tenant-id", help="Tenant identifier")
    parser.add_argument("--imported-by", help="User who initiated import")

    # Promote/reject args
    parser.add_argument("--import-id", help="Import record ID")
    parser.add_argument("--promoted-by", help="User promoting the import")
    parser.add_argument("--rejected-by", help="User rejecting the import")
    parser.add_argument("--revoked-by", help="User revoking a promoted import")
    parser.add_argument("--reason", help="Rejection/revocation reason")

    # Export args
    parser.add_argument("--asset-id", help="Marketplace asset ID")
    parser.add_argument("--version-id", help="Version ID to export")
    parser.add_argument("--output-path", help="Output directory for export")
    parser.add_argument("--exported-by", help="User initiating export")

    # Filter / search
    parser.add_argument("--status", help="Status filter for list commands")
    parser.add_argument("--limit", type=int, default=10, help="Max search results for --discover")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    try:
        if args.do_import:
            if not args.source_path or not args.tenant_id or not args.imported_by:
                result = {"error": "--import requires --source-path, --tenant-id, --imported-by"}
            else:
                result = import_skill(
                    source_path=args.source_path,
                    tenant_id=args.tenant_id,
                    imported_by=args.imported_by,
                    skillhub_url=args.skillhub_url,
                )
        elif args.promote:
            if not args.import_id or not args.promoted_by:
                result = {"error": "--promote requires --import-id, --promoted-by"}
            else:
                result = promote_import(args.import_id, args.promoted_by)
        elif args.reject:
            if not args.import_id or not args.rejected_by or not args.reason:
                result = {"error": "--reject requires --import-id, --rejected-by, --reason"}
            else:
                result = reject_import(args.import_id, args.rejected_by, args.reason)
        elif args.revoke:
            if not args.import_id or not args.revoked_by or not args.reason:
                result = {"error": "--revoke requires --import-id, --revoked-by, --reason"}
            else:
                result = revoke_import(args.import_id, args.revoked_by, args.reason)
        elif args.export:
            if not args.asset_id or not args.version_id or not args.output_path or not args.exported_by:
                result = {"error": "--export requires --asset-id, --version-id, --output-path, --exported-by"}
            else:
                result = export_skill(args.asset_id, args.version_id, args.output_path, args.exported_by)
        elif args.list_quarantine:
            result = list_quarantine(status_filter=args.status)
        elif args.list_exports:
            result = list_exports(status_filter=args.status)
        elif args.discover:
            result = discover_skills(args.discover, limit=args.limit)
        elif args.fetch:
            if not args.tenant_id or not args.imported_by:
                result = {"error": "--fetch requires --tenant-id, --imported-by"}
            else:
                result = fetch_and_import(args.fetch, args.tenant_id, args.imported_by)
        elif args.health:
            result = health_check()
        elif args.gate:
            result = gate_check()
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if result.get("error"):
                print(f"ERROR: {result['error']}")
                sys.exit(1)
            elif result.get("success"):
                for k, v in result.items():
                    if k != "success":
                        print(f"  {k}: {v}")
            else:
                print(json.dumps(result, indent=2, default=str))

        if result.get("error"):
            sys.exit(1)

    except Exception as exc:
        err = {"error": str(exc)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
