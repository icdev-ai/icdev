# CUI // SP-CTI
"""Pillar 6 — Configuration: CI/CD, dev environment, IaC, env var management."""
from __future__ import annotations

import pathlib

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _exists,
    _glob_files,
    _read,
    _search,
)


def _check_ci_pipeline(repo: pathlib.Path) -> CriterionResult:
    cid = "ci-pipeline"
    gh_workflows = _glob_files(repo, ".github/workflows/*.yml") + _glob_files(repo, ".github/workflows/*.yaml")
    if gh_workflows:
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
    # Look for deployment steps in CI
    ci_files = (
        _glob_files(repo, ".github/workflows/*.yml")
        + _glob_files(repo, ".github/workflows/*.yaml")
        + ([repo / ".gitlab-ci.yml"] if (repo / ".gitlab-ci.yml").exists() else [])
    )
    deploy_patterns = r"\bdeploy\b|\brelease\b|\bpublish\b|\bpush.*image\b|\bhelm\s+upgrade\b|\bkubectl\s+apply\b"
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
    if _exists(repo, "terraform", "infra", "infrastructure", "tf"):
        d = _exists(repo, "terraform", "infra", "infrastructure", "tf")
        if _glob_files(repo / d if d else repo, "**/*.tf"):
            return CriterionResult(cid, True, f"Terraform IaC found in {d}/")
    tf_files = _glob_files(repo, "**/*.tf")
    if tf_files:
        return CriterionResult(cid, True, f"Terraform files found ({len(tf_files)} .tf files)")
    helm = _glob_files(repo, "**/Chart.yaml")
    if helm:
        return CriterionResult(cid, True, f"Helm chart(s) found ({len(helm)} Chart.yaml)")
    if _exists(repo, "ansible", "playbooks") or _glob_files(repo, "**/*.playbook.yml"):
        return CriterionResult(cid, True, "Ansible IaC found")
    k8s = _glob_files(repo, "**/*.k8s.yaml") + _glob_files(repo, "**/k8s/**/*.yaml")
    if k8s:
        return CriterionResult(cid, True, f"Kubernetes manifests found ({len(k8s)} files)")
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
    if _exists(repo, "Makefile", "makefile"):
        mk = _read(repo, "Makefile") or _read(repo, "makefile") or ""
        targets = sum(1 for l in mk.splitlines() if l and not l.startswith("\t") and ":" in l and not l.startswith("#"))
        if targets >= 3:
            return CriterionResult(cid, True, f"Makefile with {targets} targets found")
    if _exists(repo, "Taskfile.yml", "Taskfile.yaml", "Taskfile.dist.yml"):
        return CriterionResult(cid, True, "Taskfile task runner found")
    pkg = _read(repo, "package.json")
    if pkg and _search(pkg, r'"scripts"\s*:\s*\{'):
        import json
        try:
            scripts = json.loads(pkg).get("scripts", {})
            if len(scripts) >= 3:
                return CriterionResult(cid, True, f"npm scripts ({len(scripts)} tasks) in package.json")
        except Exception:
            pass
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
