# CUI // SP-CTI
"""AGOV declarative detection over the agent event stream.

ICDEV already writes rich agent activity into ``hook_events``,
``agent_executions``, ``ai_telemetry``, ``audit_trail`` and ``ace_audit_log``
and has never read any of it back for detection. This package is the read side:
a read-only normalizer, a YAML rule pack under ``args/agent_rules/``, and an
append-only findings store.

Submodules land one per card and are imported directly rather than re-exported
here, so a partially-landed package never fails at import time:

- ``events``      normalized :class:`AgentEvent` view (agov-det-01)
- ``shell_parse`` parsed shell-command view (agov-det-02)
- ``rules``       YAML rule loader + single-event evaluator (agov-det-03)
- ``sequence``    multi-step chain evaluator (agov-det-04)
- ``findings``    append-only findings store (agov-det-05)
- ``gate``        the pre-tool-use decision seam (agov-det-06)
- ``cli``         operator CLI: --list/--check/--test/--scan (agov-det-07)

Monitor-only by default: a rule blocks nothing unless an operator opts it in.
"""
