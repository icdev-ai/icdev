
"""
Tier 3 Mission 7: Capstone — Ship a Real Child App
Goal: Wire AppManifest + GoalValidator + BlueprintSpec + compliance tool
      into a CapstoneApp that produces a comprehensive readiness report.

You must implement CapstoneApp.ship() using the components you built in T3-01 through T3-06.
All the helper classes and functions below are provided — implement only CapstoneApp.ship().
"""

import re
from dataclasses import dataclass, field
from datetime import date

# ── Provided: AppManifest (from T3-06) ───────────────────────────────────────

VALID_CANVASES = {"NDC", "SDC", "PDC", "BDC", "DDC", "ODC", "IDC"}
SLUG_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')

@dataclass
class AppManifest:
    app_name: str
    app_slug: str
    canvas: str
    description: str
    routes: list = field(default_factory=list)
    db_tables: list = field(default_factory=list)
    author: str = "forge_academy"

    def validate(self):
        issues = []
        if not self.app_name:
            issues.append("app_name cannot be empty")
        if not SLUG_PATTERN.match(self.app_slug):
            issues.append(f"app_slug '{self.app_slug}' is invalid")
        if self.canvas not in VALID_CANVASES:
            issues.append(f"canvas '{self.canvas}' is not valid")
        if len(self.routes) < 1:
            issues.append("at least one route is required")
        elif not any(r.startswith("/") for r in self.routes):
            issues.append("all routes must start with '/'")
        if len(self.db_tables) < 1:
            issues.append("at least one DB table is required")
        return (len(issues) == 0, issues)

    def completeness_score(self) -> int:
        signals = [
            len(self.description) >= 10,
            len(self.routes) >= 2,
            len(self.db_tables) >= 1,
            bool(self.author),
            self.canvas in VALID_CANVASES,
        ]
        return sum(signals)


# ── Provided: GoalValidator (from T3-03) ─────────────────────────────────────

class GoalValidator:
    def validate(self, content: str) -> dict:
        tools = re.findall(r'tools/[\w/]+\.py', content)
        steps = re.findall(r'^\s*\d+\.\s+(.+)$', content, re.MULTILINE)
        issues = []
        if not tools:
            issues.append("Goal must reference at least one tool")
        if not steps:
            issues.append("Goal must have a ## Steps section with ≥1 numbered step")
        if "DB table" not in content and "file " not in content:
            issues.append("Goal must have an # Output: line")
        return {"valid": len(issues) == 0, "issues": issues,
                "parsed": {"tools": tools, "steps": steps}}


# ── Provided: BlueprintSpec (from T3-04) ─────────────────────────────────────

COMPONENT_NAMES = ["template","route","backing_module","constants","migration","nav_link","icdev_mirror"]

class BlueprintSpec:
    def __init__(self, app_slug: str, present_files: set):
        self.app_slug = app_slug
        self.present_files = present_files  # set of file paths that "exist"
        self.template = f"tools/dashboard/templates/{app_slug}/page.html"
        self.blueprint_file = f"apps/{app_slug}/blueprint.py"
        self.backing_module = f"apps/{app_slug}/{app_slug}.py"
        self.constants_file = f"apps/{app_slug}/constants.py"
        self.migrations_dir = f"apps/{app_slug}/migrations"
        self.icdev_mirror = f"icdev/tools/dashboard/templates/{app_slug}/page.html"

    def validate(self) -> dict:
        components = {
            "template": self.template in self.present_files,
            "route": self.blueprint_file in self.present_files,
            "backing_module": self.backing_module in self.present_files,
            "constants": self.constants_file in self.present_files,
            "migration": self.migrations_dir in self.present_files,
            "nav_link": True,
            "icdev_mirror": self.icdev_mirror in self.present_files,
        }
        score = sum(components.values())
        return {"score": score, "max_score": 7, "valid": score == 7, "components": components,
                "missing": [k for k, v in components.items() if not v]}


# ── Provided: Compliance Evidence Tool (from T3-02) ──────────────────────────

EVIDENCE_DB = {
    ("ICDEV-Prod", "IA-2"): [
        {"type": "config", "description": "MFA enforced via Duo Security"},
        {"type": "scan",   "description": "Nessus 2026-04-30: compliant"},
    ],
    ("ICDEV-Dev", "IA-2"): [
        {"type": "finding", "description": "STIG V-220706: internal MFA missing (CAT II)"},
    ],
}
COMPLIANT_TYPES = {"config", "scan", "policy", "log"}
NON_COMPLIANT_TYPES = {"finding"}
TOOL_META = {"tool": "compliance_evidence_collector", "version": "1.0"}

