#!/usr/bin/env python3
# CUI // SP-CTI
"""PROFILE.md Generator — human-readable narrative from resolved dev profiles.

ADR D186: PROFILE.md is generated from dev_profile via Jinja2 (consistent with
D50 dynamic CLAUDE.md). Read-only narrative, not separately editable — source of
truth is the structured YAML profile, not this markdown file.

The generator takes a resolved profile (output of resolve_profile()) and renders
a PROFILE.md that documents the active development standards, their provenance
(which scope layer set them), and enforcement status (locked/enforced/advisory).

Usage:
    # Generate from resolved profile for a project
    python tools/builder/profile_md_generator.py --scope project --scope-id proj-123

    # Output to file
    python tools/builder/profile_md_generator.py --scope project --scope-id proj-123 \
        --output /path/to/project/PROFILE.md

    # JSON envelope (metadata + content)
    python tools/builder/profile_md_generator.py --scope project --scope-id proj-123 --json

Classification: CUI // SP-CTI
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
logger = logging.getLogger("icdev.profile_md_generator")

try:
    from jinja2 import Environment, BaseLoader
    _HAS_JINJA2 = True
except ImportError:
    _HAS_JINJA2 = False
    Environment = None  # type: ignore[assignment,misc]


# ── Jinja2 Template ──────────────────────────────────────────────────
# Stored as a string constant for zero filesystem dependencies beyond
# the resolved profile dict.  All sections are conditionally rendered.
# ─────────────────────────────────────────────────────────────────────

PROFILE_MD_TEMPLATE = r"""# PROFILE.md

> **Auto-generated** from tenant development profile cascade.
> Do not edit directly — update the profile via `dev_profile_manager.py`.
> Generated: {{ generated_at }}

---

## Overview

| Field | Value |
|-------|-------|
| Scope | {{ scope }} |
| Scope ID | {{ scope_id }} |
| Cascade layers | {{ ancestry | length }} |
| Locked dimensions | {{ locks | length }} |
| Total dimensions | {{ resolved | length }} |

### Cascade Ancestry

{% for anc in ancestry %}
{{ loop.index }}. **{{ anc.scope }}** → `{{ anc.scope_id }}`
{% endfor %}

{% if locks %}
### Locked Dimensions

| Dimension | Status |
|-----------|--------|
{% for lock in locks %}
| {{ lock }} | LOCKED |
{% endfor %}
{% endif %}

---
{% if resolved.get("language") %}

