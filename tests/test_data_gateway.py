"""Tests for tools.fathomdesk.data_gateway.FathomDeskDataGateway."""

from unittest import mock


def test_import():
    """FathomDeskDataGateway imports and instantiates without error."""
    from tools.fathomdesk.data_gateway import FathomDeskDataGateway

    gw = FathomDeskDataGateway()
    assert gw is not None


def test_historical_bars_falls_back_to_yfinance_when_openbb_unavailable():
    """OpenBB unavailable → historical_bars uses yfinance and returns a list."""
    import tools.fathomdesk.data_gateway as dgm
    from tools.fathomdesk.data_gateway import FathomDeskDataGateway

    gw = FathomDeskDataGateway()

    mock_ts = mock.MagicMock()
    mock_ts.date.return_value = "2024-01-02"
    mock_row = {"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 102.0, "Volume": 1_000_000}

    mock_hist = mock.MagicMock()
    mock_hist.empty = False
    mock_hist.iterrows.return_value = [(mock_ts, mock_row)]

    mock_ticker_obj = mock.MagicMock()
    mock_ticker_obj.history.return_value = mock_hist

    mock_yf = mock.MagicMock()
    mock_yf.Ticker.return_value = mock_ticker_obj

    with mock.patch.object(
        type(gw._obb), "available", new_callable=mock.PropertyMock, return_value=False
    ):
        with mock.patch.object(dgm, "_yfinance", mock_yf):
            bars = gw.historical_bars("SPY", period="1mo")

    assert isinstance(bars, list)
    assert len(bars) >= 1
    assert bars[0]["source"] == "yfinance"
    assert "close" in bars[0]


def test_options_chain_fallback_returns_valid_shape():
    """When OpenBB is unavailable, options_chain falls back to chain_module."""
    from tools.fathomdesk.data_gateway import FathomDeskDataGateway

    gw = FathomDeskDataGateway()

    fake_chain = {
        "ticker": "SPY",
        "contracts": [{"strike": 400.0, "expiry": "2024-02-16", "option_type": "call"}],
        "iv_rank": 38.5,
        "iv_percentile": 52.0,
    }

    with mock.patch.object(
        type(gw._obb), "available", new_callable=mock.PropertyMock, return_value=False
    ):
        with mock.patch("tools.trading.options.chain.fetch_chain", return_value=fake_chain):
            chain = gw.options_chain("SPY")

    assert chain["source"] == "chain_module"
    assert "contracts" in chain
    assert "iv_rank" in chain
    assert "iv_percentile" in chain


def test_current_quote_returns_price_and_source():
    """current_quote returns a dict with a float price and a source string."""
    import tools.fathomdesk.data_gateway as dgm
    from tools.fathomdesk.data_gateway import FathomDeskDataGateway

    gw = FathomDeskDataGateway()

    mock_info = mock.MagicMock()
    mock_info.last_price = 185.25

    mock_ticker_obj = mock.MagicMock()
    mock_ticker_obj.fast_info = mock_info

    mock_yf = mock.MagicMock()
    mock_yf.Ticker.return_value = mock_ticker_obj

    with mock.patch.object(gw, "_alpaca_latest_price", side_effect=ValueError("no key")):
        with mock.patch.object(dgm, "_yfinance", mock_yf):
            quote = gw.current_quote("AAPL")

    assert "price" in quote
    assert isinstance(quote["price"], float)
    assert quote["price"] == 185.25
    assert "source" in quote
    assert quote["source"] == "yfinance"


def test_fundamentals_missing_sector_returns_none():
    """Fundamentals data without a sector field reports sector as None."""
    from tools.fathomdesk.data_gateway import FathomDeskDataGateway

    gw = FathomDeskDataGateway()

    mock_result = {
        "ticker": "AAPL",
        "data": [{"pe_ratio": 28.5, "market_cap": 3_000_000_000_000}],
        "source": "openbb",
    }

    with mock.patch.object(gw._obb, "get_fundamentals", return_value=mock_result):
        result = gw.fundamentals("AAPL")

    assert result["sector"] is None
