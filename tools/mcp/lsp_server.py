#!/usr/bin/env python3
# CUI // SP-CTI
"""LSP-over-MCP Server — Language Server Protocol via Model Context Protocol.

Adapted from LeanStral's lean-lsp-mcp pattern (Mistral AI, 2026-03-16).
Wraps language server protocol (LSP) functionality and exposes it through MCP,
giving AI agents real-time access to type information, error diagnostics,
go-to-definition, hover docs, and completions.

100% air-gap safe — all operations are local (LSP servers run locally).

Architecture Decisions:
  D-VL-7: LSP-over-MCP exposes compiler intelligence to AI agents via MCP
  D-VL-8: Python (pylsp/pyright) first, extensible to all 6 languages

Supported Language Servers:
  - Python: pylsp (python-lsp-server) or pyright (via basedpyright)
  - TypeScript/JavaScript: typescript-language-server
  - Go: gopls
  - Rust: rust-analyzer
  - Java: jdtls (Eclipse JDT LS)
  - C#: omnisharp

Usage:
    python tools/mcp/lsp_server.py                  # Start MCP server (stdio)
    python tools/mcp/lsp_server.py --check --json    # Check available LSP servers
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# LSP Server Registry (all local, air-gap safe)
# ---------------------------------------------------------------------------

LSP_SERVERS: Dict[str, Dict[str, Any]] = {
    "python": {
        "name": "pylsp",
        "command": ["pylsp"],
        "alt_command": ["pyright-langserver", "--stdio"],
        "install_hint": "pip install python-lsp-server",
        "capabilities": ["diagnostics", "hover", "completion", "definition",
                         "references", "rename", "formatting", "type_check"],
    },
    "typescript": {
        "name": "typescript-language-server",
        "command": ["typescript-language-server", "--stdio"],
        "install_hint": "npm install -g typescript-language-server typescript",
        "capabilities": ["diagnostics", "hover", "completion", "definition",
                         "references", "rename", "formatting", "type_check"],
    },
    "go": {
        "name": "gopls",
        "command": ["gopls", "serve"],
        "install_hint": "go install golang.org/x/tools/gopls@latest",
        "capabilities": ["diagnostics", "hover", "completion", "definition",
                         "references", "rename", "formatting"],
    },
    "rust": {
        "name": "rust-analyzer",
        "command": ["rust-analyzer"],
        "install_hint": "rustup component add rust-analyzer",
        "capabilities": ["diagnostics", "hover", "completion", "definition",
                         "references", "rename", "formatting", "type_check"],
    },
    "java": {
        "name": "jdtls",
        "command": ["jdtls"],
        "install_hint": "Install Eclipse JDT Language Server",
        "capabilities": ["diagnostics", "hover", "completion", "definition",
                         "references", "rename", "formatting"],
    },
    "csharp": {
        "name": "omnisharp",
        "command": ["omnisharp", "--languageserver"],
        "install_hint": "dotnet tool install -g omnisharp",
        "capabilities": ["diagnostics", "hover", "completion", "definition",
                         "references", "rename", "formatting"],
    },
}


def _check_command(command: List[str]) -> bool:
    """Check if a command is available on PATH."""
    exe = command[0] if command else ""
    return shutil.which(exe) is not None


def check_available_servers() -> Dict[str, Any]:
    """Check which LSP servers are installed and available."""
    results = {}
    for lang, info in LSP_SERVERS.items():
        primary_available = _check_command(info["command"])
        alt_available = _check_command(info.get("alt_command", []))
        results[lang] = {
            "name": info["name"],
            "available": primary_available or alt_available,
            "primary_available": primary_available,
            "alt_available": alt_available,
            "install_hint": info["install_hint"] if not (primary_available or alt_available) else None,
            "capabilities": info["capabilities"],
        }
    return results


# ---------------------------------------------------------------------------
# Lightweight LSP Client (subprocess-based, no async)
# ---------------------------------------------------------------------------

def _run_diagnostics(file_path: str, language: str) -> Dict[str, Any]:
    """Run diagnostics on a file using language-specific tools.

    Uses direct tool invocation (not full LSP protocol) for simplicity
    and air-gap safety. Returns structured diagnostic results.
    """
    file_path = str(Path(file_path).resolve())
    diagnostics = []

    if language == "python":
        # Try ruff first (fast), then mypy for types
        diags = []
        if shutil.which("ruff"):
            try:
                result = subprocess.run(
                    ["ruff", "check", "--output-format", "json", file_path],
                    capture_output=True, text=True, timeout=30,
                    stdin=subprocess.DEVNULL,
                )
                if result.stdout.strip():
                    try:
                        items = json.loads(result.stdout)
                        for item in items:
                            diags.append({
                                "line": item.get("location", {}).get("row", 0),
                                "column": item.get("location", {}).get("column", 0),
                                "severity": "warning",
                                "code": item.get("code", ""),
                                "message": item.get("message", ""),
                                "source": "ruff",
                            })
                    except json.JSONDecodeError:
                        pass
            except Exception:
                pass

        if shutil.which("mypy"):
            try:
                result = subprocess.run(
                    ["mypy", "--ignore-missing-imports", "--no-error-summary",
                     "--output", "json", file_path],
                    capture_output=True, text=True, timeout=60,
                    stdin=subprocess.DEVNULL,
                )
                for line in (result.stdout + result.stderr).splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        diags.append({
                            "line": item.get("line", 0),
                            "column": item.get("column", 0),
                            "severity": item.get("severity", "error"),
                            "code": item.get("code", ""),
                            "message": item.get("message", ""),
                            "source": "mypy",
                        })
                    except json.JSONDecodeError:
                        # Parse classic format: file:line: severity: message
                        if ":" in line:
                            parts = line.split(":", 3)
                            if len(parts) >= 4:
                                try:
                                    diags.append({
                                        "line": int(parts[1].strip()),
                                        "column": 0,
                                        "severity": parts[2].strip().split()[0] if parts[2].strip() else "error",
                                        "message": parts[3].strip(),
                                        "source": "mypy",
                                    })
                                except (ValueError, IndexError):
                                    pass
            except Exception:
                pass

        diagnostics = diags

    elif language == "typescript":
        if shutil.which("npx"):
            try:
                result = subprocess.run(
                    ["npx", "tsc", "--noEmit", "--pretty", "false", file_path],
                    capture_output=True, text=True, timeout=60,
                    stdin=subprocess.DEVNULL,
                )
                for line in result.stdout.splitlines():
                    if "error TS" in line:
                        diagnostics.append({
                            "line": 0, "column": 0,
                            "severity": "error",
                            "message": line.strip(),
                            "source": "tsc",
                        })
            except Exception:
                pass

    elif language == "go":
        if shutil.which("go"):
            try:
                result = subprocess.run(
                    ["go", "vet", file_path],
                    capture_output=True, text=True, timeout=30,
                    stdin=subprocess.DEVNULL,
                )
                for line in result.stderr.splitlines():
                    if line.strip():
                        diagnostics.append({
                            "line": 0, "column": 0,
                            "severity": "warning",
                            "message": line.strip(),
                            "source": "go vet",
                        })
            except Exception:
                pass

    elif language == "rust":
        if shutil.which("cargo"):
            try:
                result = subprocess.run(
                    ["cargo", "check", "--message-format", "json"],
                    capture_output=True, text=True, timeout=120,
                    cwd=str(Path(file_path).parent),
                    stdin=subprocess.DEVNULL,
                )
                for line in result.stdout.splitlines():
                    try:
                        msg = json.loads(line)
                        if msg.get("reason") == "compiler-message":
                            m = msg.get("message", {})
                            diagnostics.append({
                                "line": 0, "column": 0,
                                "severity": m.get("level", "error"),
                                "message": m.get("message", ""),
                                "source": "rustc",
                            })
                    except json.JSONDecodeError:
                        pass
            except Exception:
                pass

    return {
        "file": file_path,
        "language": language,
        "diagnostics": diagnostics,
        "count": len(diagnostics),
        "errors": len([d for d in diagnostics if d.get("severity") == "error"]),
        "warnings": len([d for d in diagnostics if d.get("severity") in ("warning", "warn")]),
    }


def _get_hover_info(file_path: str, line: int, column: int, language: str) -> Dict:
    """Get hover/type info at a position. Uses pyright for Python."""
    if language == "python" and shutil.which("pyright"):
        # Pyright doesn't have a simple hover CLI — use type stubs
        # Fall back to AST-based type extraction
        try:
            import ast
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)
            # Find node at line
            for node in ast.walk(tree):
                if hasattr(node, "lineno") and node.lineno == line:
                    if isinstance(node, ast.FunctionDef):
                        args_str = ", ".join(
                            a.arg + (f": {ast.dump(a.annotation)}" if a.annotation else "")
                            for a in node.args.args
                        )
                        returns = f" -> {ast.dump(node.returns)}" if node.returns else ""
                        return {
                            "file": file_path, "line": line, "column": column,
                            "kind": "function",
                            "signature": f"def {node.name}({args_str}){returns}",
                            "docstring": ast.get_docstring(node) or "",
                        }
                    elif isinstance(node, ast.ClassDef):
                        bases = ", ".join(ast.dump(b) for b in node.bases)
                        return {
                            "file": file_path, "line": line, "column": column,
                            "kind": "class",
                            "signature": f"class {node.name}({bases})" if bases else f"class {node.name}",
                            "docstring": ast.get_docstring(node) or "",
                        }
        except Exception:
            pass

    return {
        "file": file_path, "line": line, "column": column,
        "kind": "unknown",
        "info": f"Hover info not available for {language} at {line}:{column}",
    }


def _find_definitions(file_path: str, symbol: str, language: str, project_dir: str = "") -> List[Dict]:
    """Find definitions of a symbol. Uses grep-based fallback."""
    search_dir = project_dir or str(Path(file_path).parent)
    definitions = []

    # Pattern-based definition search (air-gap safe, no LSP needed)
    patterns = {
        "python": [f"def {symbol}(", f"class {symbol}(", f"class {symbol}:", f"{symbol} ="],
        "typescript": [f"function {symbol}(", f"class {symbol}", f"const {symbol}", f"interface {symbol}"],
        "go": [f"func {symbol}(", f"type {symbol} "],
        "rust": [f"fn {symbol}(", f"struct {symbol}", f"enum {symbol}", f"trait {symbol}"],
        "java": [f"class {symbol}", f"interface {symbol}", f"{symbol}("],
        "csharp": [f"class {symbol}", f"interface {symbol}", f"{symbol}("],
    }

    lang_patterns = patterns.get(language, [f"{symbol}"])
    lang_exts = {
        "python": [".py"], "typescript": [".ts", ".tsx"], "go": [".go"],
        "rust": [".rs"], "java": [".java"], "csharp": [".cs"],
    }
    exts = lang_exts.get(language, [])

    exclude_dirs = {".git", "__pycache__", "node_modules", "venv", ".venv", ".tmp"}

    for root, dirs, files in os.walk(search_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for fname in files:
            if exts and not any(fname.endswith(e) for e in exts):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        for pat in lang_patterns:
                            if pat in line:
                                definitions.append({
                                    "file": fpath,
                                    "line": i,
                                    "text": line.strip()[:200],
                                    "pattern": pat,
                                })
            except Exception:
                continue

    return definitions[:20]  # Cap results


# ---------------------------------------------------------------------------
# MCP Tool Definitions
# ---------------------------------------------------------------------------

MCP_TOOLS = [
    {
        "name": "lsp_check_servers",
        "description": "Check which LSP servers are installed and available for each language",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "lsp_diagnostics",
        "description": "Run diagnostics (errors, warnings, type checks) on a source file using language-specific tools",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the source file"},
                "language": {"type": "string", "description": "Language (auto-detected if omitted)"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "lsp_hover",
        "description": "Get type information and documentation at a specific position in a file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the source file"},
                "line": {"type": "integer", "description": "Line number (1-based)"},
                "column": {"type": "integer", "description": "Column number (0-based)"},
                "language": {"type": "string", "description": "Language"},
            },
            "required": ["file_path", "line"],
        },
    },
    {
        "name": "lsp_find_definition",
        "description": "Find all definitions of a symbol in the project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Source file for context"},
                "symbol": {"type": "string", "description": "Symbol name to find"},
                "language": {"type": "string", "description": "Language"},
                "project_dir": {"type": "string", "description": "Project root directory"},
            },
            "required": ["file_path", "symbol"],
        },
    },
    {
        "name": "lsp_verify_loop",
        "description": "Run compiler-in-the-loop verification on a file with optional LLM repair",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the source file"},
                "language": {"type": "string", "description": "Language (auto-detected if omitted)"},
                "repair": {"type": "boolean", "description": "Enable LLM-based repair loop"},
                "project_dir": {"type": "string", "description": "Project root directory"},
            },
            "required": ["file_path"],
        },
    },
]

# Language extension map
_EXT_TO_LANG = {
    ".py": "python", ".java": "java", ".go": "go",
    ".rs": "rust", ".cs": "csharp", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
}


def _detect_language(file_path: str, language: str = "") -> str:
    """Detect language from file extension if not provided."""
    if language:
        return language
    ext = Path(file_path).suffix.lower()
    return _EXT_TO_LANG.get(ext, "")


def handle_tool_call(tool_name: str, arguments: Dict) -> Any:
    """Handle an MCP tool call."""
    if tool_name == "lsp_check_servers":
        return check_available_servers()

    elif tool_name == "lsp_diagnostics":
        file_path = arguments.get("file_path", "")
        language = _detect_language(file_path, arguments.get("language", ""))
        return _run_diagnostics(file_path, language)

    elif tool_name == "lsp_hover":
        file_path = arguments.get("file_path", "")
        line = arguments.get("line", 1)
        column = arguments.get("column", 0)
        language = _detect_language(file_path, arguments.get("language", ""))
        return _get_hover_info(file_path, line, column, language)

    elif tool_name == "lsp_find_definition":
        file_path = arguments.get("file_path", "")
        symbol = arguments.get("symbol", "")
        language = _detect_language(file_path, arguments.get("language", ""))
        project_dir = arguments.get("project_dir", "")
        return _find_definitions(file_path, symbol, language, project_dir)

    elif tool_name == "lsp_verify_loop":
        file_path = arguments.get("file_path", "")
        language = _detect_language(file_path, arguments.get("language", ""))
        repair = arguments.get("repair", False)
        project_dir = arguments.get("project_dir", "")
        from tools.analysis.verify_loop import verify_file
        return verify_file(file_path, language, repair=repair, project_dir=project_dir)

    return {"error": f"Unknown tool: {tool_name}"}


# ---------------------------------------------------------------------------
# MCP Stdio Server
# ---------------------------------------------------------------------------

def _mcp_server():
    """Run MCP server over stdio (JSON-RPC 2.0)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        req_id = request.get("id")

        if method == "tools/list":
            response = {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"tools": MCP_TOOLS},
            }
        elif method == "tools/call":
            params = request.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result = handle_tool_call(tool_name, arguments)
                response = {
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
                    },
                }
            except Exception as e:
                response = {
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -1, "message": str(e)},
                }
        elif method == "initialize":
            response = {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "icdev-lsp", "version": "1.0.0"},
                },
            }
        elif method == "notifications/initialized":
            continue
        else:
            response = {
                "jsonrpc": "2.0", "id": req_id,
                "result": {},
            }

        print(json.dumps(response), flush=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="LSP-over-MCP Server (LeanStral-adapted)")
    parser.add_argument("--check", action="store_true", help="Check available LSP servers")
    parser.add_argument("--json", action="store_true", help="JSON output for --check")
    args = parser.parse_args()

    if args.check:
        result = check_available_servers()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("\nLSP Server Availability:")
            print("=" * 50)
            for lang, info in result.items():
                icon = "+" if info["available"] else "!"
                print(f"  [{icon}] {lang:12s} — {info['name']}")
                if not info["available"]:
                    print(f"       Install: {info['install_hint']}")
            print()
        return

    # Default: run MCP server over stdio
    _mcp_server()


if __name__ == "__main__":
    main()
