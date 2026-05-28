# CUI // SP-CTI
"""Pillar 6 — Configuration: CI/CD, dev environment, IaC, env var management."""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _exists,
    _glob_files,
    _read,
    _search,
)

# ---------------------------------------------------------------------------
# Anomaly-detection threshold loader
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[5] / "args" / "agent_readiness_config.yaml"
_DEFAULTS: dict[str, Any] = {
    "min_makefile_targets": 3,
    "min_npm_scripts": 3,
    # Values below these are anomalously low for a repo claiming CI/IaC readiness.
    "min_ci_workflows": 1,
    "min_iac_files": 1,
    # Deployment keyword patterns for CD detection — configurable via agent_readiness_config.yaml.
    "deploy_keywords": [
        "deploy", "release", "publish",
        r"push.*image", r"helm\s+upgrade", r"kubectl\s+apply",
    ],
}


@lru_cache(maxsize=1)
def _load_thresholds() -> dict[str, int]:
    """Load all configuration-pillar anomaly-detection thresholds from args/agent_readiness_config.yaml.

    Falls back to hard-coded defaults if the config file is absent or malformed,
    so the pillar degrades gracefully in air-gap or stripped environments.
    """
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        cfg = data.get("pillars", {}).get("configuration", {})
        tr = cfg.get("task_runner", {})
        ci = cfg.get("ci_pipeline", {})
        iac = cfg.get("iac", {})
        cd = cfg.get("cd_deployment", {})
        raw_keywords = cd.get("deploy_keywords", _DEFAULTS["deploy_keywords"])
        deploy_keywords = [str(k) for k in raw_keywords] if raw_keywords else list(_DEFAULTS["deploy_keywords"])
        return {
            "min_makefile_targets": int(tr.get("min_makefile_targets", _DEFAULTS["min_makefile_targets"])),
            "min_npm_scripts": int(tr.get("min_npm_scripts", _DEFAULTS["min_npm_scripts"])),
            "min_ci_workflows": int(ci.get("min_workflows", _DEFAULTS["min_ci_workflows"])),
            "min_iac_files": int(iac.get("min_files", _DEFAULTS["min_iac_files"])),
            "deploy_keywords": deploy_keywords,
        }
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


def _check_ci_pipeline(repo: pathlib.Path) -> CriterionResult:
    cid = "ci-pipeline"
    thresholds = _load_thresholds()
    min_workflows = thresholds["min_ci_workflows"]
    gh_workflows = _glob_files(repo, ".github/workflows/*.yml") + _glob_files(repo, ".github/workflows/*.yaml")
    if gh_workflows:
        if len(gh_workflows) < min_workflows:
            return CriterionResult(
                cid, False,
                f"Only {len(gh_workflows)} GitHub Actions workflow(s) found (min {min_workflows}).",
                "Add more CI workflow files to reach the minimum required for anomaly-detection.",
            )
        return CriterionResult(cid, True, f"GitHub Actions CI found ({len(gh_workflows)} workflow(s))")
    if _exists(repo, ".gitlab-ci.yml", ".gitlab-ci.yaml"):
        return CriterionResult(cid, True, "GitLab CI pipeline configured")
    if _exists(repo, ".circleci/config.yml", ".circleci/config.yaml"):
        return CriterionResult(cid, True, "CircleCI pipeline configured")
    if _exists(repo, "Jenkinsfile"):
        return CriterionResult(cid, True, "Jenkins pipeline (Jenkinsfile) found")
    if _exists(repo, ".travis.yml", ".travis.yaml"):
        return CriterionResult(cid, True, "Travis CI configured")
    if _exists(repo, "azure-pipelines.yml", "azure-pipelines.yaml"):
        return CriterionResult(cid, True, "Azure Pipelines configured")
    return CriterionResult(cid, False, "No CI/CD pipeline configuration found.",
                           "Add a CI pipeline (GitHub Actions, GitLab CI, Jenkins, etc.).")


def _check_cd_deployment(repo: pathlib.Path) -> CriterionResult:
    cid = "cd-deployment"
    thresholds = _load_thresholds()
    keywords = thresholds.get("deploy_keywords", _DEFAULTS["deploy_keywords"])
    deploy_patterns = r"\b(?:" + "|".join(keywords) + r")\b"
    # Look for deployment steps in CI
    ci_files = (
        _glob_files(repo, ".github/workflows/*.yml")
        + _glob_files(repo, ".github/workflows/*.yaml")
        + ([repo / ".gitlab-ci.yml"] if (repo / ".gitlab-ci.yml").exists() else [])
    )
    for f in ci_files:
        content = pathlib.Path(f).read_text(encoding="utf-8", errors="replace")
        if _search(content, deploy_patterns):
            return CriterionResult(cid, True, f"Deployment step found in CI: {pathlib.Path(f).name}")
    if _exists(repo, "Makefile"):
        mk = _read(repo, "Makefile") or ""
        if _search(mk, r"^deploy\b|^release\b", flags=0):
            return CriterionResult(cid, True, "Deploy target found in Makefile")
    return CriterionResult(cid, False, "No CD deployment step found.",
                           "Add a deployment step to your CI pipeline for automated releases.")


