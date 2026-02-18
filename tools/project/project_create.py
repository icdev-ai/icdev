#!/usr/bin/env python3
"""Create a new ICDEV-managed project.

Generates a UUID, creates the project directory under projects/, scaffolds the
directory structure based on project type, inserts a record into icdev.db,
logs an audit trail event, and applies CUI header markings to generated source files.

Usage:
    python tools/project/project_create.py --name "My App" --type webapp --classification CUI
    python tools/project/project_create.py --name "Auth Service" --type microservice --tech-backend "Python/FastAPI" --tech-database "PostgreSQL"
"""

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
PROJECTS_DIR = BASE_DIR / "projects"

# Import project scaffolder
sys.path.insert(0, str(BASE_DIR))
from tools.project.project_scaffold import scaffold_project, SCAFFOLDERS  # noqa: E402

# Import audit logger
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    audit_log_event = None


VALID_TYPES = list(SCAFFOLDERS.keys())
VALID_CLASSIFICATIONS = ["CUI", "FOUO", "Public", "SECRET", "TOP SECRET"]
VALID_IMPACT_LEVELS = ["IL2", "IL4", "IL5", "IL6"]
VALID_ATO_STATUSES = ["none", "in_progress", "iato", "ato", "cato", "dato", "denied"]


def _audit(event_type: str, actor: str, action: str, project_id: str = None, details: dict = None):
    """Write an audit trail entry."""
    if audit_log_event is not None:
        try:
            audit_log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                project_id=project_id,
                details=details,
                db_path=DB_PATH,
            )
            return
        except Exception:
            pass

    # Direct fallback
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, 'CUI')""",
            (project_id, event_type, actor, action, json.dumps(details) if details else None),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def create_project(
    name: str,
    project_type: str = "webapp",
    classification: str = "CUI",
    description: str = "",
    tech_backend: str = "",
    tech_frontend: str = "",
    tech_database: str = "",
    skip_scaffold: bool = False,
    impact_level: str = "IL5",
    target_frameworks: str = "",
    cloud_environment: str = "aws-govcloud",
    accrediting_authority: str = "",
) -> dict:
    """Create a new project end-to-end.

    Args:
        name: Human-readable project name.
        project_type: One of webapp, microservice, api, cli, data_pipeline, iac, frontend.
        classification: CUI, FOUO, or Public.
        description: Project description.
        tech_backend: Backend technology stack.
        tech_frontend: Frontend technology stack.
        tech_database: Database technology.
        skip_scaffold: If True, create directory but skip scaffolding.

    Returns:
        dict with project_id, name, type, directory, status, and scaffold results.
    """
    # Validate inputs
    if not name or not name.strip():
        raise ValueError("Project name is required")

    if project_type not in VALID_TYPES:
        raise ValueError(f"Invalid type '{project_type}'. Valid: {VALID_TYPES}")

    if classification not in VALID_CLASSIFICATIONS:
        raise ValueError(f"Invalid classification '{classification}'. Valid: {VALID_CLASSIFICATIONS}")

    if impact_level not in VALID_IMPACT_LEVELS:
        raise ValueError(f"Invalid impact_level '{impact_level}'. Valid: {VALID_IMPACT_LEVELS}")

    # Auto-set classification from impact level if not explicitly set
    if impact_level == "IL6" and classification == "CUI":
        classification = "SECRET"

    # Generate project ID
    project_id = str(uuid.uuid4())

    # Determine directory name (slug)
    dir_name = name.lower().strip()
    for char in [" ", "/", "\\", ".", ",", "'", '"', "(", ")", "&"]:
        dir_name = dir_name.replace(char, "-")
    # Collapse multiple dashes
    while "--" in dir_name:
        dir_name = dir_name.replace("--", "-")
    dir_name = dir_name.strip("-")

    project_dir = PROJECTS_DIR / dir_name

    # Check for name collision
    if project_dir.exists():
        # Append short UUID suffix to avoid collision
        short_id = project_id[:8]
        dir_name = f"{dir_name}-{short_id}"
        project_dir = PROJECTS_DIR / dir_name

    # Create directory
    project_dir.mkdir(parents=True, exist_ok=True)

    # Scaffold project structure
    scaffold_result = None
    if not skip_scaffold:
        scaffold_result = scaffold_project(
            project_dir=str(project_dir),
            project_type=project_type,
            project_name=name,
            classification=classification,
        )

    # Insert into database
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """INSERT INTO projects
               (id, name, description, type, classification, status,
                tech_stack_backend, tech_stack_frontend, tech_stack_database,
                directory_path, created_by,
                impact_level, cloud_environment, target_frameworks,
                ato_status, accrediting_authority)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 'icdev-cli',
                       ?, ?, ?, 'none', ?)""",
            (
                project_id,
                name,
                description,
                project_type,
                classification,
                tech_backend,
                tech_frontend,
                tech_database,
                str(project_dir),
                impact_level,
                cloud_environment,
                target_frameworks,
                accrediting_authority,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        # Clean up directory if DB insert fails
        if project_dir.exists() and not any(project_dir.iterdir()):
            project_dir.rmdir()
        raise RuntimeError(f"Database error: {exc}") from exc
    finally:
        conn.close()

    # Audit trail
    _audit(
        event_type="project_created",
        actor="icdev-cli",
        action=f"Created project '{name}' ({project_type}, {classification}, {impact_level})",
        project_id=project_id,
        details={
            "name": name,
            "type": project_type,
            "classification": classification,
            "impact_level": impact_level,
            "cloud_environment": cloud_environment,
            "target_frameworks": target_frameworks,
            "accrediting_authority": accrediting_authority,
            "directory": str(project_dir),
            "tech_stack": {
                "backend": tech_backend,
                "frontend": tech_frontend,
                "database": tech_database,
            },
            "scaffolded": not skip_scaffold,
            "files_created": scaffold_result["files_created"] if scaffold_result else 0,
        },
    )

    result = {
        "project_id": project_id,
        "name": name,
        "type": project_type,
        "classification": classification,
        "impact_level": impact_level,
        "cloud_environment": cloud_environment,
        "target_frameworks": target_frameworks,
        "ato_status": "none",
        "status": "active",
        "directory": str(project_dir),
        "tech_stack": {
            "backend": tech_backend,
            "frontend": tech_frontend,
            "database": tech_database,
        },
        "created_at": datetime.utcnow().isoformat(),
    }

    if scaffold_result:
        result["scaffold"] = {
            "files_created": scaffold_result["files_created"],
        }

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Create a new ICDEV-managed project"
    )
    parser.add_argument(
        "--name", required=True,
        help="Project name (human-readable)"
    )
    parser.add_argument(
        "--type", default="webapp", choices=VALID_TYPES,
        help="Project type (default: webapp)"
    )
    parser.add_argument(
        "--classification", default="CUI", choices=VALID_CLASSIFICATIONS,
        help="Data classification level (default: CUI)"
    )
    parser.add_argument(
        "--description", default="",
        help="Project description"
    )
    parser.add_argument(
        "--tech-backend", default="",
        help="Backend technology stack (e.g. 'Python/Flask')"
    )
    parser.add_argument(
        "--tech-frontend", default="",
        help="Frontend technology stack (e.g. 'React/TypeScript')"
    )
    parser.add_argument(
        "--tech-database", default="",
        help="Database technology (e.g. 'PostgreSQL')"
    )
    parser.add_argument(
        "--impact-level", default="IL5", choices=VALID_IMPACT_LEVELS,
        help="DoD Impact Level (default: IL5)"
    )
    parser.add_argument(
        "--target-frameworks", default="",
        help="Comma-separated target frameworks (e.g. 'fedramp-high,cmmc-l2')"
    )
    parser.add_argument(
        "--cloud-environment", default="aws-govcloud",
        help="Cloud environment (e.g. 'aws-govcloud', 'azure-gov', 'on-prem')"
    )
    parser.add_argument(
        "--accrediting-authority", default="",
        help="Authorizing Official / accrediting authority"
    )
    parser.add_argument(
        "--skip-scaffold", action="store_true",
        help="Create project record and directory but skip scaffolding"
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format"
    )
    args = parser.parse_args()

    try:
        result = create_project(
            name=args.name,
            project_type=args.type,
            classification=args.classification,
            description=args.description,
            tech_backend=args.tech_backend,
            tech_frontend=args.tech_frontend,
            tech_database=args.tech_database,
            skip_scaffold=args.skip_scaffold,
            impact_level=args.impact_level,
            target_frameworks=args.target_frameworks,
            cloud_environment=args.cloud_environment,
            accrediting_authority=args.accrediting_authority,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print("Project created successfully!")
        print(f"  ID:             {result['project_id']}")
        print(f"  Name:           {result['name']}")
        print(f"  Type:           {result['type']}")
        print(f"  Classification: {result['classification']}")
        print(f"  Impact Level:   {result['impact_level']}")
        print(f"  ATO Status:     {result['ato_status']}")
        print(f"  Status:         {result['status']}")
        print(f"  Directory:      {result['directory']}")
        if result.get("cloud_environment"):
            print(f"  Cloud Env:      {result['cloud_environment']}")
        if result.get("target_frameworks"):
            print(f"  Frameworks:     {result['target_frameworks']}")
        if result.get("tech_stack", {}).get("backend"):
            print(f"  Backend:        {result['tech_stack']['backend']}")
        if result.get("tech_stack", {}).get("frontend"):
            print(f"  Frontend:       {result['tech_stack']['frontend']}")
        if result.get("tech_stack", {}).get("database"):
            print(f"  Database:       {result['tech_stack']['database']}")
        if result.get("scaffold"):
            print(f"  Files created:  {result['scaffold']['files_created']}")
        print(f"  Created at:     {result['created_at']}")


if __name__ == "__main__":
    main()
