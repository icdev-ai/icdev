#!/usr/bin/env python3
# CUI // SP-CTI
"""Profile Detector — scan code, git history, and CI/CD configs to infer dev conventions.

ADR D185: Detection is advisory only. User must accept before profile is applied.

Usage:
    python tools/builder/profile_detector.py --repo-path /path/to/repo --json
    python tools/builder/profile_detector.py --repo-path /path --accept \\
        --detection-id det-abc123 --accepted-by admin@gov --json
    python tools/builder/profile_detector.py --from-text "We use Go and Rust with snake_case" --json
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def _generate_id(prefix="det"):
    """Generate unique detection ID."""
    import hashlib
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}-{hashlib.sha256(ts.encode()).hexdigest()[:8]}"


def _load_config():
    """Load detection config from args/dev_profile_config.yaml."""
    try:
        import yaml
        config_path = BASE_DIR / "args" / "dev_profile_config.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except ImportError:
        pass
    return {}


# ── Repository Detection ─────────────────────────────────────────────


def detect_from_repo(repo_path):
    """Scan a repository and detect development conventions.

    Scans: file extensions, config files, git log, CI configs, test dirs, Dockerfiles.
    Returns dict matching dev_profile schema with confidence scores.
    """
    repo = Path(repo_path)
    if not repo.is_dir():
        return {"error": f"Not a directory: {repo_path}"}

    detected = {}
    confidence = {}

    # 1. Language detection from file extensions
    lang_result = _detect_languages(repo)
    if lang_result:
        detected["language"] = lang_result["data"]
        confidence["language"] = lang_result["confidence"]

    # 2. Style detection from config files
    style_result = _detect_style(repo)
    if style_result:
        detected["style"] = style_result["data"]
        confidence["style"] = style_result["confidence"]

    # 3. Git conventions
    git_result = _detect_git_conventions(repo)
    if git_result:
        detected["git"] = git_result["data"]
        confidence["git"] = git_result["confidence"]

    # 4. CI/CD detection
    ci_result = _detect_ci_cd(repo)
    if ci_result:
        detected["operations"] = ci_result["data"]
        confidence["operations"] = ci_result["confidence"]

    # 5. Testing detection
    test_result = _detect_testing(repo)
    if test_result:
        detected["testing"] = test_result["data"]
        confidence["testing"] = test_result["confidence"]

    # 6. Container/deployment detection
    deploy_result = _detect_deployment(repo)
    if deploy_result:
        detected.setdefault("architecture", {}).update(deploy_result.get("architecture", {}))
        detected.setdefault("operations", {}).update(deploy_result.get("operations", {}))
        confidence["deployment"] = deploy_result.get("confidence", 0.5)

    # 7. Security tooling detection
    sec_result = _detect_security_tools(repo)
    if sec_result:
        detected["security"] = sec_result["data"]
        confidence["security"] = sec_result["confidence"]

    avg_confidence = (
        sum(confidence.values()) / len(confidence) if confidence else 0.0
    )

    return {
        "status": "detected",
        "repo_path": str(repo_path),
        "detected_profile": detected,
        "confidence_per_dimension": confidence,
        "overall_confidence": round(avg_confidence, 2),
        "dimensions_detected": len(detected),
    }


def _detect_languages(repo):
    """Detect languages from file extensions."""
    ext_map = {
        ".py": "python", ".java": "java", ".go": "go",
        ".rs": "rust", ".cs": "csharp", ".ts": "typescript",
        ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript",
    }
    counts = Counter()
    for ext, lang in ext_map.items():
        files = list(repo.rglob(f"*{ext}"))
        # Exclude common non-source dirs
        files = [f for f in files if not any(
            p in f.parts for p in ("node_modules", ".git", "__pycache__", "venv", ".venv", "vendor")
        )]
        if files:
            counts[lang] = len(files)

    if not counts:
        return None

    primary = counts.most_common(1)[0][0]
    allowed = [lang for lang, _ in counts.most_common()]

    # Detect package managers
    pkg_managers = {}
    if (repo / "pyproject.toml").exists() or (repo / "requirements.txt").exists():
        if (repo / "uv.lock").exists():
            pkg_managers["python"] = "uv"
        elif (repo / "Pipfile").exists():
            pkg_managers["python"] = "pipenv"
        elif (repo / "poetry.lock").exists():
            pkg_managers["python"] = "poetry"
        else:
            pkg_managers["python"] = "pip"
    if (repo / "pom.xml").exists():
        pkg_managers["java"] = "maven"
    elif (repo / "build.gradle").exists() or (repo / "build.gradle.kts").exists():
        pkg_managers["java"] = "gradle"
    if (repo / "go.mod").exists():
        pkg_managers["go"] = "go"
    if (repo / "Cargo.toml").exists():
        pkg_managers["rust"] = "cargo"
    if (repo / "package.json").exists():
        pkg_managers.setdefault("typescript", "npm")
        pkg_managers.setdefault("javascript", "npm")

    # Detect versions from config files
    versions = {}
    if (repo / "pyproject.toml").exists():
        try:
            content = (repo / "pyproject.toml").read_text(encoding="utf-8")
            match = re.search(r'requires-python\s*=\s*"([^"]+)"', content)
            if match:
                versions["python"] = match.group(1)
        except Exception:
            pass

    confidence = min(0.95, 0.5 + (len(counts) * 0.1))
    return {
        "data": {
            "primary": primary,
            "allowed": allowed,
            "package_managers": pkg_managers,
            "versions": versions,
        },
        "confidence": round(confidence, 2),
    }


def _detect_style(repo):
    """Detect code style from config files."""
    style = {}
    confidence = 0.3  # Base confidence

    # .editorconfig
    editorconfig = repo / ".editorconfig"
    if editorconfig.exists():
        try:
            content = editorconfig.read_text(encoding="utf-8")
            if "indent_style = spaces" in content:
                style["indent_style"] = "spaces"
            elif "indent_style = tab" in content:
                style["indent_style"] = "tabs"
            match = re.search(r"indent_size\s*=\s*(\d+)", content)
            if match:
                style["indent_size"] = int(match.group(1))
            match = re.search(r"max_line_length\s*=\s*(\d+)", content)
            if match:
                style["max_line_length"] = int(match.group(1))
            confidence = 0.8
        except Exception:
            pass

    # pyproject.toml [tool.black] or [tool.ruff]
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            match = re.search(r"line-length\s*=\s*(\d+)", content)
            if match:
                style["max_line_length"] = int(match.group(1))
            if "[tool.black]" in content:
                style.setdefault("formatter", {})["python"] = "black"
            if "[tool.ruff]" in content:
                style.setdefault("linter", {})["python"] = "ruff"
                style.setdefault("formatter", {})["python"] = "ruff"
            if "[tool.isort]" in content:
                style["import_order"] = "isort"
            confidence = max(confidence, 0.7)
        except Exception:
            pass

    # .prettierrc
    for prettier_file in [".prettierrc", ".prettierrc.json"]:
        if (repo / prettier_file).exists():
            try:
                data = json.loads((repo / prettier_file).read_text(encoding="utf-8"))
                if "tabWidth" in data:
                    style["indent_size"] = data["tabWidth"]
                if "singleQuote" in data:
                    style["quote_style"] = "single" if data["singleQuote"] else "double"
                if "printWidth" in data:
                    style["max_line_length"] = data["printWidth"]
                style.setdefault("formatter", {}).update({"typescript": "prettier", "javascript": "prettier"})
                confidence = max(confidence, 0.8)
            except Exception:
                pass
            break

    if not style:
        return None

    return {"data": style, "confidence": round(confidence, 2)}


def _detect_git_conventions(repo):
    """Detect git conventions from log."""
    git_dir = repo / ".git"
    if not git_dir.exists():
        return None

    data = {}
    confidence = 0.3

    try:
        # Commit message format
        result = subprocess.run(
            ["git", "log", "--oneline", "-50", "--format=%s"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            messages = result.stdout.strip().split("\n")
            conventional = sum(
                1 for m in messages
                if re.match(r"^(feat|fix|chore|docs|style|refactor|perf|test|build|ci|revert)", m)
            )
            if conventional > len(messages) * 0.5:
                data["commit_format"] = "conventional_commits"
                # Check for scope
                scoped = sum(1 for m in messages if re.match(r"^\w+\(.+\):", m))
                if scoped > len(messages) * 0.3:
                    data["commit_format"] = "conventional_commits_with_scope"
                confidence = 0.8

        # Branch naming
        result = subprocess.run(
            ["git", "branch", "-a", "--format=%(refname:short)"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            branches = result.stdout.strip().split("\n")
            if any("develop" in b for b in branches):
                data["branching_strategy"] = "gitflow"
            elif any("feature/" in b or "bugfix/" in b for b in branches):
                data["branching_strategy"] = "gitflow"
            else:
                data["branching_strategy"] = "trunk_based"
            confidence = max(confidence, 0.6)

        # Merge strategy (from merge commits)
        result = subprocess.run(
            ["git", "log", "--merges", "-10", "--format=%s"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            merges = result.stdout.strip().split("\n") if result.stdout.strip() else []
            if len(merges) > 3:
                data["merge_strategy"] = "merge_commit"
            else:
                data["merge_strategy"] = "squash"

    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    if not data:
        return None

    return {"data": data, "confidence": round(confidence, 2)}


def _detect_ci_cd(repo):
    """Detect CI/CD platform from config files."""
    data = {}
    confidence = 0.5

    if (repo / ".github" / "workflows").is_dir():
        data["ci_cd_platform"] = "github_actions"
        confidence = 0.9
    elif (repo / ".gitlab-ci.yml").exists():
        data["ci_cd_platform"] = "gitlab"
        confidence = 0.9
    elif (repo / "Jenkinsfile").exists():
        data["ci_cd_platform"] = "jenkins"
        confidence = 0.9
    elif (repo / "azure-pipelines.yml").exists():
        data["ci_cd_platform"] = "azure_devops"
        confidence = 0.9

    if not data:
        return None

    return {"data": data, "confidence": round(confidence, 2)}


def _detect_testing(repo):
    """Detect testing conventions."""
    data = {}
    confidence = 0.3

    # Python testing
    if (repo / "tests").is_dir() or (repo / "test").is_dir():
        data["require_unit"] = True
        confidence = 0.6

    if (repo / "features").is_dir():
        data["require_bdd"] = True
        confidence = max(confidence, 0.7)

    # Coverage config
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            match = re.search(r"min[_-]?coverage\s*=\s*(\d+)", content, re.IGNORECASE)
            if match:
                data["min_coverage"] = int(match.group(1))
                confidence = max(confidence, 0.8)
            if "[tool.pytest" in content:
                data["methodology"] = "tdd"
        except Exception:
            pass

    # Jest config
    if (repo / "jest.config.js").exists() or (repo / "jest.config.ts").exists():
        data["require_unit"] = True
        confidence = max(confidence, 0.6)

    if not data:
        return None

    return {"data": data, "confidence": round(confidence, 2)}


def _detect_deployment(repo):
    """Detect deployment conventions from Dockerfiles and IaC."""
    data = {"architecture": {}, "operations": {}}
    confidence = 0.3

    # Dockerfile
    for dockerfile in ["Dockerfile", "docker/Dockerfile"]:
        df_path = repo / dockerfile
        if df_path.exists():
            try:
                content = df_path.read_text(encoding="utf-8")
                match = re.search(r"FROM\s+(\S+)", content)
                if match:
                    base_image = match.group(1)
                    data["architecture"]["container_base"] = base_image
                    if "alpine" in base_image.lower():
                        data["architecture"]["container_hardening"] = "alpine"
                    confidence = 0.7
            except Exception:
                pass
            break

    # Kubernetes
    if (repo / "k8s").is_dir() or list(repo.rglob("*deployment*.yaml")):
        data["operations"]["deployment_target"] = "kubernetes"
        confidence = max(confidence, 0.8)
    elif (repo / "docker-compose.yml").exists() or (repo / "docker-compose.yaml").exists():
        data["operations"]["deployment_target"] = "docker_compose"
        confidence = max(confidence, 0.6)

    # Terraform
    if list(repo.rglob("*.tf")):
        data["operations"]["iac_tool"] = "terraform"
        confidence = max(confidence, 0.7)

    if not data["architecture"] and not data["operations"]:
        return None

    return {**data, "confidence": round(confidence, 2)}


def _detect_security_tools(repo):
    """Detect security tooling from config files."""
    data = {}
    confidence = 0.3

    # Bandit
    if (repo / ".bandit").exists() or (repo / "bandit.yaml").exists():
        data.setdefault("sast_tools", {})["python"] = "bandit"
        confidence = 0.7

    # detect-secrets
    if (repo / ".secrets.baseline").exists():
        data["secret_detection"] = "detect-secrets"
        confidence = max(confidence, 0.7)

    # trivy / grype config
    if (repo / ".trivyignore").exists():
        data["container_scanner"] = "trivy"
        confidence = max(confidence, 0.7)

    # Check pyproject.toml for security tools
    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            if "[tool.bandit]" in content:
                data.setdefault("sast_tools", {})["python"] = "bandit"
                confidence = max(confidence, 0.7)
        except Exception:
            pass

    if not data:
        return None

    return {"data": data, "confidence": round(confidence, 2)}


# ── Text Detection (for intake) ──────────────────────────────────────


def detect_from_text(text):
    """Detect dev profile signals from customer text (intake integration).

    Uses keyword matching from config. Returns detected signals with confidence.
    """
    if not text:
        return {"detected_signals": {}, "signal_count": 0}

    config = _load_config()
    lower = text.lower()
    detected = {}

    # Check intake keywords
    intake_keywords = config.get("intake_detection", {}).get("keywords", [])
    for kw in intake_keywords:
        if kw.lower() in lower:
            detected["dev_profile_mentioned"] = True
            break

    # Check language keywords
    lang_keywords = config.get("intake_detection", {}).get("language_keywords", {})
    detected_langs = []
    for lang, keywords in lang_keywords.items():
        if any(kw.lower() in lower for kw in keywords):
            detected_langs.append(lang)
    if detected_langs:
        detected["languages"] = detected_langs

    # Check naming conventions
    naming_map = {
        "snake_case": ["snake_case", "snake case", "underscore naming"],
        "camelCase": ["camelCase", "camel case"],
        "PascalCase": ["PascalCase", "pascal case"],
    }
    for convention, keywords in naming_map.items():
        if any(kw.lower() in lower for kw in keywords):
            detected["naming_convention"] = convention
            break

    # Check specific tools/libraries
    tool_keywords = {
        "uv": ["uv ", "uv,", "uv for", "astral uv"],
        "pydantic": ["pydantic"],
        "alpine": ["alpine"],
        "ruff": ["ruff"],
        "black": ["black formatter", "use black"],
        "pytest": ["pytest"],
        "behave": ["behave", "bdd"],
    }
    detected_tools = []
    for tool, keywords in tool_keywords.items():
        if any(kw.lower() in lower for kw in keywords):
            detected_tools.append(tool)
    if detected_tools:
        detected["tools_mentioned"] = detected_tools

    # Check architecture preferences
    arch_keywords = {
        "microservices": ["microservice", "micro-service"],
        "monolith": ["monolith", "modular monolith"],
        "serverless": ["serverless", "lambda"],
    }
    for arch, keywords in arch_keywords.items():
        if any(kw.lower() in lower for kw in keywords):
            detected["architecture_preference"] = arch
            break

    return {
        "detected_signals": detected,
        "signal_count": len(detected),
        "has_dev_profile_signals": len(detected) > 0,
    }


# ── Accept Detection ─────────────────────────────────────────────────


def accept_detection(detection_id, accepted_by, db_path=None):
    """Mark a detection as accepted and create a profile from it."""
    from tools.builder.dev_profile_manager import create_profile, _get_connection

    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM dev_profile_detections WHERE id = ?",
            (detection_id,),
        ).fetchone()

        if not row:
            return {"error": f"Detection not found: {detection_id}"}

        if row["accepted"]:
            return {"error": "Detection already accepted"}

        conn.execute(
            "UPDATE dev_profile_detections SET accepted = 1, accepted_by = ?, accepted_at = ? WHERE id = ?",
            (accepted_by, datetime.now(timezone.utc).isoformat(), detection_id),
        )
        conn.commit()

        # Create profile from detection results
        detection_results = json.loads(row["detection_results"])
        detected_profile = detection_results.get("detected_profile", {})

        scope = "project" if row["project_id"] else "tenant"
        scope_id = row["project_id"] or row["tenant_id"]

        result = create_profile(
            scope=scope,
            scope_id=scope_id,
            profile_data=detected_profile,
            created_by=accepted_by,
            change_summary=f"Created from auto-detection {detection_id}",
            db_path=db_path,
        )
        return result
    finally:
        conn.close()


def store_detection(detection_results, tenant_id=None, project_id=None,
                    session_id=None, repo_url=None, db_path=None):
    """Store detection results in the database."""
    from tools.builder.dev_profile_manager import _get_connection, _generate_id

    conn = _get_connection(db_path)
    try:
        det_id = _generate_id("det")
        conn.execute(
            """INSERT INTO dev_profile_detections
               (id, tenant_id, project_id, session_id, repo_url, detected_at, detection_results)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (det_id, tenant_id, project_id, session_id, repo_url,
             datetime.now(timezone.utc).isoformat(),
             json.dumps(detection_results, default=str)),
        )
        conn.commit()
        return {"detection_id": det_id, "status": "stored"}
    finally:
        conn.close()


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Profile Detector — scan repos and text for dev conventions (D185)"
    )
    parser.add_argument("--repo-path", help="Repository path to scan")
    parser.add_argument("--from-text", help="Detect signals from text")
    parser.add_argument("--accept", action="store_true", help="Accept a detection")
    parser.add_argument("--detection-id", help="Detection ID to accept")
    parser.add_argument("--accepted-by", help="Who accepted")
    parser.add_argument("--store", action="store_true", help="Store detection results in DB")
    parser.add_argument("--tenant-id", help="Tenant ID for storage")
    parser.add_argument("--project-id", help="Project ID for storage")
    parser.add_argument("--db-path", type=Path, help="Database path override")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    db = str(args.db_path) if args.db_path else None

    if args.accept:
        result = accept_detection(args.detection_id, args.accepted_by, db_path=db)
    elif args.from_text:
        result = detect_from_text(args.from_text)
    elif args.repo_path:
        result = detect_from_repo(args.repo_path)
        if args.store and "error" not in result:
            store_result = store_detection(
                result, tenant_id=args.tenant_id,
                project_id=args.project_id, repo_url=args.repo_path,
                db_path=db,
            )
            result["detection_id"] = store_result.get("detection_id")
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        for k, v in result.items():
            if isinstance(v, (dict, list)):
                print(f"{k}: {json.dumps(v, indent=2, default=str)}")
            else:
                print(f"{k}: {v}")


if __name__ == "__main__":
    main()
