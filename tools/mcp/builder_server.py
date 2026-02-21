#!/usr/bin/env python3
# CUI // SP-CTI
"""Builder MCP server exposing TDD code generation, scaffolding, testing, and linting tools.

Tools:
    scaffold       - Generate project directory structure from templates (6 languages)
    write_tests    - Generate test files (Gherkin BDD + language-specific step defs)
    generate_code  - Generate code to make failing tests pass (GREEN phase, 6 languages)
    run_tests      - Execute test suite (pytest + behave)
    lint           - Run linters (multi-language: bandit, checkstyle, golangci-lint, clippy, etc.)
    format         - Run code formatters (multi-language: black, gofmt, rustfmt, etc.)

Runs as an MCP server over stdio with Content-Length framing.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer  # noqa: E402


def _import_tool(module_path, func_name):
    """Dynamically import a function. Returns None if unavailable."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_scaffold(args: dict) -> dict:
    """Generate project directory structure from templates."""
    scaffold = _import_tool("tools.builder.scaffolder", "scaffold_project")
    if not scaffold:
        return {"error": "scaffolder module not available"}

    name = args.get("name")
    if not name:
        raise ValueError("'name' is required")

    project_type = args.get("type", "webapp")
    output_dir = args.get("output_dir")
    tech_stack = args.get("tech_stack", {})

    return scaffold(
        name=name,
        project_type=project_type,
        output_dir=output_dir,
        tech_stack=tech_stack,
    )


def handle_write_tests(args: dict) -> dict:
    """Generate test files from a feature description (RED phase of TDD)."""
    write_tests = _import_tool("tools.builder.test_writer", "write_tests")
    if not write_tests:
        return {"error": "test_writer module not available"}

    feature = args.get("feature")
    if not feature:
        raise ValueError("'feature' description is required")

    project_dir = args.get("project_dir")
    test_type = args.get("test_type", "both")  # "unit", "bdd", or "both"
    language = args.get("language", "python")

    return write_tests(
        feature=feature,
        project_dir=project_dir,
        test_type=test_type,
        language=language,
    )


def handle_generate_code(args: dict) -> dict:
    """Generate code to make failing tests pass (GREEN phase of TDD)."""
    generate = _import_tool("tools.builder.code_generator", "generate_code")
    if not generate:
        return {"error": "code_generator module not available"}

    test_file = args.get("test_file")
    if not test_file:
        raise ValueError("'test_file' path is required")

    project_dir = args.get("project_dir")
    language = args.get("language", "python")
    return generate(test_file=test_file, project_dir=project_dir, language=language)


def handle_run_tests(args: dict) -> dict:
    """Execute the test suite using pytest and/or behave."""
    project_dir = args.get("project_dir")
    if not project_dir:
        raise ValueError("'project_dir' is required")

    test_type = args.get("test_type", "all")  # "unit", "bdd", "all"
    verbose = args.get("verbose", False)
    coverage = args.get("coverage", True)

    results = {"test_type": test_type, "project_dir": project_dir}
    project_path = Path(project_dir)

    # Run pytest (unit tests)
    if test_type in ("unit", "all"):
        cmd = [sys.executable, "-m", "pytest"]
        if coverage:
            cmd.extend(["--cov", "--cov-report=json"])
        if verbose:
            cmd.append("-v")
        cmd.append(str(project_path / "tests"))

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300, cwd=str(project_path)
            )
            results["pytest"] = {
                "returncode": proc.returncode,
                "passed": proc.returncode == 0,
                "stdout": proc.stdout[-2000:] if len(proc.stdout) > 2000 else proc.stdout,
                "stderr": proc.stderr[-1000:] if len(proc.stderr) > 1000 else proc.stderr,
            }
        except FileNotFoundError:
            results["pytest"] = {"error": "pytest not installed"}
        except subprocess.TimeoutExpired:
            results["pytest"] = {"error": "Test execution timed out (300s)"}

    # Run behave (BDD tests)
    if test_type in ("bdd", "all"):
        features_dir = project_path / "features"
        if features_dir.exists():
            cmd = [sys.executable, "-m", "behave", str(features_dir)]
            if verbose:
                cmd.append("--verbose")

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300, cwd=str(project_path)
                )
                results["behave"] = {
                    "returncode": proc.returncode,
                    "passed": proc.returncode == 0,
                    "stdout": proc.stdout[-2000:] if len(proc.stdout) > 2000 else proc.stdout,
                    "stderr": proc.stderr[-1000:] if len(proc.stderr) > 1000 else proc.stderr,
                }
            except FileNotFoundError:
                results["behave"] = {"error": "behave not installed"}
            except subprocess.TimeoutExpired:
                results["behave"] = {"error": "BDD test execution timed out (300s)"}
        else:
            results["behave"] = {"skipped": True, "reason": "No features/ directory found"}

    return results