def _check_iac_present(repo: pathlib.Path) -> CriterionResult:
    cid = "iac-present"
    thresholds = _load_thresholds()
    min_iac = thresholds["min_iac_files"]
    if _exists(repo, "terraform", "infra", "infrastructure", "tf"):
        d = _exists(repo, "terraform", "infra", "infrastructure", "tf")
        tf_files = _glob_files(repo / d if d else repo, "**/*.tf")
        if tf_files:
            if len(tf_files) < min_iac:
                return CriterionResult(
                    cid, False,
                    f"Only {len(tf_files)} Terraform file(s) found in {d}/ (min {min_iac}).",
                    "Add more .tf files to reach the anomaly-detection minimum.",
                )
            return CriterionResult(cid, True, f"Terraform IaC found in {d}/")
    tf_files = _glob_files(repo, "**/*.tf")
    if tf_files:
        if len(tf_files) >= min_iac:
            return CriterionResult(cid, True, f"Terraform files found ({len(tf_files)} .tf files)")
        return CriterionResult(
            cid, False,
            f"Only {len(tf_files)} Terraform file(s) found (min {min_iac}).",
            "Add more .tf files to reach the anomaly-detection minimum.",
        )
    helm = _glob_files(repo, "**/Chart.yaml")
    if helm:
        if len(helm) >= min_iac:
            return CriterionResult(cid, True, f"Helm chart(s) found ({len(helm)} Chart.yaml)")
        return CriterionResult(
            cid, False,
            f"Only {len(helm)} Helm Chart.yaml file(s) found (min {min_iac}).",
            "Add more Helm charts to reach the anomaly-detection minimum.",
        )
    if _exists(repo, "ansible", "playbooks") or _glob_files(repo, "**/*.playbook.yml"):
        return CriterionResult(cid, True, "Ansible IaC found")
    k8s = _glob_files(repo, "**/*.k8s.yaml") + _glob_files(repo, "**/k8s/**/*.yaml")
    if k8s:
        if len(k8s) >= min_iac:
            return CriterionResult(cid, True, f"Kubernetes manifests found ({len(k8s)} files)")
        return CriterionResult(
            cid, False,
            f"Only {len(k8s)} Kubernetes manifest(s) found (min {min_iac}).",
            "Add more K8s manifests to reach the anomaly-detection minimum.",
        )
    return CriterionResult(cid, False, "No Infrastructure-as-Code configuration found.",
                           "Add Terraform, Helm, or Kubernetes manifests for reproducible infrastructure.")


def _check_editorconfig(repo: pathlib.Path) -> CriterionResult:
    cid = "editorconfig"
    if _exists(repo, ".editorconfig"):
        return CriterionResult(cid, True, ".editorconfig found (editor consistency enforced)")
    return CriterionResult(cid, False, "No .editorconfig found.",
                           "Add .editorconfig to enforce consistent editor settings across contributors.")


def _check_makefile_or_taskfile(repo: pathlib.Path) -> CriterionResult:
    cid = "task-runner"
    thresholds = _load_thresholds()
    min_makefile = thresholds["min_makefile_targets"]
    min_scripts = thresholds["min_npm_scripts"]
    makefile_anomaly: str | None = None
    if _exists(repo, "Makefile", "makefile"):
        mk = _read(repo, "Makefile") or _read(repo, "makefile") or ""
        targets = sum(1 for l in mk.splitlines() if l and not l.startswith("\t") and ":" in l and not l.startswith("#"))
        if targets >= min_makefile:
            return CriterionResult(cid, True, f"Makefile with {targets} targets found")
        makefile_anomaly = (
            f"Makefile found but only {targets} target(s) (min {min_makefile}) — "
            "anomalously low for a project claiming task automation readiness."
        )
    if _exists(repo, "Taskfile.yml", "Taskfile.yaml", "Taskfile.dist.yml"):
        return CriterionResult(cid, True, "Taskfile task runner found")
    pkg = _read(repo, "package.json")
    if pkg and _search(pkg, r'"scripts"\s*:\s*\{'):
        import json
        try:
            scripts = json.loads(pkg).get("scripts", {})
            if len(scripts) >= min_scripts:
                return CriterionResult(cid, True, f"npm scripts ({len(scripts)} tasks) in package.json")
        except Exception:
            pass
    if makefile_anomaly:
        return CriterionResult(
            cid, False, makefile_anomaly,
            f"Add more Makefile targets to reach the anomaly-detection minimum ({min_makefile}), "
            "or add a Taskfile/npm scripts as an alternative task runner.",
        )
    return CriterionResult(cid, False, "No task runner found.",
                           "Add a Makefile, Taskfile, or npm scripts for common dev tasks.")


PILLAR = Pillar(
    id="configuration",
    name="Configuration",
    description="CI/CD pipeline, CD deployment, IaC, editor config, and task runner.",
    criteria=[
        Criterion("ci-pipeline", "CI pipeline", "A CI pipeline is configured.", "configuration", 2, _check_ci_pipeline),
        Criterion("cd-deployment", "CD deployment", "Automated deployment step exists in CI.", "configuration", 3, _check_cd_deployment),
        Criterion("iac-present", "IaC present", "Infrastructure-as-Code (Terraform, Helm, K8s) is configured.", "configuration", 4, _check_iac_present),
        Criterion("editorconfig", "EditorConfig", "EditorConfig enforces consistent editor settings.", "configuration", 2, _check_editorconfig),
        Criterion("task-runner", "Task runner", "A Makefile, Taskfile, or npm scripts provide common dev tasks.", "configuration", 2, _check_makefile_or_taskfile),
    ],
)