def collect_evidence(system_name: str, control_id: str) -> dict:
    if (system_name, control_id) not in EVIDENCE_DB:
        return {"status": "partial", "data": {"system": system_name, "control": control_id,
                "message": "No evidence records found."}, "error": None, "meta": TOOL_META}
    evidence = EVIDENCE_DB[(system_name, control_id)]
    if not evidence:
        cs = "partial"
    elif any(e["type"] in NON_COMPLIANT_TYPES for e in evidence):
        cs = "non-compliant"
    elif all(e["type"] in COMPLIANT_TYPES for e in evidence):
        cs = "compliant"
    else:
        cs = "partial"
    return {"status": "ok", "data": {"control": control_id, "system": system_name,
            "evidence": evidence, "compliance_status": cs, "evidence_count": len(evidence),
            "evidence_date": str(date.today())}, "error": None, "meta": TOOL_META}


# ── Step: CapstoneApp ────────────────────────────────────────────────────────

class CapstoneApp:
    """Integrates all T3 components into a ship-readiness report."""

    def __init__(self, manifest: AppManifest, goal_content: str,
                 blueprint_files: set, system_name: str = "ICDEV-Prod",
                 control_id: str = "IA-2"):
        self.manifest = manifest
        self.goal_content = goal_content
        self.blueprint_files = blueprint_files
        self.system_name = system_name
        self.control_id = control_id

    def ship(self) -> dict:
        """TODO: Run all component checks and return a readiness report.

        Scoring (2 pts each, max 10):
        1. manifest_valid: 2 if manifest.validate()[0] else 0
        2. goal_valid: 2 if GoalValidator().validate(goal_content)["valid"] else 0
        3. blueprint_score: 2 if BlueprintSpec(slug, files).validate()["score"] >= 5 else
                            1 if score >= 3 else 0
        4. tool_ok: 2 if collect_evidence(system_name, control_id)["status"] == "ok" else 0
        5. completeness: 2 if manifest.completeness_score() == 5 else
                         1 if manifest.completeness_score() >= 3 else 0

        Blockers: any component that scores 0.

        Return:
        {
            "ready": score >= 8 and len(blockers) == 0,
            "score": total_score,
            "max_score": 10,
            "blockers": [list of blocker names],
            "components": {
                "manifest": {"score": N, "valid": bool, "issues": [...]},
                "goal": {"score": N, "valid": bool, "issues": [...]},
                "blueprint": {"score": N, "gate_score": 0-7},
                "tool": {"score": N, "status": "ok"|"partial"|"error"},
                "completeness": {"score": N, "completeness_score": 0-5},
            },
        }
        """
        # YOUR CODE HERE
        pass


# Test
if __name__ == "__main__":
    print("=== Tier 3 Capstone ===\n")

    manifest = AppManifest(
        app_name="Mission Tracker",
        app_slug="missiontracker",
        canvas="ODC",
        description="Real-time mission status tracking for operational teams",
        routes=["/", "/api/missions", "/api/status"],
        db_tables=["missions", "mission_events"],
        author="forge_academy",
    )

    goal = """
# Mission Tracker Goal
# Tools: tools/ops/mission_tracker.py, tools/db/storage.py
# Args: args/mission_config.yaml
# Output: DB table missions

## Steps
1. Load active missions — mission_tracker.py (system_id)
2. Update status — mission_tracker.py (mission_id, status)
3. Persist updates — storage.py (missions_table)
"""

    blueprint_files = {
        "tools/dashboard/templates/missiontracker/page.html",
        "apps/missiontracker/blueprint.py",
        "apps/missiontracker/missiontracker.py",
        "apps/missiontracker/constants.py",
        "apps/missiontracker/migrations",
        "icdev/tools/dashboard/templates/missiontracker/page.html",
    }

    app = CapstoneApp(
        manifest=manifest,
        goal_content=goal,
        blueprint_files=blueprint_files,
        system_name="ICDEV-Prod",
        control_id="IA-2",
    )

    report = app.ship()
    print(f"Ready: {report['ready']}")
    print(f"Score: {report['score']}/{report['max_score']}")
    print(f"Blockers: {report['blockers']}")
    for name, comp in report['components'].items():
        print(f"  {name}: score={comp['score']}")