def handle_lint(args: dict) -> dict:
    """Run linters on a project."""
    lint = _import_tool("tools.builder.linter", "run_lint")
    if lint:
        project_dir = args.get("project_dir")
        if not project_dir:
            raise ValueError("'project_dir' is required")
        return lint(project_dir=project_dir)

    # Fallback: run bandit directly
    project_dir = args.get("project_dir")
    if not project_dir:
        raise ValueError("'project_dir' is required")

    results = {}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "bandit", "-r", project_dir, "-f", "json"],
            capture_output=True, text=True, timeout=120,
        )
        results["bandit"] = {
            "returncode": proc.returncode,
            "output": json.loads(proc.stdout) if proc.stdout else {},
        }
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        results["bandit"] = {"error": str(e)}

    return {"project_dir": project_dir, "results": results}


def handle_format(args: dict) -> dict:
    """Run code formatters on a project."""
    fmt = _import_tool("tools.builder.formatter", "run_format")
    if fmt:
        project_dir = args.get("project_dir")
        if not project_dir:
            raise ValueError("'project_dir' is required")
        return fmt(project_dir=project_dir)

    # Fallback: run black + isort directly
    project_dir = args.get("project_dir")
    if not project_dir:
        raise ValueError("'project_dir' is required")

    results = {}
    for tool_name, cmd in [("black", ["black", "."]), ("isort", ["isort", "."])]:
        try:
            proc = subprocess.run(
                [sys.executable, "-m"] + cmd,
                capture_output=True, text=True, timeout=60, cwd=project_dir,
            )
            results[tool_name] = {
                "returncode": proc.returncode,
                "output": proc.stdout[-1000:] if len(proc.stdout) > 1000 else proc.stdout,
            }
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            results[tool_name] = {"error": str(e)}

    return {"project_dir": project_dir, "results": results}


# ---------------------------------------------------------------------------
# Phase 19: Agentic generation tool handlers
# ---------------------------------------------------------------------------

def handle_agentic_fitness(args: dict) -> dict:
    """Assess component fitness for agentic architecture (6-dimension scoring)."""
    fitness = _import_tool("tools.builder.agentic_fitness", "assess_fitness")
    if fitness:
        spec = args.get("spec")
        if not spec:
            raise ValueError("'spec' is required")
        project_id = args.get("project_id")
        return fitness(spec=spec, project_id=project_id)

    # Fallback: invoke via subprocess
    spec = args.get("spec")
    if not spec:
        raise ValueError("'spec' is required")

    cmd = [sys.executable, str(BASE_DIR / "tools" / "builder" / "agentic_fitness.py"),
           "--spec", spec, "--json"]
    project_id = args.get("project_id")
    if project_id:
        cmd.extend(["--project-id", project_id])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(BASE_DIR))
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else {"stdout": proc.stdout}
        return {"error": proc.stderr or proc.stdout, "returncode": proc.returncode}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        return {"error": str(e)}


