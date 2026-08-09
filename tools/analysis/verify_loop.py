#!/usr/bin/env python3
# CUI // SP-CTI
"""Compiler-in-the-Loop Verification — iterative verify→repair→re-verify pipeline.

Adapted from Mistral AI's LeanStral closed-loop verification pattern (2026-03-16).
Generalizes D255 compiler-feedback repair loop across all 6 ICDEV™ languages.

The core insight: use compilers/linters/SAST as *perfect verifiers* in an agentic
feedback loop. When a gate fails, feed errors back to LLM for targeted repair
(max N attempts), just like LeanStral iterates with Lean's compiler.

Architecture Decisions:
  D-VL-1: Compiler-in-the-loop generalizes D255 to all 6 languages
  D-VL-2: Config-driven verifier stacks (args/verify_loop_config.yaml)
  D-VL-3: Air-gap safe — local Ollama models for repair, optional cloud
  D-VL-4: Append-only verify_loop_runs table (NIST AU)
  D-VL-5: Blocking vs non-blocking verifiers (syntax=blocking, lint=warning)

Usage:
    python tools/analysis/verify_loop.py --file src/main.py --language python --json
    python tools/analysis/verify_loop.py --file src/main.py --language python --repair --json
    python tools/analysis/verify_loop.py --file src/main.py --language python --gate --json
    python tools/analysis/verify_loop.py --project-dir src/ --language python --json
    python tools/analysis/verify_loop.py --dry-run --file src/main.py --language python --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.analysis.verify_loop")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "verify_loop_config.yaml"

# Platform-aware (D145)
_NULL_DEVICE = "NUL" if os.name == "nt" else "/dev/null"

# Language file extensions (consistent with code_analyzer.py)
_LANG_EXT: Dict[str, Tuple[str, ...]] = {
    "python": (".py",),
    "java": (".java",),
    "go": (".go",),
    "rust": (".rs",),
    "csharp": (".cs",),
    "typescript": (".ts", ".tsx"),
}

_EXT_TO_LANG: Dict[str, str] = {}
for _lang, _exts in _LANG_EXT.items():
    for _ext in _exts:
        _EXT_TO_LANG[_ext] = _lang


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config() -> Dict[str, Any]:
    """Load verify_loop_config.yaml with env var expansion."""
    try:
        import yaml
    except ImportError:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
        # Expand ${VAR:-default} patterns
        import re

        def _expand(match):
            expr = match.group(1)
            if ":-" in expr:
                var, default = expr.split(":-", 1)
                return os.environ.get(var, default)
            return os.environ.get(expr, match.group(0))

        raw = re.sub(r"\$\{([^}]+)\}", _expand, raw)
        return yaml.safe_load(raw) or {}
    except Exception:
        return {}


def _get_verifiers(language: str, config: Dict) -> List[Dict]:
    """Get verifier stack for a language from config."""
    return config.get("verifiers", {}).get(language, [])


def _get_loop_config(config: Dict) -> Dict:
    """Get loop configuration with defaults."""
    loop = config.get("loop", {})
    return {
        "max_iterations": loop.get("max_iterations", 3),
        "repair_function": loop.get("repair_function", "verify_loop_repair"),
        "timeout_seconds": loop.get("timeout_seconds", 120),
        "abort_on_blocking_failure": loop.get("abort_on_blocking_failure", True),
        "collect_all_errors": loop.get("collect_all_errors", True),
    }


# ---------------------------------------------------------------------------
# Database (append-only, NIST AU — D-VL-4)
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS verify_loop_runs (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    language TEXT NOT NULL,
    iteration INTEGER NOT NULL DEFAULT 0,
    max_iterations INTEGER NOT NULL DEFAULT 3,
    verifier_name TEXT NOT NULL,
    verifier_type TEXT NOT NULL DEFAULT 'syntax',
    blocking INTEGER NOT NULL DEFAULT 1,
    passed INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT,
    repaired INTEGER NOT NULL DEFAULT 0,
    repair_model TEXT,
    content_hash TEXT,
    project_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _get_db() -> sqlite3.Connection:
    """Get database connection, create table if needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_TABLE)
    return conn


