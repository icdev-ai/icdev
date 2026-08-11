# CUI // SP-CTI
"""Declarative detection over the agent event stream (AGOV / DET).

ICDEV already writes rich agent activity into ``hook_events``,
``agent_executions``, ``ai_telemetry``, ``audit_trail`` and ``ace_audit_log``
and never reads any of it back for detection. This package is the read side.

Modules land per card task and are deliberately small and separable:

  ``events.py``       normalized :class:`AgentEvent` view (agov-det-01)
  ``shell_parse.py``  parsed shell-command view (agov-det-02)
  ``rules.py``        YAML rule loader + single-event evaluator (agov-det-03)
  ``sequence.py``     multi-step chain evaluator (agov-det-04)

Nothing here enforces anything on its own. Rules are monitor-only unless an
operator opts a rule into ``enforce: true``; see :mod:`tools.agent_detect.rules`.
"""