def handle_generate_blueprint(args: dict) -> dict:
    """Generate deployment blueprint from fitness scorecard."""
    blueprint = _import_tool("tools.builder.app_blueprint", "generate_blueprint")
    if blueprint:
        fitness_scorecard = args.get("fitness_scorecard")
        if not fitness_scorecard:
            raise ValueError("'fitness_scorecard' path is required")
        user_decisions = args.get("user_decisions", {})
        app_name = args.get("app_name")
        if not app_name:
            raise ValueError("'app_name' is required")
        cloud_provider = args.get("cloud_provider", "aws")
        cloud_region = args.get("cloud_region", "us-gov-west-1")
        impact_level = args.get("impact_level", "IL4")
        output = args.get("output")
        return blueprint(
            fitness_scorecard=fitness_scorecard,
            user_decisions=user_decisions,
            app_name=app_name,
            cloud_provider=cloud_provider,
            cloud_region=cloud_region,
            impact_level=impact_level,
            output=output,
        )

    # Fallback: invoke via subprocess
    fitness_scorecard = args.get("fitness_scorecard")
    if not fitness_scorecard:
        raise ValueError("'fitness_scorecard' path is required")
    app_name = args.get("app_name")
    if not app_name:
        raise ValueError("'app_name' is required")

    cmd = [sys.executable, str(BASE_DIR / "tools" / "builder" / "app_blueprint.py"),
           "--fitness-scorecard", fitness_scorecard,
           "--app-name", app_name, "--json"]
    user_decisions = args.get("user_decisions")
    if user_decisions:
        cmd.extend(["--user-decisions", json.dumps(user_decisions) if isinstance(user_decisions, dict) else user_decisions])
    cloud_provider = args.get("cloud_provider")
    if cloud_provider:
        cmd.extend(["--cloud-provider", cloud_provider])
    cloud_region = args.get("cloud_region")
    if cloud_region:
        cmd.extend(["--cloud-region", cloud_region])
    impact_level = args.get("impact_level")
    if impact_level:
        cmd.extend(["--impact-level", impact_level])
    output = args.get("output")
    if output:
        cmd.extend(["--output", output])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(BASE_DIR))
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else {"stdout": proc.stdout}
        return {"error": proc.stderr or proc.stdout, "returncode": proc.returncode}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        return {"error": str(e)}


def handle_dev_profile_create(args: dict) -> dict:
    """Create a dev profile from template or explicit data (Phase 34)."""
    create = _import_tool("tools.builder.dev_profile_manager", "create_profile")
    if create:
        return create(
            scope=args.get("scope", "project"),
            scope_id=args["scope_id"],
            profile_data=args.get("profile_data"),
            template_name=args.get("template"),
            created_by=args.get("created_by", "mcp-client"),
        )
    # Fallback: subprocess
    cmd = [sys.executable, str(BASE_DIR / "tools" / "builder" / "dev_profile_manager.py"),
           "--scope", args.get("scope", "project"),
           "--scope-id", args["scope_id"], "--create", "--json"]
    if args.get("template"):
        cmd.extend(["--template", args["template"]])
    if args.get("created_by"):
        cmd.extend(["--created-by", args["created_by"]])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(BASE_DIR))
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else {"stdout": proc.stdout}
        return {"error": proc.stderr or proc.stdout, "returncode": proc.returncode}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        return {"error": str(e)}


def handle_dev_profile_get(args: dict) -> dict:
    """Get the current dev profile for a scope (Phase 34)."""
    get_fn = _import_tool("tools.builder.dev_profile_manager", "get_profile")
    if get_fn:
        return get_fn(
            scope=args.get("scope", "project"),
            scope_id=args["scope_id"],
            version=args.get("version"),
        )
    cmd = [sys.executable, str(BASE_DIR / "tools" / "builder" / "dev_profile_manager.py"),
           "--scope", args.get("scope", "project"),
           "--scope-id", args["scope_id"], "--get", "--json"]
    if args.get("version"):
        cmd.extend(["--version", str(args["version"])])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(BASE_DIR))
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else {"stdout": proc.stdout}
        return {"error": proc.stderr or proc.stdout, "returncode": proc.returncode}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        return {"error": str(e)}


