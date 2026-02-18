#!/usr/bin/env python3
"""Formatting Wrapper — runs language-specific formatters on projects.

Implements:
- format_python(project_path) -> run black + isort via subprocess
- format_javascript(project_path) -> run prettier via subprocess
- CLI: python tools/builder/formatter.py --project-path PATH
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Language support (Phase 16)
try:
    import importlib.util as _ilu
    _ls_path = Path(__file__).parent / "language_support.py"
    if _ls_path.exists():
        _ls_spec = _ilu.spec_from_file_location("language_support", _ls_path)
        _ls_mod = _ilu.module_from_spec(_ls_spec)
        _ls_spec.loader.exec_module(_ls_mod)
        detect_languages = _ls_mod.detect_languages
    else:
        detect_languages = None
except Exception:
    detect_languages = None


def format_python(project_path: str, check_only: bool = False) -> Dict:
    """Format Python code using black and isort.

    Args:
        project_path: Root path of the project.
        check_only: If True, only check formatting without modifying files.

    Returns:
        Dict with keys: success, tools_run, files_changed, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "python",
        "success": True,
        "tools_run": [],
        "files_changed": [],
        "raw_output": "",
    }

    outputs = []

    # Run isort first (import sorting)
    isort_result = _run_isort(root, check_only)
    result["tools_run"].append(isort_result)
    outputs.append(f"=== isort ===\n{isort_result['output']}")
    if not isort_result["success"] and not check_only:
        result["success"] = False

    # Run black (code formatting)
    black_result = _run_black(root, check_only)
    result["tools_run"].append(black_result)
    outputs.append(f"=== black ===\n{black_result['output']}")
    if not black_result["success"] and not check_only:
        result["success"] = False

    # In check mode, success means "already formatted"
    if check_only:
        result["success"] = all(t["success"] for t in result["tools_run"])

    result["raw_output"] = "\n".join(outputs)
    result["files_changed"] = (
        isort_result.get("files_changed", []) + black_result.get("files_changed", [])
    )
    return result


def _run_black(root: Path, check_only: bool) -> Dict:
    """Run black formatter."""
    cmd = [sys.executable, "-m", "black", "--line-length=100"]
    if check_only:
        cmd.append("--check")
    cmd.extend([
        "--exclude", r"/(venv|node_modules|\.git|__pycache__|build|dist)/",
        str(root),
    ])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        output = proc.stdout + proc.stderr
        files_changed = []
        for line in output.splitlines():
            if line.startswith("reformatted "):
                files_changed.append(line.replace("reformatted ", "").strip())

        return {
            "tool": "black",
            "success": proc.returncode == 0,
            "output": output,
            "files_changed": files_changed,
        }
    except FileNotFoundError:
        return {
            "tool": "black",
            "success": False,
            "output": "black not installed. Install with: pip install black",
            "files_changed": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "tool": "black",
            "success": False,
            "output": "black timed out after 120 seconds",
            "files_changed": [],
        }


def _run_isort(root: Path, check_only: bool) -> Dict:
    """Run isort import sorter."""
    cmd = [sys.executable, "-m", "isort", "--profile=black", "--line-length=100"]
    if check_only:
        cmd.append("--check-only")
    cmd.extend([
        "--skip", "venv",
        "--skip", "node_modules",
        "--skip", ".git",
        str(root),
    ])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )
        output = proc.stdout + proc.stderr
        files_changed = []
        for line in output.splitlines():
            if "Fixing" in line or "fixed" in line.lower():
                # isort output varies; capture file paths
                parts = line.split()
                for part in parts:
                    if part.endswith(".py"):
                        files_changed.append(part)

        return {
            "tool": "isort",
            "success": proc.returncode == 0,
            "output": output,
            "files_changed": files_changed,
        }
    except FileNotFoundError:
        return {
            "tool": "isort",
            "success": False,
            "output": "isort not installed. Install with: pip install isort",
            "files_changed": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "tool": "isort",
            "success": False,
            "output": "isort timed out after 120 seconds",
            "files_changed": [],
        }


