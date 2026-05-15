# CUI // SP-CTI
"""Infrastructure Canvas adapter registry.

Adapters provide thin, canvas-facing wrappers over DataBridge connectors.
All external HTTP traffic routes through DataBridge for secret resolution,
audit logging, and health probing (ADR D360+).

Available adapters
------------------
LocalStackAdapter  — AWS service emulation via LocalStack
                     Feature flag: LOCALSTACK_ENABLED in .env
"""

from tools.infra_canvas.adapters.localstack_adapter import LocalStackAdapter

__all__ = ["LocalStackAdapter"]
