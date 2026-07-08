# CUI // SP-CTI
"""IQE smoke test for govcon/proposals collections (prop-vv-02).

Exercises every seed query in context/iqe/queries/{govcon,proposals}/
against the real POST /api/govcon/iqe-query and /api/proposals/iqe-query
routes (prop-cap-13, prop-iqe-01), confirming the full NL -> IQE -> execute
pipeline works end-to-end for both canvases, not just that the adapters
import cleanly.

Run: python tests/e2e_prop_vv02_iqe_smoke.py
Requires DASHBOARD_URL + ICDEV_DASHBOARD_API_KEY env vars.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:5050")
_ADMIN_KEY = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
_HEADERS = {"Authorization": f"Bearer {_ADMIN_KEY}", "Content-Type": "application/json"} if _ADMIN_KEY else {}

_QUERIES_ROOT = Path(__file__).resolve().parent.parent / "context" / "iqe" / "queries"

_ROUTES = {
    "govcon": "/api/govcon/iqe-query",
    "proposals": "/api/proposals/iqe-query",
}


def _load_seed_questions(canvas: str) -> list[tuple[str, str]]:
    """Return [(source_file, question), ...] for every non-blank line under
    context/iqe/queries/<canvas>/*.md."""
    out = []
    canvas_dir = _QUERIES_ROOT / canvas
    if not canvas_dir.exists():
        return out
    for md_file in sorted(canvas_dir.glob("*.md")):
        for line in md_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append((md_file.name, line))
    return out


def run_smoke() -> int:
    passed = 0
    failed = 0
    failures = []

    for canvas, route in _ROUTES.items():
        questions = _load_seed_questions(canvas)
        print(f"\n=== {canvas} ({route}) — {len(questions)} seed questions ===")
        for source, question in questions:
            try:
                resp = requests.post(
                    f"{BASE_URL}{route}",
                    headers=_HEADERS,
                    json={"question": question, "execute": True},
                    timeout=15,
                )
                data = resp.json()
                if resp.status_code != 200:
                    raise AssertionError(f"HTTP {resp.status_code}: {data}")
                if "error" in data:
                    raise AssertionError(f"IQE error: {data['error']} (iqe={data.get('iqe')})")
                if "iqe" not in data:
                    raise AssertionError(f"no 'iqe' key in response: {data}")
                row_count = data.get("row_count", len(data.get("results", [])))
                print(f"[OK]   {source}: {question!r} -> iqe={data['iqe']!r} rows={row_count}")
                passed += 1
            except Exception as exc:
                print(f"[FAIL] {source}: {question!r} -> {exc}")
                failures.append((source, question, str(exc)))
                failed += 1

    print(f"\n{'='*60}\nIQE smoke: {passed} passed, {failed} failed / {passed + failed} total")
    for source, question, err in failures:
        print(f"  FAIL [{source}] {question!r}: {err}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_smoke())
