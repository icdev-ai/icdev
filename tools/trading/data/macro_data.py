"""Macro market overlay data layer for FathomDesk trading engine (Layer 0).

Fetches global macro indicators from FRED + yfinance, computes regime
classification (GREEN/YELLOW/RED), supply chain risk, geopolitical risk,
and per-sector impact scores. 100% deterministic — no LLM calls.

Falls back to sample data when FRED/yfinance are unavailable.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

# ICDEV™ parent path setup
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.trading.data.bond_etf_data import (  # noqa: E402
    classify_bond_etf_regime,
    get_bond_etf_snapshot,
)

# Sector macro sensitivity lookup (deterministic)
SECTOR_MACRO_SENSITIVITY = {
    "Technology": {"rate_sensitivity": -0.8, "oil_sensitivity": -0.2, "dxy_sensitivity": -0.4},
    "Energy": {"rate_sensitivity": -0.3, "oil_sensitivity": 0.9, "dxy_sensitivity": -0.2},
    "Financials": {"rate_sensitivity": 0.7, "oil_sensitivity": -0.1, "dxy_sensitivity": 0.3},
    "Consumer Staples": {"rate_sensitivity": -0.2, "oil_sensitivity": -0.4, "dxy_sensitivity": -0.1},
    "Consumer Discretionary": {"rate_sensitivity": -0.6, "oil_sensitivity": -0.3, "dxy_sensitivity": -0.2},
    "Healthcare": {"rate_sensitivity": -0.3, "oil_sensitivity": -0.1, "dxy_sensitivity": -0.1},
    "Industrials": {"rate_sensitivity": -0.4, "oil_sensitivity": -0.3, "dxy_sensitivity": -0.3},
    "Materials": {"rate_sensitivity": -0.3, "oil_sensitivity": 0.4, "dxy_sensitivity": -0.5},
    "Utilities": {"rate_sensitivity": -0.5, "oil_sensitivity": -0.2, "dxy_sensitivity": 0.0},
    "Real Estate": {"rate_sensitivity": -0.7, "oil_sensitivity": -0.1, "dxy_sensitivity": -0.1},
    "Communication Services": {"rate_sensitivity": -0.4, "oil_sensitivity": -0.1, "dxy_sensitivity": -0.2},
    "Crypto": {"rate_sensitivity": -0.5, "oil_sensitivity": 0.0, "dxy_sensitivity": -0.6},
    "Defense": {"rate_sensitivity": -0.2, "oil_sensitivity": 0.3, "dxy_sensitivity": 0.1},
    "Semiconductors": {"rate_sensitivity": -0.7, "oil_sensitivity": -0.1, "dxy_sensitivity": -0.5},
    "Cybersecurity": {"rate_sensitivity": -0.5, "oil_sensitivity": 0.0, "dxy_sensitivity": -0.2},
    "Biotech": {"rate_sensitivity": -0.6, "oil_sensitivity": 0.0, "dxy_sensitivity": -0.3},
    "Big Pharma": {"rate_sensitivity": -0.2, "oil_sensitivity": -0.1, "dxy_sensitivity": -0.1},
    "Banks": {"rate_sensitivity": 0.8, "oil_sensitivity": -0.1, "dxy_sensitivity": 0.2},
    "Oil & Gas": {"rate_sensitivity": -0.2, "oil_sensitivity": 0.9, "dxy_sensitivity": -0.3},
}

# Ticker-to-sector mapping for common tickers
TICKER_SECTOR = {
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOGL": "Technology",
    "AMZN": "Consumer Discretionary",
    "NVDA": "Technology",
    "TSLA": "Consumer Discretionary",
    "META": "Technology",
    "JPM": "Financials",
    "BAC": "Financials",
    "XOM": "Energy",
    "CVX": "Energy",
    "JNJ": "Healthcare",
    "PFE": "Healthcare",
    "WMT": "Consumer Staples",
    "PG": "Consumer Staples",
    "BTC/USD": "Crypto",
    "ETH/USD": "Crypto",
    "SOL/USD": "Crypto",
}

# Geopolitical risk keywords
GEO_RISK_KEYWORDS = [
    "war",
    "sanctions",
    "invasion",
    "blockade",
    "missile",
    "nuclear",
    "embargo",
    "conflict",
    "military",
    "strike",
    "tariff",
    "retaliation",
]

# Supply chain stress keywords
SUPPLY_CHAIN_KEYWORDS = [
    "oil shock",
    "shipping disruption",
    "supply chain",
    "port closure",
    "semiconductor shortage",
    "export ban",
    "trade war",
]


def _generate_sample_macro() -> dict:
    """Generate deterministic sample macro data for offline/dev mode.

    Uses date-based seed so values shift day-to-day but are repeatable.
    Includes Van Metre monetary system indicators.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seed = int(hashlib.sha256(today.encode("utf-8")).hexdigest()[:8], 16)

    def _pseudo(s, lo, hi):
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        return s, lo + (s % 1000) / 1000.0 * (hi - lo)

    seed, vix = _pseudo(seed, 11.0, 35.0)
    seed, yield_spread = _pseudo(seed, -0.5, 2.5)
    seed, fed_funds = _pseudo(seed, 3.0, 6.0)
    seed, dxy = _pseudo(seed, 90.0, 112.0)
    seed, oil = _pseudo(seed, 55.0, 110.0)
    seed, oil_prev = _pseudo(seed, 55.0, 110.0)
    seed, gold = _pseudo(seed, 1800.0, 2600.0)
    seed, gold_prev = _pseudo(seed, 1800.0, 2600.0)
    seed, sp500 = _pseudo(seed, 4200.0, 5800.0)
    seed, sp500_sma50 = _pseudo(seed, 4100.0, 5700.0)
    seed, nasdaq = _pseudo(seed, 13000.0, 19000.0)
    seed, nasdaq_sma50 = _pseudo(seed, 12800.0, 18800.0)

    # Van Metre monetary system indicators
    seed, money_multiplier = _pseudo(seed, 2.5, 4.5)
    seed, velocity_of_money = _pseudo(seed, 1.0, 1.6)
    seed, loan_growth_yoy = _pseudo(seed, -2.0, 8.0)
    seed, bank_cash_assets = _pseudo(seed, 2800.0, 4500.0)  # billions
    seed, rrp_balance = _pseudo(seed, 0.0, 2500.0)  # billions
    seed, gdx = _pseudo(seed, 25.0, 45.0)
    seed, gdx_sma50 = _pseudo(seed, 24.0, 44.0)

    # Stagflation composite components (deterministic sample)
    seed, gdp_growth_q = _pseudo(seed, -0.5, 3.5)   # quarterly annualized real GDP %
    seed, cpi_yoy = _pseudo(seed, 2.0, 5.5)          # CPI year-over-year %
    # Unemployment trend: encode as float where >0 means rising N months
    seed, unemp_trend_months = _pseudo(seed, -2.0, 5.0)  # positive = rising N months
    seed, energy_change_30d = _pseudo(seed, -0.08, 0.12)  # energy price 30d change

    # Deflation composite components (deterministic sample)
    # M2 YoY growth — negative = monetary contraction (rare, strong deflation signal)
    seed, m2_yoy_pct = _pseudo(seed, -1.5, 7.0)
    # Market-implied inflation expectations (FRED T5YIFR / T10YIE)
    seed, breakeven_5y5y = _pseudo(seed, 1.5, 2.8)   # 5y5y forward breakeven %
    seed, breakeven_10y = _pseudo(seed, 1.5, 2.8)    # 10y breakeven %

    # Fed balance sheet for QE/QT phase classification
    seed, fed_bs = _pseudo(seed, 6500.0, 9000.0)
    seed, fed_bs_4w_ago = _pseudo(seed, 6500.0, 9000.0)
    fed_bs_4w_roc_b = (fed_bs - fed_bs_4w_ago) / 4.0

    # Treasury supply pressure components
    seed, t10y2y_4w_roc = _pseudo(seed, -0.5, 0.5)       # percentage points
    seed, net_liquidity_4w_roc_b = _pseudo(seed, -300.0, 300.0)  # billions
    seed, _fd_coin = _pseudo(seed, 0.0, 10.0)
    fiscal_deficit_expanding = 1.0 if _fd_coin < 5.0 else 0.0

    # Credit impulse components (TOTCI 4-week and 13-week delta as % of nominal GDP)
    seed, short_credit_impulse = _pseudo(seed, -0.12, 0.22)
    seed, long_credit_impulse = _pseudo(seed, -0.25, 0.40)

    oil_change_30d = (oil - oil_prev) / oil_prev if oil_prev else 0
    gold_change_30d = (gold - gold_prev) / gold_prev if gold_prev else 0
    gold_copper_ratio = gold / max(4.0, 3.5 + (seed % 100) / 50.0)

    return {
        "vix": round(vix, 2),
        "yield_spread": round(yield_spread, 2),
        "fed_funds": round(fed_funds, 2),
        "dxy": round(dxy, 2),
        "oil": round(oil, 2),
        "oil_change_30d": round(oil_change_30d, 4),
        "gold": round(gold, 2),
        "gold_change_30d": round(gold_change_30d, 4),
        "gold_copper_ratio": round(gold_copper_ratio, 2),
        "sp500": round(sp500, 2),
        "sp500_sma50": round(sp500_sma50, 2),
        "nasdaq": round(nasdaq, 2),
        "nasdaq_sma50": round(nasdaq_sma50, 2),
        # Van Metre monetary system indicators
        "money_multiplier": round(money_multiplier, 2),
        "velocity_of_money": round(velocity_of_money, 2),
        "loan_growth_yoy": round(loan_growth_yoy, 2),
        "bank_cash_assets_b": round(bank_cash_assets, 1),
        "rrp_balance_b": round(rrp_balance, 1),
        "gdx": round(gdx, 2),
        "gdx_sma50": round(gdx_sma50, 2),
        # Stagflation composite components
        "gdp_growth_q": round(gdp_growth_q, 2),
        "cpi_yoy": round(cpi_yoy, 2),
        "unemp_trend_months": round(unemp_trend_months, 1),
        "energy_change_30d": round(energy_change_30d, 4),
        # Deflation composite components
        "m2_yoy_pct": round(m2_yoy_pct, 2),
        "breakeven_5y5y": round(breakeven_5y5y, 2),
        "breakeven_10y": round(breakeven_10y, 2),
        # Fed balance sheet for QE/QT phase
        "fed_bs_4w_roc_b": round(fed_bs_4w_roc_b, 1),
        # Treasury supply pressure components
        "t10y2y_4w_roc": round(t10y2y_4w_roc, 2),
        "net_liquidity_4w_roc_b": round(net_liquidity_4w_roc_b, 1),
        "fiscal_deficit_expanding": fiscal_deficit_expanding,
        # Credit impulse (TOTCI 4-week and 13-week delta as % of nominal GDP)
        "short_credit_impulse": round(short_credit_impulse, 4),
        "long_credit_impulse": round(long_credit_impulse, 4),
        "credit_impulse_label": _classify_credit_impulse(short_credit_impulse, long_credit_impulse),
    }


