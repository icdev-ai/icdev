# [TEMPLATE: CUI // SP-CTI]
#!/usr/bin/env python3
"""ICDEV Platform Setup — generates deployment artifacts for selected modules.

Produces docker-compose, K8s RBAC, Helm value overrides, .env templates,
and install scripts tailored to the selected module set and deployment profile.

Dependencies: Python stdlib only (pathlib, argparse, json, datetime, textwrap).

CLI::

    python tools/installer/platform_setup.py --generate docker --modules core,llm,builder,dashboard --output docker-compose.yml
    python tools/installer/platform_setup.py --generate k8s-rbac --modules core,builder --output k8s/rbac.yaml
    python tools/installer/platform_setup.py --generate env --modules core,llm --output .env.template
    python tools/installer/platform_setup.py --generate helm-values --modules core,llm,builder --output deploy/helm/values-custom.yaml
    python tools/installer/platform_setup.py --generate install-bash --modules core,llm --profile isv_startup --output install.sh
    python tools/installer/platform_setup.py --generate install-ps1 --modules core,llm --profile isv_startup --output install.ps1
    python tools/installer/platform_setup.py --list-agents --json
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANIFEST_PATH = BASE_DIR / "args" / "installation_manifest.yaml"
DEFAULT_PROFILES_PATH = BASE_DIR / "args" / "deployment_profiles.yaml"

# ── Agent/Service Registry ──────────────────────────────────────────────
# Maps module names to the agent services they require.
# Each entry: (service_name, port, dockerfile, tier, description)

AGENT_SERVICES = {
    "core": [
        ("icdev-orchestrator", 8443, "Dockerfile.agent-base", "core", "Orchestrator Agent"),
        ("icdev-architect", 8444, "Dockerfile.agent-base", "core", "Architect Agent"),
    ],
    "builder": [
        ("icdev-builder", 8445, "Dockerfile.agent-base", "domain", "Builder Agent"),
    ],
    "compliance_base": [
        ("icdev-compliance", 8446, "Dockerfile.agent-base", "domain", "Compliance Agent"),
    ],
    "security": [
        ("icdev-security", 8447, "Dockerfile.agent-base", "domain", "Security Agent"),
    ],
    "infrastructure": [
        ("icdev-infrastructure", 8448, "Dockerfile.agent-base", "domain", "Infrastructure Agent"),
    ],
    "monitoring": [
        ("icdev-knowledge", 8449, "Dockerfile.agent-base", "support", "Knowledge Agent"),
        ("icdev-monitor", 8450, "Dockerfile.agent-base", "support", "Monitor Agent"),
    ],
    "mbse": [
        ("icdev-mbse", 8451, "Dockerfile.mbse-agent", "domain", "MBSE Agent"),
    ],
    "modernization": [
        ("icdev-modernization", 8452, "Dockerfile.modernization-agent", "domain", "Modernization Agent"),
    ],
    "ricoas": [
        ("icdev-requirements", 8453, "Dockerfile.requirements-analyst-agent", "domain", "Requirements Analyst Agent"),
        ("icdev-supply-chain", 8454, "Dockerfile.supply-chain-agent", "domain", "Supply Chain Agent"),
        ("icdev-simulation", 8455, "Dockerfile.simulation-agent", "domain", "Simulation Agent"),
    ],
    "devsecops_zta": [
        ("icdev-devsecops", 8457, "Dockerfile.devsecops-agent", "domain", "DevSecOps Agent"),
    ],
    "gateway": [
        ("icdev-gateway", 8458, "Dockerfile.gateway-agent", "domain", "Gateway Agent"),
    ],
    "dashboard": [
        ("icdev-dashboard", 5000, "Dockerfile.dashboard", "support", "Web Dashboard"),
    ],
    "saas": [
        ("icdev-api-gateway", 9443, "Dockerfile.api-gateway", "core", "SaaS API Gateway"),
    ],
}

# ── Environment Variable Groups ─────────────────────────────────────────
# Maps categories to the env vars they require.

ENV_VAR_GROUPS = {
    "core": {
        "heading": "Core",
        "vars": [
            ("ICDEV_DB_PATH", "/app/data/icdev.db", "Path to main ICDEV SQLite database"),
            ("ICDEV_PROJECT_ROOT", "/app", "ICDEV application root directory"),
            ("ICDEV_LOG_LEVEL", "INFO", "Logging level (DEBUG, INFO, WARNING, ERROR)"),
        ],
    },
    "llm": {
        "heading": "LLM Provider",
        "vars": [
            ("OPENAI_API_KEY", "", "OpenAI API key (for embeddings or OpenAI-compat providers)"),
            ("ANTHROPIC_API_KEY", "", "Direct Anthropic API key (optional if using Bedrock)"),
            ("OLLAMA_BASE_URL", "http://localhost:11434/v1", "Ollama base URL for local models"),
            ("AWS_DEFAULT_REGION", "us-gov-west-1", "AWS region for Bedrock"),
            ("AWS_ACCESS_KEY_ID", "", "AWS access key (leave empty to use IAM role)"),
            ("AWS_SECRET_ACCESS_KEY", "", "AWS secret key (leave empty to use IAM role)"),
        ],
    },
    "compliance_base": {
        "heading": "Compliance",
        "vars": [
            ("ICDEV_CUI_BANNER_TOP", "CUI // SP-CTI", "CUI banner for top of documents"),
            ("ICDEV_CUI_BANNER_BOTTOM", "CUI // SP-CTI", "CUI banner for bottom of documents"),
            ("ICDEV_CUI_BANNER_ENABLED", "true", "Enable/disable CUI banners globally"),
            ("ICDEV_IMPACT_LEVEL", "IL4", "Impact level: IL2, IL4, IL5, IL6"),
        ],
    },
    "dashboard": {
        "heading": "Dashboard Auth",
        "vars": [
            ("ICDEV_DASHBOARD_SECRET", "", "Flask session secret key (auto-generated if empty)"),
            ("ICDEV_BYOK_ENABLED", "false", "Enable bring-your-own LLM key management"),
            ("ICDEV_BYOK_ENCRYPTION_KEY", "", "Fernet AES-256 key for BYOK encryption"),
        ],
    },
    "saas": {
        "heading": "SaaS Platform",
        "vars": [
            ("ICDEV_PLATFORM_DB_URL", "sqlite:///data/platform.db", "Platform database URL"),
            ("ICDEV_JWT_SECRET", "", "JWT signing secret for OAuth tokens"),
            ("ICDEV_LICENSE_PATH", "/app/license.json", "Path to license file"),
        ],
    },
    "monitoring": {
        "heading": "Monitoring",
        "vars": [
            ("SPLUNK_HEC_TOKEN", "", "Splunk HTTP Event Collector token"),
            ("SPLUNK_HOST", "", "Splunk host URL"),
            ("ELK_HOST", "", "Elasticsearch host URL"),
            ("PROMETHEUS_PUSHGATEWAY", "", "Prometheus pushgateway URL"),
        ],
    },
    "gateway": {
        "heading": "Remote Gateway",
        "vars": [
            ("ICDEV_GATEWAY_MODE", "connected", "Gateway mode: connected or air_gapped"),
            ("TELEGRAM_BOT_TOKEN", "", "Telegram bot token (IL2-IL4 only)"),
            ("SLACK_WEBHOOK_URL", "", "Slack webhook URL (IL2-IL5 only)"),
            ("MATTERMOST_URL", "", "Mattermost server URL (air-gapped safe)"),
        ],
    },
}


# ── YAML Loader (stdlib-only, same pattern as module_registry.py) ───────

def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file.  Uses PyYAML if available, otherwise returns empty dict."""
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        return {}
    except FileNotFoundError:
        return {}


