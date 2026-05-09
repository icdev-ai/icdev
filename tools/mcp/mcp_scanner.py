"""
MCP Server Security Scanner.

Scans MCP server configurations for common vulnerability patterns and
returns a structured findings report.  Pure function — no Flask, no DB.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


# ── Constants ──────────────────────────────────────────────────────────────────

_AUTH_ENV_RE = re.compile(
    r'(AUTH|TOKEN|SECRET|API_KEY|PASSWORD|BEARER|CREDENTIAL)',
    re.IGNORECASE,
)

_PRIVILEGE_TOOL_NAMES: frozenset[str] = frozenset({
    'exec', 'shell', 'run_command', 'eval', 'bash', 'sh',
    'cmd', 'system', 'subprocess', 'popen', 'spawn',
})

_PRIVILEGE_COMMANDS: frozenset[str] = frozenset({
    'bash', 'sh', 'cmd', 'powershell', 'pwsh', 'exec',
})

# Wildcard / overly-broad tool name patterns
_WILDCARD_RE = re.compile(r'^\*$|^\.\*$|^all$|^any$|\*', re.IGNORECASE)

_SEVERITY: dict[str, str] = {
    'unauthenticated_transport': 'medium',
    'no_tls': 'high',
    'wildcard_tool_names': 'high',
    'missing_classification': 'medium',
    'privilege_escalation': 'high',
}

_CWE: dict[str, str] = {
    'unauthenticated_transport': 'CWE-306',
    'no_tls': 'CWE-319',
    'wildcard_tool_names': 'CWE-284',
    'missing_classification': 'CWE-200',
    'privilege_escalation': 'CWE-269',
}

_DESC: dict[str, str] = {
    'unauthenticated_transport': (
        'stdio transport server exposes no authentication token in environment variables'
    ),
    'no_tls': (
        'SSE/HTTP server endpoint uses plaintext HTTP instead of HTTPS'
    ),
    'wildcard_tool_names': (
        'server exposes a wildcard or overly broad tool name pattern'
    ),
    'missing_classification': (
        'server configuration is missing required classification field'
    ),
    'privilege_escalation': (
        'server exposes a privileged execution tool (exec/shell/eval/run_command)'
    ),
}


# ── Config loading ─────────────────────────────────────────────────────────────

def _project_root() -> Path:
    # tools/mcp/mcp_scanner.py → project root is three levels up
    return Path(__file__).resolve().parent.parent.parent


def _find_config(base: Path) -> Path | None:
    for candidate in (
        base / 'args' / 'mcp_config.yaml',
        base / '.mcp.json',
        base / 'claude_desktop_config.json',
    ):
        if candidate.exists():
            return candidate
    return None


def _load_servers(config_path: Path) -> dict[str, Any]:
    """
    Return the mcpServers dict from a config file.

    Supported formats:
    - YAML (args/mcp_config.yaml): top-level ``mcpServers`` key
    - JSON (.mcp.json, claude_desktop_config.json): ``{"mcpServers": {...}}``
    """
    text = config_path.read_text(encoding='utf-8')
    if config_path.suffix.lower() in ('.yaml', '.yml'):
        if not _YAML_AVAILABLE:
            raise RuntimeError(
                'PyYAML is required to read mcp_config.yaml. '
                'Install with: pip install pyyaml'
            )
        data = _yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    return data.get('mcpServers', {})


# ── Individual checks ──────────────────────────────────────────────────────────

def _finding(server_id: str, cfg: dict, check: str) -> dict:
    return {
        'server_id': server_id,
        'server_name': cfg.get('name', server_id),
        'severity': _SEVERITY[check],
        'check': check,
        'description': _DESC[check],
        'cwe': _CWE[check],
    }


def _check_unauthenticated_transport(server_id: str, cfg: dict) -> dict | None:
    """stdio server with no auth env var → medium finding."""
    if 'command' not in cfg:
        return None
    env: dict = cfg.get('env') or {}
    if any(_AUTH_ENV_RE.search(k) for k in env):
        return None
    return _finding(server_id, cfg, 'unauthenticated_transport')


def _check_no_tls(server_id: str, cfg: dict) -> dict | None:
    """URL-based server using http:// → high finding."""
    for field in ('url', 'endpoint', 'baseUrl', 'sse_url', 'base_url'):
        val = cfg.get(field)
        if isinstance(val, str) and val.startswith('http://'):
            return _finding(server_id, cfg, 'no_tls')
    return None


