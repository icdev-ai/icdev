#!/usr/bin/env python3
# CUI // SP-CTI
"""BDD Test Writer — generates Cucumber/Gherkin feature files and step definitions.

Implements:
- generate_feature(project_path, requirement_text) -> .feature file
- generate_steps(project_path, feature_file, language) -> step definition file
- Supports Python (behave), Java (Cucumber-JVM), Go (godog), TypeScript (@cucumber/cucumber),
  C# (SpecFlow), and Rust (cucumber-rs)
- Applies CUI header to generated files
- Logs audit trail event (test_written)
- CLI: python tools/builder/test_writer.py --project-path PATH --requirement "User can login" --language python
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# CUI header applied to all generated files
CUI_HEADER = """\
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""

CUI_HEADER_GHERKIN = """\
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""

CUI_HEADER_C_STYLE = """\
// CUI // SP-CTI
// Controlled by: Department of Defense
// CUI Category: CTI
// Distribution: D
// POC: ICDEV System Administrator
"""

CUI_HEADER_RUST = """\
// CUI // SP-CTI
// Controlled by: Department of Defense
// CUI Category: CTI
// Distribution: D
// POC: ICDEV System Administrator
"""


def _slugify(text: str) -> str:
    """Convert text to a filename-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower().strip())
    slug = re.sub(r"[\s_-]+", "_", slug)
    return slug[:80]


def _extract_actions(requirement: str) -> List[Dict[str, str]]:
    """Parse a requirement into Given/When/Then steps.

    Uses simple heuristics to decompose a requirement into BDD steps.
    """
    req_lower = requirement.lower().strip()
    steps = []

    # Common patterns for Given (preconditions)
    given_patterns = [
        (r"(logged in|authenticated|signed in)", "the user is authenticated"),
        (r"(admin|administrator)", "the user has admin privileges"),
        (r"(database|db|data)", "the system has existing data"),
        (r"(api|endpoint|server)", "the API server is running"),
        (r"(page|screen|form)", "the user is on the relevant page"),
    ]

    # Common patterns for When (actions)
    when_patterns = [
        (r"(create|add|new|register)", "create"),
        (r"(update|edit|modify|change)", "update"),
        (r"(delete|remove|destroy)", "delete"),
        (r"(login|sign in|authenticate)", "login"),
        (r"(search|find|query|filter)", "search"),
        (r"(upload|import)", "upload"),
        (r"(download|export)", "download"),
        (r"(view|list|display|show|see)", "view"),
        (r"(submit|send|post)", "submit"),
    ]

    # Determine Given
    given_text = "the system is in its default state"
    for pattern, text in given_patterns:
        if re.search(pattern, req_lower):
            given_text = text
            break

    # Determine When
    action_text = f"the user performs the action: {requirement}"
    action_verb = "perform"
    for pattern, verb in when_patterns:
        if re.search(pattern, req_lower):
            action_verb = verb
            action_text = f"the user {verb}s the requested item"
            break

    # Determine Then
    then_text = "the action completes successfully"
    if action_verb in ("create", "register"):
        then_text = "the new item is created and visible"
    elif action_verb in ("update", "edit"):
        then_text = "the item is updated with the new values"
    elif action_verb in ("delete", "remove"):
        then_text = "the item is removed from the system"
    elif action_verb in ("login", "authenticate"):
        then_text = "the user is redirected to the dashboard"
    elif action_verb in ("search", "find", "filter"):
        then_text = "the matching results are displayed"
    elif action_verb in ("view", "list", "display"):
        then_text = "the requested data is displayed"

    steps.append({"type": "Given", "text": given_text})
    steps.append({"type": "When", "text": action_text})
    steps.append({"type": "Then", "text": then_text})

    return steps