def _fetch_live_macro() -> dict | None:
    """Attempt to fetch live macro data from FRED + yfinance.

    Returns None if dependencies are unavailable.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        from fredapi import Fred
        import os

        fred_key = os.environ.get("FRED_API_KEY", "")
        fred = Fred(api_key=fred_key) if fred_key else None
    except (ImportError, Exception):
        fred = None

    data = {}
    try:
        # VIX
        vix_data = yf.Ticker("^VIX").history(period="5d")
        data["vix"] = round(float(vix_data["Close"].iloc[-1]), 2) if len(vix_data) > 0 else 20.0

        # DXY
        dxy_data = yf.Ticker("DX-Y.NYB").history(period="5d")
        data["dxy"] = round(float(dxy_data["Close"].iloc[-1]), 2) if len(dxy_data) > 0 else 100.0

        # Oil (WTI)
        oil_data = yf.Ticker("CL=F").history(period="35d")
        if len(oil_data) >= 2:
            data["oil"] = round(float(oil_data["Close"].iloc[-1]), 2)
            oil_30d_ago = float(oil_data["Close"].iloc[0])
            data["oil_change_30d"] = round((data["oil"] - oil_30d_ago) / oil_30d_ago, 4) if oil_30d_ago else 0
        else:
            data["oil"] = 75.0
            data["oil_change_30d"] = 0.0

        # Gold
        gold_data = yf.Ticker("GC=F").history(period="35d")
        if len(gold_data) >= 2:
            data["gold"] = round(float(gold_data["Close"].iloc[-1]), 2)
            gold_30d_ago = float(gold_data["Close"].iloc[0])
            data["gold_change_30d"] = round((data["gold"] - gold_30d_ago) / gold_30d_ago, 4) if gold_30d_ago else 0
        else:
            data["gold"] = 2200.0
            data["gold_change_30d"] = 0.0

        # S&P 500
        sp_data = yf.Ticker("^GSPC").history(period="70d")
        if len(sp_data) >= 50:
            data["sp500"] = round(float(sp_data["Close"].iloc[-1]), 2)
            data["sp500_sma50"] = round(float(sp_data["Close"].tail(50).mean()), 2)
        else:
            data["sp500"] = 5200.0
            data["sp500_sma50"] = 5100.0

        # NASDAQ
        nq_data = yf.Ticker("^IXIC").history(period="70d")
        if len(nq_data) >= 50:
            data["nasdaq"] = round(float(nq_data["Close"].iloc[-1]), 2)
            data["nasdaq_sma50"] = round(float(nq_data["Close"].tail(50).mean()), 2)
        else:
            data["nasdaq"] = 16000.0
            data["nasdaq_sma50"] = 15800.0

        # Gold/copper ratio (proxy for economic health)
        copper_data = yf.Ticker("HG=F").history(period="5d")
        copper_price = float(copper_data["Close"].iloc[-1]) if len(copper_data) > 0 else 4.0
        data["gold_copper_ratio"] = round(data.get("gold", 2200) / copper_price, 2) if copper_price > 0 else 500.0

    except Exception:
        return None

    # GDX (Gold Miners ETF — Van Metre crash leading indicator)
    try:
        gdx_data = yf.Ticker("GDX").history(period="70d")
        if len(gdx_data) >= 50:
            data["gdx"] = round(float(gdx_data["Close"].iloc[-1]), 2)
            data["gdx_sma50"] = round(float(gdx_data["Close"].tail(50).mean()), 2)
        else:
            data["gdx"] = 35.0
            data["gdx_sma50"] = 34.0
    except Exception:
        data["gdx"] = 35.0
        data["gdx_sma50"] = 34.0

    # FRED data
    if fred:
        try:
            t10y2y = fred.get_series("T10Y2Y", observation_start="2024-01-01")
            t10y2y_clean = t10y2y.dropna()
            data["yield_spread"] = round(float(t10y2y_clean.iloc[-1]), 2)
            # 4-week rate of change (~20 trading days) for treasury supply pressure
            if len(t10y2y_clean) >= 21:
                data["t10y2y_4w_roc"] = round(
                    float(t10y2y_clean.iloc[-1]) - float(t10y2y_clean.iloc[-21]), 2
                )
            else:
                data["t10y2y_4w_roc"] = 0.0
        except Exception:
            data["yield_spread"] = 0.5
            data["t10y2y_4w_roc"] = 0.0

        try:
            fedfunds = fred.get_series("FEDFUNDS", observation_start="2024-01-01")
            data["fed_funds"] = round(float(fedfunds.dropna().iloc[-1]), 2)
        except Exception:
            data["fed_funds"] = 5.0

        # Van Metre monetary system indicators (FRED series)
        # Money Multiplier = M2 / Monetary Base (MULT)
        try:
            mult = fred.get_series("MULT", observation_start="2024-01-01")
            data["money_multiplier"] = round(float(mult.dropna().iloc[-1]), 2)
        except Exception:
            data["money_multiplier"] = 3.5

        # Velocity of M2 Money Stock (M2V) — quarterly
        try:
            m2v = fred.get_series("M2V", observation_start="2023-01-01")
            data["velocity_of_money"] = round(float(m2v.dropna().iloc[-1]), 2)
        except Exception:
            data["velocity_of_money"] = 1.2

        # Loans and Leases in Bank Credit YoY growth (TOTLL)
        try:
            loans = fred.get_series("TOTLL", observation_start="2023-01-01")
            loans_clean = loans.dropna()
            if len(loans_clean) >= 13:  # need ~1 year of monthly data
                current = float(loans_clean.iloc[-1])
                year_ago = float(loans_clean.iloc[-13])
                data["loan_growth_yoy"] = round(
                    (current - year_ago) / year_ago * 100,
                    2,
                )
            else:
                data["loan_growth_yoy"] = 2.0
        except Exception:
            data["loan_growth_yoy"] = 2.0

        # TOTCI: Commercial & Industrial loans (weekly H8, billions) — credit impulse
        # 4-week delta = short_credit_impulse; 13-week delta = long_credit_impulse; both as % of nominal GDP
        try:
            totci = fred.get_series("TOTCI", observation_start="2023-01-01")
            totci_clean = totci.dropna()
            gdp_nom = fred.get_series("GDP", observation_start="2022-01-01")
            gdp_level = float(gdp_nom.dropna().iloc[-1]) if len(gdp_nom.dropna()) > 0 else 28000.0
            if len(totci_clean) >= 14 and gdp_level > 0:
                latest_ci = float(totci_clean.iloc[-1])
                ci_4w_ago = float(totci_clean.iloc[-5]) if len(totci_clean) >= 5 else latest_ci
                ci_13w_ago = float(totci_clean.iloc[-14])
                data["short_credit_impulse"] = round((latest_ci - ci_4w_ago) / gdp_level * 100, 4)
                data["long_credit_impulse"] = round((latest_ci - ci_13w_ago) / gdp_level * 100, 4)
            else:
                data["short_credit_impulse"] = 0.0
                data["long_credit_impulse"] = 0.0
            data["credit_impulse_label"] = _classify_credit_impulse(
                data["short_credit_impulse"], data["long_credit_impulse"]
            )
        except Exception:
            data["short_credit_impulse"] = 0.0
            data["long_credit_impulse"] = 0.0
            data["credit_impulse_label"] = "NEUTRAL"

        # Cash Assets at Commercial Banks (CASACBW027SBOG) — weekly
        try:
            cash = fred.get_series(
                "CASACBW027SBOG",
                observation_start="2024-01-01",
            )
            data["bank_cash_assets_b"] = round(
                float(cash.dropna().iloc[-1]) / 1000,
                1,
            )  # convert millions to billions
        except Exception:
            data["bank_cash_assets_b"] = 3500.0

        # Overnight Reverse Repo (RRPONTSYD) — daily
        try:
            rrp = fred.get_series("RRPONTSYD", observation_start="2024-01-01")
            data["rrp_balance_b"] = round(
                float(rrp.dropna().iloc[-1]) / 1000,
                1,
            )  # convert millions to billions
        except Exception:
            data["rrp_balance_b"] = 200.0

        # --- Stagflation composite FRED series ---
        # Real GDP growth (quarterly annualized, A191RL1Q225SBEA)
        try:
            gdp = fred.get_series("A191RL1Q225SBEA", observation_start="2023-01-01")
            gdp_clean = gdp.dropna()
            data["gdp_growth_q"] = round(float(gdp_clean.iloc[-1]), 2)
        except Exception:
            data["gdp_growth_q"] = 2.0

        # CPI YoY (CPIAUCSL — compute from 12-month change)
        try:
            cpi = fred.get_series("CPIAUCSL", observation_start="2023-01-01")
            cpi_clean = cpi.dropna()
            if len(cpi_clean) >= 13:
                cpi_now = float(cpi_clean.iloc[-1])
                cpi_year_ago = float(cpi_clean.iloc[-13])
                data["cpi_yoy"] = round(
                    (cpi_now - cpi_year_ago) / cpi_year_ago * 100, 2
                )
            else:
                data["cpi_yoy"] = 3.0
        except Exception:
            data["cpi_yoy"] = 3.0

        # Unemployment trend — count consecutive monthly rises (UNRATE)
        try:
            unrate = fred.get_series("UNRATE", observation_start="2023-01-01")
            unrate_clean = unrate.dropna()
            vals = list(unrate_clean.tail(5))
            rising_streak = 0
            for i in range(len(vals) - 1, 0, -1):
                if vals[i] > vals[i - 1]:
                    rising_streak += 1
                else:
                    break
            data["unemp_trend_months"] = float(rising_streak)
        except Exception:
            data["unemp_trend_months"] = 0.0

        # Energy CPI 30d change (CUSR0000SEHF01 — Energy commodities)
        try:
            energy = fred.get_series(
                "CUSR0000SEHF01", observation_start="2024-01-01"
            )
            energy_clean = energy.dropna()
            if len(energy_clean) >= 2:
                e_now = float(energy_clean.iloc[-1])
                e_prev = float(energy_clean.iloc[-2])
                data["energy_change_30d"] = round(
                    (e_now - e_prev) / e_prev if e_prev else 0, 4
                )
            else:
                data["energy_change_30d"] = data.get("oil_change_30d", 0.0)
        except Exception:
            # Fall back to oil price change as energy proxy
            data["energy_change_30d"] = data.get("oil_change_30d", 0.0)

        # --- Deflation composite FRED series ---
        # M2 YoY growth (M2SL — monthly, compute 12-month % change)
        try:
            m2 = fred.get_series("M2SL", observation_start="2023-01-01")
            m2_clean = m2.dropna()
            if len(m2_clean) >= 13:
                m2_now = float(m2_clean.iloc[-1])
                m2_year_ago = float(m2_clean.iloc[-13])
                data["m2_yoy_pct"] = round(
                    (m2_now - m2_year_ago) / m2_year_ago * 100, 2
                ) if m2_year_ago else 0.0
            else:
                data["m2_yoy_pct"] = 2.0
        except Exception:
            data["m2_yoy_pct"] = 2.0

        # 5y5y forward breakeven inflation expectation (T5YIFR — daily)
        try:
            be5y5y = fred.get_series("T5YIFR", observation_start="2024-01-01")
            data["breakeven_5y5y"] = round(float(be5y5y.dropna().iloc[-1]), 2)
        except Exception:
            data["breakeven_5y5y"] = 2.3

        # 10y breakeven inflation expectation (T10YIE — daily)
        try:
            be10y = fred.get_series("T10YIE", observation_start="2024-01-01")
            data["breakeven_10y"] = round(float(be10y.dropna().iloc[-1]), 2)
        except Exception:
            data["breakeven_10y"] = 2.3

        # Fed Balance Sheet 4-week RoC (WALCL — weekly, billions) for QE/QT phase
        try:
            walcl = fred.get_series("WALCL", observation_start="2024-01-01")
            walcl_clean = walcl.dropna()
            if len(walcl_clean) >= 5:
                fed_bs_now = float(walcl_clean.iloc[-1])
                fed_bs_4w_ago = float(walcl_clean.iloc[-5])
                data["fed_bs_4w_roc_b"] = round((fed_bs_now - fed_bs_4w_ago) / 4.0, 1)
            else:
                data["fed_bs_4w_roc_b"] = 0.0
        except Exception:
            data["fed_bs_4w_roc_b"] = 0.0

        # --- Treasury supply pressure components ---
        # Fiscal deficit trajectory (MTSDS133FMS — monthly, millions; negative = deficit)
        try:
            deficit = fred.get_series("MTSDS133FMS", observation_start="2022-01-01")
            deficit_clean = deficit.dropna()
            if len(deficit_clean) >= 24:
                recent_12m = float(deficit_clean.iloc[-12:].sum())
                prior_12m = float(deficit_clean.iloc[-24:-12].sum())
                # Expanding deficit = recent 12-month sum more negative than prior year
                data["fiscal_deficit_expanding"] = 1.0 if recent_12m < prior_12m else 0.0
            else:
                data["fiscal_deficit_expanding"] = 0.0
        except Exception:
            data["fiscal_deficit_expanding"] = 0.0

        # Net liquidity 4-week trend (Fed BS - TGA - RRP, in billions)
        try:
            walcl_nl = fred.get_series("WALCL", observation_start="2024-01-01").dropna()
            tga_nl = fred.get_series("WTREGEN", observation_start="2024-01-01").dropna()
            rrp_nl = fred.get_series("RRPONTSYD", observation_start="2024-01-01").dropna()
            if len(walcl_nl) >= 5 and len(tga_nl) >= 5 and len(rrp_nl) >= 21:
                nl_now = (
                    float(walcl_nl.iloc[-1]) / 1000
                    - float(tga_nl.iloc[-1]) / 1000
                    - float(rrp_nl.iloc[-1]) / 1000
                )
                nl_4w_ago = (
                    float(walcl_nl.iloc[-5]) / 1000
                    - float(tga_nl.iloc[-5]) / 1000
                    - float(rrp_nl.iloc[-21]) / 1000
                )
                data["net_liquidity_4w_roc_b"] = round(nl_now - nl_4w_ago, 1)
            else:
                data["net_liquidity_4w_roc_b"] = 0.0
        except Exception:
            data["net_liquidity_4w_roc_b"] = 0.0
    else:
        data["yield_spread"] = 0.5
        data["fed_funds"] = 5.0
        data["money_multiplier"] = 3.5
        data["velocity_of_money"] = 1.2
        data["loan_growth_yoy"] = 2.0
        data["bank_cash_assets_b"] = 3500.0
        data["rrp_balance_b"] = 200.0
        # Stagflation defaults (no FRED key)
        data["gdp_growth_q"] = 2.0
        data["cpi_yoy"] = 3.0
        data["unemp_trend_months"] = 0.0
        data["energy_change_30d"] = data.get("oil_change_30d", 0.0)
        # Deflation defaults (no FRED key)
        data["m2_yoy_pct"] = 2.0
        data["breakeven_5y5y"] = 2.3
        data["breakeven_10y"] = 2.3
        data["fed_bs_4w_roc_b"] = 0.0
        # Treasury supply pressure defaults (no FRED key)
        data["t10y2y_4w_roc"] = 0.0
        data["fiscal_deficit_expanding"] = 0.0
        data["net_liquidity_4w_roc_b"] = 0.0
        # Credit impulse defaults (no FRED key)
        data["short_credit_impulse"] = 0.0
        data["long_credit_impulse"] = 0.0
        data["credit_impulse_label"] = "NEUTRAL"

    return data


def _classify_credit_impulse(short: float, long: float) -> str:
    """Classify credit impulse trend as ACCELERATING/DECELERATING/CONTRACTING/NEUTRAL.

    long_credit_impulse (13-week TOTCI delta as % of nominal GDP) leads GDP by ~12 months.
    short_credit_impulse (4-week delta) provides momentum confirmation.
    """
    if abs(long) < 0.05:
        return "NEUTRAL"
    elif long > 0.20:
        return "ACCELERATING"
    elif long > 0:
        return "DECELERATING"
    else:
        return "CONTRACTING"


def _compute_treasury_supply_pressure(raw: dict) -> dict:
    """Compute treasury supply pressure index (score 0-3, level LOW/MODERATE/ELEVATED).

    Three binary signals — each contributes +1:
      - fiscal_deficit_expanding: rolling 12-month deficit worsened vs prior year
      - t10y2y_4w_roc > 0: yield spread rising (more supply pressure on long end)
      - net_liquidity_4w_roc_b < 0: net liquidity (Fed BS - TGA - RRP) falling

    ELEVATED (3/3) is wired into yield spread scoring as an additional headwind.
    """
    score = 0
    flags = []

    if raw.get("fiscal_deficit_expanding", 0.0) >= 1.0:
        score += 1
        flags.append("deficit_expanding")

    if raw.get("t10y2y_4w_roc", 0.0) > 0.0:
        score += 1
        flags.append("t10y2y_rising")

    if raw.get("net_liquidity_4w_roc_b", 0.0) < 0.0:
        score += 1
        flags.append("net_liquidity_falling")

    if score == 3:
        level = "ELEVATED"
    elif score == 2:
        level = "MODERATE"
    else:
        level = "LOW"

    return {
        "score": score,
        "level": level,
        "active_flags": score,
        "flags": flags,
        "label": ", ".join(flags) if flags else "no supply pressure signals",
    }


def _score_indicator(name: str, value: float) -> dict:
    """Score a single macro indicator as GREEN/YELLOW/RED with a 0-100 score."""
    if name == "vix":
        if value < 15:
            return {"signal": "GREEN", "score": 90, "label": "Low volatility"}
        elif value <= 25:
            return {"signal": "YELLOW", "score": 55, "label": "Elevated volatility"}
        else:
            return {"signal": "RED", "score": 15, "label": "High volatility (risk-off)"}

    elif name == "yield_spread":
        if value > 1.0:
            return {"signal": "GREEN", "score": 85, "label": "Positive yield curve"}
        elif value >= 0:
            return {"signal": "YELLOW", "score": 50, "label": "Flattening yield curve"}
        else:
            return {"signal": "RED", "score": 10, "label": "Inverted yield curve (recession signal)"}

    elif name == "fed_funds":
        # Higher rates = headwind for equities
        if value < 3.0:
            return {"signal": "GREEN", "score": 85, "label": "Accommodative policy"}
        elif value <= 5.0:
            return {"signal": "YELLOW", "score": 50, "label": "Neutral/tightening policy"}
        else:
            return {"signal": "RED", "score": 20, "label": "Restrictive monetary policy"}

    elif name == "dxy":
        if value < 95:
            return {"signal": "GREEN", "score": 75, "label": "Weak dollar (EM/commodity tailwind)"}
        elif value <= 105:
            return {"signal": "YELLOW", "score": 55, "label": "Neutral dollar"}
        else:
            return {"signal": "RED", "score": 25, "label": "Strong dollar (EM headwind)"}

    elif name == "oil_change_30d":
        if abs(value) < 0.05:
            return {"signal": "GREEN", "score": 80, "label": "Stable oil prices"}
        elif abs(value) < 0.10:
            return {"signal": "YELLOW", "score": 50, "label": "Oil price volatility"}
        else:
            if value > 0:
                return {"signal": "RED", "score": 15, "label": "Oil price spike (supply chain stress)"}
            else:
                return {"signal": "YELLOW", "score": 55, "label": "Oil price drop (deflation risk)"}

    elif name == "sp500_trend":
        # value = sp500 - sp500_sma50
        if value > 0:
            return {"signal": "GREEN", "score": 75, "label": "S&P 500 above 50-day SMA (uptrend)"}
        else:
            return {"signal": "RED", "score": 30, "label": "S&P 500 below 50-day SMA (downtrend)"}

    elif name == "nasdaq_trend":
        if value > 0:
            return {"signal": "GREEN", "score": 75, "label": "NASDAQ above 50-day SMA (uptrend)"}
        else:
            return {"signal": "RED", "score": 30, "label": "NASDAQ below 50-day SMA (downtrend)"}

    elif name == "gold_change_30d":
        if value > 0.05:
            return {"signal": "YELLOW", "score": 40, "label": "Gold rising (risk-off sentiment)"}
        elif value < -0.05:
            return {"signal": "GREEN", "score": 70, "label": "Gold falling (risk-on sentiment)"}
        else:
            return {"signal": "GREEN", "score": 65, "label": "Gold stable"}

    # --- Van Metre monetary system indicators ---
    elif name == "money_multiplier":
        # Declining multiplier = reserves trapped = liquidity trap
        if value > 4.0:
            return {"signal": "GREEN", "score": 80, "label": "Healthy money multiplication"}
        elif value > 3.0:
            return {"signal": "YELLOW", "score": 50, "label": "Declining money multiplier"}
        else:
            return {"signal": "RED", "score": 15, "label": "Liquidity trap — reserves not circulating"}

    elif name == "velocity_of_money":
        # Low velocity = money trapped, deflationary
        if value > 1.4:
            return {"signal": "GREEN", "score": 75, "label": "Money circulating normally"}
        elif value > 1.1:
            return {"signal": "YELLOW", "score": 45, "label": "Declining money velocity"}
        else:
            return {"signal": "RED", "score": 10, "label": "Velocity collapse — deflationary"}

    elif name == "loan_growth_yoy":
        # Only real engine of money creation (Van Metre core thesis)
        if value > 5.0:
            return {"signal": "GREEN", "score": 85, "label": "Strong lending growth (expansionary)"}
        elif value > 1.0:
            return {"signal": "YELLOW", "score": 55, "label": "Modest lending growth"}
        elif value > -1.0:
            return {"signal": "RED", "score": 25, "label": "Lending stagnation — money destruction risk"}
        else:
            return {"signal": "RED", "score": 5, "label": "Credit contraction — deflationary"}

    elif name == "bank_cash_assets":
        # Rising bank cash = downward pressure on yields (inverse relationship)
        if value < 3000:
            return {"signal": "GREEN", "score": 70, "label": "Banks deploying cash (lending)"}
        elif value < 4000:
            return {"signal": "YELLOW", "score": 45, "label": "Banks hoarding cash"}
        else:
            return {"signal": "RED", "score": 20, "label": "Excess bank cash — yield suppression"}

    elif name == "rrp_balance":
        # High RRP = collateral shortage (dollars chasing safety)
        if value < 200:
            return {"signal": "GREEN", "score": 75, "label": "RRP drained — collateral normalized"}
        elif value < 800:
            return {"signal": "YELLOW", "score": 50, "label": "Moderate RRP usage"}
        else:
            return {"signal": "RED", "score": 20, "label": "High RRP — collateral shortage"}

    elif name == "gdx_trend":
        # GDX below SMA50 = crash leading indicator (Van Metre)
        if value > 0:
            return {"signal": "GREEN", "score": 70, "label": "GDX above 50-day SMA (risk-on)"}
        else:
            return {"signal": "RED", "score": 25, "label": "GDX below 50-day SMA (crash risk)"}

    elif name == "stagflation_risk_score":
        # Composite 0-100; higher score = worse macro environment → invert for indicator
        inverted = 100 - int(value)
        if value == 0:
            return {"signal": "GREEN", "score": 100, "label": "No stagflation signals active"}
        elif value <= 25:
            return {"signal": "GREEN", "score": inverted, "label": "Low stagflation risk (1 flag)"}
        elif value <= 50:
            return {"signal": "YELLOW", "score": inverted, "label": "Moderate stagflation risk (2 flags)"}
        elif value <= 75:
            return {"signal": "RED", "score": inverted, "label": "High stagflation risk (3 flags)"}
        else:
            return {"signal": "RED", "score": 0, "label": "Critical stagflation regime (all 4 flags)"}

    elif name == "deflation_risk_score":
        # Composite 0-100; higher score = worse deflation risk → invert for indicator
        inverted = 100 - int(value)
        if value == 0:
            return {"signal": "GREEN", "score": 100, "label": "No deflation signals active"}
        elif value <= 25:
            return {"signal": "GREEN", "score": inverted, "label": "Low deflation risk (1 flag)"}
        elif value <= 50:
            return {"signal": "YELLOW", "score": inverted, "label": "Disinflation pressure (2 flags)"}
        elif value <= 75:
            return {"signal": "RED", "score": inverted, "label": "High deflation risk (3 flags)"}
        else:
            return {"signal": "RED", "score": 0, "label": "Critical deflation regime (all 4 flags)"}

    elif name == "breakeven_5y5y":
        # 5y5y forward inflation expectation (Fed target ~2.0%).
        # Band below target = disinflation/deflation risk; band around target = anchored;
        # well above target = inflation expectations un-anchoring.
        v = float(value)
        if v < 1.5:
            return {"signal": "RED", "score": 15, "label": "Breakeven < 1.5% (deflation expectations)"}
        elif v < 2.0:
            return {"signal": "YELLOW", "score": 45, "label": "Breakeven below Fed target (disinflation)"}
        elif v <= 2.5:
            return {"signal": "GREEN", "score": 80, "label": "Inflation expectations anchored near target"}
        elif v <= 3.0:
            return {"signal": "YELLOW", "score": 50, "label": "Inflation expectations elevated"}
        else:
            return {"signal": "RED", "score": 20, "label": "Inflation expectations un-anchoring"}

    elif name == "breakeven_10y":
        # Same bands as 5y5y but over 10y horizon; slightly wider tolerance.
        v = float(value)
        if v < 1.5:
            return {"signal": "RED", "score": 15, "label": "10y breakeven < 1.5% (deflation expectations)"}
        elif v < 2.0:
            return {"signal": "YELLOW", "score": 45, "label": "10y breakeven below target"}
        elif v <= 2.6:
            return {"signal": "GREEN", "score": 75, "label": "10y inflation expectations near target"}
        elif v <= 3.2:
            return {"signal": "YELLOW", "score": 50, "label": "10y inflation expectations elevated"}
        else:
            return {"signal": "RED", "score": 20, "label": "10y inflation expectations un-anchoring"}

    elif name == "treasury_supply_pressure":
        # Score 0-3; higher = more fiscal/liquidity supply pressure = headwind for yields/macro
        if value == 0:
            return {"signal": "GREEN", "score": 100, "label": "No treasury supply pressure (0/3 flags)"}
        elif value == 1:
            return {"signal": "GREEN", "score": 70, "label": "Low treasury supply pressure (1/3 flags)"}
        elif value == 2:
            return {"signal": "YELLOW", "score": 45, "label": "Moderate treasury supply pressure (2/3 flags)"}
        else:
            return {"signal": "RED", "score": 10, "label": "ELEVATED treasury supply pressure (all 3 flags)"}

    elif name == "long_credit_impulse":
        # 13-week TOTCI delta as % of nominal GDP — leads GDP by ~12 months (slow weight)
        if value > 0.30:
            return {"signal": "GREEN", "score": 85, "label": "Credit impulse accelerating (GDP tailwind ~12mo)"}
        elif value > 0.10:
            return {"signal": "GREEN", "score": 65, "label": "Credit impulse positive (mild GDP support ~12mo)"}
        elif value > -0.10:
            return {"signal": "YELLOW", "score": 45, "label": "Credit impulse neutral/decelerating"}
        elif value > -0.30:
            return {"signal": "RED", "score": 25, "label": "Credit impulse contracting (GDP headwind ~12mo)"}
        else:
            return {"signal": "RED", "score": 5, "label": "Credit impulse deep contraction"}

    return {"signal": "YELLOW", "score": 50, "label": "Unknown indicator"}


def _compute_geopolitical_risk(headlines: list[str] | None = None) -> dict:
    """Score geopolitical risk from news headlines (deterministic keyword scan).

    Args:
        headlines: Optional list of headline strings. Uses sample if None.

    Returns:
        Dict with score (0-100), keyword_hits, and risk_level.
    """
    if headlines is None:
        # Sample headlines for dev/offline mode
        headlines = [
            "Fed holds rates steady amid inflation concerns",
            "Tech sector rallies on strong earnings",
            "Oil prices stabilize after OPEC+ meeting",
            "Trade tensions ease between major economies",
            "Consumer spending shows resilience",
        ]

    keyword_hits = {}
    for headline in headlines:
        lower = headline.lower()
        for kw in GEO_RISK_KEYWORDS:
            if kw in lower:
                keyword_hits[kw] = keyword_hits.get(kw, 0) + 1

    total_hits = sum(keyword_hits.values())
    # Each hit adds ~8 points, capped at 100
    score = min(100, total_hits * 8)

    if score < 20:
        risk_level = "LOW"
    elif score < 50:
        risk_level = "MODERATE"
    else:
        risk_level = "HIGH"

    return {
        "score": score,
        "keyword_hits": keyword_hits,
        "risk_level": risk_level,
        "headline_count": len(headlines),
    }


def _compute_supply_chain_risk(raw: dict) -> dict:
    """Compute supply chain risk score from macro indicators.

    Args:
        raw: Raw macro data dict.

    Returns:
        Dict with score (0-100), components, and risk_level.
    """
    components = {}

    # Oil price change (30d)
    oil_change = abs(raw.get("oil_change_30d", 0))
    oil_risk = min(100, int(oil_change * 500))  # 20% change = 100
    components["oil_volatility"] = oil_risk

    # Gold/copper ratio (higher = worse economic outlook)
    gc_ratio = raw.get("gold_copper_ratio", 500)
    if gc_ratio > 600:
        gc_risk = 80
    elif gc_ratio > 500:
        gc_risk = 50
    else:
        gc_risk = 20
    components["gold_copper_signal"] = gc_risk

    # Commodity volatility (gold change as proxy)
    gold_change = abs(raw.get("gold_change_30d", 0))
    commodity_risk = min(100, int(gold_change * 400))
    components["commodity_volatility"] = commodity_risk

    # Weighted average
    score = int(oil_risk * 0.45 + gc_risk * 0.30 + commodity_risk * 0.25)

    if score < 25:
        risk_level = "LOW"
    elif score < 55:
        risk_level = "MODERATE"
    else:
        risk_level = "HIGH"

    return {
        "score": score,
        "components": components,
        "risk_level": risk_level,
    }


def _compute_stagflation_risk(raw: dict) -> dict:
    """Compute stagflation composite risk score from macro indicators.

    Flags four conditions derived from the 2025-2026 stagflation-lite thesis:
      - GDP < 1.5%  — stagnation threshold (quarterly annualized real GDP growth)
      - CPI > 3.0%  — persistent above-target inflation
      - Unemployment rising 3+ consecutive months — labour market deterioration
      - Energy +5% monthly — commodity price shock amplifier

    Each active flag contributes 25 points to the 0-100 composite score.

    Args:
        raw: Raw macro data dict (must contain gdp_growth_q, cpi_yoy,
             unemp_trend_months, energy_change_30d).

    Returns:
        Dict with score (0-100), flags, active_flags count, risk_level, and label.
    """
    # Thresholds — from 2025-2026 stagflation-lite analysis
    GDP_STAGNATION_THRESHOLD = 1.5    # % quarterly annualized real growth
    CPI_INFLATION_THRESHOLD = 3.0     # % YoY CPI
    UNEMP_RISING_MONTHS = 3           # consecutive months of rising unemployment
    ENERGY_SHOCK_THRESHOLD = 0.05     # +5% monthly energy price change

    gdp_growth = raw.get("gdp_growth_q", 2.0)
    cpi_yoy = raw.get("cpi_yoy", 3.0)
    unemp_trend = raw.get("unemp_trend_months", 0.0)
    energy_change = raw.get("energy_change_30d", raw.get("oil_change_30d", 0.0))

    flags = {
        "gdp_stagnation": gdp_growth < GDP_STAGNATION_THRESHOLD,
        "cpi_elevated": cpi_yoy > CPI_INFLATION_THRESHOLD,
        "unemployment_rising": unemp_trend >= UNEMP_RISING_MONTHS,
        "energy_shock": energy_change >= ENERGY_SHOCK_THRESHOLD,
    }

    active_flags = sum(1 for v in flags.values() if v)
    score = active_flags * 25  # 0, 25, 50, 75, or 100

    if active_flags == 0:
        risk_level = "NONE"
        label = "No stagflation signals active"
    elif active_flags == 1:
        risk_level = "LOW"
        label = "Early stagflation signal — 1 of 4 conditions met"
    elif active_flags == 2:
        risk_level = "MODERATE"
        label = "Stagflation-lite — 2 of 4 conditions met"
    elif active_flags == 3:
        risk_level = "HIGH"
        label = "Stagflation risk elevated — 3 of 4 conditions met"
    else:
        risk_level = "CRITICAL"
        label = "Full stagflation regime — all 4 conditions met"

    return {
        "score": score,
        "risk_level": risk_level,
        "label": label,
        "active_flags": active_flags,
        "flags": flags,
        "inputs": {
            "gdp_growth_q": round(gdp_growth, 2),
            "cpi_yoy": round(cpi_yoy, 2),
            "unemp_trend_months": round(unemp_trend, 1),
            "energy_change_30d": round(energy_change, 4),
        },
        "thresholds": {
            "gdp_stagnation": GDP_STAGNATION_THRESHOLD,
            "cpi_elevated": CPI_INFLATION_THRESHOLD,
            "unemployment_rising": UNEMP_RISING_MONTHS,
            "energy_shock": ENERGY_SHOCK_THRESHOLD,
        },
    }


def _compute_deflation_risk(raw: dict) -> dict:
    """Compute deflation composite risk score from macro indicators.

    Symmetric to _compute_stagflation_risk. Flags four conditions for a
    deflation / balance-sheet-recession regime:
      - Credit contraction       — loan_growth_yoy < 0   (banks de-levering)
      - M2 contraction           — m2_yoy_pct < 0        (money supply shrinking)
      - Velocity collapse        — velocity_of_money < 1.1 (money not circulating)
      - Breakeven compression    — breakeven_5y5y < 2.0% (market-implied inflation
                                   expectations falling below Fed target)

    Each active flag contributes 25 points to the 0-100 composite score. Pairs
    with the inflation-side stagflation composite so the regime classifier can
    see both tails simultaneously.

    Args:
        raw: Raw macro data dict (must contain loan_growth_yoy, m2_yoy_pct,
             velocity_of_money, breakeven_5y5y).

    Returns:
        Dict with score (0-100), flags, active_flags count, risk_level, label.
    """
    # Thresholds — paired with stagflation constants above
    CREDIT_CONTRACTION_THRESHOLD = 0.0      # % YoY loan growth below 0
    M2_CONTRACTION_THRESHOLD = 0.0          # % YoY M2 below 0
    VELOCITY_COLLAPSE_THRESHOLD = 1.1       # M2V below 1.1
    BREAKEVEN_COMPRESSION_THRESHOLD = 2.0   # 5y5y forward breakeven below Fed target

    loan_growth = raw.get("loan_growth_yoy", 2.0)
    m2_yoy = raw.get("m2_yoy_pct", 2.0)
    velocity = raw.get("velocity_of_money", 1.2)
    breakeven = raw.get("breakeven_5y5y", 2.3)

    flags = {
        "credit_contraction": loan_growth < CREDIT_CONTRACTION_THRESHOLD,
        "m2_contraction": m2_yoy < M2_CONTRACTION_THRESHOLD,
        "velocity_collapse": velocity < VELOCITY_COLLAPSE_THRESHOLD,
        "breakeven_compression": breakeven < BREAKEVEN_COMPRESSION_THRESHOLD,
    }

    active_flags = sum(1 for v in flags.values() if v)
    score = active_flags * 25  # 0, 25, 50, 75, or 100

    if active_flags == 0:
        risk_level = "NONE"
        label = "No deflation signals active"
    elif active_flags == 1:
        risk_level = "LOW"
        label = "Early deflation signal — 1 of 4 conditions met"
    elif active_flags == 2:
        risk_level = "MODERATE"
        label = "Disinflation pressure — 2 of 4 conditions met"
    elif active_flags == 3:
        risk_level = "HIGH"
        label = "Deflation risk elevated — 3 of 4 conditions met"
    else:
        risk_level = "CRITICAL"
        label = "Full deflation regime — all 4 conditions met"

    return {
        "score": score,
        "risk_level": risk_level,
        "label": label,
        "active_flags": active_flags,
        "flags": flags,
        "inputs": {
            "loan_growth_yoy": round(loan_growth, 2),
            "m2_yoy_pct": round(m2_yoy, 2),
            "velocity_of_money": round(velocity, 2),
            "breakeven_5y5y": round(breakeven, 2),
        },
        "thresholds": {
            "credit_contraction": CREDIT_CONTRACTION_THRESHOLD,
            "m2_contraction": M2_CONTRACTION_THRESHOLD,
            "velocity_collapse": VELOCITY_COLLAPSE_THRESHOLD,
            "breakeven_compression": BREAKEVEN_COMPRESSION_THRESHOLD,
        },
    }


def _compute_sector_impacts(raw: dict) -> dict:
    """Compute per-sector headwind/tailwind scores from macro indicators.

    Args:
        raw: Raw macro data dict.

    Returns:
        Dict mapping sector name to impact dict.
    """
    # Normalize rate environment: higher rates = more negative for rate-sensitive
    fed_funds = raw.get("fed_funds", 5.0)
    rate_factor = (fed_funds - 3.0) / 3.0  # 0 at 3%, 1 at 6%
    rate_factor = max(-1.0, min(1.0, rate_factor))

    # Oil environment: positive = oil rising
    oil_change = raw.get("oil_change_30d", 0)
    oil_factor = oil_change * 10  # Scale up
    oil_factor = max(-1.0, min(1.0, oil_factor))

    # Dollar environment: positive = strong dollar
    dxy = raw.get("dxy", 100)
    dxy_factor = (dxy - 100) / 10  # 0 at 100, 1 at 110
    dxy_factor = max(-1.0, min(1.0, dxy_factor))

    sectors = {}
    for sector, sens in SECTOR_MACRO_SENSITIVITY.items():
        # Impact = sum of (factor * sensitivity) — positive = tailwind
        impact = (
            rate_factor * sens["rate_sensitivity"]
            + oil_factor * sens["oil_sensitivity"]
            + dxy_factor * sens["dxy_sensitivity"]
        )
        # Map to -100 to +100 score
        impact_score = int(max(-100, min(100, impact * 100)))

        if impact_score > 15:
            direction = "tailwind"
        elif impact_score < -15:
            direction = "headwind"
        else:
            direction = "neutral"

        sectors[sector] = {
            "impact_score": impact_score,
            "direction": direction,
            "rate_effect": round(rate_factor * sens["rate_sensitivity"] * 100, 1),
            "oil_effect": round(oil_factor * sens["oil_sensitivity"] * 100, 1),
            "dxy_effect": round(dxy_factor * sens["dxy_sensitivity"] * 100, 1),
        }

    return sectors


def _hmm_regime(raw: dict) -> str | None:
    """Return GREEN/YELLOW/RED from the learned HMM, or None when no model.

    Builds a single-row observation from current macro inputs and runs the
    forward algorithm. Silent None on any missing piece — caller falls
    back to the rule classifier below.
    """
    try:
        vix = raw.get("vix")
        spread = raw.get("yield_spread")
        dxy = raw.get("dxy")
        if vix is None or spread is None or dxy is None:
            return None
        # VIX term-structure slope (optional; default 0 = flat)
        try:
            from tools.trading.risk.vix_term_structure import snapshot as term_snap

            ts = term_snap()
            vix_slope = ts.slope_9d_to_6m_pct if ts.slope_9d_to_6m_pct is not None else 0.0
        except Exception:
            vix_slope = 0.0
        # Realized vol proxy: 80% of current VIX (close enough when SPY RV unavailable)
        realized_vol = float(raw.get("sp500_realized_vol") or (float(vix) * 0.8))

        from tools.trading.ml.regime_hmm import classify_regime

        out = classify_regime([[float(vix), float(vix_slope), float(realized_vol), float(spread), float(dxy)]])
        if out.get("fallback"):
            return None
        return out.get("regime")
    except Exception:
        return None


def _classify_regime(
    macro_score: int,
    raw: dict,
    indicators: list[dict],
    stagflation_risk: dict | None = None,
    deflation_risk: dict | None = None,
    qeqt_phase: str | None = None,
) -> str:
    """Classify macro regime.

    Strategy:
      1. Prefer learned HMM when trained (returns GREEN/YELLOW/RED).
      2. Let the composite scores take precedence when they are decisive:
         - STAGFLATION regime when stagflation_risk >= 3 flags
         - DEFLATION regime when deflation_risk >= 2 flags (it's a 4-flag composite,
           2 is already "disinflation pressure" — mirrors stagflation threshold).
      3. Fall back to Van Metre monetary framework for richer labels
         (EXPANSION/INFLATION/DEFLATION/LIQUIDITY_TRAP) + final
         GREEN/YELLOW/RED tiebreak when HMM is unavailable.
      4. Append QE/QT context to Van Metre labels when phase is active:
         EXPANSION+EXPANDING_QE, EXPANSION+CONTRACTING_QT,
         LIQUIDITY_TRAP+EXPANDING_QE (Japan-style suppression), etc.
    """
    hmm_label = _hmm_regime(raw)
    if hmm_label in ("GREEN", "YELLOW", "RED"):
        return hmm_label

    # Composite-driven labels — preferred over point indicators when active
    sf_flags = (stagflation_risk or {}).get("active_flags", 0)
    df_flags = (deflation_risk or {}).get("active_flags", 0)
    if sf_flags >= 3:
        base = "STAGFLATION"
    elif df_flags >= 2:
        base = "DEFLATION"
    else:
        loan_growth = raw.get("loan_growth_yoy", 2.0)
        velocity = raw.get("velocity_of_money", 1.2)
        multiplier = raw.get("money_multiplier", 3.5)
        rrp = raw.get("rrp_balance_b", 200)
        fed_funds = raw.get("fed_funds", 5.0)

        # LIQUIDITY_TRAP: low multiplier + low velocity + high RRP
        if multiplier < 3.0 and velocity < 1.1 and rrp > 500:
            base = "LIQUIDITY_TRAP"
        # DEFLATION (legacy point trigger, kept as belt-and-suspenders)
        elif loan_growth < 0 and velocity < 1.2:
            base = "DEFLATION"
        else:
            # INFLATION: high rates + strong lending + rising commodities
            oil_change = raw.get("oil_change_30d", 0)
            if fed_funds > 5.0 and loan_growth > 3.0 and oil_change > 0.05:
                base = "INFLATION"
            # EXPANSION: healthy lending + normal velocity + supportive macro
            elif macro_score >= 60 and loan_growth > 2.0 and velocity > 1.2:
                base = "EXPANSION"
            # Fallback to simple regime
            elif macro_score >= 60:
                base = "GREEN"
            elif macro_score >= 30:
                base = "YELLOW"
            else:
                base = "RED"

    # Append QE/QT suffix to Van Metre labels when Fed balance sheet is moving
    _VAN_METRE = {"EXPANSION", "INFLATION", "STAGFLATION", "DEFLATION", "LIQUIDITY_TRAP"}
    if base in _VAN_METRE and qeqt_phase and qeqt_phase != "NEUTRAL":
        suffix = "EXPANDING_QE" if qeqt_phase == "EXPANDING" else "CONTRACTING_QT"
        return f"{base}+{suffix}"
    return base


def _build_summary(
    regime: str,
    indicators: list[dict],
    geo: dict,
    supply: dict,
    stagflation: dict | None = None,
    deflation: dict | None = None,
    treasury_pressure: dict | None = None,
    fallen_angel_risk: str | None = None,
) -> str:
    """Build a prose summary of the macro environment."""
    red_count = sum(1 for i in indicators if i["signal"] == "RED")
    green_count = sum(1 for i in indicators if i["signal"] == "GREEN")

    regime_descriptions = {
        "EXPANSION": "Economy expanding — strong lending, healthy velocity.",
        "INFLATION": "Overheating — high rates, rising commodities, tightening ahead.",
        "STAGFLATION": "Stagflation — stagnant growth with persistent inflation; real assets only.",
        "DEFLATION": "Credit contraction underway — deflationary pressure building.",
        "LIQUIDITY_TRAP": "Liquidity trap — reserves trapped, money multiplier collapsed.",
        "GREEN": "Macro environment is supportive for equities.",
        "YELLOW": "Macro environment shows mixed signals.",
        "RED": "Macro environment presents significant headwinds.",
    }
    parts = [regime_descriptions.get(regime.split("+")[0], "Macro environment uncertain.")]

    parts.append(f"{green_count} indicators positive, {red_count} negative.")

    if geo["score"] > 30:
        parts.append(f"Geopolitical risk elevated ({geo['risk_level']}).")
    if supply["score"] > 40:
        parts.append(f"Supply chain stress detected ({supply['risk_level']}).")
    if stagflation and stagflation["active_flags"] >= 2:
        parts.append(
            f"Stagflation risk {stagflation['risk_level'].lower()} "
            f"({stagflation['active_flags']}/4 flags: {stagflation['label']})."
        )
    if deflation and deflation["active_flags"] >= 2:
        parts.append(
            f"Deflation risk {deflation['risk_level'].lower()} "
            f"({deflation['active_flags']}/4 flags: {deflation['label']})."
        )
    if treasury_pressure and treasury_pressure["level"] == "ELEVATED":
        parts.append(
            "Treasury supply pressure ELEVATED — deficit expanding, yield spread rising, "
            "and net liquidity falling simultaneously."
        )
    elif treasury_pressure and treasury_pressure["level"] == "MODERATE":
        parts.append(
            f"Treasury supply pressure moderate ({treasury_pressure['active_flags']}/3 flags: "
            f"{treasury_pressure['label']})."
        )
    if fallen_angel_risk == "ELEVATED":
        parts.append(
            "Fallen angel risk ELEVATED — credit contracting with secondary stress; "
            "watch for IG-to-HY downgrades as crisis precursor."
        )

    return " ".join(parts)


def fetch_macro_context(headlines: list[str] | None = None) -> dict:
    """Fetch global macro indicators and compute regime score.

    Tries live data (yfinance + FRED) first, falls back to deterministic
    sample data when dependencies or network are unavailable.

    Args:
        headlines: Optional news headlines for geopolitical risk scoring.

    Returns:
        Complete macro context dict with regime, scores, indicators,
        supply chain risk, geopolitical risk, and sector impacts.
    """
    # Try live data, fall back to sample
    raw = _fetch_live_macro()
    data_source = "live"
    if raw is None:
        raw = _generate_sample_macro()
        data_source = "sample"
        get_logger(__name__).warning(
            "MACRO DATA FALLBACK: using sample data — live FRED/yfinance unavailable. "
            "Trading decisions may be based on synthetic indicators."
        )

    # Composite risks (computed before indicator scoring)
    stagflation_risk = _compute_stagflation_risk(raw)
    deflation_risk = _compute_deflation_risk(raw)
    treasury_supply_pressure = _compute_treasury_supply_pressure(raw)

    # Score each indicator — original 8 + Van Metre 6 + stagflation 1 + deflation 1 + breakevens 2 + treasury 1 + credit impulse 1 = 20 total
    indicator_inputs = {
        "vix": raw.get("vix", 20),
        "yield_spread": raw.get("yield_spread", 0.5),
        "fed_funds": raw.get("fed_funds", 5.0),
        "dxy": raw.get("dxy", 100),
        "oil_change_30d": raw.get("oil_change_30d", 0),
        "sp500_trend": raw.get("sp500", 5200) - raw.get("sp500_sma50", 5100),
        "nasdaq_trend": raw.get("nasdaq", 16000) - raw.get("nasdaq_sma50", 15800),
        "gold_change_30d": raw.get("gold_change_30d", 0),
        # Van Metre monetary system indicators
        "money_multiplier": raw.get("money_multiplier", 3.5),
        "velocity_of_money": raw.get("velocity_of_money", 1.2),
        "loan_growth_yoy": raw.get("loan_growth_yoy", 2.0),
        "bank_cash_assets": raw.get("bank_cash_assets_b", 3500),
        "rrp_balance": raw.get("rrp_balance_b", 200),
        "gdx_trend": raw.get("gdx", 35) - raw.get("gdx_sma50", 34),
        # Stagflation composite (score maps directly to inverted indicator score)
        "stagflation_risk_score": stagflation_risk["score"],
        # Deflation composite (symmetric to stagflation)
        "deflation_risk_score": deflation_risk["score"],
        # Market-implied inflation expectations
        "breakeven_5y5y": raw.get("breakeven_5y5y", 2.3),
        "breakeven_10y": raw.get("breakeven_10y", 2.3),
        # Treasury supply pressure composite (score 0-3)
        "treasury_supply_pressure": float(treasury_supply_pressure["score"]),
        # Credit impulse: 13-week TOTCI delta as % of nominal GDP (slow weight, leads GDP ~12mo)
        "long_credit_impulse": raw.get("long_credit_impulse", 0.0),
    }

    indicators = []
    # Weights: original 8 → 50%, Van Metre 6 → 28%, stagflation 4%, deflation 4%, breakevens 2%, treasury 4%, credit impulse 8%
    # (velocity_of_money -1%, loan_growth_yoy -2%, stagflation_risk_score -2% to fund credit impulse 8%.)
    indicator_weights = {
        # Original indicators (50% total)
        "vix": 0.10,
        "yield_spread": 0.09,
        "oil_change_30d": 0.07,
        "dxy": 0.05,
        "sp500_trend": 0.09,
        "fed_funds": 0.04,
        "nasdaq_trend": 0.04,
        "gold_change_30d": 0.02,
        # Van Metre monetary system (28% total)
        "money_multiplier": 0.06,
        "velocity_of_money": 0.06,  # trimmed 1% to fund credit impulse
        "loan_growth_yoy": 0.06,  # "only real engine of inflation" (trimmed 2% to fund credit impulse)
        "bank_cash_assets": 0.04,
        "rrp_balance": 0.02,
        "gdx_trend": 0.04,  # crash leading indicator
        # Stagflation composite (4%; trimmed 2% to fund credit impulse)
        "stagflation_risk_score": 0.04,
        # Deflation composite (4%)
        "deflation_risk_score": 0.04,
        # Market-implied inflation expectations (2% total)
        "breakeven_5y5y": 0.01,
        "breakeven_10y": 0.01,
        # Treasury supply pressure composite (4%)
        "treasury_supply_pressure": 0.04,
        # Credit impulse: 13-week TOTCI delta / GDP — slow weight, leads GDP ~12 months (8%)
        "long_credit_impulse": 0.08,
    }

    for name, value in indicator_inputs.items():
        scored = _score_indicator(name, value)
        indicators.append(
            {
                "name": name,
                "value": round(value, 4),
                "score": scored["score"],
                "signal": scored["signal"],
                "label": scored["label"],
                "weight": indicator_weights.get(name, 0.05),
            }
        )

    # Wire ELEVATED treasury supply pressure into yield spread — extra headwind on long-end
    if treasury_supply_pressure["level"] == "ELEVATED":
        for ind in indicators:
            if ind["name"] == "yield_spread":
                ind["score"] = max(0, ind["score"] - 20)
                ind["label"] = ind["label"] + " [treasury supply ELEVATED]"
                if ind["score"] < 30 and ind["signal"] != "RED":
                    ind["signal"] = "RED"
                break

    # Weighted macro score (0-100)
    macro_score = sum(i["score"] * i["weight"] for i in indicators)
    macro_score = int(max(0, min(100, macro_score)))

    # Credit impulse composite overlay — slow-signal (13-week lag) adjustment on top of weighted score
    # CONTRACTING reduces composite by up to 8 pts; ACCELERATING adds up to 5 pts
    _credit_label = raw.get("credit_impulse_label", "NEUTRAL")
    _long_ci = raw.get("long_credit_impulse", 0.0)
    if _credit_label == "CONTRACTING":
        # Linear scale: full -8 pts at long_ci <= -0.25
        _ci_penalty = min(8, round(abs(min(_long_ci, 0.0)) / 0.25 * 8))
        macro_score = max(0, macro_score - _ci_penalty)
    elif _credit_label == "ACCELERATING":
        # Linear scale: full +5 pts at long_ci >= 0.40
        _ci_boost = min(5, round(max(0.0, _long_ci - 0.20) / 0.20 * 5))
        macro_score = min(100, macro_score + _ci_boost)

    # Fallen angel risk — crisis precursor when credit contracts + secondary stress confirms
    _fallen_angel_risk = "LOW"
    if _credit_label == "CONTRACTING":
        _df_flags = deflation_risk.get("active_flags", 0)
        if _df_flags >= 2 or raw.get("loan_growth_yoy", 2.0) < 0.0:
            _fallen_angel_risk = "ELEVATED"
        else:
            _fallen_angel_risk = "MODERATE"

    # QE/QT phase from Fed balance sheet 4-week rate of change
    _qeqt_phase, _ = _classify_qeqt(raw.get("fed_bs_4w_roc_b", 0.0))

    # Granular regime classification (Van Metre-inspired + composite-aware + QE/QT context)
    regime = _classify_regime(macro_score, raw, indicators, stagflation_risk, deflation_risk, _qeqt_phase)

    # Bond ETF relative momentum + regime
    _bond_snapshot = get_bond_etf_snapshot()
    _bond_regime_detail = classify_bond_etf_regime(_bond_snapshot)
    bond_etf_regime = {
        **_bond_regime_detail,
        "etf_data": {t: _bond_snapshot[t] for t in ["TLT", "HYG", "LQD", "AGG", "SPY"] if t in _bond_snapshot},
        "data_source": _bond_snapshot.get("data_source", "sample"),
    }

    # Geopolitical risk
    geo_risk = _compute_geopolitical_risk(headlines)

    # Supply chain risk
    supply_risk = _compute_supply_chain_risk(raw)

    # Sector impacts
    sector_impacts = _compute_sector_impacts(raw)

    summary = _build_summary(regime, indicators, geo_risk, supply_risk, stagflation_risk, deflation_risk, treasury_supply_pressure, _fallen_angel_risk)

    return {
        "macro_score": macro_score,
        "regime": regime,
        "qeqt_phase": _qeqt_phase,
        "data_source": data_source,
        "indicators": indicators,
        "raw_values": {
            "vix": raw.get("vix"),
            "yield_spread": raw.get("yield_spread"),
            "fed_funds": raw.get("fed_funds"),
            "dxy": raw.get("dxy"),
            "oil": raw.get("oil"),
            "oil_change_30d": raw.get("oil_change_30d"),
            "gold": raw.get("gold"),
            "gold_change_30d": raw.get("gold_change_30d"),
            "sp500": raw.get("sp500"),
            "sp500_sma50": raw.get("sp500_sma50"),
            "nasdaq": raw.get("nasdaq"),
            "nasdaq_sma50": raw.get("nasdaq_sma50"),
            # Van Metre monetary system
            "money_multiplier": raw.get("money_multiplier"),
            "velocity_of_money": raw.get("velocity_of_money"),
            "loan_growth_yoy": raw.get("loan_growth_yoy"),
            "bank_cash_assets_b": raw.get("bank_cash_assets_b"),
            "rrp_balance_b": raw.get("rrp_balance_b"),
            "gdx": raw.get("gdx"),
            "gdx_sma50": raw.get("gdx_sma50"),
            # Stagflation composite inputs
            "gdp_growth_q": raw.get("gdp_growth_q"),
            "cpi_yoy": raw.get("cpi_yoy"),
            "unemp_trend_months": raw.get("unemp_trend_months"),
            "energy_change_30d": raw.get("energy_change_30d"),
            # Deflation composite inputs + market-implied inflation expectations
            "m2_yoy_pct": raw.get("m2_yoy_pct"),
            "breakeven_5y5y": raw.get("breakeven_5y5y"),
            "breakeven_10y": raw.get("breakeven_10y"),
            # Treasury supply pressure inputs
            "fiscal_deficit_expanding": raw.get("fiscal_deficit_expanding"),
            "t10y2y_4w_roc": raw.get("t10y2y_4w_roc"),
            "net_liquidity_4w_roc_b": raw.get("net_liquidity_4w_roc_b"),
            # Credit impulse inputs (TOTCI delta as % of nominal GDP)
            "short_credit_impulse": raw.get("short_credit_impulse"),
            "long_credit_impulse": raw.get("long_credit_impulse"),
            "credit_impulse_label": raw.get("credit_impulse_label"),
        },
        "supply_chain_risk": supply_risk,
        "geopolitical_risk": geo_risk,
        "stagflation_risk": stagflation_risk,
        "deflation_risk": deflation_risk,
        "treasury_supply_pressure": treasury_supply_pressure,
        "credit_impulse": {
            "short": raw.get("short_credit_impulse", 0.0),
            "long": raw.get("long_credit_impulse", 0.0),
            "label": raw.get("credit_impulse_label", "NEUTRAL"),
        },
        "fallen_angel_risk": _fallen_angel_risk,
        "bond_etf_regime": bond_etf_regime,
        "sector_impacts": sector_impacts,
        "summary": summary,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def get_sector_for_ticker(ticker: str) -> str:
    """Look up sector for a ticker. Returns 'Technology' as default."""
    return TICKER_SECTOR.get(ticker.upper(), "Technology")


# ---------------------------------------------------------------------------
# Extended Macro Indicators for Systemic Risk & Opportunity Radar (SROR)
# ---------------------------------------------------------------------------

# FRED series IDs for extended indicators
EXTENDED_FRED_SERIES = {
    "credit_spread_hy": "BAMLH0A0HYM2",  # ICE BofA HY OAS
    "ig_oas": "BAMLC0A0CM",              # ICE BofA IG OAS (bps)
    "bbb_oas": "BAMLC0A4CBBB",          # ICE BofA BBB OAS (bps)
    "sofr": "SOFR",                      # SOFR rate (for SOFR-Fed Funds spread)
    "lei": "USSLIND",                      # Conference Board LEI
    "ism_pmi": "MANEMP",                   # ISM Manufacturing Employment (PMI proxy)
    "building_permits": "PERMIT",           # Building Permits
    "initial_claims": "IC4WSA",            # Initial Claims 4-week MA
    "sahm_rule": "SAHMREALTIME",           # Sahm Rule Recession Indicator
    "consumer_confidence": "UMCSENT",      # U. Michigan Consumer Sentiment
    "fed_balance_sheet": "WALCL",          # Fed Total Assets
    "tga_balance": "WTREGEN",             # Treasury General Account
    "bank_reserves": "WRESBAL",           # Reserve Balances at Fed
    "m2_money_supply": "M2SL",            # M2 Money Stock
    "federal_deficit": "MTSDS133FMS",     # Monthly Treasury Statement Deficit/Surplus
}


def _classify_qeqt(roc_4w_b: float) -> tuple[str, str]:
    """Classify QE/QT phase and magnitude from 4-week balance sheet RoC (billions/week)."""
    if roc_4w_b > 50:
        phase = "EXPANDING"
    elif roc_4w_b < -25:
        phase = "CONTRACTING"
    else:
        phase = "NEUTRAL"

    abs_roc = abs(roc_4w_b)
    if abs_roc > 100:
        magnitude = "AGGRESSIVE"
    elif abs_roc >= 25:
        magnitude = "MODERATE"
    else:
        magnitude = "SLOW"

    return phase, magnitude


def _classify_fiscal_deficit(deficit_pct_gdp: float, deficit_pct_gdp_yago: float) -> str:
    """Classify 12-month rolling deficit trajectory vs prior year.

    delta > +0.5 ppt of GDP → EXPANDING_DEFICIT (worsening)
    delta < -0.5 ppt       → IMPROVING
    otherwise              → STABLE
    """
    delta = deficit_pct_gdp - deficit_pct_gdp_yago
    if delta > 0.5:
        return "EXPANDING_DEFICIT"
    if delta < -0.5:
        return "IMPROVING"
    return "STABLE"


def _generate_sample_extended() -> dict:
    """Generate deterministic sample data for SROR extended indicators."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seed = int(hashlib.sha256(("sror_" + today).encode("utf-8")).hexdigest()[:8], 16)

    def _p(s, lo, hi):
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        return s, lo + (s % 1000) / 1000.0 * (hi - lo)

    seed, credit_spread_hy = _p(seed, 300, 800)       # bps
    seed, ig_oas = _p(seed, 60, 250)                   # bps
    seed, bbb_oas = _p(seed, 100, 350)                 # bps
    seed, sofr_rate = _p(seed, 4.0, 6.0)               # %
    seed, lei = _p(seed, 95.0, 105.0)                  # index
    seed, lei_prev = _p(seed, 95.0, 105.0)
    seed, ism_pmi = _p(seed, 42.0, 58.0)               # index
    seed, building_permits = _p(seed, 1200, 1800)       # thousands
    seed, building_permits_yago = _p(seed, 1200, 1800)
    seed, initial_claims = _p(seed, 180, 320)           # thousands
    seed, sahm_rule = _p(seed, -0.2, 1.0)              # pct
    seed, consumer_confidence = _p(seed, 55.0, 85.0)   # index
    seed, consumer_conf_prev1 = _p(seed, 55.0, 85.0)
    seed, consumer_conf_prev2 = _p(seed, 55.0, 85.0)
    seed, fed_bs = _p(seed, 6500, 9000)                # billions
    seed, fed_bs_prev = _p(seed, 6500, 9000)
    seed, fed_bs_4w_ago = _p(seed, 6500, 9000)         # billions, 4 weeks ago
    seed, fed_bs_13w_ago = _p(seed, 6500, 9000)        # billions, 13 weeks ago
    seed, tga = _p(seed, 200, 900)                     # billions
    seed, bank_reserves = _p(seed, 2500, 4000)         # billions
    seed, m2 = _p(seed, 20000, 22000)                  # billions
    seed, m2_yago = _p(seed, 20000, 22000)
    seed, skew = _p(seed, 110, 160)                    # CBOE SKEW index
    seed, put_call = _p(seed, 0.5, 1.5)               # ratio
    seed, bbb_oas_prev8w = _p(seed, 100, 350)          # bps, 8 weeks ago
    seed, deficit_12m_b = _p(seed, 1400, 2200)         # 12-month rolling deficit, billions
    seed, deficit_12m_b_yago = _p(seed, 1200, 2000)    # same metric 12 months prior
    seed, gdp_nominal_b = _p(seed, 26000, 29000)       # nominal GDP, billions

    m2_yoy = ((m2 - m2_yago) / m2_yago * 100) if m2_yago else 0
    permits_yoy = ((building_permits - building_permits_yago) / building_permits_yago * 100) if building_permits_yago else 0
    lei_change = lei - lei_prev
    fed_bs_change = ((fed_bs - fed_bs_prev) / fed_bs_prev * 100) if fed_bs_prev else 0
    net_liquidity = fed_bs - tga - 200  # RRP placeholder from base macro
    fed_bs_4w_roc_b = (fed_bs - fed_bs_4w_ago) / 4.0
    fed_bs_13w_roc_b = (fed_bs - fed_bs_13w_ago) / 13.0
    _qeqt_phase, _qeqt_magnitude = _classify_qeqt(fed_bs_4w_roc_b)
    cc_declining = (consumer_confidence < consumer_conf_prev1 < consumer_conf_prev2)
    # SOFR spread = SOFR minus Fed Funds effective rate (use fed_funds stub ~5.33 as proxy)
    sofr_ff_spread_bps = round((sofr_rate - 5.33) * 100, 1)
    # BBB OAS 8-week slope in bps/week (positive = widening trend)
    bbb_oas_slope_8w_bps = round((bbb_oas - bbb_oas_prev8w) / 8.0, 2)
    deficit_pct_gdp = round(deficit_12m_b / gdp_nominal_b * 100, 2)
    deficit_pct_gdp_yago = round(deficit_12m_b_yago / gdp_nominal_b * 100, 2)
    _fiscal_trajectory = _classify_fiscal_deficit(deficit_pct_gdp, deficit_pct_gdp_yago)

    return {
        "credit_spread_hy_bps": round(credit_spread_hy, 0),
        "ig_oas_bps": round(ig_oas, 1),
        "bbb_oas_bps": round(bbb_oas, 1),
        "bbb_oas_slope_8w_bps": bbb_oas_slope_8w_bps,
        "sofr_ff_spread_bps": sofr_ff_spread_bps,
        "lei_index": round(lei, 2),
        "lei_monthly_change": round(lei_change, 2),
        "ism_pmi": round(ism_pmi, 1),
        "building_permits_k": round(building_permits, 0),
        "building_permits_yoy_pct": round(permits_yoy, 1),
        "initial_claims_4wma_k": round(initial_claims, 0),
        "sahm_rule": round(sahm_rule, 2),
        "consumer_confidence": round(consumer_confidence, 1),
        "consumer_confidence_declining_3m": cc_declining,
        "fed_balance_sheet_b": round(fed_bs, 0),
        "fed_bs_change_pct": round(fed_bs_change, 2),
        "fed_bs_4w_roc_b": round(fed_bs_4w_roc_b, 1),
        "fed_bs_13w_roc_b": round(fed_bs_13w_roc_b, 1),
        "qeqt_phase": _qeqt_phase,
        "qeqt_magnitude": _qeqt_magnitude,
        "tga_balance_b": round(tga, 0),
        "bank_reserves_b": round(bank_reserves, 0),
        "m2_supply_b": round(m2, 0),
        "m2_yoy_pct": round(m2_yoy, 2),
        "net_liquidity_b": round(net_liquidity, 0),
        "skew_index": round(skew, 1),
        "put_call_ratio": round(put_call, 2),
        "deficit_12m_b": round(deficit_12m_b, 0),
        "deficit_pct_gdp": deficit_pct_gdp,
        "fiscal_deficit_trajectory": _fiscal_trajectory,
    }


