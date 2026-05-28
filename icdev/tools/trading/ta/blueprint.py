# CUI // SP-CTI
"""icdev.tools namespace mirror for tools.trading.ta.blueprint.

Re-exports create_ta_blueprint so that both import paths resolve:
    from tools.trading.ta.blueprint import create_ta_blueprint   (shim path)
    from icdev.tools.trading.ta.blueprint import create_ta_blueprint  (canonical)
"""
from tools.trading.ta.blueprint import create_ta_blueprint  # noqa: F401

__all__ = ["create_ta_blueprint"]