def generate_feature(
    project_path: str,
    requirement_text: str,
    feature_name: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> str:
    """Generate a Gherkin .feature file from a requirement.

    Args:
        project_path: Root path of the project.
        requirement_text: The requirement to generate tests for.
        feature_name: Optional feature name (auto-generated from requirement if not provided).
        tags: Optional list of Gherkin tags (e.g., ["@smoke", "@auth"]).

    Returns:
        Path to the generated .feature file.
    """
    project = Path(project_path)
    features_dir = project / "tests" / "features"
    features_dir.mkdir(parents=True, exist_ok=True)

    # Generate feature name and slug
    if not feature_name:
        feature_name = requirement_text.strip().rstrip(".")
    slug = _slugify(feature_name)
    feature_file = features_dir / f"{slug}.feature"

    # Extract BDD steps from requirement
    steps = _extract_actions(requirement_text)

    # Build tag line
    tag_line = ""
    if tags:
        tag_line = " ".join(f"@{t.lstrip('@')}" for t in tags) + "\n"

    # Build the feature content
    lines = [CUI_HEADER_GHERKIN]
    lines.append(f"{tag_line}Feature: {feature_name}")
    lines.append("  As a user")
    lines.append(f"  I want to {requirement_text.lower().strip()}")
    lines.append("  So that the system meets the specified requirement")
    lines.append("")
    lines.append(f"  Scenario: {feature_name}")

    for step in steps:
        lines.append(f"    {step['type']} {step['text']}")

    lines.append("")
    lines.append(f"  Scenario: {feature_name} - error handling")
    lines.append(f"    Given {steps[0]['text']}")
    lines.append("    When the user provides invalid input")
    lines.append("    Then an appropriate error message is displayed")
    lines.append("    And the system remains in a consistent state")
    lines.append("")

    content = "\n".join(lines)
    feature_file.write_text(content, encoding="utf-8")
    print(f"Feature file created: {feature_file}")

    # Log audit trail
    _log_audit(project_path, str(feature_file), "test_written", "feature_file_generated")

    return str(feature_file)


def _parse_feature_steps(feature_file: str) -> Tuple[Path, List[Tuple[str, str]]]:
    """Parse a .feature file and extract step lines.

    Args:
        feature_file: Path to the .feature file.

    Returns:
        Tuple of (feature_path, list of (step_type, step_text) tuples).
    """
    feature_path = Path(feature_file)
    if not feature_path.exists():
        raise FileNotFoundError(f"Feature file not found: {feature_file}")

    content = feature_path.read_text(encoding="utf-8")
    step_lines = []
    for line in content.splitlines():
        stripped = line.strip()
        for keyword in ("Given ", "When ", "Then ", "And "):
            if stripped.startswith(keyword):
                step_type = keyword.strip()
                step_text = stripped[len(keyword):]
                step_lines.append((step_type, step_text))
    return feature_path, step_lines


def _generate_python_steps(
    feature_path: Path,
    steps_dir: Path,
    step_lines: List[Tuple[str, str]],
) -> str:
    """Generate behave (Python) step definitions.

    Args:
        feature_path: Path to the source .feature file.
        steps_dir: Directory where step files are written.
        step_lines: Parsed (step_type, step_text) tuples.

    Returns:
        Path to the generated step definitions file.
    """
    step_file_name = feature_path.stem + "_steps.py"
    step_file = steps_dir / step_file_name

    py_lines = [CUI_HEADER]
    py_lines.append(f'"""Step definitions for {feature_path.name}."""')
    py_lines.append("")
    py_lines.append("from behave import given, when, then")
    py_lines.append("")
    py_lines.append("")

    seen_steps: set = set()
    for step_type, step_text in step_lines:
        key = (step_type.lower(), step_text)
        if key in seen_steps:
            continue
        seen_steps.add(key)

        decorator = step_type.lower()
        if decorator == "and":
            decorator = "then"

        func_name = _slugify(f"{decorator}_{step_text}")
        if not func_name:
            func_name = f"{decorator}_step"

        escaped_text = step_text.replace('"', '\\"')

        py_lines.append(f'@{decorator}("{escaped_text}")')
        py_lines.append(f"def {func_name}(context):")
        py_lines.append(f'    """Step: {step_type} {step_text}"""')
        py_lines.append("    # TODO: Implement this step")
        py_lines.append(f"    raise NotImplementedError('Step not yet implemented: {escaped_text}')")
        py_lines.append("")
        py_lines.append("")

    step_content = "\n".join(py_lines)
    step_file.write_text(step_content, encoding="utf-8")
    return str(step_file)


def _generate_java_steps(
    feature_path: Path,
    steps_dir: Path,
    step_lines: List[Tuple[str, str]],
) -> str:
    """Generate Cucumber-JVM (Java) step definitions.

    Args:
        feature_path: Path to the source .feature file.
        steps_dir: Directory where step files are written.
        step_lines: Parsed (step_type, step_text) tuples.

    Returns:
        Path to the generated step definitions file.
    """
    # Derive class name from feature file stem
    raw_name = feature_path.stem.replace("_", " ").title().replace(" ", "")
    class_name = raw_name + "Steps"

    step_file_name = class_name + ".java"
    step_file = steps_dir / step_file_name

    lines = [CUI_HEADER_C_STYLE]
    lines.append("package com.icdev.steps;")
    lines.append("")
    lines.append("import io.cucumber.java.en.Given;")
    lines.append("import io.cucumber.java.en.When;")
    lines.append("import io.cucumber.java.en.Then;")
    lines.append("import static org.junit.jupiter.api.Assertions.*;")
    lines.append("")
    lines.append("/**")
    lines.append(f" * Step definitions for {feature_path.name}.")
    lines.append(" * Generated by ICDEV Builder - test_writer.py")
    lines.append(" */")
    lines.append(f"public class {class_name} {{")
    lines.append("")

    seen_steps: set = set()
    for step_type, step_text in step_lines:
        key = (step_type.lower(), step_text)
        if key in seen_steps:
            continue
        seen_steps.add(key)

        annotation = step_type.capitalize()
        if annotation == "And":
            annotation = "Then"

        method_name = _slugify(f"{step_type.lower()}_{step_text}")
        if not method_name:
            method_name = f"{step_type.lower()}_step"

        escaped_text = step_text.replace('"', '\\"')

        lines.append(f'    @{annotation}("{escaped_text}")')
        lines.append(f"    public void {method_name}() {{")
        lines.append(f'        // TODO: Implement step - {step_type} {step_text}')
        lines.append("        throw new io.cucumber.java.PendingException();")
        lines.append("    }")
        lines.append("")

    lines.append("}")
    lines.append("")

    step_content = "\n".join(lines)
    step_file.write_text(step_content, encoding="utf-8")
    return str(step_file)


def _generate_go_steps(
    feature_path: Path,
    steps_dir: Path,
    step_lines: List[Tuple[str, str]],
) -> str:
    """Generate godog (Go) step definitions.

    Args:
        feature_path: Path to the source .feature file.
        steps_dir: Directory where step files are written.
        step_lines: Parsed (step_type, step_text) tuples.

    Returns:
        Path to the generated step definitions file.
    """
    feature_name = _slugify(feature_path.stem)
    step_file_name = feature_path.stem + "_steps.go"
    step_file = steps_dir / step_file_name

    lines = [CUI_HEADER_C_STYLE]
    lines.append("package steps")
    lines.append("")
    lines.append('import (')
    lines.append('    "github.com/cucumber/godog"')
    lines.append(')')
    lines.append("")

    # Collect unique step functions
    seen_steps: set = set()
    func_registrations = []

    for step_type, step_text in step_lines:
        key = (step_type.lower(), step_text)
        if key in seen_steps:
            continue
        seen_steps.add(key)

        func_name = _slugify(f"{step_type.lower()}_{step_text}")
        if not func_name:
            func_name = f"{step_type.lower()}_step"

        # Escape backtick in regex
        escaped_text = step_text.replace("`", "` + \"`\" + `")

        func_registrations.append((escaped_text, func_name))

        lines.append(f"func {func_name}() error {{")
        lines.append(f"    // TODO: Implement step - {step_type} {step_text}")
        lines.append("    return godog.ErrPending")
        lines.append("}")
        lines.append("")

    # Generate the initialization function
    lines.append(f"func {feature_name}Steps(ctx *godog.ScenarioContext) {{")
    for escaped_text, func_name in func_registrations:
        lines.append(f'    ctx.Step(`^{escaped_text}$`, {func_name})')
    lines.append("}")
    lines.append("")

    step_content = "\n".join(lines)
    step_file.write_text(step_content, encoding="utf-8")
    return str(step_file)


def _generate_typescript_steps(
    feature_path: Path,
    steps_dir: Path,
    step_lines: List[Tuple[str, str]],
) -> str:
    """Generate @cucumber/cucumber (TypeScript) step definitions.

    Args:
        feature_path: Path to the source .feature file.
        steps_dir: Directory where step files are written.
        step_lines: Parsed (step_type, step_text) tuples.

    Returns:
        Path to the generated step definitions file.
    """
    step_file_name = feature_path.stem + "_steps.ts"
    step_file = steps_dir / step_file_name

    lines = [CUI_HEADER_C_STYLE]
    lines.append("import { Given, When, Then } from '@cucumber/cucumber';")
    lines.append("import { strict as assert } from 'assert';")
    lines.append("")

    seen_steps: set = set()
    for step_type, step_text in step_lines:
        key = (step_type.lower(), step_text)
        if key in seen_steps:
            continue
        seen_steps.add(key)

        decorator = step_type.capitalize()
        if decorator == "And":
            decorator = "Then"

        escaped_text = step_text.replace("'", "\\'")

        lines.append(f"// Step: {step_type} {step_text}")
        lines.append(f"{decorator}('{escaped_text}', async function () {{")
        lines.append("    // TODO: Implement step")
        lines.append("    return 'pending';")
        lines.append("});")
        lines.append("")

    step_content = "\n".join(lines)
    step_file.write_text(step_content, encoding="utf-8")
    return str(step_file)


def _generate_csharp_steps(
    feature_path: Path,
    steps_dir: Path,
    step_lines: List[Tuple[str, str]],
) -> str:
    """Generate SpecFlow (C#) step definitions.

    Args:
        feature_path: Path to the source .feature file.
        steps_dir: Directory where step files are written.
        step_lines: Parsed (step_type, step_text) tuples.

    Returns:
        Path to the generated step definitions file.
    """
    raw_name = feature_path.stem.replace("_", " ").title().replace(" ", "")
    class_name = raw_name + "Steps"

    step_file_name = class_name + ".cs"
    step_file = steps_dir / step_file_name

    lines = [CUI_HEADER_C_STYLE]
    lines.append("using TechTalk.SpecFlow;")
    lines.append("using Xunit;")
    lines.append("")
    lines.append("namespace ICDev.Steps")
    lines.append("{")
    lines.append("    [Binding]")
    lines.append(f"    public class {class_name}")
    lines.append("    {")

    seen_steps: set = set()
    for step_type, step_text in step_lines:
        key = (step_type.lower(), step_text)
        if key in seen_steps:
            continue
        seen_steps.add(key)

        attribute = step_type.capitalize()
        if attribute == "And":
            attribute = "Then"

        method_name = _slugify(f"{step_type.lower()}_{step_text}")
        if not method_name:
            method_name = f"{step_type.lower()}_step"
        # PascalCase for C# method names
        method_name = method_name.replace("_", " ").title().replace(" ", "")

        escaped_text = step_text.replace('"', '\\"')

        lines.append(f'        [{attribute}(@"{escaped_text}")]')
        lines.append(f"        public void {method_name}()")
        lines.append("        {")
        lines.append(f"            // TODO: Implement step - {step_type} {step_text}")
        lines.append("            throw new PendingStepException();")
        lines.append("        }")
        lines.append("")

    lines.append("    }")
    lines.append("}")
    lines.append("")

    step_content = "\n".join(lines)
    step_file.write_text(step_content, encoding="utf-8")
    return str(step_file)


def _generate_rust_steps(
    feature_path: Path,
    steps_dir: Path,
    step_lines: List[Tuple[str, str]],
) -> str:
    """Generate cucumber-rs (Rust) step definitions.

    Args:
        feature_path: Path to the source .feature file.
        steps_dir: Directory where step files are written.
        step_lines: Parsed (step_type, step_text) tuples.

    Returns:
        Path to the generated step definitions file.
    """
    raw_name = feature_path.stem.replace("_", " ").title().replace(" ", "")
    world_name = raw_name + "World"

    step_file_name = feature_path.stem + "_steps.rs"
    step_file = steps_dir / step_file_name

    lines = [CUI_HEADER_RUST]
    lines.append("use cucumber::{given, when, then, World};")
    lines.append("")
    lines.append("#[derive(Debug, Default, World)]")
    lines.append(f"pub struct {world_name};")
    lines.append("")

    seen_steps: set = set()
    for step_type, step_text in step_lines:
        key = (step_type.lower(), step_text)
        if key in seen_steps:
            continue
        seen_steps.add(key)

        macro_name = step_type.lower()
        if macro_name == "and":
            macro_name = "then"

        func_name = _slugify(f"{macro_name}_{step_text}")
        if not func_name:
            func_name = f"{macro_name}_step"

        escaped_text = step_text.replace('"', '\\"')

        lines.append(f'#[{macro_name}("{escaped_text}")]')
        lines.append(f"async fn {func_name}(world: &mut {world_name}) {{")
        lines.append(f'    todo!("Implement step: {step_type} {step_text}");')
        lines.append("}")
        lines.append("")

    step_content = "\n".join(lines)
    step_file.write_text(step_content, encoding="utf-8")
    return str(step_file)


# ---------------------------------------------------------------------------
# Agentic test generation (Phase 19)
# ---------------------------------------------------------------------------
AGENTIC_TEMPLATE_DIR = BASE_DIR / "tools" / "builder" / "agentic_test_templates"

# Agent-specific test parameters for template customization
AGENT_TEST_PARAMS = {
    "orchestrator": {"port": 8443, "skills": ["task-dispatch", "workflow-manage"]},
    "architect": {"port": 8444, "skills": ["system-design", "atlas-workflow"]},
    "builder": {"port": 8445, "skills": ["code-generate", "tdd-cycle", "scaffold"]},
    "compliance": {"port": 8446, "skills": ["ssp-generate", "stig-check", "sbom-generate"]},
    "security": {"port": 8447, "skills": ["sast-scan", "dep-audit", "secret-detect"]},
    "infrastructure": {"port": 8448, "skills": ["terraform-plan", "k8s-deploy", "pipeline-gen"]},
    "knowledge": {"port": 8449, "skills": ["pattern-detect", "self-heal", "recommend"]},
    "monitor": {"port": 8450, "skills": ["log-analyze", "health-check", "alert"]},
    "mbse": {"port": 8451, "skills": ["import-xmi", "import-reqif", "sync-model"]},
    "modernization": {"port": 8452, "skills": ["analyze-legacy", "seven-r-assess", "migrate"]},
}


def generate_agentic_tests(
    project_path: str,
    agent_names: Optional[List[str]] = None,
    include_bdd: bool = True,
    include_pytest: bool = True,
) -> List[str]:
    """Generate agentic test files from templates for a project.

    Copies and customizes agentic test templates into the target project's
    test directory. Templates are sourced from the agentic_test_templates
    directory and parameterized with project-specific agent configuration.

    Args:
        project_path: Root path of the target project.
        agent_names: Optional list of agent names to generate tests for.
                     If None, generates for all configured agents.
        include_bdd: Whether to include BDD feature file templates.
        include_pytest: Whether to include pytest test templates.

    Returns:
        List of paths to generated test files.
    """
    project = Path(project_path)
    agentic_test_dir = project / "tests" / "agentic"
    agentic_test_dir.mkdir(parents=True, exist_ok=True)

    if not AGENTIC_TEMPLATE_DIR.exists():
        print(f"Warning: Agentic template directory not found: {AGENTIC_TEMPLATE_DIR}")
        return []

    generated_files = []
    agents = agent_names or list(AGENT_TEST_PARAMS.keys())

    # Copy and customize BDD feature templates
    if include_bdd:
        for template_file in AGENTIC_TEMPLATE_DIR.glob("*.feature"):
            dest = agentic_test_dir / template_file.name
            content = template_file.read_text(encoding="utf-8")

            # Parameterize agent_count placeholder
            content = content.replace("{agent_count}", str(len(agents)))

            dest.write_text(content, encoding="utf-8")
            generated_files.append(str(dest))
            print(f"Agentic BDD template: {dest}")

    # Copy and customize pytest templates
    if include_pytest:
        for template_file in AGENTIC_TEMPLATE_DIR.glob("test_*.py"):
            dest = agentic_test_dir / template_file.name
            content = template_file.read_text(encoding="utf-8")
            dest.write_text(content, encoding="utf-8")
            generated_files.append(str(dest))
            print(f"Agentic pytest template: {dest}")

    # Generate per-agent skill test feature file
    if include_bdd and agents:
        skill_feature = _generate_agent_skill_feature(agentic_test_dir, agents)
        generated_files.append(skill_feature)

    # Generate per-agent health test pytest file
    if include_pytest and agents:
        health_test = _generate_agent_health_pytest(agentic_test_dir, agents)
        generated_files.append(health_test)

    # Log audit trail
    _log_audit(project_path, json.dumps(generated_files), "test_written", "agentic_tests_generated")

    print(f"\nGenerated {len(generated_files)} agentic test files in {agentic_test_dir}")
    return generated_files


def _generate_agent_skill_feature(test_dir: Path, agents: List[str]) -> str:
    """Generate a BDD feature file testing skills for specified agents.

    Args:
        test_dir: Directory to write the feature file.
        agents: List of agent names to include.

    Returns:
        Path to the generated feature file.
    """
    lines = [CUI_HEADER_GHERKIN]
    lines.append("Feature: Project Agent Skill Verification")
    lines.append("  Verify each agent's skills execute correctly for this project")
    lines.append("")
    lines.append("  Scenario Outline: Execute agent skill")
    lines.append('    Given agent "<agent>" is running on port <port>')
    lines.append('    And skill "<skill>" is registered')
    lines.append('    When I invoke skill "<skill>" with valid parameters')
    lines.append("    Then the skill should return a successful result")
    lines.append("    And the execution should be logged in audit trail")
    lines.append("")
    lines.append("    Examples:")
    lines.append("      | agent        | port  | skill          |")

    for agent_name in agents:
        params = AGENT_TEST_PARAMS.get(agent_name, {"port": 8080, "skills": ["unknown"]})
        for skill in params["skills"]:
            padded_agent = agent_name.ljust(12)
            padded_port = str(params["port"]).ljust(5)
            padded_skill = skill.ljust(14)
            lines.append(f"      | {padded_agent} | {padded_port} | {padded_skill} |")

    lines.append("")
    content = "\n".join(lines)

    feature_file = test_dir / "test_project_agent_skills.feature"
    feature_file.write_text(content, encoding="utf-8")
    print(f"Agentic skill feature: {feature_file}")
    return str(feature_file)


def _generate_agent_health_pytest(test_dir: Path, agents: List[str]) -> str:
    """Generate a pytest file testing health endpoints for specified agents.

    Args:
        test_dir: Directory to write the test file.
        agents: List of agent names to include.

    Returns:
        Path to the generated test file.
    """
    lines = [CUI_HEADER]
    lines.append('"""Project-specific agent health tests — auto-generated by test_writer."""')
    lines.append("")
    lines.append("import pytest")
    lines.append("from unittest.mock import patch, MagicMock")
    lines.append("")
    lines.append("")
    lines.append("# Agent endpoints for this project")
    lines.append("PROJECT_AGENTS = {")

    for agent_name in agents:
        params = AGENT_TEST_PARAMS.get(agent_name, {"port": 8080})
        lines.append(f'    "{agent_name}": {{"port": {params["port"]}, "endpoint": "https://localhost:{params["port"]}/health"}},')

    lines.append("}")
    lines.append("")
    lines.append("")
    lines.append("class TestProjectAgentHealth:")
    lines.append('    """Health checks for project-specific agent configuration."""')
    lines.append("")
    lines.append("    @pytest.mark.parametrize(\"agent_name,config\", PROJECT_AGENTS.items())")
    lines.append("    def test_agent_port_configured(self, agent_name, config):")
    lines.append('        """Each agent should have a valid port."""')
    lines.append("        assert 1024 <= config[\"port\"] <= 65535, f\"Agent {agent_name} port out of range\"")
    lines.append("")
    lines.append("    @pytest.mark.parametrize(\"agent_name,config\", PROJECT_AGENTS.items())")
    lines.append("    def test_agent_https_endpoint(self, agent_name, config):")
    lines.append('        """Each agent health endpoint should use HTTPS."""')
    lines.append("        assert config[\"endpoint\"].startswith(\"https://\"), f\"Agent {agent_name} not using HTTPS\"")
    lines.append("")
    lines.append("    def test_no_duplicate_ports(self):")
    lines.append('        """All agent ports should be unique."""')
    lines.append("        ports = [c[\"port\"] for c in PROJECT_AGENTS.values()]")
    lines.append("        assert len(ports) == len(set(ports)), \"Duplicate agent ports detected\"")
    lines.append("")

    content = "\n".join(lines)

    test_file = test_dir / "test_project_agent_health.py"
    test_file.write_text(content, encoding="utf-8")
    print(f"Agentic health pytest: {test_file}")
    return str(test_file)


# Step definition generator dispatch table
STEP_GENERATORS = {
    "python": _generate_python_steps,
    "java": _generate_java_steps,
    "go": _generate_go_steps,
    "typescript": _generate_typescript_steps,
    "csharp": _generate_csharp_steps,
    "rust": _generate_rust_steps,
}


def generate_steps(
    project_path: str,
    feature_file: str,
    language: str = "python",
) -> str:
    """Generate step definitions from a .feature file in the specified language.

    Args:
        project_path: Root path of the project.
        feature_file: Path to the .feature file to generate steps for.
        language: Target language (python, java, go, typescript, csharp, rust).

    Returns:
        Path to the generated step definitions file.
    """
    project = Path(project_path)
    steps_dir = project / "tests" / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)

    feature_path, step_lines = _parse_feature_steps(feature_file)

    generator = STEP_GENERATORS.get(language)
    if not generator:
        raise ValueError(
            f"Unsupported language: {language}. "
            f"Supported: {', '.join(STEP_GENERATORS.keys())}"
        )

    step_file = generator(feature_path, steps_dir, step_lines)
    print(f"Step definitions created ({language}): {step_file}")

    # Log audit trail
    _log_audit(project_path, step_file, "test_written", f"step_definitions_generated_{language}")

    return step_file