# ── Main Class ──────────────────────────────────────────────────────────

class PlatformSetup:
    """Generates deployment artifacts for a selected set of ICDEV modules."""

    def __init__(
        self,
        manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
        profiles_path: str | Path = DEFAULT_PROFILES_PATH,
    ):
        self.manifest = _load_yaml(Path(manifest_path))
        self.profiles = _load_yaml(Path(profiles_path))
        self.modules_def = self.manifest.get("modules", {})
        self.profiles_def = self.profiles.get("profiles", {})

    # ── helpers ──────────────────────────────────────────────────────

    def _resolve_modules(self, modules: List[str]) -> List[str]:
        """Expand 'ALL' and add required dependencies."""
        if "ALL" in [m.upper() for m in modules]:
            return list(self.modules_def.keys())
        resolved = list(modules)
        # Always ensure required modules are present
        for mod_name, mod_def in self.modules_def.items():
            if mod_def.get("required", False) and mod_name not in resolved:
                resolved.insert(0, mod_name)
        # Add dependencies
        changed = True
        while changed:
            changed = False
            for mod in list(resolved):
                deps = self.modules_def.get(mod, {}).get("depends_on", [])
                for dep in deps:
                    if dep not in resolved:
                        resolved.append(dep)
                        changed = True
        return resolved

    def _get_services(self, modules: List[str]) -> list:
        """Return list of (name, port, dockerfile, tier, desc) for selected modules."""
        services = []
        seen = set()
        for mod in modules:
            for svc in AGENT_SERVICES.get(mod, []):
                if svc[0] not in seen:
                    services.append(svc)
                    seen.add(svc[0])
        return sorted(services, key=lambda s: s[1])

    # ── docker-compose ──────────────────────────────────────────────

    def generate_docker_compose(self, modules: List[str], profile_name: str = "") -> str:
        """Generate docker-compose.yml content with only selected services."""
        resolved = self._resolve_modules(modules)
        services = self._get_services(resolved)

        header = textwrap.dedent(f"""\
            # ICDEV Docker Compose — Auto-generated by platform_setup.py
            # Profile: {profile_name or 'custom'}
            # Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
            #
            # Usage:
            #   docker compose up -d
            #   docker compose logs -f icdev-dashboard
            #   docker compose down
            #
            # Customization:
            #   - Copy this file and remove services you don't need
            #   - Create .env file with your secrets (see .env.template)
            #   - Adjust resource limits in deploy sections as needed

            """)

        lines = [header, "services:\n"]

        for name, port, dockerfile, tier, desc in services:
            agent_id = name.replace("icdev-", "")
            cmd = self._get_service_cmd(name, port)
            lines.append(f"  # {desc} ({tier} tier, port {port})")
            lines.append(f"  {name}:")
            lines.append(f"    build:")
            lines.append(f"      context: .")
            lines.append(f"      dockerfile: docker/{dockerfile}")
            lines.append(f"    image: icdev/{agent_id}:latest")
            lines.append(f"    container_name: {name}")
            lines.append(f"    ports:")
            lines.append(f'      - "{port}:{port}"')
            lines.append(f"    environment:")
            lines.append(f"      - ICDEV_DB_PATH=/app/data/icdev.db")
            lines.append(f"      - ICDEV_PROJECT_ROOT=/app")
            lines.append(f"      - PORT={port}")
            lines.append(f"    env_file:")
            lines.append(f"      - .env")
            lines.append(f"    volumes:")
            lines.append(f"      - icdev-data:/app/data")
            lines.append(f"    healthcheck:")
            lines.append(f"      test: ['CMD', 'curl', '-f', 'http://localhost:{port}/health']")
            lines.append(f"      interval: 30s")
            lines.append(f"      timeout: 10s")
            lines.append(f"      start_period: 10s")
            lines.append(f"      retries: 3")
            lines.append(f"    restart: unless-stopped")
            if cmd:
                lines.append(f"    command: {cmd}")
            lines.append("")

        lines.append("volumes:")
        lines.append("  icdev-data:")
        lines.append("    driver: local")
        lines.append("")

        return "\n".join(lines)

    def _get_service_cmd(self, name: str, port: int) -> str:
        """Return the command for a service container."""
        if name == "icdev-dashboard":
            return f'["python", "tools/dashboard/app.py", "--port", "{port}"]'
        if name == "icdev-api-gateway":
            return f'["python", "tools/saas/api_gateway.py", "--port", "{port}"]'
        agent_id = name.replace("icdev-", "") + "-agent"
        return f'["tools/a2a/agent_server.py", "--agent-id", "{agent_id}", "--port", "{port}"]'

    # ── K8s RBAC ────────────────────────────────────────────────────

    def generate_k8s_rbac(self, modules: List[str]) -> str:
        """Generate K8s RBAC manifest with ServiceAccounts, Roles, and RoleBindings."""
        resolved = self._resolve_modules(modules)
        services = self._get_services(resolved)

        lines = [
            "# ICDEV Kubernetes RBAC — Auto-generated by platform_setup.py",
            f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "",
        ]

        # ── Shared Role (baseline for all agents) ──
        lines.append("# Shared agent role — baseline permissions for all agents")
        lines.append("apiVersion: rbac.authorization.k8s.io/v1")
        lines.append("kind: Role")
        lines.append("metadata:")
        lines.append("  name: icdev-agent-role")
        lines.append("  namespace: icdev")
        lines.append("  labels:")
        lines.append("    app.kubernetes.io/part-of: icdev")
        lines.append("rules:")
        lines.append("  - apiGroups: ['']")
        lines.append("    resources: [pods, services, endpoints]")
        lines.append("    verbs: [get, list, watch]")
        lines.append("  - apiGroups: ['']")
        lines.append("    resources: [configmaps]")
        lines.append("    verbs: [get, list, watch]")
        lines.append("  - apiGroups: ['']")
        lines.append("    resources: [secrets]")
        lines.append("    verbs: [get]")
        lines.append("  - apiGroups: ['']")
        lines.append("    resources: [events]")
        lines.append("    verbs: [create, patch]")
        lines.append("")
        lines.append("---")

        # ── Elevated Role (for orchestrator — needs pod management) ──
        lines.append("# Orchestrator elevated role — needs pod/job management for agent lifecycle")
        lines.append("apiVersion: rbac.authorization.k8s.io/v1")
        lines.append("kind: Role")
        lines.append("metadata:")
        lines.append("  name: icdev-orchestrator-role")
        lines.append("  namespace: icdev")
        lines.append("  labels:")
        lines.append("    app.kubernetes.io/part-of: icdev")
        lines.append("rules:")
        lines.append("  - apiGroups: ['']")
        lines.append("    resources: [pods, services, endpoints, configmaps]")
        lines.append("    verbs: [get, list, watch, create, update, patch, delete]")
        lines.append("  - apiGroups: ['']")
        lines.append("    resources: [secrets]")
        lines.append("    verbs: [get, list]")
        lines.append("  - apiGroups: ['']")
        lines.append("    resources: [events]")
        lines.append("    verbs: [create, patch]")
        lines.append("  - apiGroups: [apps]")
        lines.append("    resources: [deployments, replicasets]")
        lines.append("    verbs: [get, list, watch, update, patch]")
        lines.append("  - apiGroups: [batch]")
        lines.append("    resources: [jobs]")
        lines.append("    verbs: [get, list, watch, create, delete]")
        lines.append("")
        lines.append("---")

        # ── Infrastructure Role (for infra agent — needs broader K8s access) ──
        has_infra = any(s[0] == "icdev-infrastructure" for s in services)
        if has_infra:
            lines.append("# Infrastructure agent role — needs broader access for IaC operations")
            lines.append("apiVersion: rbac.authorization.k8s.io/v1")
            lines.append("kind: Role")
            lines.append("metadata:")
            lines.append("  name: icdev-infrastructure-role")
            lines.append("  namespace: icdev")
            lines.append("  labels:")
            lines.append("    app.kubernetes.io/part-of: icdev")
            lines.append("rules:")
            lines.append("  - apiGroups: ['']")
            lines.append("    resources: [pods, services, endpoints, configmaps, persistentvolumeclaims]")
            lines.append("    verbs: [get, list, watch, create, update, patch, delete]")
            lines.append("  - apiGroups: ['']")
            lines.append("    resources: [secrets]")
            lines.append("    verbs: [get, list]")
            lines.append("  - apiGroups: ['']")
            lines.append("    resources: [events]")
            lines.append("    verbs: [create, patch]")
            lines.append("  - apiGroups: [apps]")
            lines.append("    resources: [deployments, statefulsets, replicasets]")
            lines.append("    verbs: [get, list, watch, create, update, patch, delete]")
            lines.append("  - apiGroups: [networking.k8s.io]")
            lines.append("    resources: [networkpolicies, ingresses]")
            lines.append("    verbs: [get, list, watch, create, update, patch]")
            lines.append("")
            lines.append("---")

        # ── Per-agent ServiceAccounts and RoleBindings ──
        for name, port, dockerfile, tier, desc in services:
            agent_id = name.replace("icdev-", "")
            sa_name = f"icdev-{agent_id}-sa"

            # Determine which role to bind
            if agent_id == "orchestrator":
                role_name = "icdev-orchestrator-role"
            elif agent_id == "infrastructure":
                role_name = "icdev-infrastructure-role"
            else:
                role_name = "icdev-agent-role"

            lines.append(f"# {desc} — ServiceAccount")
            lines.append("apiVersion: v1")
            lines.append("kind: ServiceAccount")
            lines.append("metadata:")
            lines.append(f"  name: {sa_name}")
            lines.append("  namespace: icdev")
            lines.append("  labels:")
            lines.append("    app.kubernetes.io/part-of: icdev")
            lines.append(f"    app.kubernetes.io/component: {agent_id}")
            lines.append(f"    icdev/tier: {tier}")
            lines.append("")
            lines.append("---")
            lines.append(f"# {desc} — RoleBinding")
            lines.append("apiVersion: rbac.authorization.k8s.io/v1")
            lines.append("kind: RoleBinding")
            lines.append("metadata:")
            lines.append(f"  name: {sa_name}-binding")
            lines.append("  namespace: icdev")
            lines.append("  labels:")
            lines.append("    app.kubernetes.io/part-of: icdev")
            lines.append(f"    app.kubernetes.io/component: {agent_id}")
            lines.append("subjects:")
            lines.append("  - kind: ServiceAccount")
            lines.append(f"    name: {sa_name}")
            lines.append("    namespace: icdev")
            lines.append("roleRef:")
            lines.append("  kind: Role")
            lines.append(f"  name: {role_name}")
            lines.append("  apiGroup: rbac.authorization.k8s.io")
            lines.append("")
            lines.append("---")

        # Remove trailing ---
        while lines and lines[-1].strip() == "---":
            lines.pop()
        lines.append("")

        return "\n".join(lines)

    # ── Helm values override ────────────────────────────────────────

    def generate_helm_values(self, modules: List[str]) -> str:
        """Generate a Helm values.yaml override enabling only selected agents."""
        resolved = self._resolve_modules(modules)

        # All possible helm agent keys (matching values.yaml structure)
        agent_keys = {
            "orchestrator": "core",
            "architect": "core",
            "builder": "builder",
            "compliance": "compliance_base",
            "security": "security",
            "infrastructure": "infrastructure",
            "knowledge": "monitoring",
            "monitor": "monitoring",
            "mbse": "mbse",
            "modernization": "modernization",
            "requirements-analyst": "ricoas",
            "supply-chain": "ricoas",
            "simulation": "ricoas",
            "devsecops": "devsecops_zta",
            "gateway": "gateway",
            "dashboard": "dashboard",
        }

        lines = [
            "# ICDEV Helm Values Override — Auto-generated by platform_setup.py",
            f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"# Modules: {', '.join(resolved)}",
            "#",
            "# Usage:",
            "#   helm install icdev deploy/helm/ -f deploy/helm/values.yaml -f this-file.yaml",
            "",
        ]

        for agent_key, module_name in sorted(agent_keys.items()):
            enabled = module_name in resolved
            lines.append(f"{agent_key}:")
            lines.append(f"  enabled: {'true' if enabled else 'false'}")
            lines.append("")

        return "\n".join(lines)

    # ── .env template ───────────────────────────────────────────────

    def generate_env_template(
        self,
        modules: List[str],
        compliance_posture: Optional[List[str]] = None,
    ) -> str:
        """Generate .env.template with relevant env vars for selected modules."""
        resolved = self._resolve_modules(modules)
        compliance_posture = compliance_posture or []

        lines = [
            "# ICDEV Environment Variables — Auto-generated by platform_setup.py",
            f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"# Modules: {', '.join(resolved)}",
            "#",
            "# Copy this file to .env and fill in your values:",
            "#   cp .env.template .env",
            "#   chmod 600 .env",
            "#",
            "# IMPORTANT: Never commit .env to version control.",
            "",
        ]

        for group_key, group_def in ENV_VAR_GROUPS.items():
            # Core and LLM are always included if any module needs them
            if group_key not in resolved and group_key != "core":
                continue
            lines.append(f"# --- {group_def['heading']} ---")
            for var_name, default, comment in group_def["vars"]:
                lines.append(f"# {comment}")
                if default:
                    lines.append(f"{var_name}={default}")
                else:
                    lines.append(f"# {var_name}=")
                lines.append("")

        return "\n".join(lines)

    # ── Install scripts ─────────────────────────────────────────────

    def generate_install_script_bash(
        self,
        profile_name: str,
        modules: List[str],
        platform: str = "docker",
    ) -> str:
        """Generate install.sh for Linux/macOS."""
        module_csv = ",".join(modules)

        return textwrap.dedent(f"""\
            #!/usr/bin/env bash
            # ICDEV Modular Installer -- Linux/macOS
            # Profile: {profile_name}
            # Platform: {platform}
            set -euo pipefail

            # ── Colors ──────────────────────────────────────────────────
            RED='\\033[0;31m'
            GREEN='\\033[0;32m'
            YELLOW='\\033[1;33m'
            CYAN='\\033[0;36m'
            NC='\\033[0m'

            info()  {{ echo -e "${{GREEN}}[INFO]${{NC}}  $*"; }}
            warn()  {{ echo -e "${{YELLOW}}[WARN]${{NC}}  $*"; }}
            err()   {{ echo -e "${{RED}}[ERROR]${{NC}} $*" >&2; }}
            step()  {{ echo -e "\\n${{CYAN}}==> $*${{NC}}"; }}

            echo ""
            echo -e "${{CYAN}}================================================================${{NC}}"
            echo -e "${{CYAN}}  ICDEV Modular Installer${{NC}}"
            echo -e "${{CYAN}}  Profile: {profile_name}${{NC}}"
            echo -e "${{CYAN}}================================================================${{NC}}"
            echo ""

            SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
            PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
            cd "$PROJECT_ROOT"

            # ── Step 1: Prerequisites ───────────────────────────────────
            step "Step 1/5: Checking prerequisites"

            if ! command -v python3 &>/dev/null; then
                err "python3 is required but not found."
                err "Install Python 3.11+ from https://www.python.org/downloads/"
                exit 1
            fi
            PYTHON_VERSION=$(python3 --version 2>&1 | awk '{{print $2}}')
            info "Python: $PYTHON_VERSION"

            if ! python3 -c "import pip" &>/dev/null; then
                err "pip is required but not found. Run: python3 -m ensurepip"
                exit 1
            fi
            info "pip available"

            # Optional: docker
            if command -v docker &>/dev/null; then
                info "Docker: $(docker --version 2>/dev/null | head -1)"
            else
                warn "Docker not found. Install Docker for container-based deployment."
            fi

            # Optional: kubectl
            if command -v kubectl &>/dev/null; then
                info "kubectl: $(kubectl version --client --short 2>/dev/null || kubectl version --client 2>/dev/null | head -1)"
            else
                warn "kubectl not found. Install for Kubernetes deployment."
            fi

            # Optional: helm
            if command -v helm &>/dev/null; then
                info "Helm: $(helm version --short 2>/dev/null)"
            else
                warn "Helm not found. Install for Helm-based deployment."
            fi

            # ── Step 2: Python Virtual Environment ──────────────────────
            step "Step 2/5: Setting up Python virtual environment"

            VENV_DIR="$PROJECT_ROOT/.venv"
            if [[ -d "$VENV_DIR" ]]; then
                info "Existing venv found at $VENV_DIR"
            else
                info "Creating virtual environment at $VENV_DIR"
                python3 -m venv "$VENV_DIR"
            fi

            source "$VENV_DIR/bin/activate"
            info "Activated venv: $(which python)"

            # ── Step 3: Install Dependencies ────────────────────────────
            step "Step 3/5: Installing Python dependencies"

            if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
                pip install --quiet --upgrade pip
                pip install --quiet -r "$PROJECT_ROOT/requirements.txt"
                info "Dependencies installed from requirements.txt"
            else
                warn "requirements.txt not found -- skipping pip install"
            fi

            # ── Step 4: Initialize Database ─────────────────────────────
            step "Step 4/5: Initializing ICDEV database"

            mkdir -p "$PROJECT_ROOT/data"
            python tools/db/init_icdev_db.py
            info "Database initialized at data/icdev.db"

            # ── Step 5: Run Installer ───────────────────────────────────
            step "Step 5/5: Running ICDEV modular installer"

            if [[ -n "${{1:-}}" ]]; then
                python tools/installer/installer.py --profile "$1"
            else
                python tools/installer/installer.py --profile {profile_name} --modules {module_csv}
            fi

            echo ""
            echo -e "${{GREEN}}================================================================${{NC}}"
            echo -e "${{GREEN}}  ICDEV installation complete!${{NC}}"
            echo -e "${{GREEN}}================================================================${{NC}}"
            echo ""
            echo "  Next steps:"
            echo "    source .venv/bin/activate"
            echo "    python tools/dashboard/app.py          # Start dashboard on port 5000"
            echo "    python tools/testing/health_check.py   # Verify installation"
            echo ""
        """)

    def generate_install_script_powershell(
        self,
        profile_name: str,
        modules: List[str],
        platform: str = "docker",
    ) -> str:
        """Generate install.ps1 for Windows."""
        module_csv = ",".join(modules)

        return textwrap.dedent(f"""\
            # ICDEV Modular Installer -- Windows PowerShell
            # Profile: {profile_name}
            # Platform: {platform}
            #Requires -Version 5.1

            $ErrorActionPreference = "Stop"

            function Write-Info  {{ param($msg) Write-Host "[INFO]  $msg" -ForegroundColor Green }}
            function Write-Warn  {{ param($msg) Write-Host "[WARN]  $msg" -ForegroundColor Yellow }}
            function Write-Err   {{ param($msg) Write-Host "[ERROR] $msg" -ForegroundColor Red }}
            function Write-Step  {{ param($msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }}

            Write-Host ""
            Write-Host "================================================================" -ForegroundColor Cyan
            Write-Host "  ICDEV Modular Installer" -ForegroundColor Cyan
            Write-Host "  Profile: {profile_name}" -ForegroundColor Cyan
            Write-Host "================================================================" -ForegroundColor Cyan
            Write-Host ""

            $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
            $ProjectRoot = Split-Path -Parent $ScriptDir
            Set-Location $ProjectRoot

            # -- Step 1: Prerequisites ------------------------------------------------
            Write-Step "Step 1/5: Checking prerequisites"

            $PythonCmd = $null
            foreach ($cmd in @("python", "python3")) {{
                try {{
                    $ver = & $cmd --version 2>&1
                    if ($LASTEXITCODE -eq 0) {{
                        $PythonCmd = $cmd
                        Write-Info "Python: $ver"
                        break
                    }}
                }} catch {{}}
            }}

            if (-not $PythonCmd) {{
                Write-Err "Python 3.11+ is required but not found."
                Write-Err "Install from https://www.python.org/downloads/"
                exit 1
            }}

            try {{
                & $PythonCmd -c "import pip" 2>&1 | Out-Null
                Write-Info "pip available"
            }} catch {{
                Write-Err "pip is required. Run: $PythonCmd -m ensurepip"
                exit 1
            }}

            # Optional: docker
            try {{
                $dockerVer = docker --version 2>&1
                if ($LASTEXITCODE -eq 0) {{ Write-Info "Docker: $dockerVer" }}
            }} catch {{
                Write-Warn "Docker not found. Install Docker Desktop for container deployment."
            }}

            # Optional: kubectl
            try {{
                $kubectlVer = kubectl version --client 2>&1 | Select-Object -First 1
                if ($LASTEXITCODE -eq 0) {{ Write-Info "kubectl: $kubectlVer" }}
            }} catch {{
                Write-Warn "kubectl not found. Install for Kubernetes deployment."
            }}

            # -- Step 2: Python Virtual Environment ------------------------------------
            Write-Step "Step 2/5: Setting up Python virtual environment"

            $VenvDir = Join-Path $ProjectRoot ".venv"
            if (Test-Path $VenvDir) {{
                Write-Info "Existing venv found at $VenvDir"
            }} else {{
                Write-Info "Creating virtual environment at $VenvDir"
                & $PythonCmd -m venv $VenvDir
            }}

            $ActivateScript = Join-Path $VenvDir "Scripts" "Activate.ps1"
            if (Test-Path $ActivateScript) {{
                . $ActivateScript
                Write-Info "Activated venv"
            }} else {{
                Write-Err "Could not find venv activation script at $ActivateScript"
                exit 1
            }}

            # -- Step 3: Install Dependencies ------------------------------------------
            Write-Step "Step 3/5: Installing Python dependencies"

            $ReqFile = Join-Path $ProjectRoot "requirements.txt"
            if (Test-Path $ReqFile) {{
                pip install --quiet --upgrade pip
                pip install --quiet -r $ReqFile
                Write-Info "Dependencies installed from requirements.txt"
            }} else {{
                Write-Warn "requirements.txt not found -- skipping pip install"
            }}

            # -- Step 4: Initialize Database -------------------------------------------
            Write-Step "Step 4/5: Initializing ICDEV database"

            $DataDir = Join-Path $ProjectRoot "data"
            if (-not (Test-Path $DataDir)) {{ New-Item -ItemType Directory -Path $DataDir | Out-Null }}

            & $PythonCmd tools/db/init_icdev_db.py
            Write-Info "Database initialized at data/icdev.db"

            # -- Step 5: Run Installer -------------------------------------------------
            Write-Step "Step 5/5: Running ICDEV modular installer"

            if ($args.Count -gt 0) {{
                & $PythonCmd tools/installer/installer.py --profile $args[0]
            }} else {{
                & $PythonCmd tools/installer/installer.py --profile {profile_name} --modules {module_csv}
            }}

            Write-Host ""
            Write-Host "================================================================" -ForegroundColor Green
            Write-Host "  ICDEV installation complete!" -ForegroundColor Green
            Write-Host "================================================================" -ForegroundColor Green
            Write-Host ""
            Write-Host "  Next steps:"
            Write-Host "    .venv\\Scripts\\Activate.ps1"
            Write-Host "    python tools/dashboard/app.py          # Start dashboard on port 5000"
            Write-Host "    python tools/testing/health_check.py   # Verify installation"
            Write-Host ""
        """)

    # ── List agents ─────────────────────────────────────────────────

    def list_agents(self, modules: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """List all agent services, optionally filtered by modules."""
        if modules:
            resolved = self._resolve_modules(modules)
            services = self._get_services(resolved)
        else:
            services = []
            for svcs in AGENT_SERVICES.values():
                services.extend(svcs)
            services = sorted(set(services), key=lambda s: s[1])

        return [
            {
                "name": s[0],
                "port": s[1],
                "dockerfile": s[2],
                "tier": s[3],
                "description": s[4],
            }
            for s in services
        ]


# ── CLI ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Platform Setup — generate deployment artifacts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s --generate docker --modules core,llm,builder,dashboard
              %(prog)s --generate k8s-rbac --modules core,builder --output k8s/rbac.yaml
              %(prog)s --generate env --modules core,llm --output .env.template
              %(prog)s --generate helm-values --modules core,llm,builder
              %(prog)s --generate install-bash --profile isv_startup
              %(prog)s --generate install-ps1 --profile dod_team
              %(prog)s --list-agents --json
        """),
    )
    parser.add_argument(
        "--generate",
        choices=["docker", "k8s-rbac", "env", "helm-values", "install-bash", "install-ps1"],
        help="Type of artifact to generate",
    )
    parser.add_argument(
        "--modules",
        type=str,
        default="",
        help="Comma-separated list of modules (e.g., core,llm,builder,dashboard)",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default="",
        help="Deployment profile name (e.g., isv_startup, dod_team, govcloud_full)",
    )
    parser.add_argument(
        "--compliance",
        type=str,
        default="",
        help="Comma-separated compliance posture modules for env template",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output file path (prints to stdout if omitted)",
    )
    parser.add_argument(
        "--list-agents",
        action="store_true",
        help="List all agent services and their ports",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format (for --list-agents)",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        default=str(DEFAULT_MANIFEST_PATH),
        help="Path to installation_manifest.yaml",
    )
    parser.add_argument(
        "--profiles-file",
        type=str,
        default=str(DEFAULT_PROFILES_PATH),
        help="Path to deployment_profiles.yaml",
    )

    args = parser.parse_args()
    setup = PlatformSetup(args.manifest, args.profiles_file)

    # Resolve modules from profile or CLI
    modules: List[str] = []
    profile_name = args.profile or "custom"

    if args.profile and args.profile in setup.profiles_def:
        profile = setup.profiles_def[args.profile]
        profile_modules = profile.get("modules", [])
        if isinstance(profile_modules, str) and profile_modules.upper() == "ALL":
            modules = list(setup.modules_def.keys())
        elif isinstance(profile_modules, list):
            modules = profile_modules
        profile_name = profile.get("name", args.profile)

    if args.modules:
        cli_modules = [m.strip() for m in args.modules.split(",") if m.strip()]
        # CLI modules override or extend profile modules
        if modules:
            for m in cli_modules:
                if m not in modules:
                    modules.append(m)
        else:
            modules = cli_modules

    if not modules and not args.list_agents:
        modules = ["core", "llm", "compliance_base"]

    compliance = [c.strip() for c in args.compliance.split(",") if c.strip()] if args.compliance else []

    # ── Handle --list-agents ──
    if args.list_agents:
        agents = setup.list_agents(modules if modules else None)
        if args.json:
            print(json.dumps({"agents": agents, "count": len(agents)}, indent=2))
        else:
            print(f"{'Name':<28} {'Port':<7} {'Tier':<10} Description")
            print("-" * 75)
            for a in agents:
                print(f"{a['name']:<28} {a['port']:<7} {a['tier']:<10} {a['description']}")
        return

    # ── Generate artifact ──
    if not args.generate:
        parser.print_help()
        return

    output = ""
    if args.generate == "docker":
        output = setup.generate_docker_compose(modules, profile_name)
    elif args.generate == "k8s-rbac":
        output = setup.generate_k8s_rbac(modules)
    elif args.generate == "env":
        output = setup.generate_env_template(modules, compliance)
    elif args.generate == "helm-values":
        output = setup.generate_helm_values(modules)
    elif args.generate == "install-bash":
        output = setup.generate_install_script_bash(profile_name, modules)
    elif args.generate == "install-ps1":
        output = setup.generate_install_script_powershell(profile_name, modules)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(output)
        print(f"Generated: {out_path}")
    else:
        print(output)


if __name__ == "__main__":
    main()
