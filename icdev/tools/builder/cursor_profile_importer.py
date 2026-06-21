#!/usr/bin/env python3
# CUI // SP-CTI
"""Cursor AI Profile Importer — reads .cursor/rules/*.mdc files and seeds ICDEV dev profiles.

Scans the repository for Cursor AI configuration files, parses their best-practice
rules, and creates or updates an ICDEV dev_profile with the extracted dimensions.

Usage:
    python tools/builder/cursor_profile_importer.py --scan .cursor/rules/ --json
    python tools/builder/cursor_profile_importer.py --create --scope platform --scope-id cursor-default --json
    python tools/builder/cursor_profile_importer.py --scan .cursor/rules/ --create --scope platform --scope-id cursor-default --json
"""

import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


MDC_DIR = BASE_DIR / ".cursor" / "rules"


def _parse_mdc_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from a .mdc file.

    Tolerates malformed YAML with unescaped inner quotes by pre-processing
    the description field.
    """
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1].strip()
            body = parts[2].strip()
            try:
                import yaml
                return {"frontmatter": yaml.safe_load(fm), "body": body}
            except yaml.parser.ParserError:
                # Try to fix unescaped inner quotes: "text "inner" text"
                # Strategy: replace the first colon-space-quote pattern and
                # then fix remaining unescaped quotes inside the value.
                fixed = _fix_yaml_quotes(fm)
                try:
                    return {"frontmatter": yaml.safe_load(fixed), "body": body}
                except Exception:
                    return {"frontmatter": {}, "body": body}
            except ImportError:
                return {"frontmatter": {}, "body": body}
    return {"frontmatter": {}, "body": content}


def _fix_yaml_quotes(text: str) -> str:
    """Fix unescaped inner double-quotes in YAML string values."""
    lines = text.splitlines()
    fixed_lines = []
    for line in lines:
        # Match key: "value with "unescaped" quotes"
        m = re.match(r'^(\s*\w+\s*:\s*)"(.+)"\s*$', line)
        if m:
            key = m.group(1)
            val = m.group(2)
            # Escape inner quotes by replacing them with single quotes or escaping
            # But simpler: wrap the whole value in single quotes if it contains unescaped double quotes
            if '"' in val:
                # Use YAML literal scalar | or just single-quote wrap
                fixed_lines.append(f"{key}'{val}'")
            else:
                fixed_lines.append(line)
        else:
            fixed_lines.append(line)
    return "\n".join(fixed_lines)


def _extract_rules_from_body(body: str) -> list:
    """Extract numbered or bulleted rules from markdown body."""
    rules = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        # Numbered bold rules like "1. **Name** — description"
        m = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s*[—:-]\s*(.+)$", line)
        if m:
            rules.append({"title": m.group(1), "text": m.group(2)})
            continue
        # Plain numbered rules like "1. snake_case naming, 100-char lines"
        m = re.match(r"^\d+\.\s+(.+)$", line)
        if m:
            text = m.group(1)
            # Try to extract bold title inside
            bm = re.match(r"\*\*(.+?)\*\*\s*[—:-]\s*(.+)$", text)
            if bm:
                rules.append({"title": bm.group(1), "text": bm.group(2)})
            else:
                rules.append({"title": "", "text": text})
            continue
        # Bullet rules
        m = re.match(r"^[-*]\s+(.+)$", line)
        if m:
            rules.append({"title": "", "text": m.group(1)})
            continue
        # Header-based sections
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            rules.append({"title": m.group(1), "text": "", "is_section": True})
    return rules


def _detect_commands(body: str) -> list:
    """Extract shell/python commands from code blocks."""
    commands = []
    # Match fenced code blocks
    for block in re.findall(r"```(?:bash|python)?\n(.*?)```", body, re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("python ") or line.startswith("pytest "):
                commands.append(line)
    return commands


def _map_to_dimensions(parsed_files: list) -> dict:
    """Map extracted Cursor rules to ICDEV dev_profile dimensions."""
    dimensions = {
        "language": {},
        "style": {},
        "testing": {},
        "architecture": {},
        "security": {},
        "compliance": {},
        "operations": {},
        "documentation": {},
        "git": {},
        "ai": {},
    }

    # Track globs for language detection
    all_globs = set()
    for pf in parsed_files:
        globs = pf.get("frontmatter", {}).get("globs", [])
        all_globs.update(globs)

    # Detect primary language from globs
    lang_map = {
        "**/*.py": "python",
        "**/*.ts": "typescript",
        "**/*.tsx": "typescript",
        "**/*.js": "javascript",
        "**/*.go": "go",
        "**/*.rs": "rust",
        "**/*.java": "java",
        "**/*.cs": "csharp",
    }
    detected_langs = []
    for g in all_globs:
        if g in lang_map:
            detected_langs.append(lang_map[g])
    if detected_langs:
        dimensions["language"]["primary"] = detected_langs[0]
        dimensions["language"]["allowed"] = list(set(detected_langs))

    # Parse icdev.mdc (alwaysApply root rule)
    root_file = None
    for pf in parsed_files:
        if pf.get("frontmatter", {}).get("alwaysApply"):
            root_file = pf
            break

    if root_file:
        body = root_file.get("body", "")
        rules = _extract_rules_from_body(body)
        commands = _detect_commands(body)

        for rule in rules:
            text = rule.get("text", "")
            title = rule.get("title", "")
            combined = f"{title} {text}".lower()

            # Style rules
            if "snake_case" in combined:
                dimensions["style"]["naming_convention"] = "snake_case"
            if "camelcase" in combined:
                dimensions["style"]["naming_convention"] = "camelCase"
            if "100-char" in combined or "100 char" in combined or "line length" in combined:
                m = re.search(r"(\d+)-char", combined)
                if m:
                    dimensions["style"]["max_line_length"] = int(m.group(1))
            if "black" in combined or "prettier" in combined or "ruff" in combined:
                dimensions["style"]["formatter"] = {"python": "black", "typescript": "prettier"}
            if "eslint" in combined or "ruff" in combined:
                dimensions["style"]["linter"] = {"python": "ruff", "typescript": "eslint"}
            if "indent" in combined:
                m = re.search(r"(\d+)\s*space", combined)
                if m:
                    dimensions["style"]["indent_size"] = int(m.group(1))
                    dimensions["style"]["indent_style"] = "spaces"

            # Testing rules
            if "pytest" in combined:
                dimensions["testing"]["require_unit"] = True
            if "behave" in combined:
                dimensions["testing"]["require_bdd"] = True
                dimensions["testing"]["bdd_framework"] = {"python": "behave", "typescript": "cucumber-js"}
            if "coverage" in combined:
                m = re.search(r">=\s*(\d+)%", combined)
                if m:
                    dimensions["testing"]["min_coverage"] = int(m.group(1))
                else:
                    m2 = re.search(r"(\d+)%\s*coverage", combined)
                    if m2:
                        dimensions["testing"]["min_coverage"] = int(m2.group(1))

            # Security rules
            if "cat1" in combined or "stig" in combined:
                dimensions["security"]["stig_compliance"] = True
                dimensions["security"]["sast_tools"] = {"python": "bandit", "typescript": "eslint-security"}
            if "secret" in combined or "secrets" in combined:
                dimensions["security"]["secret_management"] = "env_vars"
            if "critical vuln" in combined or "vulnerability" in combined:
                dimensions["security"]["vulnerability_sla"] = {"critical": "48h", "high": "14d", "medium": "60d", "low": "90d"}
            if "encryption" in combined or "aes" in combined:
                dimensions["security"]["encryption_standard"] = "aes_256"

            # Compliance rules
            if "cui" in combined:
                dimensions["compliance"]["cui_required"] = True
                dimensions["compliance"]["classification_level"] = "cui"
            if "il4" in combined:
                dimensions["compliance"]["classification_level"] = "cui"
            if "il5" in combined:
                dimensions["compliance"]["classification_level"] = "cui"
            if "il6" in combined:
                dimensions["compliance"]["classification_level"] = "secret"
            # Only override to secret if "SECRET" appears in an explicit classification context
            if re.search(r"classification[:\s]+secret\b|\bsecret\b.*\bmarking|\bsecret\b.*\bil6", combined):
                dimensions["compliance"]["classification_level"] = "secret"
            if "sbom" in combined:
                dimensions["compliance"]["sbom_format"] = "cyclonedx"
            if "ato" in combined or "ssp" in combined or "poam" in combined:
                dimensions["compliance"]["ato_approach"] = "continuous"
                if "frameworks" not in dimensions["compliance"]:
                    dimensions["compliance"]["frameworks"] = []
                if "NIST 800-53" not in dimensions["compliance"]["frameworks"]:
                    dimensions["compliance"]["frameworks"].append("NIST 800-53")

            # Architecture rules
            if "forge" in combined:
                dimensions["architecture"]["framework"] = "FORGE"
            if "flask" in combined:
                dimensions["architecture"]["web_framework"] = {"python": "flask"}
            if "webapp" in combined or "web app" in combined:
                dimensions["architecture"]["project_type"] = "webapp"

            # Documentation
            if "readme" in combined:
                dimensions["documentation"]["readme_required"] = True
            if "adr" in combined:
                dimensions["documentation"]["adr_required"] = True

            # Git / Workflow
            if "tdd" in combined or "red → green" in combined or "red -> green" in combined:
                dimensions["git"]["workflow"] = "tdd"
            if "conventional commits" in combined:
                dimensions["git"]["commit_format"] = "conventional_commits"
            if "merge" in combined and "strategy" in combined:
                if "squash" in combined:
                    dimensions["git"]["merge_strategy"] = "squash"

        # Extract commands for operations
        for cmd in commands:
            if "terraform" in cmd.lower():
                dimensions["operations"]["deployment_target"] = "terraform"
            if "ansible" in cmd.lower():
                dimensions["operations"]["provisioning"] = "ansible"
            if "kubernetes" in cmd.lower() or "k8s" in cmd.lower():
                dimensions["operations"]["container_orchestrator"] = "kubernetes"
            if "github_actions" in cmd.lower() or "gitlab" in cmd.lower():
                dimensions["operations"]["ci_cd_platform"] = "github_actions"

        # AI dimension from Karpathy principles
        if "karpathy" in body.lower() or "state assumptions" in body.lower():
            dimensions["ai"]["prompt_governance"] = "karpathy_principles"
            dimensions["ai"]["pre_design_gate"] = True

    # Parse workflow files for additional tooling
    for pf in parsed_files:
        if pf.get("frontmatter", {}).get("alwaysApply"):
            continue
        body = pf.get("body", "")
        rules = _extract_rules_from_body(body)
        commands = _detect_commands(body)

        for cmd in commands:
            if "sast_runner" in cmd:
                dimensions["security"]["sast_tools"] = {"python": "bandit", "typescript": "eslint-security"}
            if "dependency_auditor" in cmd:
                dimensions["security"]["dependency_audit"] = True
            if "secret_detector" in cmd:
                dimensions["security"]["secret_scanning"] = True
            if "container_scanner" in cmd:
                dimensions["security"]["container_scanning"] = True
            if "vuln_scanner" in cmd:
                dimensions["security"]["vulnerability_scanning"] = True
            if "stig_checker" in cmd:
                dimensions["security"]["stig_compliance"] = True
            if "cui_marker" in cmd:
                dimensions["compliance"]["cui_required"] = True

    # Clean empty dimensions
    return {k: v for k, v in dimensions.items() if v}


def scan_cursor_rules(directory: str = None) -> dict:
    """Scan a directory for .mdc files and parse them."""
    path = Path(directory) if directory else MDC_DIR
    if not path.exists():
        return {"error": f"Directory not found: {path}"}

    parsed = []
    for f in sorted(path.glob("*.mdc")):
        content = f.read_text(encoding="utf-8")
        parsed.append({"file": str(f.name), **_parse_mdc_frontmatter(content)})

    dimensions = _map_to_dimensions(parsed)
    return {
        "status": "scanned",
        "directory": str(path),
        "files_scanned": len(parsed),
        "files": [p["file"] for p in parsed],
        "dimensions": dimensions,
    }


def _generate_id(prefix="dprof"):
    """Generate a unique ID with prefix."""
    import hashlib
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    h = hashlib.sha256(ts.encode()).hexdigest()[:8]
    return f"{prefix}-{h}"


def seed_profile(scope: str, scope_id: str, directory: str = None, created_by: str = "cursor-importer", db_path: str = None) -> dict:
    """Scan Cursor rules and create a dev profile from them.

    Uses direct DB insert to avoid transaction-abort issues in
    dev_profile_manager.create_profile caused by failing audit_trail
    INSERTs in PostgreSQL.
    """
    scan_result = scan_cursor_rules(directory)
    if "error" in scan_result:
        return scan_result

    dimensions = scan_result.get("dimensions", {})
    if not dimensions:
        return {"error": "No dimensions extracted from Cursor rules"}

    # Direct insert (bypasses create_profile's broken _log_event)
    from tools.builder.dev_profile_manager import _get_connection, VALID_SCOPES

    if scope not in VALID_SCOPES:
        return {"error": f"Invalid scope: {scope}. Must be one of {VALID_SCOPES}"}

    conn = _get_connection(db_path)
    try:
        # Determine next version
        row = conn.execute(
            "SELECT MAX(version) as max_v FROM dev_profiles WHERE scope = ? AND scope_id = ?",
            (scope, scope_id),
        ).fetchone()
        next_version = (row["max_v"] or 0) + 1

        # Deactivate previous versions
        conn.execute(
            "UPDATE dev_profiles SET is_active = 0 WHERE scope = ? AND scope_id = ? AND is_active = 1",
            (scope, scope_id),
        )

        profile_id = _generate_id("dprof")
        conn.execute(
            """INSERT INTO dev_profiles
               (id, scope, scope_id, version, profile_md, profile_yaml,
                inherits_from, created_by, created_at, is_active, change_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                profile_id,
                scope,
                scope_id,
                next_version,
                "",
                json.dumps(dimensions, indent=2),
                None,
                created_by,
                datetime.now(timezone.utc).isoformat(),
                "Seeded from Cursor AI .mdc rules",
            ),
        )

        conn.commit()
        return {
            "status": "created",
            "profile_id": profile_id,
            "scope": scope,
            "scope_id": scope_id,
            "version": next_version,
            "inherits_from": None,
            "dimensions": list(dimensions.keys()),
            "seeded_dimensions": list(dimensions.keys()),
            "source_files": scan_result.get("files", []),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Import Cursor AI rules into ICDEV dev profiles")
    parser.add_argument("--scan", nargs="?", const=str(MDC_DIR), default=None, help="Scan directory for .mdc files")
    parser.add_argument("--create", action="store_true", help="Create a dev profile from scanned rules")
    parser.add_argument("--scope", default="platform")
    parser.add_argument("--scope-id", default="default")
    parser.add_argument("--created-by", default="cursor-importer")
    parser.add_argument("--db-path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.create:
        result = seed_profile(args.scope, args.scope_id, args.scan, args.created_by, args.db_path)
    elif args.scan:
        result = scan_cursor_rules(args.scan)
    else:
        result = {"error": "Use --scan or --create"}

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