def _log_audit(project_path: str, file_path: str, event_type: str, action: str) -> None:
    """Log an audit trail event for test generation."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(
            """INSERT INTO audit_trail (project_id, event_type, actor, action, affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                None,  # Project ID could be looked up from path
                event_type,
                "builder/test_writer",
                action,
                json.dumps([file_path]),
                "CUI",
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        # Don't fail the main operation if audit logging fails
        print(f"Warning: audit logging failed: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate BDD tests in Cucumber/Gherkin format"
    )
    parser.add_argument(
        "--project-path", required=True, help="Root path of the project"
    )
    parser.add_argument(
        "--requirement", required=False, help="Requirement text to generate tests for"
    )
    parser.add_argument(
        "--feature-name", help="Feature name (auto-generated from requirement if not provided)"
    )
    parser.add_argument(
        "--tags", nargs="*", help="Gherkin tags (e.g., @smoke @auth)"
    )
    parser.add_argument(
        "--steps-only", action="store_true",
        help="Only generate step definitions for an existing feature file"
    )
    parser.add_argument(
        "--feature-file", help="Path to existing .feature file (with --steps-only)"
    )
    parser.add_argument(
        "--language", default="python",
        choices=["python", "java", "go", "typescript", "csharp", "rust"],
        help="Target language for step definitions (default: python)",
    )
    parser.add_argument(
        "--agentic", action="store_true",
        help="Generate agentic test suite from templates (Phase 19)",
    )
    parser.add_argument(
        "--agents", nargs="*",
        help="Agent names to generate tests for (with --agentic). Default: all agents.",
    )
    parser.add_argument(
        "--no-bdd", action="store_true",
        help="Skip BDD feature file generation (with --agentic)",
    )
    parser.add_argument(
        "--no-pytest", action="store_true",
        help="Skip pytest file generation (with --agentic)",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    if args.agentic:
        # Generate agentic test suite from templates (Phase 19)
        files = generate_agentic_tests(
            project_path=args.project_path,
            agent_names=args.agents,
            include_bdd=not args.no_bdd,
            include_pytest=not args.no_pytest,
        )
        print(f"\nGenerated {len(files)} agentic test files:")
        for f in files:
            print(f"  {f}")
    elif args.steps_only:
        if not args.feature_file:
            parser.error("--feature-file is required with --steps-only")
        step_file = generate_steps(args.project_path, args.feature_file, language=args.language)
        print(f"\nStep definitions ({args.language}): {step_file}")
    else:
        if not args.requirement:
            parser.error("--requirement is required (or use --agentic for agentic tests)")
        # Generate feature file (language-agnostic Gherkin)
        feature_file = generate_feature(
            project_path=args.project_path,
            requirement_text=args.requirement,
            feature_name=args.feature_name,
            tags=args.tags,
        )
        # Generate step definitions in target language
        step_file = generate_steps(args.project_path, feature_file, language=args.language)
        print("\nGenerated files:")
        print(f"  Feature: {feature_file}")
        print(f"  Steps ({args.language}): {step_file}")


if __name__ == "__main__":
    main()
