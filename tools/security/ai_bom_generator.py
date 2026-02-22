#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""AI Bill of Materials (AI BOM) Generator.

Catalogs all AI/ML components in a project: LLM providers, embedding models,
AI framework dependencies, and MCP server configurations. Stores components
in the ai_bom table (migration 005), evaluates freshness gates per
security_gates.yaml atlas_ai thresholds, and logs audit events.

Usage:
    python tools/security/ai_bom_generator.py --project-id proj-123 --project-dir /path
    python tools/security/ai_bom_generator.py --project-id proj-123 --project-dir /path --json
    python tools/security/ai_bom_generator.py --project-id proj-123 --gate --json
    python tools/security/ai_bom_generator.py --project-id proj-123 --project-dir /path --human

Databases:
    - data/icdev.db: ai_bom, projects, audit_trail

See also:
    - tools/compliance/sbom_generator.py (software BOM — same pattern)
    - tools/compliance/atlas_assessor.py (ATLAS assessment engine)
    - args/llm_config.yaml (LLM provider/model registry)
    - args/security_gates.yaml (atlas_ai gate thresholds)
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
LLM_CONFIG_PATH = BASE_DIR / "args" / "llm_config.yaml"
MCP_CONFIG_PATH = BASE_DIR / ".mcp.json"
GATES_PATH = BASE_DIR / "args" / "security_gates.yaml"

# AI framework packages to detect in requirements files
AI_FRAMEWORK_PACKAGES = {
    "openai", "anthropic", "boto3", "ibm-watsonx-ai",
    "google-generativeai", "langchain", "langchain-core",
    "langchain-community", "transformers", "torch", "tensorflow",
    "numpy", "scikit-learn", "scipy", "pandas", "keras",
    "onnx", "onnxruntime", "sentence-transformers", "tiktoken",
    "tokenizers", "safetensors", "accelerate", "peft",
    "huggingface-hub", "diffusers",
}


