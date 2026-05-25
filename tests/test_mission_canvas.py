# CUI // SP-CTI
"""Tests for Mission Canvas module."""

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from tools.mission_canvas.constants import CANVAS_NAME, INTENT_RULES, OBJECT_TYPES


def test_constants():
    assert CANVAS_NAME == "mission_canvas"
    assert len(INTENT_RULES) >= 10
    assert "zones" in OBJECT_TYPES


def test_blueprint_factory():
    from tools.mission_canvas.blueprint import create_mission_canvas_blueprint

    import os
    os.environ["ICDEV_MISSION_CANVAS_ENABLED"] = "true"
    bp = create_mission_canvas_blueprint()
    assert bp is not None
    assert bp.name == "mission_canvas"


def test_blueprint_disabled():
    from tools.mission_canvas.blueprint import create_mission_canvas_blueprint

    import os
    os.environ["ICDEV_MISSION_CANVAS_ENABLED"] = "false"
    bp = create_mission_canvas_blueprint()
    assert bp is None
    os.environ["ICDEV_MISSION_CANVAS_ENABLED"] = "true"


def test_wrapper_imports():
    """All wrapper modules should import without errors."""
    from tools.mission_canvas import (
        orchestrator,
        twin,
    )

    assert orchestrator is not None
    assert twin is not None
