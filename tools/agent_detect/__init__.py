# CUI // SP-CTI
"""AGOV detection subsystem — read-only views and rules over the agent event stream.

``events`` is the normalizer: it projects the agent activity ICDEV already
stores (``hook_events``, ``agent_executions``, ``ai_telemetry``,
``audit_trail``, ``ace_audit_log``) into one :class:`AgentEvent` shape. It
creates no tables and writes nothing.
"""
