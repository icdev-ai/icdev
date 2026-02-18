#!/usr/bin/env python3
"""
CUI // SP-CTI
================================================================================
ICDEV Framework Migration Tool
Transforms legacy framework code to modern equivalents for DoD modernization.
Supports: Struts->Spring Boot, EJB->Spring, WCF->ASP.NET Core gRPC,
          WebForms->Razor Pages, Django 1.x->4.x, Flask 0.x->3.x
================================================================================
CUI // SP-CTI
"""

import re
import os
import sys
import json
import shutil
import argparse
import textwrap
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, OrderedDict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
PATTERNS_FILE = BASE_DIR / "context" / "modernization" / "framework_migration_patterns.json"

MIGRATION_MAP = {
    ("struts", "spring-boot"): "migrate_struts_to_spring",
    ("ejb", "spring"): "migrate_ejb_to_spring",
    ("wcf", "aspnet-core-grpc"): "migrate_wcf_to_aspnet_core",
    ("webforms", "razor"): "migrate_webforms_to_razor",
    ("django-1", "django-4"): "migrate_django_version",
    ("flask-0", "flask-3"): "migrate_flask_version",
}

CUI_BANNER = "CUI // SP-CTI"


# ---------------------------------------------------------------------------
# Pattern loading
# ---------------------------------------------------------------------------
def load_framework_patterns(source_framework, target_framework):
    """Load migration patterns from the context JSON file.

    Finds the matching migration path for the given source and target
    frameworks.  Returns a dict with component_mappings, config_mappings,
    and migration_steps.  Returns None when no match is found or the
    patterns file is missing.
    """
    if not PATTERNS_FILE.exists():
        print(f"[WARN] Patterns file not found: {PATTERNS_FILE}", file=sys.stderr)
        return None

    with open(PATTERNS_FILE, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    source_lower = source_framework.lower()
    target_lower = target_framework.lower()

    for pattern in data.get("migration_patterns", []):
        src = pattern.get("source_framework", "").lower()
        tgt = pattern.get("target_framework", "").lower()

        # Flexible matching: allow partial / alias matches
        src_match = (
            source_lower in src
            or src in source_lower
            or source_lower.replace("-", " ") in src
        )
        tgt_match = (
            target_lower in tgt
            or tgt in target_lower
            or target_lower.replace("-", " ") in tgt
        )

        if src_match and tgt_match:
            return {
                "id": pattern.get("id"),
                "source_framework": pattern.get("source_framework"),
                "target_framework": pattern.get("target_framework"),
                "target_version": pattern.get("target_version"),
                "component_mappings": pattern.get("component_mappings", []),
                "config_mappings": pattern.get("config_mappings", []),
                "migration_steps": pattern.get("migration_steps", []),
                "breaking_changes": pattern.get("breaking_changes", []),
                "estimated_effort_factor": pattern.get("estimated_effort_factor", 1.0),
            }

    print(
        f"[WARN] No migration pattern found for {source_framework} -> {target_framework}",
        file=sys.stderr,
    )
    return None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _copy_source_tree(source_path, output_path):
    """Copy the entire source tree into the output directory.

    If output_path already exists it is removed first so the migration
    always starts from a clean copy of the source.
    """
    source = Path(source_path)
    output = Path(output_path)

    if output.exists():
        shutil.rmtree(output)

    shutil.copytree(source, output)
    return output


def _apply_file_transforms(file_path, transforms):
    """Apply a list of regex transforms to a single file.

    Each transform is a dict with:
        pattern     - regex pattern string
        replacement - replacement string (may contain back-references)
        flags       - optional list of flag names (e.g. ["MULTILINE", "DOTALL"])

    Returns the total number of substitutions made across all transforms.
    """
    fp = Path(file_path)
    if not fp.exists() or not fp.is_file():
        return 0

    try:
        content = fp.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return 0

    total_changes = 0
    new_content = content

    for t in transforms:
        pattern = t.get("pattern", "")
        replacement = t.get("replacement", "")
        flag_names = t.get("flags", [])

        # Build regex flags
        flags = 0
        for fn in flag_names:
            flags |= getattr(re, fn.upper(), 0)

        try:
            result, count = re.subn(pattern, replacement, new_content, flags=flags)
            if count > 0:
                new_content = result
                total_changes += count
        except re.error as exc:
            print(f"[WARN] Regex error in transform for {fp}: {exc}", file=sys.stderr)

    if total_changes > 0:
        fp.write_text(new_content, encoding="utf-8")

    return total_changes


def _collect_files(root, extensions):
    """Recursively collect files matching any of the given extensions."""
    root = Path(root)
    results = []
    for ext in extensions:
        results.extend(sorted(root.rglob(f"*{ext}")))
    return results


def _add_todo_comment(content, comment_prefix, message):
    """Insert a TODO comment at the top of file content."""
    return f"{comment_prefix} TODO [ICDEV-MIGRATION]: {message}\n{content}"


# ---------------------------------------------------------------------------
# Migration report
# ---------------------------------------------------------------------------
def _generate_migration_report(source_path, output_path, transformations):
    """Write a migration_report.md into the output directory.

    The report contains: overall summary, files changed, transformations
    per file, and manual review items (TODO comments that were added).
    """
    output = Path(output_path)
    report_lines = [
        CUI_BANNER,
        "",
        "# Framework Migration Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Source:** `{source_path}`",
        f"**Output:** `{output_path}`",
        "",
        "---",
        "",
        "## Summary",
        "",
    ]

    total_files = transformations.get("files_changed", 0)
    total_transforms = transformations.get("total_transformations", 0)
    source_fw = transformations.get("source_framework", "unknown")
    target_fw = transformations.get("target_framework", "unknown")
    generated_files = transformations.get("generated_files", [])

    report_lines.append(f"| Metric | Value |")
    report_lines.append(f"|--------|-------|")
    report_lines.append(f"| Source framework | {source_fw} |")
    report_lines.append(f"| Target framework | {target_fw} |")
    report_lines.append(f"| Files modified | {total_files} |")
    report_lines.append(f"| Total transformations | {total_transforms} |")
    report_lines.append(f"| Files generated | {len(generated_files)} |")
    report_lines.append("")

    # Per-file breakdown
    file_details = transformations.get("file_details", {})
    if file_details:
        report_lines.append("## Files Changed")
        report_lines.append("")
        for fpath, detail in sorted(file_details.items()):
            count = detail.get("changes", 0)
            desc = detail.get("description", "")
            report_lines.append(f"- **`{fpath}`** — {count} transformation(s)")
            if desc:
                report_lines.append(f"  - {desc}")
        report_lines.append("")

    # Generated files
    if generated_files:
        report_lines.append("## Generated Files")
        report_lines.append("")
        for gf in generated_files:
            report_lines.append(f"- `{gf}`")
        report_lines.append("")

    # Manual review items (scan for TODO comments)
    report_lines.append("## Manual Review Items")
    report_lines.append("")
    todo_items = []
    for fp in sorted(output.rglob("*")):
        if fp.is_file() and fp.suffix in (
            ".java", ".py", ".cs", ".cshtml", ".xml", ".json",
            ".yaml", ".yml", ".properties", ".proto", ".config",
        ):
            try:
                text = fp.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if "TODO [ICDEV-MIGRATION]" in line:
                    rel = fp.relative_to(output)
                    todo_items.append((str(rel), i, line.strip()))

    if todo_items:
        for rel, lineno, text in todo_items:
            report_lines.append(f"- `{rel}` (line {lineno}): {text}")
    else:
        report_lines.append("_No manual review items found._")

    report_lines.append("")
    report_lines.append("---")
    report_lines.append("")
    report_lines.append(CUI_BANNER)
    report_lines.append("")

    report_path = output / "migration_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    return str(report_path)


# ---------------------------------------------------------------------------
# 1. Struts -> Spring Boot
# ---------------------------------------------------------------------------
def migrate_struts_to_spring(source_path, output_path):
    """Migrate Java Struts application to Spring Boot.

    Transforms Action classes to @Controller classes, ActionForm beans to
    DTOs, struts-config.xml mappings to annotations, and generates Spring
    Boot scaffolding files.
    """
    output = _copy_source_tree(source_path, output_path)
    transformations = {
        "source_framework": "Apache Struts",
        "target_framework": "Spring Boot",
        "files_changed": 0,
        "total_transformations": 0,
        "file_details": {},
        "generated_files": [],
    }

    java_files = _collect_files(output, [".java"])
    xml_files = _collect_files(output, [".xml"])

    # --- Transform Action classes to @Controller ---
    action_transforms = [
        {
            "pattern": r"import\s+org\.apache\.struts\.action\.Action\s*;",
            "replacement": "import org.springframework.stereotype.Controller;",
            "flags": [],
        },
        {
            "pattern": r"import\s+org\.apache\.struts\.action\.ActionForm\s*;",
            "replacement": "// ActionForm removed — use DTO/POJO instead",
            "flags": [],
        },
        {
            "pattern": r"import\s+org\.apache\.struts\.action\.ActionForward\s*;",
            "replacement": "// ActionForward removed — return view name String",
            "flags": [],
        },
        {
            "pattern": r"import\s+org\.apache\.struts\.action\.ActionMapping\s*;",
            "replacement": "import org.springframework.web.bind.annotation.RequestMapping;",
            "flags": [],
        },
        {
            "pattern": r"import\s+org\.apache\.struts\.action\.\*\s*;",
            "replacement": (
                "import org.springframework.stereotype.Controller;\n"
                "import org.springframework.web.bind.annotation.RequestMapping;\n"
                "import org.springframework.web.bind.annotation.RequestMethod;"
            ),
            "flags": [],
        },
        {
            "pattern": r"extends\s+Action\b",
            "replacement": "/* @Controller migrated from Struts Action */",
            "flags": [],
        },
        {
            "pattern": (
                r"public\s+ActionForward\s+execute\s*\(\s*"
                r"ActionMapping\s+\w+\s*,\s*"
                r"ActionForm\s+\w+\s*,\s*"
                r"HttpServletRequest\s+(\w+)\s*,\s*"
                r"HttpServletResponse\s+(\w+)\s*\)"
            ),
            "replacement": (
                r"@RequestMapping(method = RequestMethod.GET)"
                r"\n    public String handleRequest(HttpServletRequest \1, HttpServletResponse \2)"
            ),
            "flags": ["MULTILINE"],
        },
        {
            "pattern": r"return\s+mapping\.findForward\(\s*\"(\w+)\"\s*\)\s*;",
            "replacement": r'return "\1";',
            "flags": [],
        },
    ]

    # Add @Controller annotation to classes that extended Action
    controller_annotation_transform = {
        "pattern": r"(public\s+class\s+\w+)\s*/\*\s*@Controller migrated from Struts Action\s*\*/",
        "replacement": r"@Controller\n\1",
        "flags": [],
    }

    for jf in java_files:
        changes = _apply_file_transforms(jf, action_transforms)
        if changes > 0:
            # Apply controller annotation fixup in a second pass
            changes += _apply_file_transforms(jf, [controller_annotation_transform])
            rel = jf.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": changes,
                "description": "Struts Action -> Spring @Controller",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += changes

    # --- Transform ActionForm subclasses to DTOs ---
    form_transforms = [
        {
            "pattern": r"extends\s+ActionForm\b",
            "replacement": "/* DTO - migrated from ActionForm */",
            "flags": [],
        },
        {
            "pattern": r"import\s+org\.apache\.struts\.action\.ActionForm\s*;",
            "replacement": "// TODO [ICDEV-MIGRATION]: ActionForm removed, validate DTO fields manually",
            "flags": [],
        },
    ]

    for jf in java_files:
        try:
            content = jf.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        if "ActionForm" in content:
            changes = _apply_file_transforms(jf, form_transforms)
            if changes > 0:
                rel = jf.relative_to(output)
                entry = transformations["file_details"].get(str(rel), {"changes": 0, "description": ""})
                entry["changes"] += changes
                if entry["description"]:
                    entry["description"] += "; ActionForm -> DTO"
                else:
                    entry["description"] = "ActionForm -> DTO"
                    transformations["files_changed"] += 1
                transformations["file_details"][str(rel)] = entry
                transformations["total_transformations"] += changes

    # --- Transform struts-config.xml ---
    for xf in xml_files:
        if xf.name == "struts-config.xml":
            try:
                content = xf.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue
            # Extract action-mappings for documentation
            action_paths = re.findall(r'<action\s+path="([^"]*)"', content)
            form_beans = re.findall(r'<form-bean\s+name="([^"]*)"', content)

            # Add TODO header
            header = (
                "// TODO [ICDEV-MIGRATION]: This struts-config.xml has been superseded.\n"
                "// Action paths migrated to @RequestMapping annotations.\n"
                "// Form beans migrated to DTO classes.\n"
                f"// Action paths found: {', '.join(action_paths)}\n"
                f"// Form beans found: {', '.join(form_beans)}\n"
            )
            xf.write_text(header + content, encoding="utf-8")
            rel = xf.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": 1,
                "description": "Marked struts-config.xml as superseded",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += 1

    # --- Generate Spring Boot Application class ---
    src_main_java = output / "src" / "main" / "java"
    if not src_main_java.exists():
        src_main_java = output

    # Find a package directory
    package_name = "com.dod.app"
    for jf in java_files:
        try:
            content = jf.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        match = re.search(r"package\s+([\w.]+)\s*;", content)
        if match:
            package_name = match.group(1)
            break

    app_class = textwrap.dedent(f"""\
        // {CUI_BANNER}
        package {package_name};

        import org.springframework.boot.SpringApplication;
        import org.springframework.boot.autoconfigure.SpringBootApplication;

        /**
         * Spring Boot Application entry point.
         * Generated by ICDEV Framework Migrator.
         */
        @SpringBootApplication
        public class Application {{

            public static void main(String[] args) {{
                SpringApplication.run(Application.class, args);
            }}
        }}
        // {CUI_BANNER}
    """)

    app_path = src_main_java / "Application.java"
    app_path.write_text(app_class, encoding="utf-8")
    transformations["generated_files"].append(str(app_path.relative_to(output)))

    # --- Generate application.properties ---
    props_content = textwrap.dedent(f"""\
        # {CUI_BANNER}
        # Spring Boot Application Properties
        # Generated by ICDEV Framework Migrator
        server.port=8080
        spring.application.name=migrated-struts-app

        # TODO [ICDEV-MIGRATION]: Configure datasource
        # spring.datasource.url=jdbc:postgresql://localhost:5432/appdb
        # spring.datasource.username=${{DB_USERNAME}}
        # spring.datasource.password=${{DB_PASSWORD}}

        # TODO [ICDEV-MIGRATION]: Configure view resolver
        spring.mvc.view.prefix=/WEB-INF/views/
        spring.mvc.view.suffix=.jsp

        # Actuator for health checks
        management.endpoints.web.exposure.include=health,info
        # {CUI_BANNER}
    """)

    resources_dir = output / "src" / "main" / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    props_path = resources_dir / "application.properties"
    props_path.write_text(props_content, encoding="utf-8")
    transformations["generated_files"].append(str(props_path.relative_to(output)))

    return transformations


# ---------------------------------------------------------------------------
# 2. EJB -> Spring
# ---------------------------------------------------------------------------
def migrate_ejb_to_spring(source_path, output_path):
    """Migrate Java EJB application to Spring Boot.

    Transforms EJB session beans to Spring @Service components, replaces
    JNDI lookups with @Autowired injection, and converts deployment
    descriptors to Spring configuration.
    """
    output = _copy_source_tree(source_path, output_path)
    transformations = {
        "source_framework": "EJB (Enterprise JavaBeans)",
        "target_framework": "Spring Boot",
        "files_changed": 0,
        "total_transformations": 0,
        "file_details": {},
        "generated_files": [],
    }

    java_files = _collect_files(output, [".java"])
    xml_files = _collect_files(output, [".xml"])

    # --- Core EJB annotation transforms ---
    ejb_transforms = [
        {
            "pattern": r"import\s+javax\.ejb\.Stateless\s*;",
            "replacement": "import org.springframework.stereotype.Service;",
            "flags": [],
        },
        {
            "pattern": r"@Stateless\b(?:\s*\([^)]*\))?",
            "replacement": "@Service",
            "flags": [],
        },
        {
            "pattern": r"import\s+javax\.ejb\.Stateful\s*;",
            "replacement": (
                "import org.springframework.stereotype.Service;\n"
                "import org.springframework.context.annotation.Scope;"
            ),
            "flags": [],
        },
        {
            "pattern": r"@Stateful\b(?:\s*\([^)]*\))?",
            "replacement": '@Service\n@Scope("session")',
            "flags": [],
        },
        {
            "pattern": r"import\s+javax\.ejb\.EJB\s*;",
            "replacement": "import org.springframework.beans.factory.annotation.Autowired;",
            "flags": [],
        },
        {
            "pattern": r"@EJB\b(?:\s*\([^)]*\))?",
            "replacement": "@Autowired",
            "flags": [],
        },
        {
            "pattern": r"import\s+javax\.ejb\.Local\s*;",
            "replacement": "// @Local removed — Spring uses interface-based DI directly",
            "flags": [],
        },
        {
            "pattern": r"@Local\b(?:\s*\([^)]*\))?",
            "replacement": "// TODO [ICDEV-MIGRATION]: @Local removed, Spring injects by interface type",
            "flags": [],
        },
        {
            "pattern": r"import\s+javax\.ejb\.Remote\s*;",
            "replacement": "// @Remote removed — expose via REST/gRPC if remote access needed",
            "flags": [],
        },
        {
            "pattern": r"@Remote\b(?:\s*\([^)]*\))?",
            "replacement": "// TODO [ICDEV-MIGRATION]: @Remote removed, expose as REST endpoint if needed",
            "flags": [],
        },
        # JNDI lookup replacement
        {
            "pattern": r"new\s+InitialContext\(\)\s*\.\s*lookup\(\s*\"[^\"]*\"\s*\)",
            "replacement": "// TODO [ICDEV-MIGRATION]: Replace JNDI lookup with @Autowired injection",
            "flags": [],
        },
        {
            "pattern": r"import\s+javax\.naming\.InitialContext\s*;",
            "replacement": "// InitialContext removed — use Spring @Autowired",
            "flags": [],
        },
        {
            "pattern": r"import\s+javax\.naming\.NamingException\s*;",
            "replacement": "// NamingException removed — no JNDI in Spring Boot",
            "flags": [],
        },
    ]

    for jf in java_files:
        changes = _apply_file_transforms(jf, ejb_transforms)
        if changes > 0:
            rel = jf.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": changes,
                "description": "EJB annotations/JNDI -> Spring @Service/@Autowired",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += changes

    # --- Process ejb-jar.xml ---
    for xf in xml_files:
        if xf.name == "ejb-jar.xml":
            try:
                content = xf.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue

            # Extract bean class names for Spring @Bean config
            bean_classes = re.findall(r"<ejb-class>([^<]+)</ejb-class>", content)
            bean_names = re.findall(r"<ejb-name>([^<]+)</ejb-name>", content)

            # Generate Spring configuration class
            config_lines = [
                f"// {CUI_BANNER}",
                "package com.dod.config;",
                "",
                "import org.springframework.context.annotation.Configuration;",
                "import org.springframework.context.annotation.Bean;",
                "",
                "/**",
                " * Spring configuration generated from ejb-jar.xml.",
                " * Generated by ICDEV Framework Migrator.",
                " */",
                "@Configuration",
                "public class EjbMigrationConfig {",
                "",
            ]

            for name, cls in zip(bean_names, bean_classes):
                simple_name = cls.rsplit(".", 1)[-1] if "." in cls else cls
                config_lines.append(f"    // TODO [ICDEV-MIGRATION]: Verify bean: {name} -> {cls}")
                config_lines.append(f"    // @Bean")
                config_lines.append(f"    // public {simple_name} {name}() {{")
                config_lines.append(f"    //     return new {simple_name}();")
                config_lines.append(f"    // }}")
                config_lines.append("")

            config_lines.append("}")
            config_lines.append(f"// {CUI_BANNER}")

            config_path = output / "EjbMigrationConfig.java"
            config_path.write_text("\n".join(config_lines), encoding="utf-8")
            transformations["generated_files"].append(str(config_path.relative_to(output)))

            # Mark ejb-jar.xml as superseded
            header = "<!-- TODO [ICDEV-MIGRATION]: This ejb-jar.xml has been superseded by Spring config -->\n"
            xf.write_text(header + content, encoding="utf-8")
            rel = xf.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": 1,
                "description": "Scanned for bean definitions; generated Spring @Bean config",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += 1

    return transformations


# ---------------------------------------------------------------------------
# 3. WCF -> ASP.NET Core gRPC/REST
# ---------------------------------------------------------------------------
def migrate_wcf_to_aspnet_core(source_path, output_path):
    """Migrate C# WCF services to ASP.NET Core gRPC/REST.

    Transforms [ServiceContract]/[OperationContract] to [ApiController]
    with action methods, converts data contracts to plain DTOs, generates
    .proto skeleton for gRPC alternative, and creates Program.cs scaffolding.
    """
    output = _copy_source_tree(source_path, output_path)
    transformations = {
        "source_framework": "WCF",
        "target_framework": "ASP.NET Core gRPC/REST",
        "files_changed": 0,
        "total_transformations": 0,
        "file_details": {},
        "generated_files": [],
    }

    cs_files = _collect_files(output, [".cs"])
    config_files = _collect_files(output, [".config"])

    # --- Service and Operation contract transforms ---
    wcf_transforms = [
        {
            "pattern": r"\[ServiceContract\b[^\]]*\]",
            "replacement": "[ApiController]\n[Route(\"api/[controller]\")]",
            "flags": [],
        },
        {
            "pattern": r"\[OperationContract\b[^\]]*\]",
            "replacement": "// TODO [ICDEV-MIGRATION]: Assign correct HTTP verb\n    [HttpPost]",
            "flags": [],
        },
        {
            "pattern": r"\[DataContract\b[^\]]*\]",
            "replacement": "// DataContract removed — plain DTO",
            "flags": [],
        },
        {
            "pattern": r"\[DataMember\b[^\]]*\]",
            "replacement": "// DataMember removed — plain property",
            "flags": [],
        },
        {
            "pattern": r"using\s+System\.ServiceModel\s*;",
            "replacement": (
                "using Microsoft.AspNetCore.Mvc;\n"
                "// TODO [ICDEV-MIGRATION]: System.ServiceModel removed"
            ),
            "flags": [],
        },
        {
            "pattern": r"using\s+System\.Runtime\.Serialization\s*;",
            "replacement": "// System.Runtime.Serialization removed — using plain DTOs",
            "flags": [],
        },
        # ServiceHost instantiation
        {
            "pattern": r"new\s+ServiceHost\s*\([^)]*\)",
            "replacement": "// TODO [ICDEV-MIGRATION]: ServiceHost removed — use Kestrel in Program.cs",
            "flags": [],
        },
    ]

    # Track service/operation names for proto generation
    service_names = []
    operation_names = defaultdict(list)

    for csf in cs_files:
        try:
            content = csf.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        # Collect service interface names
        svc_matches = re.findall(
            r"\[ServiceContract[^\]]*\]\s*(?:public\s+)?interface\s+(\w+)", content
        )
        for svc in svc_matches:
            service_names.append(svc)
            ops = re.findall(
                r"\[OperationContract[^\]]*\]\s*\w+\s+(\w+)\s*\(", content
            )
            operation_names[svc].extend(ops)

        changes = _apply_file_transforms(csf, wcf_transforms)
        if changes > 0:
            rel = csf.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": changes,
                "description": "WCF contracts -> ASP.NET Core ApiController/DTO",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += changes

    # --- Transform config files ---
    for cf in config_files:
        try:
            content = cf.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        if "system.serviceModel" in content:
            header = (
                "<!-- TODO [ICDEV-MIGRATION]: WCF <system.serviceModel> configuration "
                "has been superseded by ASP.NET Core Kestrel + appsettings.json -->\n"
            )
            cf.write_text(header + content, encoding="utf-8")
            rel = cf.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": 1,
                "description": "Marked WCF system.serviceModel config as superseded",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += 1

    # --- Generate .proto skeleton ---
    proto_lines = [
        f"// {CUI_BANNER}",
        'syntax = "proto3";',
        "",
        'option csharp_namespace = "Dod.App.Grpc";',
        "",
        "// Generated by ICDEV Framework Migrator",
        "// TODO [ICDEV-MIGRATION]: Review and finalize proto definitions",
        "",
    ]
    for svc in service_names:
        clean_name = svc.lstrip("I")  # Remove interface I prefix
        proto_lines.append(f"service {clean_name} {{")
        for op in operation_names.get(svc, []):
            proto_lines.append(f"  rpc {op} ({op}Request) returns ({op}Response);")
        proto_lines.append("}")
        proto_lines.append("")
        # Generate message stubs
        for op in operation_names.get(svc, []):
            proto_lines.append(f"message {op}Request {{")
            proto_lines.append("  // TODO [ICDEV-MIGRATION]: Define request fields")
            proto_lines.append("}")
            proto_lines.append("")
            proto_lines.append(f"message {op}Response {{")
            proto_lines.append("  // TODO [ICDEV-MIGRATION]: Define response fields")
            proto_lines.append("}")
            proto_lines.append("")

    proto_lines.append(f"// {CUI_BANNER}")
    proto_path = output / "Protos" / "services.proto"
    proto_path.parent.mkdir(parents=True, exist_ok=True)
    proto_path.write_text("\n".join(proto_lines), encoding="utf-8")
    transformations["generated_files"].append(str(proto_path.relative_to(output)))

    # --- Generate Program.cs skeleton ---
    program_cs = textwrap.dedent(f"""\
        // {CUI_BANNER}
        // ASP.NET Core Program.cs — Generated by ICDEV Framework Migrator
        using Microsoft.AspNetCore.Builder;
        using Microsoft.Extensions.DependencyInjection;

        var builder = WebApplication.CreateBuilder(args);

        // Add services
        builder.Services.AddControllers();
        builder.Services.AddGrpc();
        // TODO [ICDEV-MIGRATION]: Register application services here

        var app = builder.Build();

        app.UseRouting();
        app.UseAuthorization();
        app.MapControllers();
        // TODO [ICDEV-MIGRATION]: Map gRPC services here
        // app.MapGrpcService<MyService>();

        app.Run();
        // {CUI_BANNER}
    """)
    program_path = output / "Program.cs"
    program_path.write_text(program_cs, encoding="utf-8")
    transformations["generated_files"].append(str(program_path.relative_to(output)))

    # --- Generate appsettings.json skeleton ---
    appsettings = {
        "_classification": CUI_BANNER,
        "Logging": {"LogLevel": {"Default": "Information"}},
        "Kestrel": {
            "Endpoints": {
                "Grpc": {
                    "Url": "https://0.0.0.0:5001",
                    "Protocols": "Http2",
                },
                "Rest": {
                    "Url": "https://0.0.0.0:5000",
                    "Protocols": "Http1AndHttp2",
                },
            }
        },
        "_TODO": "Configure TLS certificates and additional service settings",
    }
    appsettings_path = output / "appsettings.json"
    appsettings_path.write_text(
        json.dumps(appsettings, indent=2) + "\n", encoding="utf-8"
    )
    transformations["generated_files"].append(str(appsettings_path.relative_to(output)))

    return transformations


# ---------------------------------------------------------------------------
# 4. WebForms -> Razor Pages
# ---------------------------------------------------------------------------
def migrate_webforms_to_razor(source_path, output_path):
    """Migrate C# ASP.NET WebForms to Razor Pages.

    Transforms .aspx files to .cshtml Razor pages, converts server controls
    to HTML tag helpers, migrates code-behind to PageModel classes, and
    generates _Layout.cshtml from MasterPage if present.
    """
    output = _copy_source_tree(source_path, output_path)
    transformations = {
        "source_framework": "ASP.NET WebForms",
        "target_framework": "Razor Pages (.NET 8)",
        "files_changed": 0,
        "total_transformations": 0,
        "file_details": {},
        "generated_files": [],
    }

    aspx_files = _collect_files(output, [".aspx"])
    codebehind_files = _collect_files(output, [".aspx.cs"])
    master_files = _collect_files(output, [".master"])

    # --- Transform .aspx -> .cshtml ---
    aspx_transforms = [
        {
            "pattern": r'<asp:TextBox\s+ID="(\w+)"\s+runat="server"\s*/>',
            "replacement": r'<input asp-for="\1" />',
            "flags": [],
        },
        {
            "pattern": r'<asp:TextBox\s+ID="(\w+)"\s+runat="server"\s*>\s*</asp:TextBox>',
            "replacement": r'<input asp-for="\1" />',
            "flags": [],
        },
        {
            "pattern": r'<asp:Button\s+[^>]*OnClick="(\w+)"\s+Text="([^"]*)"\s*[^>]*/>',
            "replacement": r'<button type="submit">\2</button>',
            "flags": [],
        },
        {
            "pattern": r'<asp:Button\s+[^>]*Text="([^"]*)"\s+OnClick="(\w+)"\s*[^>]*/>',
            "replacement": r'<button type="submit">\1</button>',
            "flags": [],
        },
        {
            "pattern": r"<asp:GridView\b[^>]*>",
            "replacement": (
                "<!-- TODO [ICDEV-MIGRATION]: GridView replaced with table + @foreach -->\n"
                "<table class=\"table\">"
            ),
            "flags": [],
        },
        {
            "pattern": r"</asp:GridView>",
            "replacement": "</table>",
            "flags": [],
        },
        {
            "pattern": r'<asp:Label\s+ID="(\w+)"\s+runat="server"\s*[^>]*/?>(?:</asp:Label>)?',
            "replacement": r'<span id="\1">@Model.\1</span>',
            "flags": [],
        },
        {
            "pattern": r'<asp:HyperLink\s+[^>]*NavigateUrl="([^"]*)"\s+Text="([^"]*)"\s*[^>]*/>',
            "replacement": r'<a asp-page="\1">\2</a>',
            "flags": [],
        },
        {
            "pattern": r"<%=\s*([^%]+?)\s*%>",
            "replacement": r"@(\1)",
            "flags": [],
        },
        {
            "pattern": r'<%#\s*Eval\("(\w+)"\)\s*%>',
            "replacement": r"@Model.\1",
            "flags": [],
        },
        # Remove runat="server" from standard HTML elements
        {
            "pattern": r'\s+runat="server"',
            "replacement": "",
            "flags": [],
        },
        # Remove Page directive (will be replaced with @page)
        {
            "pattern": r"<%@\s*Page\s+[^%]*%>",
            "replacement": '@page\n@model PageModel\n// TODO [ICDEV-MIGRATION]: Set correct PageModel type',
            "flags": [],
        },
    ]

    for af in aspx_files:
        changes = _apply_file_transforms(af, aspx_transforms)
        if changes > 0:
            # Rename .aspx to .cshtml
            new_name = af.with_suffix(".cshtml")
            af.rename(new_name)
            rel = new_name.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": changes,
                "description": ".aspx WebForms controls -> Razor tag helpers",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += changes

    # --- Transform code-behind .aspx.cs -> PageModel ---
    codebehind_transforms = [
        {
            "pattern": r"protected\s+void\s+Page_Load\s*\([^)]*\)",
            "replacement": "public void OnGet()",
            "flags": [],
        },
        {
            "pattern": r"protected\s+void\s+(\w+)_Click\s*\([^)]*\)",
            "replacement": r"public IActionResult OnPost()",
            "flags": [],
        },
        {
            "pattern": r"ViewState\[",
            "replacement": "// TODO [ICDEV-MIGRATION]: ViewState not available in Razor Pages\n        // ViewState[",
            "flags": [],
        },
        {
            "pattern": r":\s*System\.Web\.UI\.Page\b",
            "replacement": ": PageModel",
            "flags": [],
        },
        {
            "pattern": r"using\s+System\.Web\.UI\s*;",
            "replacement": "using Microsoft.AspNetCore.Mvc.RazorPages;",
            "flags": [],
        },
        {
            "pattern": r"using\s+System\.Web\.UI\.WebControls\s*;",
            "replacement": "// WebControls removed — using Razor tag helpers",
            "flags": [],
        },
    ]

    for cbf in codebehind_files:
        changes = _apply_file_transforms(cbf, codebehind_transforms)
        if changes > 0:
            # Rename to .cshtml.cs
            new_stem = cbf.name.replace(".aspx.cs", ".cshtml.cs")
            new_path = cbf.parent / new_stem
            cbf.rename(new_path)
            rel = new_path.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": changes,
                "description": "Code-behind Page_Load -> OnGet(), events -> OnPost()",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += changes

    # --- Generate _Layout.cshtml from MasterPage ---
    for mf in master_files:
        try:
            content = mf.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue

        layout_content = content
        # Transform ContentPlaceHolder to RenderBody/RenderSection
        layout_content = re.sub(
            r'<asp:ContentPlaceHolder\s+ID="MainContent"\s+runat="server"\s*/>',
            "@RenderBody()",
            layout_content,
        )
        layout_content = re.sub(
            r'<asp:ContentPlaceHolder\s+ID="(\w+)"\s+runat="server"\s*/>',
            r'@RenderSection("\1", required: false)',
            layout_content,
        )
        layout_content = re.sub(r"<%@\s*Master\s+[^%]*%>", "", layout_content)
        layout_content = re.sub(r'\s+runat="server"', "", layout_content)

        layout_header = (
            f"@* {CUI_BANNER} *@\n"
            "<!DOCTYPE html>\n"
            "<!-- Layout generated from MasterPage by ICDEV Framework Migrator -->\n"
        )
        layout_content = layout_header + layout_content.strip() + f"\n@* {CUI_BANNER} *@\n"

        pages_dir = output / "Pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        layout_path = pages_dir / "_Layout.cshtml"
        layout_path.write_text(layout_content, encoding="utf-8")
        transformations["generated_files"].append(str(layout_path.relative_to(output)))

    return transformations


# ---------------------------------------------------------------------------
# 5. Django 1.x -> 4.x
# ---------------------------------------------------------------------------
def migrate_django_version(source_path, output_path, from_ver="1", to_ver="4"):
    """Migrate Django 1.x application to Django 4.x.

    Transforms url() to path(), updates settings (MIDDLEWARE_CLASSES to
    MIDDLEWARE), adds on_delete to ForeignKey, and replaces deprecated
    imports and view functions.
    """
    output = _copy_source_tree(source_path, output_path)
    transformations = {
        "source_framework": f"Django {from_ver}.x",
        "target_framework": f"Django {to_ver}.x",
        "files_changed": 0,
        "total_transformations": 0,
        "file_details": {},
        "generated_files": [],
    }

    py_files = _collect_files(output, [".py"])
    html_files = _collect_files(output, [".html"])

    # --- URL pattern transforms ---
    url_transforms = [
        {
            "pattern": r"from\s+django\.conf\.urls\s+import\s+url\b",
            "replacement": "from django.urls import path, re_path",
            "flags": [],
        },
        {
            "pattern": r"from\s+django\.conf\.urls\.defaults\s+import\s+\*",
            "replacement": "from django.urls import path, re_path, include",
            "flags": [],
        },
        # Simple url() -> path() (named patterns)
        {
            "pattern": r"url\(r'\^([^$(?\\]+)\$',\s*(\w[\w.]*),\s*name='(\w+)'\)",
            "replacement": r"path('\1', \2, name='\3')",
            "flags": [],
        },
        # url() with regex and no name -> re_path
        {
            "pattern": r"url\(r'(\^[^']+)',",
            "replacement": r"re_path(r'\1',",
            "flags": [],
        },
    ]

    # --- Settings transforms ---
    settings_transforms = [
        {
            "pattern": r"MIDDLEWARE_CLASSES\s*=",
            "replacement": "MIDDLEWARE =",
            "flags": [],
        },
        {
            "pattern": r"django\.contrib\.auth\.views\.login\b",
            "replacement": "django.contrib.auth.views.LoginView.as_view()",
            "flags": [],
        },
        {
            "pattern": r"django\.contrib\.auth\.views\.logout\b",
            "replacement": "django.contrib.auth.views.LogoutView.as_view()",
            "flags": [],
        },
    ]

    # --- Model transforms (ForeignKey on_delete) ---
    model_transforms = [
        {
            "pattern": r"(ForeignKey\([^)]*?)(\))\s*$",
            "replacement": r"\1, on_delete=models.CASCADE\2  # TODO [ICDEV-MIGRATION]: Verify on_delete behavior",
            "flags": ["MULTILINE"],
        },
        # Only add on_delete if not already present
        # This is a safety transform applied after the first one to clean up duplicates
    ]

    # More targeted ForeignKey fix: only if on_delete is missing
    fk_no_ondelete = {
        "pattern": r"((?:ForeignKey|OneToOneField)\([^)]*?)(\))(?![^#]*on_delete)",
        "replacement": r"\1, on_delete=models.CASCADE\2  # TODO [ICDEV-MIGRATION]: Verify on_delete",
        "flags": [],
    }

    # --- View transforms ---
    view_transforms = [
        {
            "pattern": r"from\s+django\.shortcuts\s+import\s+render_to_response\b",
            "replacement": "from django.shortcuts import render",
            "flags": [],
        },
        {
            "pattern": r"render_to_response\(\s*(['\"][^'\"]+['\"])\s*,\s*(\w+)",
            "replacement": r"render(request, \1, \2",
            "flags": [],
        },
        {
            "pattern": r"from\s+django\.utils\.encoding\s+import\s+force_text\b",
            "replacement": "from django.utils.encoding import force_str",
            "flags": [],
        },
        {
            "pattern": r"\bforce_text\(",
            "replacement": "force_str(",
            "flags": [],
        },
        {
            "pattern": r"from\s+django\.utils\.translation\s+import\s+ugettext_lazy\s+as\s+_",
            "replacement": "from django.utils.translation import gettext_lazy as _",
            "flags": [],
        },
        {
            "pattern": r"from\s+django\.utils\.translation\s+import\s+ugettext\b",
            "replacement": "from django.utils.translation import gettext",
            "flags": [],
        },
    ]

    # --- Template transforms ---
    template_transforms = [
        {
            "pattern": r"\{%\s*load\s+url\s+from\s+future\s*%\}",
            "replacement": "<!-- url from future tag removed — no longer needed -->",
            "flags": [],
        },
    ]

    # Apply all transforms to Python files
    all_py_transforms = url_transforms + settings_transforms + view_transforms
    for pf in py_files:
        changes = _apply_file_transforms(pf, all_py_transforms)

        # Special handling: ForeignKey on_delete
        try:
            content = pf.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            content = ""

        if "ForeignKey" in content or "OneToOneField" in content:
            # Only apply if on_delete is truly missing
            fk_changes = _apply_file_transforms(pf, [fk_no_ondelete])
            changes += fk_changes

        if changes > 0:
            rel = pf.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": changes,
                "description": "Django 1.x -> 4.x patterns (urls, settings, models, views)",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += changes

    # Apply template transforms
    for hf in html_files:
        changes = _apply_file_transforms(hf, template_transforms)
        if changes > 0:
            rel = hf.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": changes,
                "description": "Django template tag updates",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += changes

    return transformations


# ---------------------------------------------------------------------------
# 6. Flask 0.x -> 3.x
# ---------------------------------------------------------------------------
def migrate_flask_version(source_path, output_path, from_ver="0", to_ver="3"):
    """Migrate Flask 0.x application to Flask 3.x.

    Transforms flask.ext.X imports to flask_x, normalizes request.json
    usage, adds async TODO annotations, and updates Blueprint patterns.
    """
    output = _copy_source_tree(source_path, output_path)
    transformations = {
        "source_framework": f"Flask {from_ver}.x",
        "target_framework": f"Flask {to_ver}.x",
        "files_changed": 0,
        "total_transformations": 0,
        "file_details": {},
        "generated_files": [],
    }

    py_files = _collect_files(output, [".py"])

    flask_transforms = [
        # flask.ext.X -> flask_x
        {
            "pattern": r"from\s+flask\.ext\.(\w+)\s+import",
            "replacement": r"from flask_\1 import",
            "flags": [],
        },
        {
            "pattern": r"import\s+flask\.ext\.(\w+)",
            "replacement": r"import flask_\1",
            "flags": [],
        },
        # flask.escape -> markupsafe.escape
        {
            "pattern": r"from\s+flask\s+import\s+([^#\n]*)\bescape\b",
            "replacement": r"from flask import \1  # escape removed\nfrom markupsafe import escape",
            "flags": [],
        },
        {
            "pattern": r"from\s+flask\s+import\s+([^#\n]*)\bMarkup\b",
            "replacement": r"from flask import \1  # Markup removed\nfrom markupsafe import Markup",
            "flags": [],
        },
        # before_first_request removal
        {
            "pattern": r"@(\w+)\.before_first_request",
            "replacement": (
                "# TODO [ICDEV-MIGRATION]: @before_first_request removed in Flask 2.3\n"
                "# Move this initialization logic into create_app() factory function\n"
                r"# @\1.before_first_request  # REMOVED"
            ),
            "flags": [],
        },
        # request.json normalization (add note about get_json)
        {
            "pattern": r"request\.json(?!\s*\()",
            "replacement": "request.get_json()  # TODO [ICDEV-MIGRATION]: Normalized to get_json()",
            "flags": [],
        },
        # JSONEncoder deprecation
        {
            "pattern": r"app\.json_encoder\s*=",
            "replacement": "# TODO [ICDEV-MIGRATION]: json_encoder removed in Flask 2.3, use app.json_provider_class\n# app.json_encoder =",
            "flags": [],
        },
        {
            "pattern": r"from\s+flask\.json\s+import\s+JSONEncoder",
            "replacement": "# TODO [ICDEV-MIGRATION]: JSONEncoder removed in Flask 2.3\n# from flask.json import JSONEncoder",
            "flags": [],
        },
    ]

    # Async support annotations for route handlers
    async_annotation = {
        "pattern": r"(@\w+\.route\([^)]+\))\s*\ndef\s+(\w+)\(",
        "replacement": r"\1\n# TODO [ICDEV-MIGRATION]: Consider 'async def' for I/O-bound views (Flask 2.0+)\ndef \2(",
        "flags": [],
    }

    for pf in py_files:
        changes = _apply_file_transforms(pf, flask_transforms)

        # Apply async annotations only to files that have route decorators
        try:
            content = pf.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            content = ""

        if ".route(" in content:
            async_changes = _apply_file_transforms(pf, [async_annotation])
            changes += async_changes

        if changes > 0:
            rel = pf.relative_to(output)
            transformations["file_details"][str(rel)] = {
                "changes": changes,
                "description": "Flask 0.x -> 3.x (imports, deprecations, async hints)",
            }
            transformations["files_changed"] += 1
            transformations["total_transformations"] += changes

    return transformations


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def _parse_version_hint(framework_key):
    """Extract from/to version hints from framework keys like 'django-1'."""
    parts = framework_key.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0], parts[1]
    return framework_key, None


