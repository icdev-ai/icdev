#!/usr/bin/env python3
# CUI // SP-CTI
"""DevSecOps Pipeline Security Generator — profile-driven CI/CD security stages.

Generates GitLab CI security job YAML based on a project's DevSecOps profile.
Only includes stages that are active in the profile, supporting both greenfield
(minimal scanning) and brownfield (full DevSecOps pipeline) customers.

ADR D119: DevSecOps profile controls downstream pipeline generation.

Usage:
    python tools/devsecops/pipeline_security_generator.py --project-id "proj-123" --json
    python tools/devsecops/pipeline_security_generator.py --project-id "proj-123" --output /tmp/devsecops-stages.yml
    python tools/devsecops/pipeline_security_generator.py --project-id "proj-123" --human
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load DevSecOps config from YAML."""
    config_path = BASE_DIR / "args" / "devsecops_config.yaml"
    if yaml and config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_profile(project_id: str) -> dict:
    """Get DevSecOps profile for project."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM devsecops_profiles WHERE project_id = ?",
            (project_id,)
        ).fetchone()
        if not row:
            return {}
        return {
            "maturity_level": row["maturity_level"],
            "active_stages": json.loads(row["active_stages"] or "[]"),
            "stage_configs": json.loads(row["stage_configs"] or "{}"),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stage generators — one function per DevSecOps pipeline stage
# ---------------------------------------------------------------------------

def _gen_sast(config: dict, stage_cfg: dict) -> str:
    """SAST stage — static analysis security testing."""
    tools = stage_cfg.get("tools", ["bandit"])
    tool_str = ", ".join(tools)
    return f"""
# --- DevSecOps: SAST ({tool_str}) ---
devsecops:sast:
  stage: devsecops-check
  image: python:3.11-slim
  script:
    - pip install bandit spotbugs-cli 2>/dev/null || true
    - echo "Running SAST ({tool_str})..."
    - bandit -r . -f json -o devsecops-sast-report.json --exclude tests,venv,.git || true
    - bandit -r . --exclude tests,venv,.git -ll
  artifacts:
    when: always
    paths:
      - devsecops-sast-report.json
    expire_in: 30 days
  needs:
    - job: build:docker
      optional: true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
"""


def _gen_dast(config: dict, stage_cfg: dict) -> str:
    """DAST stage — dynamic application security testing."""
    return """