def _fetch_live_extended() -> dict | None:
    """Fetch extended macro data from FRED + yfinance for SROR.

    Returns None if dependencies unavailable.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        from fredapi import Fred
        import os
        fred_key = os.environ.get("FRED_API_KEY", "")
        fred = Fred(api_key=fred_key) if fred_key else None
    except (ImportError, Exception):
        fred = None

    data: dict = {}

    # yfinance indicators
    try:
        skew_data = yf.Ticker("^SKEW").history(period="5d")
        data["skew_index"] = round(float(skew_data["Close"].iloc[-1]), 1) if len(skew_data) > 0 else 130.0
    except Exception:
        data["skew_index"] = 130.0

    try:
        # Use VIX put/call as proxy (CBOE doesn't have direct ticker)
        # Fallback to sample
        data["put_call_ratio"] = 0.85
    except Exception:
        data["put_call_ratio"] = 0.85

    if not fred:
        return None

    # FRED extended series
    try:
        hy = fred.get_series("BAMLH0A0HYM2", observation_start="2024-01-01")
        data["credit_spread_hy_bps"] = round(float(hy.dropna().iloc[-1]) * 100, 0)
    except Exception:
        data["credit_spread_hy_bps"] = 450.0

    try:
        ig = fred.get_series("BAMLC0A0CM", observation_start="2024-01-01")
        data["ig_oas_bps"] = round(float(ig.dropna().iloc[-1]) * 100, 1)
    except Exception:
        data["ig_oas_bps"] = 110.0

    try:
        bbb = fred.get_series("BAMLC0A4CBBB", observation_start="2024-01-01")
        bbb_clean = bbb.dropna()
        data["bbb_oas_bps"] = round(float(bbb_clean.iloc[-1]) * 100, 1)
        if len(bbb_clean) >= 40:
            # ~8 weeks of business days (8 * 5 = 40 observations)
            val_now = float(bbb_clean.iloc[-1]) * 100
            val_8w_ago = float(bbb_clean.iloc[-40]) * 100
            data["bbb_oas_slope_8w_bps"] = round((val_now - val_8w_ago) / 8.0, 2)
        else:
            data["bbb_oas_slope_8w_bps"] = 0.0
    except Exception:
        data["bbb_oas_bps"] = 175.0
        data["bbb_oas_slope_8w_bps"] = 0.0

    try:
        sofr_s = fred.get_series("SOFR", observation_start="2024-01-01")
        fedfunds_s = fred.get_series("FEDFUNDS", observation_start="2024-01-01")
        sofr_val = float(sofr_s.dropna().iloc[-1])
        ff_val = float(fedfunds_s.dropna().iloc[-1])
        data["sofr_ff_spread_bps"] = round((sofr_val - ff_val) * 100, 1)
    except Exception:
        data["sofr_ff_spread_bps"] = 5.0

    try:
        lei = fred.get_series("USSLIND", observation_start="2023-01-01")
        lei_clean = lei.dropna()
        data["lei_index"] = round(float(lei_clean.iloc[-1]), 2)
        if len(lei_clean) >= 2:
            data["lei_monthly_change"] = round(float(lei_clean.iloc[-1]) - float(lei_clean.iloc[-2]), 2)
        else:
            data["lei_monthly_change"] = 0.0
    except Exception:
        data["lei_index"] = 100.0
        data["lei_monthly_change"] = 0.0

    try:
        ism = fred.get_series("MANEMP", observation_start="2024-01-01")
        data["ism_pmi"] = round(float(ism.dropna().iloc[-1]), 1)
    except Exception:
        data["ism_pmi"] = 50.0

    try:
        permits = fred.get_series("PERMIT", observation_start="2023-01-01")
        permits_clean = permits.dropna()
        data["building_permits_k"] = round(float(permits_clean.iloc[-1]), 0)
        if len(permits_clean) >= 13:
            yago = float(permits_clean.iloc[-13])
            data["building_permits_yoy_pct"] = round(
                (float(permits_clean.iloc[-1]) - yago) / yago * 100, 1
            ) if yago else 0.0
        else:
            data["building_permits_yoy_pct"] = 0.0
    except Exception:
        data["building_permits_k"] = 1500.0
        data["building_permits_yoy_pct"] = 0.0

    try:
        claims = fred.get_series("IC4WSA", observation_start="2024-01-01")
        data["initial_claims_4wma_k"] = round(float(claims.dropna().iloc[-1]), 0)
    except Exception:
        data["initial_claims_4wma_k"] = 220.0

    try:
        sahm = fred.get_series("SAHMREALTIME", observation_start="2024-01-01")
        data["sahm_rule"] = round(float(sahm.dropna().iloc[-1]), 2)
    except Exception:
        data["sahm_rule"] = 0.1

    try:
        umcs = fred.get_series("UMCSENT", observation_start="2023-01-01")
        umcs_clean = umcs.dropna()
        data["consumer_confidence"] = round(float(umcs_clean.iloc[-1]), 1)
        if len(umcs_clean) >= 3:
            vals = [float(v) for v in umcs_clean.tail(3)]
            data["consumer_confidence_declining_3m"] = (vals[2] < vals[1] < vals[0])
        else:
            data["consumer_confidence_declining_3m"] = False
    except Exception:
        data["consumer_confidence"] = 70.0
        data["consumer_confidence_declining_3m"] = False

    try:
        walcl = fred.get_series("WALCL", observation_start="2023-01-01")
        walcl_clean = walcl.dropna()
        bs_val = float(walcl_clean.iloc[-1]) / 1000  # millions to billions
        data["fed_balance_sheet_b"] = round(bs_val, 0)
        if len(walcl_clean) >= 5:
            prev = float(walcl_clean.iloc[-5]) / 1000
            data["fed_bs_change_pct"] = round((bs_val - prev) / prev * 100, 2) if prev else 0.0
        else:
            data["fed_bs_change_pct"] = 0.0
        # 4-week RoC: WALCL is weekly; 4 observations back = 4 weeks ago
        if len(walcl_clean) >= 5:
            bs_4w_ago = float(walcl_clean.iloc[-5]) / 1000
            roc_4w = (bs_val - bs_4w_ago) / 4.0
        else:
            roc_4w = 0.0
        # 13-week RoC: 13 observations back = 13 weeks ago
        if len(walcl_clean) >= 14:
            bs_13w_ago = float(walcl_clean.iloc[-14]) / 1000
            roc_13w = (bs_val - bs_13w_ago) / 13.0
        else:
            roc_13w = roc_4w
        data["fed_bs_4w_roc_b"] = round(roc_4w, 1)
        data["fed_bs_13w_roc_b"] = round(roc_13w, 1)
        qeqt_phase, qeqt_magnitude = _classify_qeqt(roc_4w)
        data["qeqt_phase"] = qeqt_phase
        data["qeqt_magnitude"] = qeqt_magnitude
    except Exception:
        data["fed_balance_sheet_b"] = 7500.0
        data["fed_bs_change_pct"] = 0.0
        data["fed_bs_4w_roc_b"] = 0.0
        data["fed_bs_13w_roc_b"] = 0.0
        data["qeqt_phase"] = "NEUTRAL"
        data["qeqt_magnitude"] = "SLOW"

    try:
        tga = fred.get_series("WTREGEN", observation_start="2024-01-01")
        data["tga_balance_b"] = round(float(tga.dropna().iloc[-1]) / 1000, 0)
    except Exception:
        data["tga_balance_b"] = 500.0

    try:
        res = fred.get_series("WRESBAL", observation_start="2024-01-01")
        data["bank_reserves_b"] = round(float(res.dropna().iloc[-1]) / 1000, 0)
    except Exception:
        data["bank_reserves_b"] = 3200.0

    try:
        m2 = fred.get_series("M2SL", observation_start="2023-01-01")
        m2_clean = m2.dropna()
        data["m2_supply_b"] = round(float(m2_clean.iloc[-1]), 0)
        if len(m2_clean) >= 13:
            yago = float(m2_clean.iloc[-13])
            data["m2_yoy_pct"] = round(
                (float(m2_clean.iloc[-1]) - yago) / yago * 100, 2
            ) if yago else 0.0
        else:
            data["m2_yoy_pct"] = 0.0
    except Exception:
        data["m2_supply_b"] = 21000.0
        data["m2_yoy_pct"] = 0.0

    # Net liquidity = Fed BS - TGA - RRP
    rrp = 200  # will be overridden if base macro context available
    data["net_liquidity_b"] = round(
        data.get("fed_balance_sheet_b", 7500) -
        data.get("tga_balance_b", 500) -
        rrp, 0
    )

    # Federal deficit/surplus trajectory (MTSDS133FMS — Monthly Treasury Statement)
    try:
        mts = fred.get_series("MTSDS133FMS", observation_start="2022-01-01")
        mts_clean = mts.dropna()
        if len(mts_clean) >= 12:
            # 12-month rolling sum; MTSDS133FMS is negative for deficit
            deficit_12m_mm = float(mts_clean.iloc[-12:].sum())
            deficit_12m_b = -deficit_12m_mm / 1000.0  # positive = deficit, in billions
            data["deficit_12m_b"] = round(deficit_12m_b, 0)
            # Nominal GDP for % of GDP calculation
            try:
                gdp_s = fred.get_series("GDP", observation_start="2023-01-01")
                gdp_nominal_b = float(gdp_s.dropna().iloc[-1])  # already in billions
            except Exception:
                gdp_nominal_b = 28000.0
            deficit_pct_gdp = round(deficit_12m_b / gdp_nominal_b * 100, 2)
            data["deficit_pct_gdp"] = deficit_pct_gdp
            # Year-ago window requires 24 months of data
            if len(mts_clean) >= 24:
                deficit_12m_b_yago = -float(mts_clean.iloc[-24:-12].sum()) / 1000.0
                deficit_pct_gdp_yago = round(deficit_12m_b_yago / gdp_nominal_b * 100, 2)
            else:
                deficit_pct_gdp_yago = deficit_pct_gdp
            data["fiscal_deficit_trajectory"] = _classify_fiscal_deficit(
                deficit_pct_gdp, deficit_pct_gdp_yago
            )
        else:
            data["deficit_12m_b"] = 1800.0
            data["deficit_pct_gdp"] = 6.4
            data["fiscal_deficit_trajectory"] = "STABLE"
    except Exception:
        data["deficit_12m_b"] = 1800.0
        data["deficit_pct_gdp"] = 6.4
        data["fiscal_deficit_trajectory"] = "STABLE"

    return data


def fetch_extended_macro() -> dict:
    """Fetch extended macro indicators for SROR.

    Tries live FRED+yfinance first, falls back to deterministic sample.
    Returns a dict with all extended indicator values.
    """
    live = _fetch_live_extended()
    if live and len(live) >= 10:
        data_source = "live"
        data = live
    else:
        data_source = "sample"
        data = _generate_sample_extended()

    # Fill any missing keys from sample
    sample = _generate_sample_extended()
    for k, v in sample.items():
        if k not in data:
            data[k] = v

    data["data_source"] = data_source
    data["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return data


def score_extended_indicator(name: str, value) -> dict:
    """Score an extended macro indicator for SROR. Returns signal/score/label."""

    if name == "credit_spread_hy_bps":
        v = float(value)
        if v < 350:
            return {"signal": "GREEN", "score": 85, "label": "Tight credit spreads (risk-on)"}
        elif v < 500:
            return {"signal": "YELLOW", "score": 55, "label": "Moderate credit spreads"}
        elif v < 700:
            return {"signal": "RED", "score": 25, "label": "Wide credit spreads (stress)"}
        else:
            return {"signal": "RED", "score": 5, "label": "Extreme credit spreads (crisis-level)"}

    elif name == "ig_oas_bps":
        v = float(value)
        if v < 100:
            return {"signal": "GREEN", "score": 85, "label": "IG spreads tight (investment-grade benign)"}
        elif v <= 180:
            return {"signal": "YELLOW", "score": 50, "label": "IG spreads widening (credit caution)"}
        else:
            return {"signal": "RED", "score": 20, "label": "IG spreads wide (credit stress)"}

    elif name == "bbb_oas_bps":
        v = float(value)
        if v < 150:
            return {"signal": "GREEN", "score": 85, "label": "BBB spreads tight (fallen-angel risk low)"}
        elif v <= 250:
            return {"signal": "YELLOW", "score": 50, "label": "BBB spreads elevated (fallen-angel watch)"}
        else:
            return {"signal": "RED", "score": 20, "label": "BBB spreads wide (fallen-angel risk high)"}

    elif name == "sofr_ff_spread_bps":
        v = float(value)
        if v < 20:
            return {"signal": "GREEN", "score": 85, "label": "SOFR-FF spread tight (funding stress absent)"}
        elif v <= 50:
            return {"signal": "YELLOW", "score": 50, "label": "SOFR-FF spread elevated (funding pressure)"}
        else:
            return {"signal": "RED", "score": 15, "label": "SOFR-FF spread wide (repo/funding stress)"}

    elif name == "lei_monthly_change":
        v = float(value)
        if v > 0.2:
            return {"signal": "GREEN", "score": 85, "label": "LEI rising (expansion)"}
        elif v > -0.2:
            return {"signal": "YELLOW", "score": 55, "label": "LEI flat"}
        elif v > -0.5:
            return {"signal": "RED", "score": 30, "label": "LEI declining (slowdown)"}
        else:
            return {"signal": "RED", "score": 10, "label": "LEI falling sharply (recession risk)"}

    elif name == "ism_pmi":
        v = float(value)
        if v > 55:
            return {"signal": "GREEN", "score": 85, "label": "Manufacturing expanding strongly"}
        elif v > 50:
            return {"signal": "GREEN", "score": 65, "label": "Manufacturing expanding"}
        elif v > 47:
            return {"signal": "YELLOW", "score": 40, "label": "Manufacturing contracting mildly"}
        else:
            return {"signal": "RED", "score": 15, "label": "Manufacturing deep contraction"}

    elif name == "building_permits_yoy_pct":
        v = float(value)
        if v > 5:
            return {"signal": "GREEN", "score": 80, "label": "Housing permits growing"}
        elif v > -5:
            return {"signal": "YELLOW", "score": 55, "label": "Housing permits flat"}
        elif v > -15:
            return {"signal": "RED", "score": 30, "label": "Housing permits declining"}
        else:
            return {"signal": "RED", "score": 10, "label": "Housing permits collapsing (recession lead)"}

    elif name == "initial_claims_4wma_k":
        v = float(value)
        if v < 220:
            return {"signal": "GREEN", "score": 85, "label": "Low jobless claims (tight labor)"}
        elif v < 260:
            return {"signal": "YELLOW", "score": 55, "label": "Rising jobless claims"}
        elif v < 350:
            return {"signal": "RED", "score": 30, "label": "Elevated claims (labor weakening)"}
        else:
            return {"signal": "RED", "score": 10, "label": "Spiking claims (recession)"}

    elif name == "sahm_rule":
        v = float(value)
        if v < 0.2:
            return {"signal": "GREEN", "score": 90, "label": "Sahm Rule clear — no recession"}
        elif v < 0.5:
            return {"signal": "YELLOW", "score": 45, "label": "Sahm Rule approaching threshold"}
        else:
            return {"signal": "RED", "score": 5, "label": "Sahm Rule TRIGGERED — recession signal"}

    elif name == "consumer_confidence":
        v = float(value)
        if v > 75:
            return {"signal": "GREEN", "score": 80, "label": "Consumer confidence high"}
        elif v > 60:
            return {"signal": "YELLOW", "score": 55, "label": "Consumer confidence moderate"}
        else:
            return {"signal": "RED", "score": 25, "label": "Consumer confidence low (demand risk)"}

    elif name == "fed_bs_change_pct":
        v = float(value)
        if v > 0.5:
            return {"signal": "GREEN", "score": 80, "label": "Fed expanding balance sheet (QE)"}
        elif v > -0.5:
            return {"signal": "YELLOW", "score": 55, "label": "Fed balance sheet stable"}
        else:
            return {"signal": "RED", "score": 25, "label": "Fed tightening (QT)"}

    elif name == "tga_balance_b":
        v = float(value)
        if v < 300:
            return {"signal": "GREEN", "score": 75, "label": "TGA low (liquidity add)"}
        elif v < 600:
            return {"signal": "YELLOW", "score": 55, "label": "TGA moderate"}
        else:
            return {"signal": "RED", "score": 30, "label": "TGA high (liquidity drain)"}

    elif name == "net_liquidity_b":
        v = float(value)
        if v > 6500:
            return {"signal": "GREEN", "score": 80, "label": "Net liquidity ample"}
        elif v > 5500:
            return {"signal": "YELLOW", "score": 55, "label": "Net liquidity adequate"}
        else:
            return {"signal": "RED", "score": 25, "label": "Net liquidity tight (stress)"}

    elif name == "m2_yoy_pct":
        v = float(value)
        if v > 4:
            return {"signal": "GREEN", "score": 80, "label": "M2 growing (monetary expansion)"}
        elif v > 0:
            return {"signal": "YELLOW", "score": 55, "label": "M2 growth slowing"}
        else:
            return {"signal": "RED", "score": 15, "label": "M2 contracting (deflationary)"}

    elif name == "bank_reserves_b":
        v = float(value)
        if v > 3200:
            return {"signal": "GREEN", "score": 75, "label": "Bank reserves adequate"}
        elif v > 2800:
            return {"signal": "YELLOW", "score": 50, "label": "Bank reserves declining"}
        else:
            return {"signal": "RED", "score": 20, "label": "Bank reserves low (funding stress)"}

    elif name == "skew_index":
        v = float(value)
        if v < 120:
            return {"signal": "GREEN", "score": 80, "label": "Low tail risk pricing"}
        elif v < 140:
            return {"signal": "YELLOW", "score": 55, "label": "Moderate tail risk"}
        else:
            return {"signal": "RED", "score": 20, "label": "High tail risk (skew elevated)"}

    elif name == "put_call_ratio":
        v = float(value)
        if v < 0.7:
            return {"signal": "GREEN", "score": 75, "label": "Low put/call (bullish sentiment)"}
        elif v < 1.0:
            return {"signal": "YELLOW", "score": 55, "label": "Balanced put/call"}
        elif v < 1.2:
            return {"signal": "YELLOW", "score": 40, "label": "Elevated put/call (hedging)"}
        else:
            return {"signal": "RED", "score": 15, "label": "Extreme put/call (fear)"}

    elif name == "consumer_confidence_declining_3m":
        if value:
            return {"signal": "RED", "score": 25, "label": "Consumer confidence declining 3+ months"}
        else:
            return {"signal": "GREEN", "score": 75, "label": "Consumer confidence stable/rising"}

    return {"signal": "YELLOW", "score": 50, "label": f"Unknown extended indicator: {name}"}


# ---------------------------------------------------------------------------
# Fear & Greed Index  (7-component CNN-style composite, 0 = extreme fear, 100 = extreme greed)
# ---------------------------------------------------------------------------

def _fg_clamp(value: float, lo: float, hi: float) -> float:
    """Linearly interpolate value from [lo..hi] → [0..100]."""
    if hi == lo:
        return 50.0
    return max(0.0, min(100.0, (value - lo) / (hi - lo) * 100.0))


def _fg_label(score: float) -> str:
    if score < 25:
        return "Extreme Fear"
    if score < 45:
        return "Fear"
    if score < 56:
        return "Neutral"
    if score < 75:
        return "Greed"
    return "Extreme Greed"


def get_value_signal(fg_score: float, buffett_pct: float) -> str:
    """Combined Buffett/Munger entry-exit signal from Fear & Greed + Buffett Indicator."""
    if fg_score < 25 and buffett_pct < 100:
        return "BUFFETT WINDOW: ACCUMULATE QUALITY"
    if fg_score < 25:
        return "FEAR PRESENT: SELECTIVE BUYING"
    if fg_score < 45 and buffett_pct < 115:
        return "CAUTIOUS OPTIMISM"
    if fg_score < 56:
        return "NEUTRAL — WAIT FOR SETUP"
    if fg_score < 75:
        return "GREED BUILDING: BE SELECTIVE"
    if buffett_pct > 175:
        return "EXTREME GREED: RAISE CASH NOW"
    return "BUFFETT EXIT: TRIM POSITIONS"


def compute_fear_greed(
    macro_ctx: dict | None = None,
    bond_snap: dict | None = None,
    ema_breadth: dict | None = None,
) -> dict:
    """Compute a 7-component Fear & Greed index (0 = extreme fear, 100 = extreme greed).

    Args:
        macro_ctx:   Output of fetch_macro_context() — provides VIX, SPY, put/call.
        bond_snap:   Output of bond_etf_data.get_bond_etf_snapshot() — SPY/TLT/HYG/LQD momentum.
        ema_breadth: Output of batch_scanner.compute_200ema_breadth() — breadth metrics.

    Returns dict with composite score, label, per-component breakdown, and entry/exit signal.
    """
    try:
        import yfinance as yf
        _yf_ok = True
    except ImportError:
        _yf_ok = False

    if macro_ctx is None:
        try:
            macro_ctx = fetch_macro_context()
        except Exception:
            macro_ctx = {}

    raw = macro_ctx.get("raw_values", {})
    components: dict[str, float] = {}

    # 1. Market Momentum — SPY vs 125-day SMA
    try:
        spy_price = raw.get("sp500") or 0.0
        spy_sma50 = raw.get("sp500_sma50") or 0.0
        # Use SMA-50 as proxy for SMA-125 when not available; deviation still meaningful
        sma_ref = spy_sma50 or spy_price
        if sma_ref > 0 and spy_price > 0:
            deviation_pct = (spy_price - sma_ref) / sma_ref * 100
            components["momentum"] = round(_fg_clamp(deviation_pct, -5.0, 5.0), 1)
        else:
            components["momentum"] = 50.0
    except Exception:
        components["momentum"] = 50.0

    # 2. Stock Price Strength — net 52W hi/lo ratio from breadth
    try:
        if ema_breadth and "net_hi_lo_ratio" in ema_breadth:
            ratio = float(ema_breadth["net_hi_lo_ratio"])
            components["strength"] = round(_fg_clamp(ratio, -0.4, 0.4), 1)
        else:
            components["strength"] = 50.0
    except Exception:
        components["strength"] = 50.0

    # 3. Stock Breadth — % above 200 EMA
    try:
        if ema_breadth and "pct_above_200ema" in ema_breadth:
            pct = float(ema_breadth["pct_above_200ema"])
            components["breadth"] = round(_fg_clamp(pct, 20.0, 80.0), 1)
        else:
            components["breadth"] = 50.0
    except Exception:
        components["breadth"] = 50.0

    # 4. Put/Call Ratio — lower PCR = greed (everyone buying calls)
    try:
        pcr = raw.get("put_call_ratio")
        if pcr is None and _yf_ok:
            # Fetch CBOE equity PCR via yfinance (^PCCE proxy)
            try:
                import yfinance as yf
                pcr_data = yf.Ticker("^PCCE").history(period="5d")
                if not pcr_data.empty:
                    pcr = float(pcr_data["Close"].iloc[-1])
            except Exception:
                pcr = None
        if pcr is not None:
            # PCR < 0.7 = greed (100), PCR > 1.2 = fear (0)
            components["put_call"] = round(_fg_clamp(pcr, 1.2, 0.7), 1)
        else:
            components["put_call"] = 50.0
    except Exception:
        components["put_call"] = 50.0

    # 5. Market Volatility — VIX vs its 50-day average (lower VIX = greed)
    try:
        vix_current = raw.get("vix") or 0.0
        if _yf_ok and vix_current > 0:
            try:
                import yfinance as yf
                vix_hist = yf.Ticker("^VIX").history(period="3mo", interval="1d")
                if len(vix_hist) >= 50:
                    vix_50ma = float(vix_hist["Close"].iloc[-50:].mean())
                    # VIX below its 50-MA = calm = greed; above = fear
                    components["vix"] = round(_fg_clamp(vix_50ma - vix_current, -8.0, 8.0), 1)
                else:
                    components["vix"] = 50.0
            except Exception:
                components["vix"] = 50.0
        else:
            components["vix"] = 50.0
    except Exception:
        components["vix"] = 50.0

    # 6. Safe Haven Demand — SPY 4W momentum vs TLT 4W momentum
    try:
        if bond_snap and "SPY" in bond_snap and "TLT" in bond_snap:
            spy_mom = bond_snap["SPY"].get("momentum_4w") or 0.0
            tlt_mom = bond_snap["TLT"].get("momentum_4w") or 0.0
            spread = spy_mom - tlt_mom  # positive = stocks leading = greed
            components["safe_haven"] = round(_fg_clamp(spread, -0.08, 0.08), 1)
        else:
            components["safe_haven"] = 50.0
    except Exception:
        components["safe_haven"] = 50.0

    # 7. Junk Bond Demand — HYG momentum vs LQD momentum (tight spread = greed)
    try:
        if bond_snap and "HYG" in bond_snap and "LQD" in bond_snap:
            hyg_mom = bond_snap["HYG"].get("momentum_4w") or 0.0
            lqd_mom = bond_snap["LQD"].get("momentum_4w") or 0.0
            spread = hyg_mom - lqd_mom  # positive = risk appetite = greed
            components["junk_bond"] = round(_fg_clamp(spread, -0.06, 0.06), 1)
        else:
            components["junk_bond"] = 50.0
    except Exception:
        components["junk_bond"] = 50.0

    composite = round(sum(components.values()) / len(components), 1)
    label = _fg_label(composite)

    # Fetch latest Buffett indicator for entry/exit signal
    try:
        buffett = compute_buffett_indicator()
        buffett_pct = buffett.get("ratio_pct", 130.0)
    except Exception:
        buffett_pct = 130.0

    return {
        "composite": composite,
        "label":     label,
        "components": components,
        "entry_exit_signal": get_value_signal(composite, buffett_pct),
    }


# ---------------------------------------------------------------------------
# Buffett Indicator  (Total Market Cap / GDP)
# ---------------------------------------------------------------------------

def _buffett_signal(ratio_pct: float) -> str:
    if ratio_pct < 75:
        return "DEEPLY UNDERVALUED"
    if ratio_pct < 100:
        return "UNDERVALUED"
    if ratio_pct < 115:
        return "FAIR VALUE"
    if ratio_pct < 145:
        return "OVERVALUED"
    if ratio_pct < 175:
        return "EXPENSIVE"
    return "DANGEROUSLY OVERVALUED"


def compute_buffett_indicator() -> dict:
    """Compute the Buffett Indicator: Wilshire 5000 total-return index / US GDP.

    Uses yfinance '^W5000' for market cap proxy and FRED 'GDP' for nominal GDP (already
    fetched elsewhere in this module). Falls back to sample values when unavailable.

    Returns dict with ratio_pct, wilshire_trn, gdp_trn, signal.
    """
    wilshire_trn: float | None = None
    gdp_trn: float | None = None

    # --- Wilshire 5000 via yfinance ---
    try:
        import yfinance as yf
        w5 = yf.Ticker("^W5000")
        hist = w5.history(period="5d")
        if not hist.empty:
            # ^W5000 is a price index, not market-cap directly.
            # Market cap ≈ index_value * 1.35B shares equivalent (conventional scaling).
            # Better: use the Wilshire 5000 Full-Cap Index level directly as a market-cap proxy
            # (the index is designed so 1 point ≈ $1B of market cap historically).
            w5_level = float(hist["Close"].iloc[-1])
            wilshire_trn = round(w5_level / 1_000, 4)   # index points → approximate $T
    except Exception:
        wilshire_trn = None

    # --- GDP level via FRED (series "GDP", quarterly, seasonally adjusted, billions) ---
    try:
        import os
        fred_key = os.environ.get("FRED_API_KEY", "")
        if fred_key:
            from fredapi import Fred  # type: ignore
            fred = Fred(api_key=fred_key)
            gdp_series = fred.get_series("GDP", observation_start="2020-01-01")
            gdp_clean  = gdp_series.dropna()
            if len(gdp_clean) > 0:
                gdp_billions = float(gdp_clean.iloc[-1])
                gdp_trn = round(gdp_billions / 1_000, 4)
    except Exception:
        gdp_trn = None

    # Fallback: US GDP ~$29T (2025 estimate)
    if gdp_trn is None or gdp_trn < 1:
        gdp_trn = 29.0

    # Fallback: Wilshire 5000 ~$46T (2025 estimate, very high)
    if wilshire_trn is None or wilshire_trn < 1:
        wilshire_trn = 46.0

    ratio_pct = round(wilshire_trn / gdp_trn * 100, 1)
    signal    = _buffett_signal(ratio_pct)

    return {
        "ratio_pct":    ratio_pct,
        "wilshire_trn": wilshire_trn,
        "gdp_trn":      gdp_trn,
        "signal":       signal,
    }