def _store_result(result: Dict, project_id: str = "") -> None:
    """Store verification result (append-only)."""
    try:
        conn = _get_db()
        conn.execute(
            """INSERT INTO verify_loop_runs
               (id, file_path, language, iteration, max_iterations,
                verifier_name, verifier_type, blocking, passed,
                error_count, error_summary, repaired, repair_model,
                content_hash, project_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                f"vl-{uuid.uuid4().hex[:12]}",
                result.get("file_path", ""),
                result.get("language", ""),
                result.get("iteration", 0),
                result.get("max_iterations", 3),
                result.get("verifier_name", ""),
                result.get("verifier_type", "syntax"),
                1 if result.get("blocking") else 0,
                1 if result.get("passed") else 0,
                result.get("error_count", 0),
                (result.get("error_summary") or "")[:2000],
                1 if result.get("repaired") else 0,
                result.get("repair_model", ""),
                result.get("content_hash", ""),
                project_id,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        # Non-critical — don't fail verification on DB errors
        logger.warning("_store_result: best-effort INSERT into verify_loop_runs failed (non-blocking): %s", exc)


# ---------------------------------------------------------------------------
# Verifier execution
# ---------------------------------------------------------------------------


def _run_verifier(
    verifier: Dict,
    file_path: str,
    project_dir: str = "",
    timeout: int = 120,
) -> Dict:
    """Run a single verifier command and capture results.

    Returns dict with: name, blocking, passed, errors, stderr, return_code.
    """
    name = verifier.get("name", "unknown")
    command_template = verifier.get("command", [])
    blocking = verifier.get("blocking", False)
    optional = verifier.get("optional", False)
    working_dir = verifier.get("working_dir", "")

    # Expand placeholders
    temp_dir = tempfile.gettempdir()
    command = []
    for part in command_template:
        part = part.replace("{file}", file_path)
        part = part.replace("{temp_dir}", temp_dir)
        part = part.replace("{project_dir}", project_dir or str(Path(file_path).parent))
        command.append(part)

    if working_dir:
        working_dir = working_dir.replace("{project_dir}", project_dir or str(Path(file_path).parent))
    else:
        working_dir = None

    # Check if command exists
    exe = command[0] if command else ""
    if not exe:
        return {
            "name": name,
            "blocking": blocking,
            "passed": True,
            "errors": [],
            "stderr": "",
            "return_code": 0,
            "skipped": True,
            "reason": "No command configured",
        }

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL,
        )
        passed = result.returncode == 0
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        error_text = stderr or stdout if not passed else ""

        # Parse error lines
        errors = []
        if error_text:
            for line in error_text.splitlines():
                line = line.strip()
                if line and not line.startswith("---"):
                    errors.append(line)

        return {
            "name": name,
            "blocking": blocking,
            "passed": passed,
            "errors": errors[:50],  # Cap at 50 errors
            "stderr": error_text[:2000],
            "return_code": result.returncode,
            "skipped": False,
        }
    except FileNotFoundError:
        if optional:
            return {
                "name": name,
                "blocking": False,
                "passed": True,
                "errors": [],
                "stderr": "",
                "return_code": 0,
                "skipped": True,
                "reason": f"{exe} not found (optional)",
            }
        return {
            "name": name,
            "blocking": blocking,
            "passed": False,
            "errors": [f"Command not found: {exe}"],
            "stderr": f"Command not found: {exe}",
            "return_code": -1,
            "skipped": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "blocking": blocking,
            "passed": False,
            "errors": [f"Timeout after {timeout}s"],
            "stderr": f"Timeout after {timeout}s",
            "return_code": -1,
            "skipped": False,
        }
    except Exception as e:
        return {
            "name": name,
            "blocking": blocking,
            "passed": False,
            "errors": [str(e)],
            "stderr": str(e),
            "return_code": -1,
            "skipped": False,
        }


def _run_all_verifiers(
    verifiers: List[Dict],
    file_path: str,
    project_dir: str = "",
    timeout: int = 120,
) -> List[Dict]:
    """Run all verifiers for a file, collecting results."""
    results = []
    for v in verifiers:
        r = _run_verifier(v, file_path, project_dir, timeout)
        results.append(r)
    return results


# ---------------------------------------------------------------------------
# LLM Repair
# ---------------------------------------------------------------------------


def _repair_with_llm(
    file_path: str,
    source_code: str,
    errors: List[Dict],
    language: str,
    config: Dict,
) -> Optional[str]:
    """Invoke LLM to repair source code based on verifier errors.

    Returns repaired source code or None if repair fails.
    Air-gap safe: uses local models when ICDEV_AIR_GAPPED=true.
    """
    repair_cfg = config.get("repair", {})
    air_gap_cfg = config.get("air_gap", {})
    loop_cfg = _get_loop_config(config)

    system_prompt = repair_cfg.get("system_prompt", "Fix all errors. Return only code.")
    max_chars = repair_cfg.get("max_source_chars", 50000)
    temperature = repair_cfg.get("temperature", 0.2)

    # Build error summary
    error_lines = []
    for err in errors:
        if not err.get("passed") and err.get("errors"):
            error_lines.append(f"[{err['name']}] {'BLOCKING' if err.get('blocking') else 'WARNING'}:")
            for e in err["errors"][:10]:
                error_lines.append(f"  {e}")
    error_text = "\n".join(error_lines)

    if not error_text.strip():
        return None

    # Truncate source if needed
    truncated = source_code[:max_chars] if len(source_code) > max_chars else source_code

    user_message = (
        f"Language: {language}\n"
        f"File: {Path(file_path).name}\n\n"
        f"--- ERRORS ---\n{error_text}\n\n"
        f"--- SOURCE CODE ---\n{truncated}\n\n"
        f"Return ONLY the corrected source code. No explanations."
    )

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        function_name = loop_cfg.get("repair_function", "verify_loop_repair")

        # Air-gap override: force local model
        prefer_local = str(air_gap_cfg.get("prefer_local", "false")).lower() == "true"
        model_override = ""
        if prefer_local:
            model_override = air_gap_cfg.get("local_repair_model", "qwen3-local")

        request = LLMRequest(
            messages=[{"role": "user", "content": user_message}],
            system_prompt=system_prompt,
            model=model_override,
            temperature=temperature,
            max_tokens=max_chars,
            skip_injection_scan=True,  # Internal pipeline
        )

        response = router.invoke(function_name, request)
        if response and response.content:
            repaired = response.content.strip()
            # Strip markdown code fences if present
            if repaired.startswith("```"):
                lines = repaired.split("\n")
                # Remove first and last fence lines
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                repaired = "\n".join(lines)
            return repaired
    except ImportError:
        pass  # LLM not available
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Main verify loop
# ---------------------------------------------------------------------------


def verify_file(
    file_path: str,
    language: str = "",
    repair: bool = False,
    dry_run: bool = False,
    project_dir: str = "",
    project_id: str = "",
    config: Optional[Dict] = None,
) -> Dict:
    """Run compiler-in-the-loop verification on a single file.

    Args:
        file_path: Path to the source file.
        language: Programming language (auto-detected if empty).
        repair: Whether to attempt LLM-based repair on failure.
        dry_run: If True, only report what would be verified (no execution).
        project_dir: Project root directory.
        project_id: ICDEV™ project ID for audit trail.
        config: Pre-loaded config (loaded from YAML if None).

    Returns:
        Dict with: file, language, iterations, verifier_results, passed,
                    blocking_failures, warning_failures, repaired, gate_passed.
    """
    file_path = str(Path(file_path).resolve())
    if config is None:
        config = _load_config()

    # Auto-detect language
    if not language:
        ext = Path(file_path).suffix.lower()
        language = _EXT_TO_LANG.get(ext, "")
    if not language:
        return {
            "file": file_path,
            "language": "unknown",
            "error": f"Cannot detect language for {Path(file_path).suffix}",
            "passed": False,
            "gate_passed": False,
        }

    verifiers = _get_verifiers(language, config)
    if not verifiers:
        return {
            "file": file_path,
            "language": language,
            "error": f"No verifiers configured for {language}",
            "passed": True,
            "gate_passed": True,
            "verifier_results": [],
        }

    loop_cfg = _get_loop_config(config)
    max_iterations = loop_cfg["max_iterations"]
    timeout = loop_cfg["timeout_seconds"]

    if dry_run:
        return {
            "file": file_path,
            "language": language,
            "dry_run": True,
            "verifiers": [v["name"] for v in verifiers],
            "max_iterations": max_iterations if repair else 1,
            "repair_enabled": repair,
        }

    # Read source for content hash
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            original_source = f.read()
    except Exception as e:
        return {
            "file": file_path,
            "language": language,
            "error": f"Cannot read file: {e}",
            "passed": False,
            "gate_passed": False,
        }

    content_hash = hashlib.sha256(original_source.encode()).hexdigest()[:16]
    iterations_run = 0
    all_iteration_results = []
    repaired_flag = False
    repair_model = ""

    for iteration in range(max_iterations if repair else 1):
        iterations_run = iteration + 1
        results = _run_all_verifiers(verifiers, file_path, project_dir, timeout)

        # Classify results
        blocking_failures = [r for r in results if not r["passed"] and r["blocking"] and not r.get("skipped")]
        warning_failures = [r for r in results if not r["passed"] and not r["blocking"] and not r.get("skipped")]
        all_passed = len(blocking_failures) == 0

        iter_record = {
            "iteration": iteration + 1,
            "results": results,
            "blocking_failures": len(blocking_failures),
            "warning_failures": len(warning_failures),
            "all_blocking_passed": all_passed,
        }
        all_iteration_results.append(iter_record)

        # Store each verifier result
        audit_cfg = config.get("audit", {})
        if audit_cfg.get("store_results", True):
            for r in results:
                _store_result(
                    {
                        "file_path": file_path,
                        "language": language,
                        "iteration": iteration + 1,
                        "max_iterations": max_iterations,
                        "verifier_name": r["name"],
                        "verifier_type": r["name"],
                        "blocking": r["blocking"],
                        "passed": r["passed"],
                        "error_count": len(r.get("errors", [])),
                        "error_summary": r.get("stderr", "")[:2000] if not r["passed"] else "",
                        "repaired": repaired_flag,
                        "repair_model": repair_model,
                        "content_hash": content_hash,
                    },
                    project_id,
                )

        # If all blocking passed, we're done
        if all_passed:
            break

        # Attempt repair if enabled and not on last iteration
        if repair and iteration < max_iterations - 1:
            failed_results = blocking_failures + warning_failures
            repaired_code = _repair_with_llm(file_path, original_source, failed_results, language, config)
            if repaired_code and repaired_code != original_source:
                # Write repaired code back
                try:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(repaired_code)
                    original_source = repaired_code
                    content_hash = hashlib.sha256(repaired_code.encode()).hexdigest()[:16]
                    repaired_flag = True
                    # Extract model info from LLM router if available
                    try:
                        from tools.llm.router import LLMRouter

                        router = LLMRouter()
                        _, model_name = router.get_provider_for_function(
                            _get_loop_config(config).get("repair_function", "verify_loop_repair")
                        )
                        repair_model = model_name or "unknown"
                    except Exception:
                        repair_model = "unknown"
                except Exception:
                    pass  # Write failed — continue with original
            else:
                break  # Repair returned nothing useful, stop iterating

    # Final assessment
    final_results = all_iteration_results[-1]["results"] if all_iteration_results else []
    final_blocking = [r for r in final_results if not r["passed"] and r["blocking"] and not r.get("skipped")]
    final_warnings = [r for r in final_results if not r["passed"] and not r["blocking"] and not r.get("skipped")]

    gate_cfg = config.get("gate", {})
    max_unresolved_blocking = gate_cfg.get("max_unresolved_blocking", 0)
    max_unresolved_warnings = gate_cfg.get("max_unresolved_warnings", 10)

    gate_passed = len(final_blocking) <= max_unresolved_blocking and len(final_warnings) <= max_unresolved_warnings

    return {
        "file": file_path,
        "language": language,
        "iterations": iterations_run,
        "max_iterations": max_iterations if repair else 1,
        "repair_enabled": repair,
        "repaired": repaired_flag,
        "repair_model": repair_model,
        "content_hash": content_hash,
        "blocking_failures": len(final_blocking),
        "warning_failures": len(final_warnings),
        "passed": len(final_blocking) == 0,
        "gate_passed": gate_passed,
        "iteration_details": all_iteration_results,
    }


def verify_project(
    project_dir: str,
    language: str = "",
    repair: bool = False,
    dry_run: bool = False,
    project_id: str = "",
    config: Optional[Dict] = None,
) -> Dict:
    """Run verify loop on all matching files in a project directory."""
    project_dir = str(Path(project_dir).resolve())
    if config is None:
        config = _load_config()

    exclude_dirs = {
        "venv",
        ".venv",
        "env",
        "node_modules",
        ".git",
        "__pycache__",
        "build",
        "dist",
        ".tox",
        ".eggs",
        "vendor",
        "target",
        "bin",
        "obj",
        ".tmp",
        "playwright",
    }

    # Collect files
    files = []
    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for fname in filenames:
            ext = Path(fname).suffix.lower()
            file_lang = _EXT_TO_LANG.get(ext, "")
            if language and file_lang != language:
                continue
            if file_lang:
                files.append((os.path.join(root, fname), file_lang))

    results = []
    total_blocking = 0
    total_warnings = 0
    total_repaired = 0

    for fpath, flang in files:
        r = verify_file(
            fpath,
            flang,
            repair=repair,
            dry_run=dry_run,
            project_dir=project_dir,
            project_id=project_id,
            config=config,
        )
        results.append(r)
        total_blocking += r.get("blocking_failures", 0)
        total_warnings += r.get("warning_failures", 0)
        if r.get("repaired"):
            total_repaired += 1

    gate_cfg = config.get("gate", {})
    gate_passed = total_blocking <= gate_cfg.get("max_unresolved_blocking", 0) and total_warnings <= gate_cfg.get(
        "max_unresolved_warnings", 10
    )

    return {
        "project_dir": project_dir,
        "language_filter": language or "all",
        "files_checked": len(files),
        "total_blocking_failures": total_blocking,
        "total_warning_failures": total_warnings,
        "total_repaired": total_repaired,
        "repair_enabled": repair,
        "gate_passed": gate_passed,
        "file_results": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Compiler-in-the-Loop Verification (LeanStral-adapted)")
    parser.add_argument("--file", help="Single file to verify")
    parser.add_argument("--project-dir", help="Project directory to verify")
    parser.add_argument("--language", default="", help="Language (auto-detected if omitted)")
    parser.add_argument("--repair", action="store_true", help="Enable LLM-based repair loop")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without executing")
    parser.add_argument("--project-id", default="", help="ICDEV™ project ID for audit trail")
    parser.add_argument("--gate", action="store_true", help="Evaluate gate (exit 0=pass, 1=fail)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if not args.file and not args.project_dir:
        parser.error("Provide --file or --project-dir")

    config = _load_config()

    if args.file:
        result = verify_file(
            args.file,
            args.language,
            repair=args.repair,
            dry_run=args.dry_run,
            project_id=args.project_id,
            config=config,
        )
    else:
        result = verify_project(
            args.project_dir,
            args.language,
            repair=args.repair,
            dry_run=args.dry_run,
            project_id=args.project_id,
            config=config,
        )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        _print_human(result)
    else:
        print(json.dumps(result, indent=2, default=str))

    if args.gate:
        sys.exit(0 if result.get("gate_passed", False) else 1)


def _print_human(result: Dict):
    """Human-readable terminal output."""
    if "file_results" in result:
        # Project-level result
        print(f"\n{'=' * 60}")
        print(f"  Verify Loop — {result['project_dir']}")
        print(f"{'=' * 60}")
        print(f"  Files checked:      {result['files_checked']}")
        print(f"  Blocking failures:  {result['total_blocking_failures']}")
        print(f"  Warning failures:   {result['total_warning_failures']}")
        print(f"  Files repaired:     {result['total_repaired']}")
        status = "PASS" if result.get("gate_passed") else "FAIL"
        print(f"  Gate:               {status}")
        print(f"{'=' * 60}\n")

        for fr in result.get("file_results", []):
            status_icon = "+" if fr.get("passed") else "!"
            fname = Path(fr.get("file", "")).name
            iters = fr.get("iterations", 0)
            blk = fr.get("blocking_failures", 0)
            warn = fr.get("warning_failures", 0)
            repaired = " [REPAIRED]" if fr.get("repaired") else ""
            print(f"  [{status_icon}] {fname} — iter:{iters} blk:{blk} warn:{warn}{repaired}")
    else:
        # File-level result
        print(f"\n{'=' * 60}")
        print(f"  Verify Loop — {Path(result.get('file', '')).name}")
        print(f"{'=' * 60}")
        print(f"  Language:           {result.get('language', 'unknown')}")
        print(f"  Iterations:         {result.get('iterations', 0)}/{result.get('max_iterations', 1)}")
        print(f"  Blocking failures:  {result.get('blocking_failures', 0)}")
        print(f"  Warning failures:   {result.get('warning_failures', 0)}")
        print(f"  Repaired:           {'Yes' if result.get('repaired') else 'No'}")
        status = "PASS" if result.get("gate_passed") else "FAIL"
        print(f"  Gate:               {status}")
        print(f"{'=' * 60}\n")

        for iter_detail in result.get("iteration_details", []):
            print(f"  --- Iteration {iter_detail['iteration']} ---")
            for r in iter_detail.get("results", []):
                icon = "+" if r["passed"] else ("~" if r.get("skipped") else "!")
                label = "BLOCK" if r["blocking"] else "WARN"
                skip_note = f" (skipped: {r.get('reason', '')})" if r.get("skipped") else ""
                print(f"    [{icon}] {r['name']} ({label}){skip_note}")
                if not r["passed"] and not r.get("skipped"):
                    for err in r.get("errors", [])[:5]:
                        print(f"        {err}")


if __name__ == "__main__":
    main()