def handle_dev_profile_resolve(args: dict) -> dict:
    """Resolve 5-layer cascade for a scope (Phase 34)."""
    resolve = _import_tool("tools.builder.dev_profile_manager", "resolve_profile")
    if resolve:
        return resolve(
            scope=args.get("scope", "project"),
            scope_id=args["scope_id"],
        )
    cmd = [sys.executable, str(BASE_DIR / "tools" / "builder" / "dev_profile_manager.py"),
           "--scope", args.get("scope", "project"),
           "--scope-id", args["scope_id"], "--resolve", "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(BASE_DIR))
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else {"stdout": proc.stdout}
        return {"error": proc.stderr or proc.stdout, "returncode": proc.returncode}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        return {"error": str(e)}


def handle_dev_profile_detect(args: dict) -> dict:
    """Auto-detect dev profile from repository (Phase 34, D185 advisory)."""
    detect = _import_tool("tools.builder.profile_detector", "detect_from_repo")
    if detect:
        result = detect(args["repo_path"])
        # Optionally store detection results
        if args.get("store", False):
            store = _import_tool("tools.builder.profile_detector", "store_detection")
            if store:
                store(result, tenant_id=args.get("tenant_id"), project_id=args.get("project_id"))
        return result
    cmd = [sys.executable, str(BASE_DIR / "tools" / "builder" / "profile_detector.py"),
           "--repo", args["repo_path"], "--json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(BASE_DIR))
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else {"stdout": proc.stdout}
        return {"error": proc.stderr or proc.stdout, "returncode": proc.returncode}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        return {"error": str(e)}


def handle_generate_child_app(args: dict) -> dict:
    """Generate a mini-ICDEV clone child application."""
    generate = _import_tool("tools.builder.child_app_generator", "generate_child_app")
    if generate:
        blueprint = args.get("blueprint")
        if not blueprint:
            raise ValueError("'blueprint' path is required")
        output = args.get("output")
        return generate(blueprint=blueprint, output=output)

    # Fallback: invoke via subprocess
    blueprint = args.get("blueprint")
    if not blueprint:
        raise ValueError("'blueprint' path is required")

    cmd = [sys.executable, str(BASE_DIR / "tools" / "builder" / "child_app_generator.py"),
           "--blueprint", blueprint, "--json"]
    output = args.get("output")
    if output:
        cmd.extend(["--output", output])

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(BASE_DIR))
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else {"stdout": proc.stdout}
        return {"error": proc.stderr or proc.stdout, "returncode": proc.returncode}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

def create_server() -> MCPServer:
    server = MCPServer(name="icdev-builder", version="1.0.0")

    server.register_tool(
        name="scaffold",
        description="Generate a project directory structure from templates. Includes CUI markings, README, compliance directory, Dockerfile, .gitignore, and test scaffolding.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Project name"},
                "type": {
                    "type": "string",
                    "description": "Project type (includes multi-language scaffolds)",
                    "enum": [
                        "webapp", "microservice", "api", "cli", "data_pipeline", "iac",
                        "java-backend", "java-microservice",
                        "go-backend", "go-microservice",
                        "rust-backend",
                        "csharp-backend", "csharp-api",
                        "typescript-backend", "typescript-api",
                    ],
                    "default": "webapp",
                },
                "output_dir": {"type": "string", "description": "Output directory (optional, defaults to projects/)"},
                "tech_stack": {
                    "type": "object",
                    "description": "Technology stack preferences",
                    "properties": {
                        "backend": {"type": "string"},
                        "frontend": {"type": "string"},
                        "database": {"type": "string"},
                    },
                },
            },
            "required": ["name"],
        },
        handler=handle_scaffold,
    )

    server.register_tool(
        name="write_tests",
        description="Generate test files from a feature description (RED phase of TDD). Creates Gherkin BDD feature files and language-specific step definitions/unit tests.",
        input_schema={
            "type": "object",
            "properties": {
                "feature": {"type": "string", "description": "Feature description to generate tests for"},
                "project_dir": {"type": "string", "description": "Path to the project directory"},
                "test_type": {
                    "type": "string",
                    "description": "Type of tests to generate",
                    "enum": ["unit", "bdd", "both"],
                    "default": "both",
                },
                "language": {
                    "type": "string",
                    "description": "Target language for step definitions and unit tests",
                    "enum": ["python", "java", "javascript", "typescript", "go", "rust", "csharp"],
                    "default": "python",
                },
            },
            "required": ["feature"],
        },
        handler=handle_write_tests,
    )

    server.register_tool(
        name="generate_code",
        description="Generate implementation code to make failing tests pass (GREEN phase of TDD). Analyzes test file to determine required code. Supports 6 languages.",
        input_schema={
            "type": "object",
            "properties": {
                "test_file": {"type": "string", "description": "Path to the failing test file"},
                "project_dir": {"type": "string", "description": "Path to the project directory"},
                "language": {
                    "type": "string",
                    "description": "Target language for generated code",
                    "enum": ["python", "java", "javascript", "typescript", "go", "rust", "csharp"],
                    "default": "python",
                },
            },
            "required": ["test_file"],
        },
        handler=handle_generate_code,
    )

    server.register_tool(
        name="run_tests",
        description="Execute the test suite using pytest (unit tests) and/or behave (BDD). Returns pass/fail status, output, and coverage data.",
        input_schema={
            "type": "object",
            "properties": {
                "project_dir": {"type": "string", "description": "Path to the project directory"},
                "test_type": {
                    "type": "string",
                    "description": "Which tests to run",
                    "enum": ["unit", "bdd", "all"],
                    "default": "all",
                },
                "verbose": {"type": "boolean", "description": "Verbose output", "default": False},
                "coverage": {"type": "boolean", "description": "Generate coverage report", "default": True},
            },
            "required": ["project_dir"],
        },
        handler=handle_run_tests,
    )

    server.register_tool(
        name="lint",
        description="Run linters on a project (multi-language: bandit, checkstyle, golangci-lint, clippy, dotnet analyzers, eslint).",
        input_schema={
            "type": "object",
            "properties": {
                "project_dir": {"type": "string", "description": "Path to the project directory"},
            },
            "required": ["project_dir"],
        },
        handler=handle_lint,
    )

    server.register_tool(
        name="format",
        description="Run code formatters on a project (multi-language: black, google-java-format, gofmt, rustfmt, dotnet-format, prettier).",
        input_schema={
            "type": "object",
            "properties": {
                "project_dir": {"type": "string", "description": "Path to the project directory"},
            },
            "required": ["project_dir"],
        },
        handler=handle_format,
    )

    # Phase 19: Agentic generation tools
    server.register_tool(
        name="agentic_fitness",
        description="Assess component fitness for agentic architecture. Scores across 6 dimensions (autonomy, statefulness, tool-density, collaboration, error-recovery, domain-complexity). Returns scorecard with overall_score and per-dimension ratings.",
        input_schema={
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "Application specification or description to assess"},
                "project_id": {"type": "string", "description": "Project ID for tracking (optional)"},
            },
            "required": ["spec"],
        },
        handler=handle_agentic_fitness,
    )

    server.register_tool(
        name="generate_blueprint",
        description="Generate a deployment blueprint from a fitness scorecard. Produces agent topology, port assignments, MCP server config, goal selection, and infrastructure plan for the child application.",
        input_schema={
            "type": "object",
            "properties": {
                "fitness_scorecard": {"type": "string", "description": "Path to the fitness scorecard JSON file"},
                "user_decisions": {
                    "type": "object",
                    "description": "User decisions (cloud provider, MBSE, ATO, port offset, etc.)",
                },
                "app_name": {"type": "string", "description": "Name of the child application"},
                "cloud_provider": {
                    "type": "string",
                    "description": "Cloud provider",
                    "enum": ["aws", "gcp", "azure", "oracle"],
                    "default": "aws",
                },
                "cloud_region": {"type": "string", "description": "Cloud region", "default": "us-gov-west-1"},
                "impact_level": {
                    "type": "string",
                    "description": "Impact level",
                    "enum": ["IL2", "IL4", "IL5", "IL6"],
                    "default": "IL4",
                },
                "output": {"type": "string", "description": "Output path for the blueprint JSON (optional)"},
            },
            "required": ["fitness_scorecard", "app_name"],
        },
        handler=handle_generate_blueprint,
    )

    server.register_tool(
        name="generate_child_app",
        description="Generate a mini-ICDEV clone child application from a blueprint. Runs the 12-step generation pipeline: scaffold, agents, goals, tools, memory, DB, MCP, CLAUDE.md, CI/CD, compliance, Docker, K8s.",
        input_schema={
            "type": "object",
            "properties": {
                "blueprint": {"type": "string", "description": "Path to the blueprint JSON file"},
                "output": {"type": "string", "description": "Output directory for the generated application (optional)"},
            },
            "required": ["blueprint"],
        },
        handler=handle_generate_child_app,
    )

    # Phase 34: Dev profile management tools (D183-D188)
    server.register_tool(
        name="dev_profile_create",
        description="Create a tenant/project development profile from a starter template or explicit data. Supports 6 starter templates (dod_baseline, fedramp_baseline, healthcare_baseline, financial_baseline, law_enforcement, startup). Profiles define coding standards, tooling preferences, and compliance requirements.",
        input_schema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": "Profile scope level",
                    "enum": ["platform", "tenant", "program", "project", "user"],
                    "default": "project",
                },
                "scope_id": {"type": "string", "description": "Scope entity ID (e.g., tenant-abc, proj-123)"},
                "template": {"type": "string", "description": "Starter template name (e.g., dod_baseline, fedramp_baseline, startup)"},
                "profile_data": {"type": "object", "description": "Explicit profile data (merged on top of template if both given)"},
                "created_by": {"type": "string", "description": "Creator identity", "default": "mcp-client"},
            },
            "required": ["scope_id"],
        },
        handler=handle_dev_profile_create,
    )

    server.register_tool(
        name="dev_profile_get",
        description="Get the current active development profile for a scope. Returns all profile dimensions (language, style, testing, architecture, security, compliance, operations, documentation, git, ai).",
        input_schema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["platform", "tenant", "program", "project", "user"],
                    "default": "project",
                },
                "scope_id": {"type": "string", "description": "Scope entity ID"},
                "version": {"type": "integer", "description": "Specific version number (omit for current)"},
            },
            "required": ["scope_id"],
        },
        handler=handle_dev_profile_get,
    )

    server.register_tool(
        name="dev_profile_resolve",
        description="Resolve the 5-layer cascade (platform -> tenant -> program -> project -> user) to produce the effective merged profile with provenance tracking. Shows which scope set each value and whether dimensions are locked.",
        input_schema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["platform", "tenant", "program", "project", "user"],
                    "default": "project",
                },
                "scope_id": {"type": "string", "description": "Scope entity ID to resolve from"},
            },
            "required": ["scope_id"],
        },
        handler=handle_dev_profile_resolve,
    )

    server.register_tool(
        name="dev_profile_detect",
        description="Auto-detect development profile from an existing repository. Scans files, git history, CI/CD configs, and code patterns to suggest profile dimensions with confidence scores. Advisory only (D185) — requires explicit acceptance.",
        input_schema={
            "type": "object",
            "properties": {
                "repo_path": {"type": "string", "description": "Path to repository to scan"},
                "tenant_id": {"type": "string", "description": "Tenant ID (for storing detection results)"},
                "project_id": {"type": "string", "description": "Project ID (for storing detection results)"},
                "store": {"type": "boolean", "description": "Whether to store detection results in DB", "default": False},
            },
            "required": ["repo_path"],
        },
        handler=handle_dev_profile_detect,
    )

    return server


if __name__ == "__main__":
    server = create_server()
    server.run()