## Language Standards
{% set lang = resolved.language %}
{% set prov = provenance.get("language", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

- **Primary language**: {{ lang.get("primary", "not set") }}
{% if lang.get("allowed") %}
- **Allowed languages**: {{ lang.allowed | join(", ") }}
{% endif %}
{% if lang.get("forbidden") %}
- **Forbidden languages**: {{ lang.forbidden | join(", ") }}
{% endif %}
{% if lang.get("versions") %}
- **Version requirements**:
{% for k, v in lang.versions.items() %}
  - {{ k }}: {{ v }}
{% endfor %}
{% endif %}
{% if lang.get("package_managers") %}
- **Package managers**:
{% for k, v in lang.package_managers.items() %}
  - {{ k }}: {{ v }}
{% endfor %}
{% endif %}
{% if lang.get("virtual_env_tool") %}
- **Virtual env tool**: {{ lang.virtual_env_tool }}
{% endif %}
{% if lang.get("banned_packages") %}
- **Banned packages**: {{ lang.banned_packages | join(", ") }}
{% endif %}
{% endif %}
{% if resolved.get("style") %}

## Code Style
{% set style = resolved.style %}
{% set prov = provenance.get("style", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

| Setting | Value |
|---------|-------|
{% if style.get("naming_convention") %}
| Naming convention | `{{ style.naming_convention }}` |
{% endif %}
{% if style.get("indent_style") %}
| Indent style | {{ style.indent_style }} |
{% endif %}
{% if style.get("indent_size") %}
| Indent size | {{ style.indent_size }} |
{% endif %}
{% if style.get("max_line_length") %}
| Max line length | {{ style.max_line_length }} |
{% endif %}
{% if style.get("docstring_format") %}
| Docstring format | {{ style.docstring_format }} |
{% endif %}
{% if style.get("import_order") %}
| Import order | {{ style.import_order }} |
{% endif %}
{% if style.get("trailing_commas") is not none %}
| Trailing commas | {{ style.trailing_commas }} |
{% endif %}
{% if style.get("quote_style") %}
| Quote style | {{ style.quote_style }} |
{% endif %}

{% if style.get("formatter") %}
**Formatters:**
{% for k, v in style.formatter.items() %}
- {{ k }}: `{{ v }}`
{% endfor %}
{% endif %}

{% if style.get("linter") %}
**Linters:**
{% for k, v in style.linter.items() %}
- {{ k }}: `{{ v }}`
{% endfor %}
{% endif %}
{% endif %}
{% if resolved.get("testing") %}

## Testing Standards
{% set test = resolved.testing %}
{% set prov = provenance.get("testing", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

| Setting | Value |
|---------|-------|
{% if test.get("methodology") %}
| Methodology | {{ test.methodology }} |
{% endif %}
{% if test.get("min_coverage") %}
| Min coverage | {{ test.min_coverage }}% |
{% endif %}
{% if test.get("require_bdd") is not none %}
| BDD required | {{ test.require_bdd }} |
{% endif %}
{% if test.get("require_unit") is not none %}
| Unit tests required | {{ test.require_unit }} |
{% endif %}
{% if test.get("require_e2e") is not none %}
| E2E required | {{ test.require_e2e }} |
{% endif %}
{% if test.get("test_naming_pattern") %}
| Test naming | `{{ test.test_naming_pattern }}` |
{% endif %}
{% if test.get("flaky_test_policy") %}
| Flaky test policy | {{ test.flaky_test_policy }} |
{% endif %}

{% if test.get("bdd_framework") %}
**BDD frameworks:**
{% for k, v in test.bdd_framework.items() %}
- {{ k }}: `{{ v }}`
{% endfor %}
{% endif %}
{% endif %}
{% if resolved.get("architecture") %}

## Architecture
{% set arch = resolved.architecture %}
{% set prov = provenance.get("architecture", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

| Setting | Value |
|---------|-------|
{% if arch.get("database") %}
| Database | {{ arch.database }} |
{% endif %}
{% if arch.get("auth_pattern") %}
| Auth pattern | {{ arch.auth_pattern }} |
{% endif %}
{% if arch.get("api_style") %}
| API style | {{ arch.api_style }} |
{% endif %}
{% if arch.get("error_handling") %}
| Error handling | {{ arch.error_handling }} |
{% endif %}
{% if arch.get("logging_format") %}
| Logging format | {{ arch.logging_format }} |
{% endif %}
{% if arch.get("project_structure") %}
| Project structure | {{ arch.project_structure }} |
{% endif %}

{% if arch.get("web_framework") %}
**Web frameworks:**
{% for k, v in arch.web_framework.items() %}
- {{ k }}: `{{ v }}`
{% endfor %}
{% endif %}

{% if arch.get("orm") %}
**ORMs:**
{% for k, v in arch.orm.items() %}
- {{ k }}: `{{ v }}`
{% endfor %}
{% endif %}

{% if arch.get("container_base") %}
**Container base images:**
{% for k, v in arch.container_base.items() %}
- {{ k }}: `{{ v }}`
{% endfor %}
{% endif %}
{% endif %}
{% if resolved.get("security") %}

## Security
{% set sec = resolved.security %}
{% set prov = provenance.get("security", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

| Setting | Value |
|---------|-------|
{% if sec.get("encryption_standard") %}
| Encryption standard | {{ sec.encryption_standard }} |
{% endif %}
{% if sec.get("secret_management") %}
| Secret management | {{ sec.secret_management }} |
{% endif %}
{% if sec.get("dependency_audit") is not none %}
| Dependency audit | {{ sec.dependency_audit }} |
{% endif %}
{% if sec.get("container_hardening") %}
| Container hardening | {{ sec.container_hardening }} |
{% endif %}
{% if sec.get("secret_rotation_days") %}
| Secret rotation | Every {{ sec.secret_rotation_days }} days |
{% endif %}
{% if sec.get("image_signing") is not none %}
| Image signing | {{ sec.image_signing }} |
{% endif %}

{% if sec.get("vulnerability_sla") %}
**Vulnerability SLAs:**
{% for sev, deadline in sec.vulnerability_sla.items() %}
- {{ sev }}: {{ deadline }}
{% endfor %}
{% endif %}

{% if sec.get("sast_tools") %}
**SAST tools:**
{% for k, v in sec.sast_tools.items() %}
- {{ k }}: `{{ v }}`
{% endfor %}
{% endif %}
{% endif %}
{% if resolved.get("compliance") %}

## Compliance
{% set comp = resolved.compliance %}
{% set prov = provenance.get("compliance", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

| Setting | Value |
|---------|-------|
{% if comp.get("frameworks") %}
| Frameworks | {{ comp.frameworks | join(", ") }} |
{% endif %}
{% if comp.get("cui_required") is not none %}
| CUI required | {{ comp.cui_required }} |
{% endif %}
{% if comp.get("audit_trail_required") is not none %}
| Audit trail required | {{ comp.audit_trail_required }} |
{% endif %}
{% if comp.get("classification_level") %}
| Classification level | {{ comp.classification_level }} |
{% endif %}
{% if comp.get("sbom_format") %}
| SBOM format | {{ comp.sbom_format }} |
{% endif %}
{% if comp.get("ato_approach") %}
| ATO approach | {{ comp.ato_approach }} |
{% endif %}
{% endif %}
{% if resolved.get("operations") %}

## Operations
{% set ops = resolved.operations %}
{% set prov = provenance.get("operations", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

| Setting | Value |
|---------|-------|
{% if ops.get("deployment_target") %}
| Deployment target | {{ ops.deployment_target }} |
{% endif %}
{% if ops.get("container_runtime") %}
| Container runtime | {{ ops.container_runtime }} |
{% endif %}
{% if ops.get("ci_cd_platform") %}
| CI/CD platform | {{ ops.ci_cd_platform }} |
{% endif %}
{% if ops.get("region") %}
| Region | {{ ops.region }} |
{% endif %}
{% if ops.get("service_mesh") %}
| Service mesh | {{ ops.service_mesh }} |
{% endif %}
{% if ops.get("network_policy") %}
| Network policy | {{ ops.network_policy }} |
{% endif %}
{% if ops.get("backup_strategy") %}
| Backup strategy | {{ ops.backup_strategy }} |
{% endif %}
{% if ops.get("monitoring_stack") %}
| Monitoring | {{ ops.monitoring_stack | join(", ") }} |
{% endif %}
{% endif %}
{% if resolved.get("documentation") %}

## Documentation
{% set doc = resolved.documentation %}
{% set prov = provenance.get("documentation", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

| Requirement | Required |
|-------------|----------|
{% if doc.get("readme_required") is not none %}
| README | {{ doc.readme_required }} |
{% endif %}
{% if doc.get("api_docs_required") is not none %}
| API docs | {{ doc.api_docs_required }} |
{% endif %}
{% if doc.get("adr_required") is not none %}
| ADRs | {{ doc.adr_required }} |
{% endif %}
{% if doc.get("changelog_required") is not none %}
| Changelog | {{ doc.changelog_required }} |
{% endif %}
{% if doc.get("runbook_required") is not none %}
| Runbooks | {{ doc.runbook_required }} |
{% endif %}
{% if doc.get("diagram_tool") %}
| Diagram tool | {{ doc.diagram_tool }} |
{% endif %}
{% if doc.get("spec_format") %}
| Spec format | {{ doc.spec_format }} |
{% endif %}
{% endif %}
{% if resolved.get("git") %}

## Git Workflow
{% set git = resolved.git %}
{% set prov = provenance.get("git", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

| Setting | Value |
|---------|-------|
{% if git.get("branching_strategy") %}
| Branching strategy | {{ git.branching_strategy }} |
{% endif %}
{% if git.get("merge_strategy") %}
| Merge strategy | {{ git.merge_strategy }} |
{% endif %}
{% if git.get("commit_format") %}
| Commit format | {{ git.commit_format }} |
{% endif %}
{% if git.get("branch_naming") %}
| Branch naming | `{{ git.branch_naming }}` |
{% endif %}
{% if git.get("min_approvals") %}
| Min approvals | {{ git.min_approvals }} |
{% endif %}
{% if git.get("gpg_signing") %}
| GPG signing | {{ git.gpg_signing }} |
{% endif %}
{% if git.get("pr_size_limit") %}
| PR size limit | {{ git.pr_size_limit }} lines |
{% endif %}
{% if git.get("delete_branch_on_merge") is not none %}
| Delete branch on merge | {{ git.delete_branch_on_merge }} |
{% endif %}
{% endif %}
{% if resolved.get("ai") %}

## AI / LLM Usage
{% set ai = resolved.ai %}
{% set prov = provenance.get("ai", {}) %}
_Source: {{ prov.get("source_scope", "—") }} | Enforcement: {{ prov.get("enforcement", "advisory") }}{{ " | LOCKED" if prov.get("locked") else "" }}_

| Setting | Value |
|---------|-------|
{% if ai.get("preferred_provider") %}
| Preferred provider | {{ ai.preferred_provider }} |
{% endif %}
{% if ai.get("code_gen_model") %}
| Code gen model | {{ ai.code_gen_model }} |
{% endif %}
{% if ai.get("compliance_model") %}
| Compliance model | {{ ai.compliance_model }} |
{% endif %}
{% if ai.get("token_budget_monthly") %}
| Monthly token budget | {{ ai.token_budget_monthly | int }} |
{% endif %}
{% if ai.get("attribution") %}
| Attribution | {{ ai.attribution }} |
{% endif %}
{% if ai.get("byok_enabled") is not none %}
| BYOK enabled | {{ ai.byok_enabled }} |
{% endif %}
{% if ai.get("prompt_governance") %}
| Prompt governance | {{ ai.prompt_governance }} |
{% endif %}
{% endif %}

---

> This PROFILE.md was auto-generated from the development profile cascade.
> To update standards, modify the profile at the appropriate scope layer
> using `python tools/builder/dev_profile_manager.py`.
"""


# ── Fallback Generator (no Jinja2) ──────────────────────────────────

def _fallback_generate(resolved_profile):
    """Generate PROFILE.md without Jinja2 using string formatting."""
    resolved = resolved_profile.get("resolved", {})
    provenance = resolved_profile.get("provenance", {})
    locks = resolved_profile.get("locks", [])
    ancestry = resolved_profile.get("ancestry", [])
    scope = resolved_profile.get("scope", "unknown")
    scope_id = resolved_profile.get("scope_id", "unknown")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# PROFILE.md",
        "",
        f"> **Auto-generated** from tenant development profile cascade.",
        f"> Do not edit directly — update the profile via `dev_profile_manager.py`.",
        f"> Generated: {now}",
        "",
        "---",
        "",
        "## Overview",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Scope | {scope} |",
        f"| Scope ID | {scope_id} |",
        f"| Cascade layers | {len(ancestry)} |",
        f"| Locked dimensions | {len(locks)} |",
        f"| Total dimensions | {len(resolved)} |",
        "",
        "### Cascade Ancestry",
        "",
    ]

    for i, anc in enumerate(ancestry, 1):
        lines.append(f"{i}. **{anc['scope']}** → `{anc['scope_id']}`")

    if locks:
        lines.extend([
            "",
            "### Locked Dimensions",
            "",
            "| Dimension | Status |",
            "|-----------|--------|",
        ])
        for lock in locks:
            lines.append(f"| {lock} | LOCKED |")

    lines.extend(["", "---", ""])

    # Render each dimension generically
    for dim_name, dim_data in resolved.items():
        prov = provenance.get(dim_name, {})
        source = prov.get("source_scope", "—")
        enforcement = prov.get("enforcement", "advisory")
        locked = prov.get("locked", False)

        title = dim_name.replace("_", " ").title()
        lines.append(f"## {title}")
        lines.append(f"_Source: {source} | Enforcement: {enforcement}"
                      + (" | LOCKED" if locked else "") + "_")
        lines.append("")

        if isinstance(dim_data, dict):
            # Render as table for flat values, nested as sub-sections
            flat_items = []
            nested_items = []
            for k, v in dim_data.items():
                if isinstance(v, dict):
                    nested_items.append((k, v))
                elif isinstance(v, list):
                    flat_items.append((k, ", ".join(str(i) for i in v)))
                else:
                    flat_items.append((k, str(v)))

            if flat_items:
                lines.append("| Setting | Value |")
                lines.append("|---------|-------|")
                for k, v in flat_items:
                    lines.append(f"| {k} | {v} |")
                lines.append("")

            for k, v in nested_items:
                lines.append(f"**{k}:**")
                for nk, nv in v.items():
                    lines.append(f"- {nk}: `{nv}`")
                lines.append("")
        else:
            lines.append(f"- {dim_data}")
            lines.append("")

    lines.extend([
        "---",
        "",
        "> This PROFILE.md was auto-generated from the development profile cascade.",
        "> To update standards, modify the profile at the appropriate scope layer",
        "> using `python tools/builder/dev_profile_manager.py`.",
    ])

    return "\n".join(lines)


# ── Main Generator ───────────────────────────────────────────────────

def generate_profile_md(resolved_profile):
    """Generate PROFILE.md content from a resolved profile dict.

    Args:
        resolved_profile: Output from dev_profile_manager.resolve_profile().
            Expected keys: resolved, provenance, locks, ancestry, scope, scope_id.

    Returns:
        str: Rendered PROFILE.md content.
    """
    if not resolved_profile or "error" in resolved_profile:
        return f"# PROFILE.md\n\n> Error: {resolved_profile.get('error', 'No profile data')}\n"

    if _HAS_JINJA2:
        env = Environment(loader=BaseLoader())  # nosec B701 — generates Markdown, not HTML
        template = env.from_string(PROFILE_MD_TEMPLATE)
        rendered = template.render(
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            scope=resolved_profile.get("scope", "unknown"),
            scope_id=resolved_profile.get("scope_id", "unknown"),
            ancestry=resolved_profile.get("ancestry", []),
            locks=resolved_profile.get("locks", []),
            resolved=resolved_profile.get("resolved", {}),
            provenance=resolved_profile.get("provenance", {}),
        )
        return rendered
    else:
        return _fallback_generate(resolved_profile)


def generate_and_store(scope, scope_id, db_path=None):
    """Resolve profile, generate PROFILE.md, and store it back in the DB.

    Returns dict with status, profile_md content, and metadata.
    """
    try:
        from tools.builder.dev_profile_manager import resolve_profile, _get_connection
    except ImportError:
        sys.path.insert(0, str(BASE_DIR))
        from tools.builder.dev_profile_manager import resolve_profile, _get_connection

    result = resolve_profile(scope, scope_id, db_path=db_path)
    if "error" in result:
        return result

    profile_md = generate_profile_md(result)

    # Store in the active profile's profile_md column
    conn = _get_connection(db_path)
    try:
        conn.execute(
            """UPDATE dev_profiles SET profile_md = ?
               WHERE scope = ? AND scope_id = ? AND is_active = 1""",
            (profile_md, scope, scope_id),
        )
        conn.commit()
    finally:
        conn.close()

    content_hash = hashlib.sha256(profile_md.encode()).hexdigest()[:12]

    return {
        "status": "generated",
        "scope": scope,
        "scope_id": scope_id,
        "profile_md": profile_md,
        "content_hash": content_hash,
        "dimensions_count": len(result.get("resolved", {})),
        "locks_count": len(result.get("locks", [])),
        "ancestry_depth": len(result.get("ancestry", [])),
        "engine": "jinja2" if _HAS_JINJA2 else "fallback",
    }


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate PROFILE.md from resolved dev profile (D186)"
    )
    parser.add_argument("--scope", default="project",
                        choices=("platform", "tenant", "program", "project", "user"),
                        help="Profile scope (default: project)")
    parser.add_argument("--scope-id", required=True, help="Scope entity ID")
    parser.add_argument("--output", type=Path,
                        help="Write PROFILE.md to file path")
    parser.add_argument("--db-path", type=Path, help="Database path override")
    parser.add_argument("--json", action="store_true", help="JSON envelope output")
    parser.add_argument("--store", action="store_true", default=True,
                        help="Store generated PROFILE.md in DB (default: true)")
    parser.add_argument("--no-store", action="store_true",
                        help="Do not store in DB, only output")

    args = parser.parse_args()
    db = str(args.db_path) if args.db_path else None

    if args.no_store:
        # Resolve and render only, no DB write
        try:
            from tools.builder.dev_profile_manager import resolve_profile
        except ImportError:
            sys.path.insert(0, str(BASE_DIR))
            from tools.builder.dev_profile_manager import resolve_profile

        result = resolve_profile(args.scope, args.scope_id, db_path=db)
        if "error" in result:
            print(json.dumps(result, indent=2))
            sys.exit(1)

        profile_md = generate_profile_md(result)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(profile_md, encoding="utf-8")
            if args.json:
                print(json.dumps({
                    "status": "written",
                    "path": str(args.output),
                    "content_hash": hashlib.sha256(profile_md.encode()).hexdigest()[:12],
                }, indent=2))
            else:
                print(f"PROFILE.md written to {args.output}")
        else:
            if args.json:
                print(json.dumps({
                    "status": "generated",
                    "profile_md": profile_md,
                }, indent=2))
            else:
                print(profile_md)
    else:
        # Full pipeline: resolve, generate, store
        result = generate_and_store(args.scope, args.scope_id, db_path=db)

        if args.output and "profile_md" in result:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result["profile_md"], encoding="utf-8")
            result["output_path"] = str(args.output)

        if args.json:
            # Don't dump full markdown in JSON envelope — just metadata
            output = {k: v for k, v in result.items() if k != "profile_md"}
            output["profile_md_length"] = len(result.get("profile_md", ""))
            print(json.dumps(output, indent=2))
        else:
            if "error" in result:
                print(f"Error: {result['error']}")
                sys.exit(1)
            if args.output:
                print(f"PROFILE.md written to {args.output}")
            else:
                print(result.get("profile_md", ""))


if __name__ == "__main__":
    main()
