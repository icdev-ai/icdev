# [TEMPLATE: CUI // SP-CTI]
"""CLI bridge — route LLM traffic through the Claude Code CLI provider.

When ICDEV™ runs without cloud API keys (or in an air-gapped environment),
the ``claude-cli`` provider can serve requests by shelling out to a locally
authenticated Claude Code CLI. This package provides:

- :mod:`activate` — auto-enable logic + routing-chain rewrite that prepends
  ``claude-cli`` to every function's fallback chain.

The router calls :func:`tools.llm.cli_bridge.activate.maybe_activate` after
loading its config, so no manual wiring is required.
"""
