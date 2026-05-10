# CUI // SP-CTI
"""FORGE Academy integrations — bridge to AISG, RAG, and ICDEV systems."""

import logging

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AISG skill gap tracker
# ---------------------------------------------------------------------------

def record_skill_usage(email: str, skill_tag: str) -> None:
    """Feed mission step completions into AISG skill gap tracker."""
    if not email or not skill_tag:
        return
    try:
        from tools.aisg.skill_gap_tracker import record_usage
        record_usage(email, skill_tag)
    except Exception as e:
        _log.warning("skill_gap_tracker.record_usage failed: %s", e)


# ---------------------------------------------------------------------------
# AISG learning track integration
# ---------------------------------------------------------------------------

def advance_learning_track(email: str, track_id: str = "practitioner") -> None:
    """Advance AISG learning track milestone when a FORGE mission completes."""
    if not email:
        return
    try:
        from tools.aisg.learning_paths import record_task_complete, activate_track
        activate_track(email, track_id)
        record_task_complete(email, track_id)
    except Exception as e:
        _log.warning("learning_paths advance failed: %s", e)


# ---------------------------------------------------------------------------
# AISG pattern deployment (for guided missions)
# ---------------------------------------------------------------------------

def deploy_pattern(pattern_id: str, target_dir: str = None) -> dict:
    """Deploy an AISG pattern from a guided mission."""
    try:
        from tools.aisg.pattern_registry import deploy_pattern as _deploy
        return _deploy(pattern_id, target_dir or ".")
    except Exception as e:
        _log.warning("pattern_registry.deploy_pattern failed: %s", e)
        return {"status": "error", "message": str(e)}


def list_patterns() -> list:
    try:
        from tools.aisg.pattern_registry import list_patterns as _list
        return _list()
    except Exception as e:
        _log.warning("pattern_registry.list_patterns failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Role detection via AISG WizardEngine
# ---------------------------------------------------------------------------

def detect_role_from_answers(answers: dict) -> dict:
    """Run WizardEngine to detect recommended role + goals from profile answers."""
    try:
        import secrets
        from tools.aisg.wizard import WizardEngine
        token = secrets.token_hex(8)
        engine = WizardEngine()
        return engine.process(token, answers)
    except Exception as e:
        _log.warning("WizardEngine.process failed: %s", e)
        return {"recommended_goals": [], "recommended_skills": [], "sprint_plan_tasks": []}


# ---------------------------------------------------------------------------
# Visual agent builder (workflow builder bridge)
# ---------------------------------------------------------------------------

def create_workflow(name: str, canvas_json: dict, user_email: str,
                    trust_tier: str = "suggest") -> dict:
    """Save a workflow design and generate FORGE goal markdown."""
    try:
        from tools.aisg.visual_agent_builder import create_design, generate_goal_md
        goal_md = generate_goal_md(canvas_json, name)
        design_id = create_design(name, canvas_json, user_email, trust_tier)
        return {"design_id": design_id, "goal_md": goal_md, "status": "ok"}
    except Exception as e:
        _log.warning("visual_agent_builder failed: %s", e)
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# ICDEV Studio canvas launcher (for studio-type missions)
# ---------------------------------------------------------------------------

CANVAS_ROUTES = {
    "ndc": "/network-design",
    "sdc": "/security-canvas",
    "pdc": "/pipeline",
    "bdc": "/business-canvas",
    "ddc": "/data-canvas",
    "odc": "/observability",
    "idc": "/intake",
}

def get_studio_url(canvas: str, scenario_id: str = None) -> str:
    base = CANVAS_ROUTES.get(canvas.lower(), "/forge-studio")
    if scenario_id:
        return f"{base}?scenario={scenario_id}"
    return base


# ---------------------------------------------------------------------------
# Multi-stream chat launcher (for chat-lab missions)
# ---------------------------------------------------------------------------

def get_chat_lab_url(scenario_id: str, role: str = None) -> str:
    """Generate a /simulate/chat URL pre-configured for a training scenario."""
    params = f"scenario={scenario_id}"
    if role:
        params += f"&role={role}"
    return f"/simulate/chat?{params}"