def _tool_names(cfg: dict) -> list[str]:
    """Extract tool names from a server config's optional 'tools' list."""
    tools = cfg.get('tools') or []
    names: list[str] = []
    for t in tools:
        if isinstance(t, str):
            names.append(t)
        elif isinstance(t, dict):
            name = t.get('name', '')
            if name:
                names.append(name)
    return names


def _check_wildcard_tool_names(server_id: str, cfg: dict) -> dict | None:
    """Any wildcard/overly-broad tool name → high finding."""
    for name in _tool_names(cfg):
        if _WILDCARD_RE.search(name):
            return _finding(server_id, cfg, 'wildcard_tool_names')
    return None


def _check_missing_classification(server_id: str, cfg: dict) -> dict | None:
    """Missing classification field → medium finding."""
    if 'classification' not in cfg:
        return _finding(server_id, cfg, 'missing_classification')
    return None


def _check_privilege_escalation(server_id: str, cfg: dict) -> dict | None:
    """Privileged tool name or privileged command → high finding."""
    for name in _tool_names(cfg):
        if name.lower() in _PRIVILEGE_TOOL_NAMES:
            return _finding(server_id, cfg, 'privilege_escalation')

    command = cfg.get('command', '')
    if isinstance(command, str):
        # Strip path components and .exe suffix for comparison
        base = Path(command).name.lower()
        if base.endswith('.exe'):
            base = base[:-4]
        if base in _PRIVILEGE_COMMANDS:
            return _finding(server_id, cfg, 'privilege_escalation')

    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def scan_mcp_servers(config_path: str | Path | None = None) -> dict:
    """
    Scan MCP server configurations for vulnerability patterns.

    Args:
        config_path: Path to an MCP config file.  When *None* the function
                     searches in order: ``args/mcp_config.yaml``,
                     ``.mcp.json``, ``claude_desktop_config.json`` relative
                     to the project root.

    Returns::

        {
            "servers_scanned": int,
            "findings": [
                {
                    "server_id": str,
                    "server_name": str,
                    "severity": "high" | "medium",
                    "check": str,
                    "description": str,
                    "cwe": str
                }
            ],
            "high_count": int,
            "medium_count": int,
            "passed": bool   # True only when high_count == 0
        }
    """
    base = _project_root()

    if config_path is not None:
        path: Path | None = Path(config_path)
    else:
        path = _find_config(base)

    if path is None or not path.exists():
        return {
            'servers_scanned': 0,
            'findings': [],
            'high_count': 0,
            'medium_count': 0,
            'passed': True,
            'error': 'no MCP config file found',
        }

    servers = _load_servers(path)
    findings: list[dict] = []

    for server_id, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue

        for chk_fn in (
            _check_unauthenticated_transport,
            _check_no_tls,
            _check_missing_classification,
            _check_wildcard_tool_names,
            _check_privilege_escalation,
        ):
            result = chk_fn(server_id, cfg)
            if result:
                findings.append(result)

    high_count = sum(1 for f in findings if f['severity'] == 'high')
    medium_count = sum(1 for f in findings if f['severity'] == 'medium')

    return {
        'servers_scanned': len(servers),
        'findings': findings,
        'high_count': high_count,
        'medium_count': medium_count,
        'passed': high_count == 0,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description='Scan MCP server configurations for vulnerability patterns'
    )
    parser.add_argument('--config', metavar='PATH', help='path to MCP config file')
    parser.add_argument(
        '--json', dest='json_output', action='store_true', help='emit JSON output'
    )
    args = parser.parse_args(argv)

    result = scan_mcp_servers(args.config)

    if args.json_output:
        print(json.dumps(result, indent=2))
        return

    config_label = args.config or '(auto-detected)'
    print(f'MCP Security Scan  [{config_label}]')
    print(f'  Servers scanned : {result["servers_scanned"]}')
    print(
        f'  Findings        : {len(result["findings"])}'
        f'  (high={result["high_count"]}, medium={result["medium_count"]})'
    )
    print(f'  Passed          : {result["passed"]}')
    if result.get('error'):
        print(f'  Warning         : {result["error"]}')
    if result['findings']:
        print()
        for f in result['findings']:
            tag = f['severity'].upper()
            print(
                f'  [{tag:6}] {f["server_id"]}: {f["check"]}'
                f' — {f["description"]}  ({f["cwe"]})'
            )


if __name__ == '__main__':
    main()
