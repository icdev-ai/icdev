# CUI // SP-CTI
"""AGOV declarative detection over the agent event stream.

ICDEV already writes rich agent activity into ``hook_events``,
``agent_executions``, ``ai_telemetry``, ``audit_trail`` and ``ace_audit_log``
and has never read any of it back for detection. This package is the read side.

Submodules land one per card and are imported directly rather than re-exported
here, so a partially-landed package never fails at import time:

- ``events``      normalized :class:`AgentEvent` view (agov-det-01)
- ``shell_parse`` parsed shell-command view (agov-det-02)
- ``rules``       YAML rule loader + single-event evaluator (agov-det-03)
- ``sequence``    multi-step chain evaluator (agov-det-04)
"""
