# CUI // SP-CTI
"""Infrastructure Canvas adapter registry.

Adapters provide thin, canvas-facing wrappers over DataBridge connectors.
All external HTTP traffic routes through DataBridge for secret resolution,
audit logging, and health probing (ADR D360+).

Available adapters
------------------
FlociAdapter  — AWS service emulation via floci (flx-gen-01)
                Switch: ``tools/cloud/emulator.py`` — FLOCI_ENABLED in .env,
                default false, with LOCALSTACK_ENABLED honoured as a
                deprecated alias.
"""

from tools.infra_canvas.adapters.floci_adapter import FlociAdapter

__all__ = ["FlociAdapter"]