def format_javascript(project_path: str, check_only: bool = False) -> Dict:
    """Format JavaScript code using prettier.

    Args:
        project_path: Root path of the project.
        check_only: If True, only check formatting without modifying files.

    Returns:
        Dict with keys: success, tools_run, files_changed, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "javascript",
        "success": True,
        "tools_run": [],
        "files_changed": [],
        "raw_output": "",
    }

    # Find prettier command
    prettier_cmd = None
    for cmd_parts in [["npx", "prettier"], ["prettier"]]:
        try:
            check = subprocess.run(
                cmd_parts + ["--version"],
                capture_output=True, text=True, timeout=30, cwd=str(root),
            )
            if check.returncode == 0:
                prettier_cmd = cmd_parts
                break
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    if not prettier_cmd:
        result["success"] = False
        result["raw_output"] = "prettier not found. Install with: npm install -g prettier"
        result["tools_run"].append({
            "tool": "prettier",
            "success": False,
            "output": "prettier not found",
            "files_changed": [],
        })
        return result

    # Build prettier command
    cmd = prettier_cmd[:]
    if check_only:
        cmd.append("--check")
    else:
        cmd.append("--write")
    cmd.extend([
        "--ignore-path", ".gitignore",
        str(root / "src" / "**" / "*.{js,jsx,ts,tsx,json,css,md}"),
    ])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120, cwd=str(root),
        )
        output = proc.stdout + proc.stderr
        files_changed = []
        for line in output.splitlines():
            line = line.strip()
            if line and not line.startswith("Checking") and not line.startswith("All"):
                if line.endswith((".js", ".ts", ".jsx", ".tsx", ".json", ".css")):
                    files_changed.append(line)

        prettier_result = {
            "tool": "prettier",
            "success": proc.returncode == 0,
            "output": output,
            "files_changed": files_changed,
        }
        result["tools_run"].append(prettier_result)
        result["success"] = proc.returncode == 0
        result["files_changed"] = files_changed
        result["raw_output"] = output

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "prettier timed out after 120 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running prettier: {str(e)}"

    return result


def format_java(project_path: str, check_only: bool = False) -> Dict:
    """Format Java code using google-java-format.

    Args:
        project_path: Root path of the project.
        check_only: If True, only check formatting without modifying files.

    Returns:
        Dict with keys: language, success, tools_run, files_changed, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "java",
        "success": True,
        "tools_run": [],
        "files_changed": [],
        "raw_output": "",
    }

    # Check if google-java-format is available
    try:
        version_check = subprocess.run(
            ["google-java-format", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "google-java-format not found. Install from: https://github.com/google/google-java-format"
            result["tools_run"].append({
                "tool": "google-java-format",
                "success": False,
                "output": "google-java-format not found",
                "files_changed": [],
            })
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "google-java-format not found. Install from: https://github.com/google/google-java-format"
        result["tools_run"].append({
            "tool": "google-java-format",
            "success": False,
            "output": "google-java-format not found",
            "files_changed": [],
        })
        return result

    # Find all .java files (excluding build directories)
    java_files = []
    for f in root.rglob("*.java"):
        skip = False
        for excl in ("build", "target", ".git", "node_modules", "bin", "obj"):
            if excl in f.parts:
                skip = True
                break
        if not skip:
            java_files.append(str(f))

    if not java_files:
        result["raw_output"] = "No .java files found"
        result["tools_run"].append({
            "tool": "google-java-format",
            "success": True,
            "output": "No .java files found",
            "files_changed": [],
        })
        return result

    if check_only:
        cmd = ["google-java-format", "--dry-run"] + java_files
    else:
        cmd = ["google-java-format", "--replace"] + java_files

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        output = proc.stdout + proc.stderr
        files_changed = []
        if not check_only:
            # google-java-format --replace doesn't list changed files, so we report all
            files_changed = java_files if proc.returncode == 0 else []
        else:
            # --dry-run outputs files that would be changed
            for line in output.splitlines():
                line = line.strip()
                if line and line.endswith(".java"):
                    files_changed.append(line)

        fmt_result = {
            "tool": "google-java-format",
            "success": proc.returncode == 0,
            "output": output,
            "files_changed": files_changed,
        }
        result["tools_run"].append(fmt_result)
        result["success"] = proc.returncode == 0
        result["files_changed"] = files_changed
        result["raw_output"] = output

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "google-java-format timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running google-java-format: {str(e)}"

    return result


def format_go(project_path: str, check_only: bool = False) -> Dict:
    """Format Go code using gofmt.

    Args:
        project_path: Root path of the project.
        check_only: If True, only list files that need formatting.

    Returns:
        Dict with keys: language, success, tools_run, files_changed, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "go",
        "success": True,
        "tools_run": [],
        "files_changed": [],
        "raw_output": "",
    }

    # Check if gofmt is available
    try:
        version_check = subprocess.run(
            ["gofmt", "-h"],
            capture_output=True, text=True, timeout=10,
        )
        # gofmt -h returns non-zero but prints usage; check for FileNotFoundError instead
    except FileNotFoundError:
        result["success"] = False
        result["raw_output"] = "gofmt not found. Install Go from https://go.dev"
        result["tools_run"].append({
            "tool": "gofmt",
            "success": False,
            "output": "gofmt not found",
            "files_changed": [],
        })
        return result
    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "gofmt timed out"
        return result

    if check_only:
        # gofmt -l lists files that need formatting
        cmd = ["gofmt", "-l", str(root)]
    else:
        # gofmt -w writes reformatted source to files
        cmd = ["gofmt", "-w", str(root)]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        output = proc.stdout + proc.stderr
        files_changed = []
        if check_only:
            # -l outputs filenames that would be changed
            for line in output.splitlines():
                line = line.strip()
                if line and line.endswith(".go"):
                    files_changed.append(line)
            # In check mode, success means no files need formatting
            success = proc.returncode == 0 and len(files_changed) == 0
        else:
            # -w doesn't list files, but stdout may contain them
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line and line.endswith(".go"):
                    files_changed.append(line)
            success = proc.returncode == 0

        fmt_result = {
            "tool": "gofmt",
            "success": success,
            "output": output,
            "files_changed": files_changed,
        }
        result["tools_run"].append(fmt_result)
        result["success"] = success
        result["files_changed"] = files_changed
        result["raw_output"] = output

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "gofmt timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running gofmt: {str(e)}"

    return result


def format_rust(project_path: str, check_only: bool = False) -> Dict:
    """Format Rust code using cargo fmt.

    Args:
        project_path: Root path of the project.
        check_only: If True, only check formatting without modifying files.

    Returns:
        Dict with keys: language, success, tools_run, files_changed, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "rust",
        "success": True,
        "tools_run": [],
        "files_changed": [],
        "raw_output": "",
    }

    # Check if cargo is available
    try:
        version_check = subprocess.run(
            ["cargo", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "cargo not found. Install Rust toolchain from https://rustup.rs"
            result["tools_run"].append({
                "tool": "cargo-fmt",
                "success": False,
                "output": "cargo not found",
                "files_changed": [],
            })
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "cargo not found. Install Rust toolchain from https://rustup.rs"
        result["tools_run"].append({
            "tool": "cargo-fmt",
            "success": False,
            "output": "cargo not found",
            "files_changed": [],
        })
        return result

    if check_only:
        cmd = ["cargo", "fmt", "--check"]
    else:
        cmd = ["cargo", "fmt"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        output = proc.stdout + proc.stderr
        files_changed = []
        # cargo fmt --check outputs diffs for files that need formatting
        if check_only and proc.returncode != 0:
            for line in output.splitlines():
                if line.startswith("Diff in "):
                    # Format: "Diff in /path/to/file.rs at line N:"
                    parts = line.split(" at line ")
                    if parts:
                        file_path = parts[0].replace("Diff in ", "").strip()
                        if file_path not in files_changed:
                            files_changed.append(file_path)

        fmt_result = {
            "tool": "cargo-fmt",
            "success": proc.returncode == 0,
            "output": output,
            "files_changed": files_changed,
        }
        result["tools_run"].append(fmt_result)
        result["success"] = proc.returncode == 0
        result["files_changed"] = files_changed
        result["raw_output"] = output

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "cargo fmt timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running cargo fmt: {str(e)}"

    return result


def format_csharp(project_path: str, check_only: bool = False) -> Dict:
    """Format C# code using dotnet format.

    Args:
        project_path: Root path of the project.
        check_only: If True, only verify formatting without modifying files.

    Returns:
        Dict with keys: language, success, tools_run, files_changed, raw_output.
    """
    root = Path(project_path)
    result = {
        "language": "csharp",
        "success": True,
        "tools_run": [],
        "files_changed": [],
        "raw_output": "",
    }

    # Check if dotnet is available
    try:
        version_check = subprocess.run(
            ["dotnet", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if version_check.returncode != 0:
            result["success"] = False
            result["raw_output"] = "dotnet not found. Install .NET SDK from https://dotnet.microsoft.com"
            result["tools_run"].append({
                "tool": "dotnet-format",
                "success": False,
                "output": "dotnet not found",
                "files_changed": [],
            })
            return result
    except (subprocess.TimeoutExpired, FileNotFoundError):
        result["success"] = False
        result["raw_output"] = "dotnet not found. Install .NET SDK from https://dotnet.microsoft.com"
        result["tools_run"].append({
            "tool": "dotnet-format",
            "success": False,
            "output": "dotnet not found",
            "files_changed": [],
        })
        return result

    if check_only:
        cmd = ["dotnet", "format", "--verify-no-changes"]
    else:
        cmd = ["dotnet", "format"]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, cwd=str(root),
        )
        output = proc.stdout + proc.stderr
        files_changed = []
        # dotnet format outputs file paths it changed
        for line in output.splitlines():
            line = line.strip()
            if line and (line.endswith(".cs") or line.endswith(".csproj")):
                files_changed.append(line)

        fmt_result = {
            "tool": "dotnet-format",
            "success": proc.returncode == 0,
            "output": output,
            "files_changed": files_changed,
        }
        result["tools_run"].append(fmt_result)
        result["success"] = proc.returncode == 0
        result["files_changed"] = files_changed
        result["raw_output"] = output

    except subprocess.TimeoutExpired:
        result["success"] = False
        result["raw_output"] = "dotnet format timed out after 300 seconds"
    except Exception as e:
        result["success"] = False
        result["raw_output"] = f"Error running dotnet format: {str(e)}"

    return result


def format_project(project_path: str, check_only: bool = False) -> Dict:
    """Format a project, auto-detecting languages.

    Args:
        project_path: Root path of the project.
        check_only: If True, only check formatting.

    Returns:
        Dict with results for each detected language.
    """
    # Use language_support.detect_languages if available, fall back to linter.detect_language
    if detect_languages is not None:
        languages = detect_languages(project_path)
    else:
        try:
            from tools.builder.linter import detect_language
            languages = detect_language(project_path)
        except ImportError:
            # Fallback inline detection
            def _detect_language(path):
                langs = []
                root = Path(path)
                if (root / "pyproject.toml").exists() or (root / "setup.py").exists():
                    langs.append("python")
                if (root / "package.json").exists():
                    langs.append("javascript")
                if not langs:
                    if list(root.glob("**/*.py")):
                        langs.append("python")
                    if list(root.glob("**/*.js")):
                        langs.append("javascript")
                return langs
            languages = _detect_language(project_path)

    # TypeScript reuses format_javascript (prettier handles both)
    FORMATTERS = {
        "python": format_python,
        "javascript": format_javascript,
        "typescript": format_javascript,
        "java": format_java,
        "go": format_go,
        "rust": format_rust,
        "csharp": format_csharp,
    }

    results = {
        "project_path": project_path,
        "languages_detected": languages,
        "results": {},
        "overall_success": True,
    }

    if not languages:
        results["message"] = "No supported languages detected"
        return results

    for lang in languages:
        formatter_fn = FORMATTERS.get(lang)
        if formatter_fn:
            lang_result = formatter_fn(project_path, check_only=check_only)
            results["results"][lang] = lang_result
            if not lang_result["success"]:
                results["overall_success"] = False

    return results


def main():
    parser = argparse.ArgumentParser(description="Format project code")
    parser.add_argument("--project-path", required=True, help="Root path of the project")
    parser.add_argument(
        "--language",
        choices=["python", "javascript", "typescript", "java", "go", "rust", "csharp", "auto"],
        default="auto",
        help="Language to format (default: auto-detect)",
    )
    parser.add_argument("--check", action="store_true", help="Check only (no modifications)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # TypeScript reuses format_javascript (prettier handles both)
    FORMATTERS = {
        "python": format_python,
        "javascript": format_javascript,
        "typescript": format_javascript,
        "java": format_java,
        "go": format_go,
        "rust": format_rust,
        "csharp": format_csharp,
    }

    if args.language == "auto":
        results = format_project(args.project_path, check_only=args.check)
    elif args.language in FORMATTERS:
        results = FORMATTERS[args.language](args.project_path, check_only=args.check)
    else:
        results = format_project(args.project_path, check_only=args.check)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        if isinstance(results, dict) and "results" in results:
            langs = results.get("languages_detected", [])
            print(f"Languages detected: {', '.join(langs) if langs else 'none'}")
            for lang, res in results.get("results", {}).items():
                _print_format_result(res)
            mode = "check" if args.check else "format"
            status = "PASS" if results["overall_success"] else "FAIL"
            print(f"\nOverall {mode}: {status}")
        else:
            _print_format_result(results)


def _print_format_result(result: Dict) -> None:
    """Print formatting result in human-readable format."""
    lang = result.get("language", "unknown")
    success = result.get("success", False)
    tools = result.get("tools_run", [])
    changed = result.get("files_changed", [])

    print(f"\n--- {lang} ---")
    for tool in tools:
        status = "OK" if tool["success"] else "ISSUES"
        print(f"  {tool['tool']}: {status}")

    if changed:
        print(f"  Files changed ({len(changed)}):")
        for f in changed[:20]:
            print(f"    {f}")
        if len(changed) > 20:
            print(f"    ... and {len(changed) - 20} more")
    elif success:
        print("  All files already formatted.")


if __name__ == "__main__":
    main()
