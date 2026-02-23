# [TEMPLATE: CUI // SP-CTI]
"""Behave environment configuration for ICDEV BDD tests."""

import os
import sys


def before_all(context):
    """Set up global test context."""
    # Ensure project root is in path
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    context.project_root = project_root


def before_scenario(context, scenario):
    """Set up per-scenario context."""
    context.result = None
    context.project_dir = context.project_root


def after_scenario(context, scenario):
    """Clean up after each scenario."""
    pass