def main():
    """CLI entry point for the ICDEV Framework Migration Tool."""
    parser = argparse.ArgumentParser(
        description=(
            f"{CUI_BANNER}\n"
            "ICDEV Framework Migration Tool\n"
            "Transforms legacy framework code to modern equivalents."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(f"""\
            Supported migration paths:
              struts      -> spring-boot     (Java Struts to Spring Boot)
              ejb         -> spring          (Java EJB to Spring)
              wcf         -> aspnet-core-grpc (C# WCF to ASP.NET Core gRPC/REST)
              webforms    -> razor           (C# WebForms to Razor Pages)
              django-1    -> django-4        (Django 1.x to 4.x)
              flask-0     -> flask-3         (Flask 0.x to 3.x)

            {CUI_BANNER}
        """),
    )

    parser.add_argument(
        "--source",
        required=True,
        help="Path to the source project directory",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to the output directory (migrated code will be written here)",
    )
    parser.add_argument(
        "--from",
        dest="from_framework",
        required=True,
        choices=["struts", "ejb", "wcf", "webforms", "django-1", "flask-0"],
        help="Source framework identifier",
    )
    parser.add_argument(
        "--to",
        dest="to_framework",
        required=True,
        choices=["spring-boot", "spring", "aspnet-core-grpc", "razor", "django-4", "flask-3"],
        help="Target framework identifier",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        default=False,
        help="Generate a migration_report.md in the output directory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        dest="json_output",
        help="Output results as JSON to stdout",
    )

    args = parser.parse_args()

    # Validate source path
    source_path = Path(args.source).resolve()
    if not source_path.exists():
        print(f"[ERROR] Source path does not exist: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not source_path.is_dir():
        print(f"[ERROR] Source path is not a directory: {source_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output).resolve()

    # Ensure output is NOT the same as source (safety check)
    if output_path == source_path:
        print(
            "[ERROR] Output path must differ from source path. "
            "This tool never modifies source code in place.",
            file=sys.stderr,
        )
        sys.exit(1)

    migration_key = (args.from_framework, args.to_framework)
    if migration_key not in MIGRATION_MAP:
        print(
            f"[ERROR] Unsupported migration path: {args.from_framework} -> {args.to_framework}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load patterns from context JSON
    patterns = load_framework_patterns(args.from_framework, args.to_framework)
    if patterns:
        print(f"[INFO] Loaded migration pattern: {patterns.get('id', 'unknown')}")
        print(f"[INFO] {patterns['source_framework']} -> {patterns['target_framework']}")
        print(f"[INFO] Estimated effort factor: {patterns.get('estimated_effort_factor', 'N/A')}x")
    else:
        print("[WARN] No patterns file loaded — proceeding with built-in transforms only")

    # Dispatch to the correct migration function
    func_name = MIGRATION_MAP[migration_key]
    print(f"\n[INFO] Starting migration: {args.from_framework} -> {args.to_framework}")
    print(f"[INFO] Source: {source_path}")
    print(f"[INFO] Output: {output_path}")
    print()

    if func_name == "migrate_struts_to_spring":
        result = migrate_struts_to_spring(source_path, output_path)
    elif func_name == "migrate_ejb_to_spring":
        result = migrate_ejb_to_spring(source_path, output_path)
    elif func_name == "migrate_wcf_to_aspnet_core":
        result = migrate_wcf_to_aspnet_core(source_path, output_path)
    elif func_name == "migrate_webforms_to_razor":
        result = migrate_webforms_to_razor(source_path, output_path)
    elif func_name == "migrate_django_version":
        _, from_v = _parse_version_hint(args.from_framework)
        _, to_v = _parse_version_hint(args.to_framework)
        result = migrate_django_version(
            source_path, output_path, from_ver=from_v or "1", to_ver=to_v or "4"
        )
    elif func_name == "migrate_flask_version":
        _, from_v = _parse_version_hint(args.from_framework)
        _, to_v = _parse_version_hint(args.to_framework)
        result = migrate_flask_version(
            source_path, output_path, from_ver=from_v or "0", to_ver=to_v or "3"
        )
    else:
        print(f"[ERROR] Migration function not implemented: {func_name}", file=sys.stderr)
        sys.exit(1)

    # Generate migration report if requested
    report_path = None
    if args.report:
        report_path = _generate_migration_report(source_path, output_path, result)
        print(f"[INFO] Migration report written to: {report_path}")

    # Output
    if args.json_output:
        output_data = {
            "classification": CUI_BANNER,
            "status": "completed",
            "source": str(source_path),
            "output": str(output_path),
            "from_framework": args.from_framework,
            "to_framework": args.to_framework,
            "transformations": result,
            "report_path": report_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        print(json.dumps(output_data, indent=2, default=str))
    else:
        print()
        print(f"{'=' * 60}")
        print(f"  Migration Complete: {result.get('source_framework', '')} -> {result.get('target_framework', '')}")
        print(f"{'=' * 60}")
        print(f"  Files modified:     {result.get('files_changed', 0)}")
        print(f"  Transformations:    {result.get('total_transformations', 0)}")
        print(f"  Files generated:    {len(result.get('generated_files', []))}")
        if result.get("generated_files"):
            for gf in result["generated_files"]:
                print(f"    + {gf}")
        print(f"{'=' * 60}")
        if report_path:
            print(f"  Report: {report_path}")
        print()
        print(f"  {CUI_BANNER}")


if __name__ == "__main__":
    main()


# CUI // SP-CTI
