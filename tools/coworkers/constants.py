# CUI // SP-CTI
"""Co-Workers canvas constants.

Canvas key, env flag, route prefix, and the paths to the config-driven
co-worker sources.  Kept tiny and dependency-free so importing the blueprint
at app startup never raises.
"""
from __future__ import annotations

from pathlib import Path

CANVAS_KEY = "cwk"
CANVAS_ENV_FLAG = "ICDEV_CWK_ENABLED"
URL_PREFIX = "/coworkers"

# Repo root: .../icdev/tools/coworkers/constants.py -> parents[3] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Primary co-worker roster: the existing chat-agent persona registry.
PERSONAS_YAML = _REPO_ROOT / "args" / "chat_personas.yaml"

# Secondary roster: ACE autonomous role catalog (best-effort, optional).
ACE_ROLES_DIR = _REPO_ROOT / "args" / "ace" / "roles"

# Personas that are plumbing, not collaborators — hidden from the roster.
HIDDEN_PERSONA_KEYS = {"default"}

# Default accent + initial when a source omits them.
DEFAULT_ACCENT = "#4a90d9"
