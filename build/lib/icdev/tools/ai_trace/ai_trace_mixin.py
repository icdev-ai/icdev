# CUI // SP-CTI
"""AI Trace Mixin — canonical re-export from tools.canvas.ai_trace_mixin.

All canvas blueprints should import from here going forward:

    from tools.ai_trace.ai_trace_mixin import record_canvas_decision

The implementation lives in tools/canvas/ai_trace_mixin.py. This module
re-exports it so both import paths work and either can be the canonical
reference without a circular dependency.
"""
from tools.canvas.ai_trace_mixin import record_canvas_decision  # noqa: F401

__all__ = ["record_canvas_decision"]
