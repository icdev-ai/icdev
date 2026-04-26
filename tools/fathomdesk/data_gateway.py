#!/usr/bin/env python3
"""FathomDesk Data Gateway — unified market-data facade.

Aggregates OpenBB (fundamentals/options), Alpaca (broker), and yfinance
(price fallback) behind a single class so callers don't need to wire
adapters individually.  All external dependencies are guarded — the class
instantiates cleanly even when none of them are installed.

Usage:
    from tools.fathomdesk.data_gateway import FathomDeskDataGateway
    gw = FathomDeskDataGateway()
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_yfinance = None
try:
    import yfinance as _yfinance  # type: ignore[import-untyped]
except ImportError:
    _yfinance = None

from tools.fathomdesk.openbb_gateway import gateway as _obb_singleton, OpenBBGateway  # noqa: E402
from tools.fathomdesk.broker_adapter import BrokerAdapter  # noqa: E402


class FathomDeskDataGateway:
    """Unified data facade over OpenBB, Alpaca, and yfinance.

    Attributes:
        _obb: The module-level :class:`OpenBBGateway` singleton.
        _alpaca: Lazily-initialised :class:`BrokerAdapter`; ``None`` until
            first call to :meth:`_get_alpaca`.
    """

    def __init__(self) -> None:
        self._obb: OpenBBGateway = _obb_singleton
        self._alpaca: BrokerAdapter | None = None

    def _get_alpaca(self) -> BrokerAdapter:
        """Return the :class:`BrokerAdapter`, creating it on first access."""
        if self._alpaca is None:
            self._alpaca = BrokerAdapter()
        return self._alpaca
