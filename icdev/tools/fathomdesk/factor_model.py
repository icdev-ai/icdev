#!/usr/bin/env python3
"""Fama-French 3-factor exposure model for FathomDesk.

Downloads daily FF3 factors from Kenneth French's data library and runs
OLS regression of ticker excess returns on Rm-Rf, SMB, HML over a
trailing window.

Usage:
    from tools.fathomdesk.factor_model import compute_factor_exposure
    result = compute_factor_exposure("AAPL")
    # {"beta_market": 1.12, "beta_smb": -0.34, "beta_hml": -0.21,
    #  "r_squared": 0.62, "as_of_date": "2026-05-01"}
"""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.fathomdesk.data_gateway import FathomDeskDataGateway  # noqa: E402

_FF3_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_Factors_daily_CSV.zip"
)
_CACHE_DIR = Path(tempfile.gettempdir()) / "icdev_fathomdesk"
_CACHE_FILE = _CACHE_DIR / "ff3_factors_daily.csv"
_CACHE_MAX_AGE_HOURS = 24


def _cache_fresh() -> bool:
    if not _CACHE_FILE.exists():
        return False
    age_s = datetime.now(timezone.utc).timestamp() - _CACHE_FILE.stat().st_mtime
    return age_s < _CACHE_MAX_AGE_HOURS * 3600


def _fetch_ff3() -> dict[str, dict[str, float]]:
    """Return FF3 daily factors keyed by ISO date 'YYYY-MM-DD'.

    Values are {mkt_rf, smb, hml, rf} in decimal (converted from percent).
    Results are cached for 24 hours in the system temp directory.
    """
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not _cache_fresh():
        # B310: URL is a module-level HTTPS constant — scheme verified below
        if not _FF3_URL.startswith("https://"):
            raise RuntimeError(f"FF3 URL must use HTTPS, got: {_FF3_URL[:30]}")
        with urllib.request.urlopen(_FF3_URL, timeout=30) as resp:  # nosec B310
            raw = resp.read()
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            inner = next(n for n in zf.namelist() if n.upper().endswith(".CSV"))
            content = zf.read(inner).decode("latin-1")
        _CACHE_FILE.write_text(content, encoding="utf-8", newline="")
    else:
        content = _CACHE_FILE.read_text(encoding="utf-8")

    factors: dict[str, dict[str, float]] = {}
    past_header = False
    for row in csv.reader(io.StringIO(content)):
        if not row:
            continue
        first = row[0].strip()
        if not past_header:
            # The header row has "Mkt-RF" as first non-empty cell or second cell
            cells = [c.strip().lower() for c in row]
            if "mkt-rf" in cells or "mkt_rf" in cells:
                past_header = True
            continue
        # Daily data rows: first col is 8-digit YYYYMMDD
        if len(first) != 8 or not first.isdigit():
            # Reached monthly/annual section or footer — stop
            break
        try:
            iso = f"{first[:4]}-{first[4:6]}-{first[6:]}"
            factors[iso] = {
                "mkt_rf": float(row[1]) / 100.0,
                "smb":    float(row[2]) / 100.0,
                "hml":    float(row[3]) / 100.0,
                "rf":     float(row[4]) / 100.0,
            }
        except (ValueError, IndexError):
            continue
    return factors


def compute_factor_exposure(
    ticker: str,
    as_of_date: str | None = None,
    lookback: int = 252,
) -> dict[str, float | str]:
    """Compute Fama-French 3-factor exposures for *ticker*.

    Args:
        ticker:     Equity symbol (e.g. "AAPL").
        as_of_date: ISO date string (YYYY-MM-DD); defaults to today.
        lookback:   Trailing trading days for OLS (default 252 ≈ 1 year).

    Returns:
        dict with keys: beta_market, beta_smb, beta_hml, r_squared, as_of_date

    Raises:
        ValueError: Insufficient aligned observations for regression.
        RuntimeError: Network failure fetching FF3 data or ticker prices.
    """
    if as_of_date is None:
        as_of_date = date.today().isoformat()

    # --- 1. Fetch FF3 factors -------------------------------------------------
    try:
        ff3 = _fetch_ff3()
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch FF3 factors: {exc}") from exc

    if not ff3:
        raise RuntimeError("FF3 factor data is empty — parse may have failed.")

    # --- 2. Fetch ticker OHLCV and compute daily returns ----------------------
    gw = FathomDeskDataGateway()
    # Request 2 years to ensure enough overlap after alignment with FF3 dates
    bars = gw.historical_bars(ticker, period="2y", interval="1d", as_of_date=as_of_date)

    if len(bars) < 2:
        raise ValueError(f"Insufficient price data for {ticker}: {len(bars)} bar(s) returned.")

    closes: dict[str, float] = {b["date"]: float(b["close"]) for b in bars}
    sorted_dates = sorted(closes)

    ticker_ret: dict[str, float] = {}
    for i in range(1, len(sorted_dates)):
        d_prev, d_curr = sorted_dates[i - 1], sorted_dates[i]
        prev_close = closes[d_prev]
        if prev_close != 0.0:
            ticker_ret[d_curr] = (closes[d_curr] - prev_close) / prev_close

    # --- 3. Align on common dates on/before as_of_date -----------------------
    common = sorted(
        d for d in set(ticker_ret) & set(ff3)
        if d <= as_of_date
    )
    common = common[-lookback:]

    if len(common) < 30:
        raise ValueError(
            f"Only {len(common)} aligned observations for {ticker}; "
            f"need ≥ 30 to fit FF3 model."
        )

    # --- 4. Build OLS arrays --------------------------------------------------
    n = len(common)
    # Excess return: r_ticker - RF
    y = np.array(
        [ticker_ret[d] - ff3[d]["rf"] for d in common],
        dtype=np.float64,
    )
    X = np.column_stack([
        np.ones(n, dtype=np.float64),
        [ff3[d]["mkt_rf"] for d in common],
        [ff3[d]["smb"]    for d in common],
        [ff3[d]["hml"]    for d in common],
    ])

    # --- 5. OLS via numpy.linalg.lstsq ----------------------------------------
    # coeffs = [alpha, beta_market, beta_smb, beta_hml]
    coeffs, _, _, _ = np.linalg.lstsq(X, y, rcond=None)

    y_hat = X @ coeffs
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - float(y.mean())) ** 2))
    r_squared = (1.0 - ss_res / ss_tot) if ss_tot > 0.0 else 0.0

    return {
        "beta_market": float(coeffs[1]),
        "beta_smb":    float(coeffs[2]),
        "beta_hml":    float(coeffs[3]),
        "r_squared":   round(r_squared, 6),
        "as_of_date":  as_of_date,
    }


if __name__ == "__main__":
    import json

    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    result = compute_factor_exposure(ticker)
    print(json.dumps(result, indent=2))
