# CUI // SP-CTI
"""FathomDesk Authenticated Smoke Test.

Catches the categories of bugs that CodeLens + Coherence + Selenium miss:

  1. API field-name drift        e.g. 'composite' vs 'composite_score'
  2. Authenticated-only failures e.g. missing 'session' import causing 500
  3. Empty-data pages            e.g. price_to_book never populated
  4. DB schema drift             e.g. as_of_date vs fetched_at column names
  5. Broken HTML pages           500s, redirect loops, blueprint import errors

Usage:
  python tools/testing/fathomdesk_smoke.py
  python tools/testing/fathomdesk_smoke.py --url http://localhost:5100 --email x@y.com --password secret
  python tools/testing/fathomdesk_smoke.py --record   # bootstrap contracts from live responses
  python tools/testing/fathomdesk_smoke.py --json     # machine-readable output
  python tools/testing/fathomdesk_smoke.py --fast     # skip DB schema checks
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

# ── ANSI colours ──────────────────────────────────────────────────────────────
_NO_COLOR = not sys.stdout.isatty() or os.environ.get("NO_COLOR")

def _c(code: str, text: str) -> str:
    return text if _NO_COLOR else f"\033[{code}m{text}\033[0m"

GREEN  = lambda t: _c("32", t)
RED    = lambda t: _c("31", t)
YELLOW = lambda t: _c("33", t)
CYAN   = lambda t: _c("36", t)
BOLD   = lambda t: _c("1",  t)
DIM    = lambda t: _c("2",  t)

PASS = GREEN("PASS")
FAIL = RED("FAIL")
WARN = YELLOW("WARN")
SKIP = DIM("SKIP")


# ── Result model ──────────────────────────────────────────────────────────────
@dataclass
class CheckResult:
    name:    str
    status:  str          # "pass" | "fail" | "warn" | "skip"
    detail:  str  = ""
    elapsed: float = 0.0

    @property
    def icon(self) -> str:
        return {"pass": PASS, "fail": FAIL, "warn": WARN, "skip": SKIP}[self.status]


@dataclass
class SmokeReport:
    results: list[CheckResult] = field(default_factory=list)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)

    @property
    def passed(self)  -> int: return sum(1 for r in self.results if r.status == "pass")
    @property
    def failed(self)  -> int: return sum(1 for r in self.results if r.status == "fail")
    @property
    def warned(self)  -> int: return sum(1 for r in self.results if r.status == "warn")
    @property
    def ok(self)      -> bool: return self.failed == 0


# -- API Contracts -------------------------------------------──────────────────
# Each contract: {required, data_key, data_required, data_min, warn_empty}
# required       — top-level keys that MUST exist in every response
# data_key       — dotted path to the data object/array (e.g. "latest" or "results")
# data_required  — keys that must exist inside the data object when data is present
# data_min       — minimum list length for array endpoints (warn if below)
# warn_empty     — emit WARN (not FAIL) when data_key is empty/None
#
# Convention: "no_data" status is accepted as valid (sweep hasn't run yet).

CONTRACTS: dict[str, dict[str, Any]] = {
    # ── Auth ──
    # /api/auth/me returns {"authenticated": true, "user": {"id": ..., "email": ...}}
    "GET /api/auth/me": {
        "required": ["authenticated", "user"],
        "data_key": "user",
        "data_required": ["id", "email"],
    },

    # ── Value / Fear & Greed ──
    # Returns {"latest": {...}, "history": [...]} — no top-level "status" key
    "GET /api/value/fear-greed": {
        "required":      ["latest", "history"],
        "data_key":      "latest",
        "data_required": ["composite_score", "label", "components", "entry_exit_signal"],
        "warn_empty":    True,
    },

    # ── Value / Buffett ──
    # Returns {"latest": {...}, "history": [...]} — no top-level "status" key
    "GET /api/value/buffett-indicator": {
        "required":      ["latest", "history"],
        "data_key":      "latest",
        "data_required": ["ratio_pct", "signal", "wilshire_trn", "gdp_trn"],
        "warn_empty":    True,
    },

    # ── Value / PE-NAV ──
    "GET /api/value/pe-nav": {
        "required":   ["results", "count"],
        "data_key":   "results",
        "data_min":   1,
        "warn_empty": True,
    },

    # ── Value / Screener ──
    "GET /api/value/screener": {
        "required": ["results"],
    },

    # ── Market ──
    "GET /api/market/latest": {
        "required": [],
    },

    # ── Breadth (actual route: /api/market/breadth) ──
    "GET /api/market/breadth": {
        "required": ["latest", "history"],
        "warn_empty": True,
    },

    # ── Regime ──
    "GET /api/regime": {
        "required": [],
    },

    # ── Alerts ──
    # /api/alerts/badge returns {"unack_count": N}
    "GET /api/alerts/badge": {
        "required": ["unack_count"],
    },
    "GET /api/alerts/rules": {
        "required": ["rules"],
    },

    # ── Portfolio ──
    "GET /api/portfolio/state": {
        "required": [],
    },

    # ── Signals (actual route: /api/signals) ──
    "GET /api/signals": {
        "required": ["signals"],
        "data_key": "signals",
        "warn_empty": True,
    },

    # ── News (actual route: /api/news/reading) ──
    "GET /api/news/reading": {
        "required": [],
    },

    # ── Oracle (actual route: /api/oracle/reading) ──
    "GET /api/oracle/reading": {
        "required": [],
    },

    # ── Risk (actual route: /api/risk) ──
    "GET /api/risk": {
        "required": [],
    },

    # ── Watchlist (actual route: /api/watchlist) ──
    "GET /api/watchlist": {
        "required": ["count", "items"],
    },

    # ── Radar ──
    # Unlike the value/breadth endpoints, this one signals "no data yet" with
    # 404 + {"error": "no_snapshot"} rather than 200 + {"status": "no_data"}.
    # Treated as the same empty-data condition; a 404 carrying any other body
    # (including Flask's HTML 404 for a genuinely missing route) still fails.
    "GET /api/radar/latest": {
        "required": [],
        "no_data_404_error": "no_snapshot",
    },
}

# ── Page routes (HTML) ────────────────────────────────────────────────────────
# [path, expected_status]  — 200 or 302 redirect (both ok for pages)
PAGE_ROUTES: list[tuple[str, int]] = [
    ("/",           200),
    ("/portfolio",  200),
    ("/market",     200),
    ("/value",      200),
    ("/breadth",    200),
    ("/news",       200),
    ("/oracle",     200),
    ("/signals",    200),
    ("/analysis",   200),
    ("/watchlist",  200),
    ("/risk",       200),
    ("/settings",   200),
    ("/radar",      200),
    ("/graph",      200),
    ("/scenarios",  200),
    ("/today",      200),
    ("/alerts",     200),
    ("/advisor",    200),
]

# ── DB Schema contracts ───────────────────────────────────────────────────────
# {table: [required_columns]}
DB_SCHEMA: dict[str, list[str]] = {
    "ad_fundamental_metrics": [
        "ticker", "pe_ratio", "price_to_book", "roe", "gross_margin", "debt_to_equity",
    ],
    "ad_fear_greed_snapshots": [
        "composite_score", "label", "components_json", "entry_exit_signal", "created_at",
    ],
    "ad_buffett_snapshots": [
        "ratio_pct", "signal", "wilshire_trn", "gdp_trn", "created_at",
    ],
    "ad_quality_scores": [
        "composite_quality_score", "pe_nav_score", "roe_gap", "nav_quadrant",
    ],
    "ad_user_sessions": [
        "id", "user_id", "mfa_satisfied", "expires_at",
    ],
    "ad_signals": [
        "ticker", "direction", "composite_score",
    ],
}

# ── Data-presence checks ──────────────────────────────────────────────────────
# Tables that should have at least N rows for the feature to work
DATA_PRESENCE: dict[str, int] = {
    "ad_fundamental_metrics": 1,
    "ad_fear_greed_snapshots": 1,
    "ad_buffett_snapshots":    1,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    email    = args.email    or os.environ.get("FATHOMDESK_TEST_EMAIL")
    password = args.password or os.environ.get("FATHOMDESK_TEST_PASSWORD")
    if not email or not password:
        # Try reading from .env
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "FATHOMDESK_TEST_EMAIL":
                    email = email or v
                if k == "FATHOMDESK_TEST_PASSWORD":
                    password = password or v
    if not email or not password:
        print(RED("ERROR") + ": credentials required. Set --email/--password or "
              "FATHOMDESK_TEST_EMAIL/FATHOMDESK_TEST_PASSWORD env vars.")
        sys.exit(1)
    return email, password


def _login(base: str, email: str, password: str) -> requests.Session:
    """Authenticate and return a requests.Session with the ad_session cookie set."""
    s = requests.Session()
    s.headers.update({"User-Agent": "FathomDesk-Smoke/1.0"})
    resp = s.post(
        f"{base}/api/auth/login",
        json={"email": email, "password": password},
        timeout=15,
        allow_redirects=False,
    )
    if resp.status_code not in (200, 201):
        print(RED("LOGIN FAILED") + f" — HTTP {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)
    body = resp.json()
    if body.get("mfa_required"):
        print(YELLOW("MFA required") + " — smoke test cannot proceed without MFA bypass. "
              "Disable MFA for the test account or pass a pre-issued API token.")
        sys.exit(1)
    if not body.get("ok"):
        print(RED("LOGIN FAILED") + f" — {body}")
        sys.exit(1)
    return s


def _json_error_is(resp: Any, sentinel: str) -> bool:
    """True when *resp* is JSON carrying exactly ``{"error": sentinel}``.

    Keeps the no-data allowance narrow: Flask's HTML 404 for a route that no
    longer exists is not JSON, so it still fails.
    """
    try:
        body = resp.json()
    except Exception:
        return False
    return isinstance(body, dict) and body.get("error") == sentinel


def _get_nested(obj: Any, dotted_key: str) -> Any:
    """Resolve 'a.b.c' into obj['a']['b']['c']."""
    parts = dotted_key.split(".")
    cur = obj
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


# ── Check runners ─────────────────────────────────────────────────────────────

def check_pages(base: str, session: requests.Session, report: SmokeReport) -> None:
    print(f"\n{BOLD('-- Page Routes ---------------------------------------------')}")
    for path, expected in PAGE_ROUTES:
        t0 = time.time()
        try:
            r = session.get(f"{base}{path}", timeout=15, allow_redirects=True)
            elapsed = time.time() - t0
            status = r.status_code
            body_lower = r.text.lower()

            if status == expected:
                # Look for 500-indicator markers even in a 200 response
                if any(m in body_lower for m in ("internal server error", "traceback", "werkzeug debugger")):
                    cr = CheckResult(f"PAGE {path}", "fail",
                                     f"HTTP {status} but response contains server error markers", elapsed)
                else:
                    cr = CheckResult(f"PAGE {path}", "pass", f"HTTP {status}", elapsed)
            elif status in (301, 302, 308) and expected == 200:
                # Unexpected redirect — might mean auth broke
                loc = r.headers.get("Location", "?")
                cr = CheckResult(f"PAGE {path}", "warn",
                                 f"Redirect -> {loc} (expected 200)", elapsed)
            else:
                cr = CheckResult(f"PAGE {path}", "fail",
                                 f"Expected {expected}, got {status}", elapsed)
        except Exception as exc:
            cr = CheckResult(f"PAGE {path}", "fail", str(exc), time.time() - t0)

        report.add(cr)
        timing = DIM(f"  {cr.elapsed*1000:.0f}ms")
        print(f"  {cr.icon}  {path:<35} {DIM(cr.detail)}{timing}")


def check_api(base: str, session: requests.Session, report: SmokeReport,
              record: bool = False, recorded: dict | None = None) -> dict:
    """Run API contract checks. Returns recorded responses when record=True."""
    print(f"\n{BOLD('-- API Contracts -------------------------------------------')}")
    recordings: dict = {}

    for route, contract in CONTRACTS.items():
        method, path = route.split(" ", 1)
        t0 = time.time()
        try:
            fn = getattr(session, method.lower())
            resp = fn(f"{base}{path}", timeout=20)
            elapsed = time.time() - t0

            # Must be HTTP 200 — except for a contract that documents its own
            # "no data yet" status code (see no_data_404_error above).
            if resp.status_code != 200:
                sentinel = contract.get("no_data_404_error")
                if sentinel and resp.status_code == 404 and _json_error_is(resp, sentinel):
                    report.add(CheckResult(route, "warn",
                        f"{sentinel} — sweep has not run yet", elapsed))
                    print(f"  {WARN}  {route:<55} {YELLOW(f'{sentinel} — sweep has not run yet')}")
                    continue
                report.add(CheckResult(route, "fail",
                    f"HTTP {resp.status_code} (expected 200)", elapsed))
                print(f"  {FAIL}  {route:<55} HTTP {resp.status_code}")
                continue

            # Must be valid JSON
            try:
                body = resp.json()
            except Exception:
                report.add(CheckResult(route, "fail", "Response is not valid JSON", elapsed))
                print(f"  {FAIL}  {route:<55} non-JSON response")
                continue

            if record:
                recordings[route] = body
                report.add(CheckResult(route, "pass", "recorded", elapsed))
                print(f"  {PASS}  {route:<55} {DIM('recorded')} {DIM(f'{elapsed*1000:.0f}ms')}")
                continue

            failures: list[str] = []
            warnings: list[str] = []

            # Allow "no_data" status responses through without data checks.
            # The required-key list describes the *populated* shape, so it must
            # be skipped too — a no_data body carries {status, message} and
            # nothing else, and checking it anyway reported three endpoints as
            # broken purely because their sweep had not run yet.
            no_data = (body.get("status") == "no_data") if isinstance(body, dict) else False

            if no_data:
                # Surfaced as WARN, not a silent PASS — the endpoint is healthy
                # but the page behind it renders empty until the sweep runs.
                warnings.append("no_data — sweep has not run yet")
            else:
                # 1 — Required top-level keys
                for key in contract.get("required", []):
                    if _get_nested(body, key) is None and key not in body:
                        failures.append(f"missing required key '{key}'")

                # 2 — data_key check (object)
                dk = contract.get("data_key")
                if dk:
                    data_obj = _get_nested(body, dk)
                    if data_obj is None:
                        sev = "warn" if contract.get("warn_empty") else "fail"
                        (warnings if sev == "warn" else failures).append(
                            f"'{dk}' is null/missing (data not yet populated?)")
                    elif isinstance(data_obj, list):
                        min_len = contract.get("data_min", 0)
                        if len(data_obj) < min_len:
                            sev = "warn" if contract.get("warn_empty") else "fail"
                            (warnings if sev == "warn" else failures).append(
                                f"'{dk}' has {len(data_obj)} items (min {min_len})")
                    elif isinstance(data_obj, dict):
                        for req_field in contract.get("data_required", []):
                            if req_field not in data_obj:
                                failures.append(f"'{dk}.{req_field}' missing from response")

                # 3 — history_key sanity check
                hk = contract.get("history_key")
                if hk:
                    hist = body.get(hk)
                    if hist is not None and not isinstance(hist, list):
                        failures.append(f"'{hk}' should be a list, got {type(hist).__name__}")

            # 4 — error key check (error should not be present on 200 responses)
            if isinstance(body, dict) and "error" in body:
                failures.append(f"response contains 'error': {str(body['error'])[:80]}")

            if failures:
                detail = "; ".join(failures[:3])
                report.add(CheckResult(route, "fail", detail, elapsed))
                print(f"  {FAIL}  {route:<55} {RED(detail[:70])}")
            elif warnings:
                detail = "; ".join(warnings[:2])
                report.add(CheckResult(route, "warn", detail, elapsed))
                print(f"  {WARN}  {route:<55} {YELLOW(detail[:70])}")
            else:
                report.add(CheckResult(route, "pass", "", elapsed))
                print(f"  {PASS}  {route:<55} {DIM(f'{elapsed*1000:.0f}ms')}")

        except Exception as exc:
            report.add(CheckResult(route, "fail", str(exc)[:120], time.time() - t0))
            print(f"  {FAIL}  {route:<55} {RED(str(exc)[:70])}")

    return recordings


def _get_smoke_conn():
    """Get a DB connection that skips _ensure_schema() to avoid DDL lock contention."""
    try:
        from tools.db.storage import get_connection
        return get_connection()
    except Exception:
        from tools.trading.db import get_conn
        return get_conn()


def check_db_schema(report: SmokeReport) -> None:
    print(f"\n{BOLD('-- DB Schema -----------------------------------------------')}")
    sys.stdout.flush()
    try:
        conn = _get_smoke_conn()
    except BaseException as exc:
        report.add(CheckResult("DB connection", "fail", str(exc)))
        print(f"  {FAIL}  DB connection: {exc}")
        sys.stdout.flush()
        return

    for table, required_cols in DB_SCHEMA.items():
        t0 = time.time()
        try:
            # Ask the DB for actual column names
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            ).fetchall()
            actual_cols = {r[0] if not hasattr(r, "keys") else r["column_name"] for r in rows}

            if not actual_cols:
                # Might be SQLite — fall back to PRAGMA
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                actual_cols = {r[1] for r in rows}

            if not actual_cols:
                report.add(CheckResult(f"SCHEMA {table}", "fail",
                                       "table not found", time.time() - t0))
                print(f"  {FAIL}  {table:<45} table not found")
                continue

            missing = [c for c in required_cols if c not in actual_cols]
            elapsed = time.time() - t0
            if missing:
                detail = f"missing columns: {missing}"
                report.add(CheckResult(f"SCHEMA {table}", "fail", detail, elapsed))
                print(f"  {FAIL}  {table:<45} {RED(detail)}")
            else:
                report.add(CheckResult(f"SCHEMA {table}", "pass", "", elapsed))
                print(f"  {PASS}  {table:<45} {DIM(f'{len(actual_cols)} cols')} {DIM(f'{elapsed*1000:.0f}ms')}")

        except BaseException as exc:
            report.add(CheckResult(f"SCHEMA {table}", "fail", str(exc)[:100], time.time() - t0))
            print(f"  {FAIL}  {table:<45} {RED(str(exc)[:70])}")
            sys.stdout.flush()

    # Data presence checks
    print(f"\n{BOLD('-- Data Presence -------------------------------------------')}")
    for table, min_rows in DATA_PRESENCE.items():
        t0 = time.time()
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            elapsed = time.time() - t0
            if count < min_rows:
                report.add(CheckResult(f"DATA {table}", "warn",
                                       f"{count} rows (min {min_rows}) — sweep not run?", elapsed))
                print(f"  {WARN}  {table:<45} {YELLOW(f'{count} rows — sweep pending')}")
            else:
                report.add(CheckResult(f"DATA {table}", "pass", f"{count} rows", elapsed))
                print(f"  {PASS}  {table:<45} {DIM(f'{count} rows')} {DIM(f'{elapsed*1000:.0f}ms')}")
        except Exception as exc:
            report.add(CheckResult(f"DATA {table}", "fail", str(exc)[:80], time.time() - t0))
            print(f"  {FAIL}  {table:<45} {RED(str(exc)[:70])}")

    try:
        conn.close()
    except Exception:
        pass


# ── Main ──────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> SmokeReport:
    base  = args.url.rstrip("/")
    email, password = _resolve_credentials(args)

    print(BOLD(f"\nFathomDesk Smoke Test @ {base}"))
    print(DIM(f"Auth: {email}\n"))

    # ── Login ──
    t0 = time.time()
    try:
        session = _login(base, email, password)
        elapsed = time.time() - t0
        print(f"{PASS}  Login ({elapsed*1000:.0f}ms)")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"{FAIL}  Login: {exc}")
        sys.exit(1)

    report = SmokeReport()

    # ── Pages ──
    check_pages(base, session, report)

    # ── API contracts ──
    recorded = check_api(base, session, report, record=args.record)

    if args.record and recorded:
        out = BASE_DIR / "tools" / "testing" / "contracts" / "fathomdesk_contracts.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(recorded, indent=2, default=str), encoding="utf-8")
        print(f"\n{GREEN('Contracts recorded')} -> {out}")

    # ── DB schema + data presence ──
    if not args.fast:
        check_db_schema(report)

    # ── Summary ──
    print(f"\n{BOLD('-- Summary -------------------------------------------------')}")
    total = len(report.results)
    if report.ok:
        verdict = GREEN(f"ALL {report.passed}/{total} PASSED")
    else:
        verdict = RED(f"{report.failed} FAILED  |  {report.passed} passed  |  {report.warned} warned")
    print(f"  {verdict}")

    if report.failed:
        print(f"\n{BOLD('Failures:')}")
        for r in report.results:
            if r.status == "fail":
                print(f"  {FAIL}  {r.name}")
                print(f"         {RED(r.detail)}")

    if args.json_out:
        out = {
            "ok":      report.ok,
            "passed":  report.passed,
            "failed":  report.failed,
            "warned":  report.warned,
            "results": [
                {"name": r.name, "status": r.status, "detail": r.detail, "elapsed_ms": round(r.elapsed * 1000)}
                for r in report.results
            ],
        }
        print("\n" + json.dumps(out, indent=2))

    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="FathomDesk authenticated smoke test")
    ap.add_argument("--url",      default="http://localhost:5100", help="FathomDesk base URL")
    ap.add_argument("--email",    default="", help="Test account email")
    ap.add_argument("--password", default="", help="Test account password")
    ap.add_argument("--record",   action="store_true", help="Record live responses as new contracts")
    ap.add_argument("--fast",     action="store_true", help="Skip DB schema checks")
    ap.add_argument("--json",     dest="json_out", action="store_true", help="Emit JSON result")
    args = ap.parse_args()

    report = run(args)
    sys.exit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