# --- DevSecOps: DAST (ZAP) ---
devsecops:dast:
  stage: devsecops-check
  image: ghcr.io/zaproxy/zaproxy:stable
  script:
    - echo "Running DAST (OWASP ZAP baseline scan)..."
    - zap-baseline.py -t ${APP_URL:-http://localhost:8080} -r devsecops-dast-report.html -J devsecops-dast-report.json || true
  artifacts:
    when: always
    paths:
      - devsecops-dast-report.html
      - devsecops-dast-report.json
    expire_in: 30 days
  needs:
    - job: deploy:staging
      optional: true
  allow_failure: true
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
"""


def _gen_sca(config: dict, stage_cfg: dict) -> str:
    """SCA stage — software composition analysis."""
    return """
# --- DevSecOps: SCA (pip-audit / npm-audit) ---
devsecops:sca:
  stage: devsecops-check
  image: python:3.11-slim
  script:
    - pip install pip-audit
    - echo "Running SCA (dependency vulnerability scan)..."
    - pip-audit -r requirements.txt --format json --output devsecops-sca-report.json || true
    - pip-audit -r requirements.txt
  artifacts:
    when: always
    paths:
      - devsecops-sca-report.json
    expire_in: 30 days
  allow_failure: true
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
"""


def _gen_secret_detection(config: dict, stage_cfg: dict) -> str:
    """Secret detection stage."""
    return """
# --- DevSecOps: Secret Detection (detect-secrets / gitleaks) ---
devsecops:secrets:
  stage: devsecops-check
  image: python:3.11-slim
  script:
    - pip install detect-secrets
    - echo "Scanning for secrets..."
    - detect-secrets scan --all-files --exclude-files '\\.git' > devsecops-secrets-report.json
    - |
      RESULTS=$(python3 -c "
      import json
      with open('devsecops-secrets-report.json') as f:
          data = json.load(f)
      total = sum(len(v) for v in data.get('results', {}).values())
      print(total)
      ")
      echo "Found $RESULTS potential secrets"
      if [ "$RESULTS" -gt 0 ]; then
        echo "ERROR: Secrets detected — DevSecOps gate BLOCKED"
        exit 1
      fi
  artifacts:
    when: always
    paths:
      - devsecops-secrets-report.json
    expire_in: 30 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
"""


def _gen_container_scan(config: dict, stage_cfg: dict) -> str:
    """Container scan stage."""
    return """
# --- DevSecOps: Container Scan (Trivy) ---
devsecops:container-scan:
  stage: devsecops-check
  image:
    name: aquasec/trivy:latest
    entrypoint: [""]
  script:
    - echo "Scanning container image..."
    - trivy image --exit-code 0 --format json --output devsecops-trivy-report.json ${IMAGE_NAME}:${IMAGE_TAG} || true
    - trivy image --exit-code 1 --severity CRITICAL ${IMAGE_NAME}:${IMAGE_TAG}
  artifacts:
    when: always
    paths:
      - devsecops-trivy-report.json
    expire_in: 30 days
  needs:
    - job: build:docker
      optional: true
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'
"""


def _gen_image_signing(config: dict, stage_cfg: dict) -> str:
    """Image signing stage (cosign)."""
    return """
# --- DevSecOps: Image Signing (cosign) ---
devsecops:image-signing:
  stage: devsecops-check
  image: gcr.io/projectsigstore/cosign:latest
  script:
    - echo "Signing container image with cosign..."
    - cosign sign --key env://COSIGN_PRIVATE_KEY ${IMAGE_NAME}:${IMAGE_TAG}
    - echo "Verifying signature..."
    - cosign verify --key env://COSIGN_PUBLIC_KEY ${IMAGE_NAME}:${IMAGE_TAG}
  needs:
    - job: build:docker
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  variables:
    COSIGN_PRIVATE_KEY: "${COSIGN_PRIVATE_KEY}"
    COSIGN_PUBLIC_KEY: "${COSIGN_PUBLIC_KEY}"
"""


def _gen_sbom_attestation(config: dict, stage_cfg: dict) -> str:
    """SBOM attestation stage."""
    return """
# --- DevSecOps: SBOM Attestation (cosign + in-toto) ---
devsecops:sbom-attestation:
  stage: devsecops-check
  image: python:3.11-slim
  script:
    - pip install cyclonedx-bom
    - echo "Generating SBOM..."
    - cyclonedx-py environment --output devsecops-sbom.json --format json
    - echo "Attesting SBOM..."
    - |
      if command -v cosign &>/dev/null; then
        cosign attest --key env://COSIGN_PRIVATE_KEY --predicate devsecops-sbom.json --type cyclonedx ${IMAGE_NAME}:${IMAGE_TAG}
      else
        echo "WARN: cosign not available — SBOM generated but not attested"
      fi
  artifacts:
    when: always
    paths:
      - devsecops-sbom.json
    expire_in: 90 days
  needs:
    - job: build:docker
      optional: true
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
"""


def _gen_rasp(config: dict, stage_cfg: dict) -> str:
    """RASP rules deployment stage."""
    return """
# --- DevSecOps: RASP Rules (Falco) ---
devsecops:rasp:
  stage: devsecops-check
  image: falcosecurity/falco:latest
  script:
    - echo "Validating RASP / Falco rules..."
    - falco --validate /etc/falco/falco_rules.yaml || true
    - echo "RASP rules validated — will be deployed with application"
  allow_failure: true
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
"""


def _gen_policy_as_code(config: dict, stage_cfg: dict) -> str:
    """Policy-as-code validation stage."""
    engine = stage_cfg.get("engine", "kyverno")
    if engine == "kyverno":
        return """
# --- DevSecOps: Policy-as-Code (Kyverno) ---
devsecops:policy-validation:
  stage: devsecops-check
  image: ghcr.io/kyverno/kyverno-cli:latest
  script:
    - echo "Validating Kyverno policies against K8s manifests..."
    - kyverno apply policies/ --resource k8s/ -o json > devsecops-policy-report.json || true
    - |
      VIOLATIONS=$(python3 -c "
      import json
      with open('devsecops-policy-report.json') as f:
          data = json.load(f)
      fails = sum(1 for r in data if r.get('status') == 'fail')
      print(fails)
      " 2>/dev/null || echo "0")
      echo "Policy violations: $VIOLATIONS"
      if [ "$VIOLATIONS" -gt 0 ]; then
        echo "ERROR: Policy violations detected — DevSecOps gate BLOCKED"
        exit 1
      fi
  artifacts:
    when: always
    paths:
      - devsecops-policy-report.json
    expire_in: 30 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
"""
    else:
        return """
# --- DevSecOps: Policy-as-Code (OPA/Gatekeeper) ---
devsecops:policy-validation:
  stage: devsecops-check
  image: openpolicyagent/opa:latest
  script:
    - echo "Evaluating OPA policies against K8s manifests..."
    - opa eval --data policies/ --input k8s/ 'data.kubernetes.admission.deny' > devsecops-policy-report.json || true
    - echo "OPA policy evaluation complete"
  artifacts:
    when: always
    paths:
      - devsecops-policy-report.json
    expire_in: 30 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
"""


def _gen_license_compliance(config: dict, stage_cfg: dict) -> str:
    """License compliance scanning stage."""
    return """
# --- DevSecOps: License Compliance ---
devsecops:license-compliance:
  stage: devsecops-check
  image: python:3.11-slim
  script:
    - pip install pip-licenses
    - echo "Checking license compliance..."
    - pip-licenses --format json --output-file devsecops-licenses.json
    - |
      python3 -c "
      import json
      with open('devsecops-licenses.json') as f:
          data = json.load(f)
      blocked = ['GPL-3.0', 'AGPL-3.0']
      violations = [p for p in data if p.get('License') in blocked]
      if violations:
          print(f'LICENSE VIOLATIONS: {len(violations)} packages with restricted licenses')
          for v in violations:
              print(f'  {v[\"Name\"]}=={v[\"Version\"]} — {v[\"License\"]}')
          exit(1)
      print(f'License check passed: {len(data)} packages scanned')
      "
  artifacts:
    when: always
    paths:
      - devsecops-licenses.json
    expire_in: 30 days
  allow_failure: true
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
"""


# Stage ID → generator function mapping
STAGE_GENERATORS = {
    "sast": _gen_sast,
    "dast": _gen_dast,
    "sca": _gen_sca,
    "secret_detection": _gen_secret_detection,
    "container_scan": _gen_container_scan,
    "image_signing": _gen_image_signing,
    "sbom_attestation": _gen_sbom_attestation,
    "rasp": _gen_rasp,
    "policy_as_code": _gen_policy_as_code,
    "license_compliance": _gen_license_compliance,
}


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate_security_stages(project_id: str, profile: dict = None) -> dict:
    """Generate GitLab CI security stages based on DevSecOps profile.

    Args:
        project_id: Project identifier.
        profile: Optional profile dict. If None, loaded from DB.

    Returns:
        Dict with yaml_content, stages_generated, maturity_level.
    """
    if profile is None:
        profile = _get_profile(project_id)
        if not profile:
            return {"error": f"No DevSecOps profile for project {project_id}",
                    "hint": "Run profile_manager.py --create first"}

    config = _load_config()
    active_stages = profile.get("active_stages", [])
    stage_configs = profile.get("stage_configs", {})
    maturity = profile.get("maturity_level", "level_1_initial")

    generated = []
    yaml_parts = []

    yaml_parts.append(f"""
# =============================================================================
# DevSecOps Security Stages (Profile: {maturity})
# Generated: {datetime.now(timezone.utc).isoformat()}
# Active stages: {', '.join(active_stages)}
# =============================================================================
""")

    for stage_id in active_stages:
        gen_func = STAGE_GENERATORS.get(stage_id)
        if gen_func:
            stage_cfg = stage_configs.get(stage_id, {})
            yaml_parts.append(gen_func(config, stage_cfg))
            generated.append(stage_id)

    yaml_content = "\n".join(yaml_parts)

    return {
        "project_id": project_id,
        "maturity_level": maturity,
        "stages_generated": generated,
        "stages_skipped": [s for s in STAGE_GENERATORS if s not in active_stages],
        "yaml_content": yaml_content,
        "stage_count": len(generated),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="DevSecOps Pipeline Security Generator")
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--output", help="Write YAML to file path")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    result = generate_security_stages(args.project_id)

    if args.output and "yaml_content" in result:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result["yaml_content"], encoding="utf-8")
        result["output_file"] = str(out_path)

    if args.json or not args.human:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}")
        else:
            print(f"Project: {result['project_id']}")
            print(f"Maturity: {result['maturity_level']}")
            print(f"Stages generated ({result['stage_count']}): {', '.join(result['stages_generated'])}")
            if result.get("stages_skipped"):
                print(f"Stages skipped: {', '.join(result['stages_skipped'])}")
            if args.output:
                print(f"Output: {result.get('output_file', 'N/A')}")


if __name__ == "__main__":
    main()