class AIBOMGenerator:
    """Generate an AI Bill of Materials cataloging AI/ML components."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH

    # -----------------------------------------------------------------
    # Database helpers
    # -----------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with Row factory."""
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Database not found: {self.db_path}\n"
                "Run: python tools/db/init_icdev_db.py"
            )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _get_project(self, conn: sqlite3.Connection, project_id: str) -> Dict:
        """Load project data from database."""
        row = conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Project '{project_id}' not found.")
        return dict(row)

    def _log_audit_event(
        self, conn: sqlite3.Connection, project_id: str,
        action: str, details: Dict,
    ) -> None:
        """Log an audit trail event for AI BOM generation."""
        try:
            conn.execute(
                """INSERT INTO audit_trail
                   (project_id, event_type, actor, action, details,
                    affected_files, classification)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    "ai_bom_generated",
                    "icdev-security-engine",
                    action,
                    json.dumps(details),
                    json.dumps([]),
                    "CUI",
                ),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: Could not log audit event: {e}", file=sys.stderr)

    # -----------------------------------------------------------------
    # Scanning methods
    # -----------------------------------------------------------------

    def _scan_llm_config(self, project_dir: Path) -> List[Dict]:
        """Scan args/llm_config.yaml for LLM and embedding model components."""
        components = []

        # Try project-local first, fall back to platform config
        config_path = project_dir / "args" / "llm_config.yaml"
        if not config_path.exists():
            config_path = LLM_CONFIG_PATH
        if not config_path.exists():
            return components

        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except ImportError:
            # Fallback: parse YAML manually for basic model extraction
            config = self._parse_yaml_fallback(config_path)
        except Exception:
            return components

        # Extract LLM models
        models = config.get("models", {})
        for model_name, model_info in models.items():
            if not isinstance(model_info, dict):
                continue
            components.append({
                "component_type": "model",
                "component_name": model_name,
                "version": model_info.get("model_id", "unknown"),
                "provider": model_info.get("provider", "unknown"),
                "license": "proprietary",
                "source": str(config_path),
            })

        # Extract embedding models
        embeddings = config.get("embeddings", {})
        embed_models = embeddings.get("models", {})
        for embed_name, embed_info in embed_models.items():
            if not isinstance(embed_info, dict):
                continue
            components.append({
                "component_type": "model",
                "component_name": f"embedding:{embed_name}",
                "version": embed_info.get("model_id", "unknown"),
                "provider": embed_info.get("provider", "unknown"),
                "license": "proprietary",
                "source": str(config_path),
            })

        return components

    def _parse_yaml_fallback(self, config_path: Path) -> Dict:
        """Minimal YAML-like parser for llm_config when pyyaml unavailable."""
        result: Dict = {"models": {}, "embeddings": {"models": {}}}
        try:
            content = config_path.read_text(encoding="utf-8")
        except Exception:
            return result

        # Extract model entries: lines matching "  model_name:" at 2-space indent
        current_section = None
        current_model = None
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue

            # Top-level section detection
            if not line.startswith(" ") and stripped.endswith(":"):
                section_name = stripped.rstrip(":")
                if section_name == "models":
                    current_section = "models"
                elif section_name == "embeddings":
                    current_section = "embeddings"
                else:
                    current_section = None
                current_model = None
                continue

            # Model entries (2-space indent under models)
            if current_section == "models" and line.startswith("  ") and not line.startswith("    "):
                model_name = stripped.rstrip(":")
                if model_name and ":" not in model_name:
                    current_model = model_name
                    result["models"][current_model] = {}
                continue

            # Model properties (4-space indent)
            if current_model and current_section == "models" and line.startswith("    "):
                match = re.match(r'\s+(\w+):\s*(.+)', line)
                if match:
                    key = match.group(1)
                    value = match.group(2).strip()
                    result["models"][current_model][key] = value

        return result

    def _scan_requirements(self, project_dir: Path) -> List[Dict]:
        """Scan requirements.txt for AI framework dependencies."""
        components = []

        req_file = project_dir / "requirements.txt"
        if not req_file.exists():
            return components

        try:
            with open(req_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    if "://" in line or line.startswith("."):
                        continue

                    match = re.match(
                        r'^([a-zA-Z0-9._-]+)\s*(?:([<>=!~]+)\s*([a-zA-Z0-9.*_-]+))?',
                        line,
                    )
                    if not match:
                        continue

                    name = match.group(1).lower().replace("_", "-")
                    version = match.group(3) or "unspecified"

                    if name not in AI_FRAMEWORK_PACKAGES:
                        continue

                    components.append({
                        "component_type": "library",
                        "component_name": name,
                        "version": version,
                        "provider": "pypi",
                        "license": self._infer_license(name),
                        "source": str(req_file),
                    })
        except Exception:
            pass

        return components

    def _scan_mcp_config(self, project_dir: Path) -> List[Dict]:
        """Scan .mcp.json for MCP server configurations."""
        components = []

        mcp_path = project_dir / ".mcp.json"
        if not mcp_path.exists():
            mcp_path = MCP_CONFIG_PATH
        if not mcp_path.exists():
            return components

        try:
            with open(mcp_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            return components

        servers = config.get("mcpServers", {})
        for server_name, server_info in servers.items():
            if not isinstance(server_info, dict):
                continue
            command = server_info.get("command", "unknown")
            args_list = server_info.get("args", [])
            script = args_list[0] if args_list else "unknown"

            components.append({
                "component_type": "service",
                "component_name": server_name,
                "version": "1.0.0",
                "provider": f"{command}:{script}",
                "license": "proprietary",
                "source": str(mcp_path),
            })

        return components

    # -----------------------------------------------------------------
    # Utility methods
    # -----------------------------------------------------------------

    def _compute_hash(self, component_data: Dict) -> str:
        """Compute SHA-256 hash of component data for change detection."""
        key_fields = f"{component_data.get('component_type', '')}" \
                     f"/{component_data.get('component_name', '')}" \
                     f"@{component_data.get('version', '')}" \
                     f":{component_data.get('provider', '')}"
        return hashlib.sha256(key_fields.encode("utf-8")).hexdigest()

    def _assess_risk(self, component: Dict) -> str:
        """Assess risk level of an AI component.

        Risk factors:
        - External/cloud-hosted models: higher risk (data exfiltration surface)
        - Unversioned components: higher risk (supply chain)
        - Known high-risk frameworks: elevated
        """
        comp_type = component.get("component_type", "")
        provider = component.get("provider", "").lower()
        version = component.get("version", "unspecified")
        name = component.get("component_name", "").lower()

        # Cloud-hosted LLM models are medium-high risk (data leaves boundary)
        if comp_type == "model":
            if provider in ("bedrock", "anthropic", "openai", "gemini"):
                return "medium"
            if provider == "ollama":
                return "low"
            return "medium"

        # Unversioned libraries are higher risk
        if comp_type == "library" and version in ("unspecified", "unknown"):
            return "high"

        # Known ML frameworks with large attack surfaces
        high_risk_libs = {"torch", "tensorflow", "transformers"}
        if name in high_risk_libs:
            return "medium"

        # MCP servers are service boundaries
        if comp_type == "service":
            return "medium"

        return "low"

    def _infer_license(self, package_name: str) -> str:
        """Infer license for known AI packages."""
        licenses = {
            "openai": "MIT",
            "anthropic": "MIT",
            "boto3": "Apache-2.0",
            "google-generativeai": "Apache-2.0",
            "langchain": "MIT",
            "langchain-core": "MIT",
            "langchain-community": "MIT",
            "transformers": "Apache-2.0",
            "torch": "BSD-3-Clause",
            "tensorflow": "Apache-2.0",
            "numpy": "BSD-3-Clause",
            "scikit-learn": "BSD-3-Clause",
            "scipy": "BSD-3-Clause",
            "pandas": "BSD-3-Clause",
            "keras": "Apache-2.0",
            "tiktoken": "MIT",
            "tokenizers": "Apache-2.0",
            "ibm-watsonx-ai": "Apache-2.0",
        }
        return licenses.get(package_name, "unknown")

    # -----------------------------------------------------------------
    # Core operations
    # -----------------------------------------------------------------

    def scan_project(self, project_id: str, project_dir: str) -> Dict:
        """Scan a project for all AI/ML components.

        Args:
            project_id: The project identifier.
            project_dir: Path to the project directory.

        Returns:
            Dict with components list and scan metadata.
        """
        project_path = Path(project_dir)
        if not project_path.is_dir():
            raise ValueError(f"Project directory not found: {project_dir}")

        now = datetime.now(timezone.utc)
        all_components = []

        # 1. Scan LLM config for models and embeddings
        llm_components = self._scan_llm_config(project_path)
        all_components.extend(llm_components)

        # 2. Scan requirements.txt for AI framework dependencies
        req_components = self._scan_requirements(project_path)
        all_components.extend(req_components)

        # 3. Scan .mcp.json for MCP server configurations
        mcp_components = self._scan_mcp_config(project_path)
        all_components.extend(mcp_components)

        # Enrich each component with hash and risk level
        for comp in all_components:
            comp["hash"] = self._compute_hash(comp)
            comp["risk_level"] = self._assess_risk(comp)

        # Build type summary
        type_counts = {}
        for comp in all_components:
            ctype = comp.get("component_type", "unknown")
            type_counts[ctype] = type_counts.get(ctype, 0) + 1

        return {
            "project_id": project_id,
            "project_dir": str(project_path),
            "scan_date": now.isoformat(),
            "total_components": len(all_components),
            "type_counts": type_counts,
            "components": all_components,
        }

    def store_bom(self, project_id: str, components: List[Dict]) -> int:
        """Store AI BOM components in the database.

        Args:
            project_id: The project identifier.
            components: List of component dicts from scan_project.

        Returns:
            Number of components stored.
        """
        conn = self._get_connection()
        try:
            now = datetime.now(timezone.utc).isoformat()
            stored = 0

            for comp in components:
                comp_id = str(uuid.uuid4())
                try:
                    conn.execute(
                        """INSERT INTO ai_bom
                           (id, project_id, component_type, component_name,
                            version, provider, license, risk_level,
                            classification, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            comp_id,
                            project_id,
                            comp.get("component_type", "library"),
                            comp.get("component_name", "unknown"),
                            comp.get("version", "unknown"),
                            comp.get("provider", "unknown"),
                            comp.get("license", "unknown"),
                            comp.get("risk_level", "medium"),
                            "CUI",
                            now,
                            now,
                        ),
                    )
                    stored += 1
                except sqlite3.IntegrityError:
                    # Component already exists, update it
                    conn.execute(
                        """UPDATE ai_bom SET version = ?, provider = ?,
                           license = ?, risk_level = ?, updated_at = ?
                           WHERE project_id = ? AND component_name = ?
                           AND component_type = ?""",
                        (
                            comp.get("version", "unknown"),
                            comp.get("provider", "unknown"),
                            comp.get("license", "unknown"),
                            comp.get("risk_level", "medium"),
                            now,
                            project_id,
                            comp.get("component_name", "unknown"),
                            comp.get("component_type", "library"),
                        ),
                    )
                    stored += 1

            conn.commit()

            # Log audit event
            self._log_audit_event(conn, project_id, "AI BOM stored", {
                "components_stored": stored,
                "timestamp": now,
            })

            return stored
        finally:
            conn.close()

    def evaluate_gate(self, project_id: str) -> Dict:
        """Evaluate the AI BOM security gate.

        Gate checks (from security_gates.yaml atlas_ai):
        - ai_bom_required: BOM must exist in database
        - ai_bom_max_age_days: BOM must not be stale (default 90 days)

        Returns:
            Dict with pass/fail, reasons, and component counts.
        """
        conn = self._get_connection()
        try:
            blocking = []
            warnings = []

            # Check if any AI BOM entries exist
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM ai_bom WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            bom_count = row["cnt"] if row else 0

            if bom_count == 0:
                blocking.append("ai_bom_missing: No AI BOM entries found")

            # Check staleness (default 90 days from security_gates.yaml)
            max_age_days = 90
            if bom_count > 0:
                stale_row = conn.execute(
                    """SELECT MIN(created_at) as oldest,
                              MAX(created_at) as newest
                       FROM ai_bom WHERE project_id = ?""",
                    (project_id,),
                ).fetchone()
                if stale_row and stale_row["newest"]:
                    try:
                        newest_str = stale_row["newest"]
                        newest = datetime.fromisoformat(
                            newest_str.replace("Z", "+00:00")
                        )
                        age_days = (
                            datetime.now(timezone.utc) - newest
                        ).days
                        if age_days > max_age_days:
                            warnings.append(
                                f"ai_bom_stale: BOM is {age_days} days old "
                                f"(threshold: {max_age_days} days)"
                            )
                    except Exception:
                        pass

            # Check for high/critical risk components
            risk_row = conn.execute(
                """SELECT COUNT(*) as cnt FROM ai_bom
                   WHERE project_id = ?
                   AND risk_level IN ('critical', 'high')""",
                (project_id,),
            ).fetchone()
            high_risk = risk_row["cnt"] if risk_row else 0
            if high_risk > 0:
                warnings.append(
                    f"ai_bom_high_risk: {high_risk} component(s) with "
                    "high/critical risk level"
                )

            gate_pass = len(blocking) == 0

            return {
                "pass": gate_pass,
                "gate": "atlas_ai_bom",
                "project_id": project_id,
                "total_components": bom_count,
                "high_risk_components": high_risk,
                "blocking_issues": blocking,
                "warnings": warnings,
            }
        finally:
            conn.close()

    def generate_report(self, project_id: str) -> Dict:
        """Generate a formatted AI BOM report from stored data.

        Args:
            project_id: The project identifier.

        Returns:
            Dict with formatted BOM data and summary.
        """
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """SELECT * FROM ai_bom
                   WHERE project_id = ?
                   ORDER BY component_type, component_name""",
                (project_id,),
            ).fetchall()

            components = [dict(r) for r in rows]

            # Build type summary
            type_counts = {}
            risk_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            for comp in components:
                ctype = comp.get("component_type", "unknown")
                type_counts[ctype] = type_counts.get(ctype, 0) + 1
                risk = comp.get("risk_level", "medium")
                if risk in risk_counts:
                    risk_counts[risk] += 1

            return {
                "project_id": project_id,
                "total_components": len(components),
                "type_counts": type_counts,
                "risk_counts": risk_counts,
                "components": components,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # CLI
    # -----------------------------------------------------------------

    def run_cli(self) -> None:
        """Standard CLI entry point."""
        parser = argparse.ArgumentParser(
            description="Generate AI Bill of Materials (AI BOM)"
        )
        parser.add_argument(
            "--project-id", required=True,
            help="Project ID",
        )
        parser.add_argument(
            "--project-dir",
            help="Path to project directory to scan",
        )
        parser.add_argument(
            "--gate", action="store_true",
            help="Evaluate AI BOM gate pass/fail only",
        )
        parser.add_argument(
            "--json", action="store_true",
            help="JSON output",
        )
        parser.add_argument(
            "--human", action="store_true",
            help="Human-readable colored output",
        )
        parser.add_argument(
            "--db-path", type=Path, default=None,
            help="Database path override",
        )
        args = parser.parse_args()

        if args.db_path:
            self.db_path = args.db_path

        try:
            if args.gate:
                result = self.evaluate_gate(args.project_id)
                if args.json:
                    print(json.dumps(result, indent=2))
                else:
                    status = "PASS" if result["pass"] else "FAIL"
                    print(f"AI BOM Gate: {status}")
                    print(f"  Components: {result['total_components']}")
                    print(f"  High Risk:  {result['high_risk_components']}")
                    if result["blocking_issues"]:
                        print(f"  Blocking ({len(result['blocking_issues'])}):")
                        for issue in result["blocking_issues"]:
                            print(f"    - {issue}")
                    if result["warnings"]:
                        print(f"  Warnings ({len(result['warnings'])}):")
                        for w in result["warnings"]:
                            print(f"    - {w}")
                return

            if not args.project_dir:
                print(
                    "ERROR: --project-dir is required for scanning",
                    file=sys.stderr,
                )
                sys.exit(1)

            # Scan project
            scan_result = self.scan_project(
                args.project_id, args.project_dir
            )

            # Store in database
            stored = self.store_bom(
                args.project_id,
                scan_result["components"],
            )

            # Build output
            output = {
                "status": "success",
                "project_id": args.project_id,
                "project_dir": args.project_dir,
                "scan_date": scan_result["scan_date"],
                "total_components": scan_result["total_components"],
                "components_stored": stored,
                "type_counts": scan_result["type_counts"],
                "components": scan_result["components"],
            }

            if args.json:
                print(json.dumps(output, indent=2))
            else:
                print("AI BOM generated successfully:")
                print(f"  Project:         {args.project_id}")
                print(f"  Directory:       {args.project_dir}")
                print(f"  Components:      {scan_result['total_components']}")
                print(f"  Stored:          {stored}")
                for ctype, count in scan_result["type_counts"].items():
                    print(f"    {ctype}: {count}")
                print("")
                for comp in scan_result["components"]:
                    risk = comp.get("risk_level", "?")
                    print(
                        f"  [{risk.upper():8s}] "
                        f"{comp['component_type']:10s} "
                        f"{comp['component_name']} "
                        f"({comp.get('version', '?')})"
                    )

        except (FileNotFoundError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    AIBOMGenerator().run_cli()
