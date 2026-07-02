# CUI // SP-CTI
"""Visual regression tester — perceptual hash comparison against stored baselines.

Complements tools/testing/screenshot_validator.py (which does LLM assertion checks).
This module does pixel-level structural comparison via average hash (Pillow) or
byte-diff fallback (stdlib only).

Usage:
    # Capture a baseline
    python tools/testing/visual_regression.py \\
        --capture-baseline route=/security:screenshot=playwright/screenshots/security.png

    # Compare against baseline
    python tools/testing/visual_regression.py \\
        --compare route=/security:screenshot=playwright/screenshots/security.png \\
        --threshold 10 --json

    # List stored baselines
    python tools/testing/visual_regression.py --list-baselines --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.testing.visual_regression")

# ---------------------------------------------------------------------------
# Pillow (optional — graceful degradation to byte-diff)
# ---------------------------------------------------------------------------
try:
    from PIL import Image as _PILImage
    _PILLOW_AVAILABLE = True
except ImportError:
    _PILImage = None  # type: ignore[assignment]
    _PILLOW_AVAILABLE = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASELINE_DIR = BASE_DIR / "data" / "visual_baselines"
_DEDUP_DB = BASE_DIR / "data" / "visreg_filed.db"


# ---------------------------------------------------------------------------
# Baseline management
# ---------------------------------------------------------------------------

def _route_slug(route: str) -> str:
    return route.strip("/").replace("/", "__") or "root"


def capture_baseline(route: str, screenshot_path: str) -> Path:
    """Copy *screenshot_path* to the baseline store for *route*.

    Returns the path of the saved baseline file.
    """
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(screenshot_path)
    if not src.exists():
        raise FileNotFoundError(f"Screenshot not found: {src}")
    dest = BASELINE_DIR / f"{_route_slug(route)}.png"
    shutil.copy2(src, dest)
    logger.info("visual_regression: baseline saved %s → %s", route, dest)
    return dest


def list_baselines() -> Dict[str, Path]:
    """Return {route_slug: Path} for all stored baselines."""
    if not BASELINE_DIR.exists():
        return {}
    return {p.stem: p for p in sorted(BASELINE_DIR.glob("*.png"))}


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _avg_hash_pillow(path: Path) -> int:
    img = _PILImage.open(path).convert("L").resize((8, 8), _PILImage.LANCZOS)
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p >= avg else "0" for p in pixels)
    return int(bits, 2)


def _hamming(h1: int, h2: int) -> int:
    return bin(h1 ^ h2).count("1")


def _byte_hash(path: Path) -> int:
    """Coarse byte-level proxy — not perceptual, just a content fingerprint."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return int(digest[:16], 16)  # 64-bit int from first 8 bytes of sha256


def _compare_hashes(
    path_a: Path, path_b: Path, threshold: int
) -> Dict[str, object]:
    if _PILLOW_AVAILABLE:
        h1 = _avg_hash_pillow(path_a)
        h2 = _avg_hash_pillow(path_b)
        distance = _hamming(h1, h2)
        return {"distance": distance, "similar": distance <= threshold, "method": "avg_hash"}
    else:
        # Byte-diff fallback: 0 = identical, 1 = different (no gradation)
        same = path_a.read_bytes() == path_b.read_bytes()
        return {"distance": 0 if same else 64, "similar": same, "method": "byte_diff"}


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def compare(route: str, screenshot_path: str, threshold: int = 10) -> Dict[str, object]:
    """Compare *screenshot_path* against the stored baseline for *route*.

    Returns a result dict. If no baseline exists, similar=True (skip — can't compare).
    """
    slug = _route_slug(route)
    baseline = BASELINE_DIR / f"{slug}.png"
    shot = Path(screenshot_path)

    result: Dict[str, object] = {
        "route": route,
        "baseline_exists": baseline.exists(),
        "similar": True,
        "distance": 0,
        "threshold": threshold,
        "method": "none",
        "baseline_path": str(baseline),
        "screenshot_path": str(shot),
    }

    if not baseline.exists():
        logger.debug("visual_regression: no baseline for %s — skipping compare", route)
        return result

    if not shot.exists():
        result["similar"] = False
        result["distance"] = -1
        result["method"] = "error"
        result["error"] = f"Screenshot not found: {shot}"
        logger.warning("visual_regression: screenshot missing for %s: %s", route, shot)
        return result

    try:
        hash_result = _compare_hashes(baseline, shot, threshold)
        result.update(hash_result)
    except Exception as exc:
        result["similar"] = False
        result["distance"] = -1
        result["method"] = "error"
        result["error"] = str(exc)
        logger.warning("visual_regression: compare error for %s: %s", route, exc)

    return result


# ---------------------------------------------------------------------------
# Batch regression run
# ---------------------------------------------------------------------------

def run_regression(
    routes_and_screenshots: List[Tuple[str, str]],
    threshold: int = 10,
) -> Dict[str, object]:
    """Run visual regression on a list of (route, screenshot_path) pairs."""
    regressions: List[Dict[str, object]] = []
    passed = 0

    for route, screenshot_path in routes_and_screenshots:
        result = compare(route, screenshot_path, threshold=threshold)
        if result.get("similar"):
            passed += 1
        else:
            regressions.append(result)

    total = len(routes_and_screenshots)
    return {
        "total": total,
        "passed": passed,
        "failed": len(regressions),
        "regressions": regressions,
    }


# ---------------------------------------------------------------------------
# Kanban task filing for regressions
# ---------------------------------------------------------------------------

def _ensure_dedup_db() -> sqlite3.Connection:
    _DEDUP_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DEDUP_DB))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS filed_routes "
        "(route TEXT PRIMARY KEY, task_id TEXT, filed_at TEXT)"
    )
    conn.commit()
    return conn


def _already_filed(dedup_conn: sqlite3.Connection, route: str) -> bool:
    row = dedup_conn.execute(
        "SELECT task_id FROM filed_routes WHERE route = ?", (route,)
    ).fetchone()
    return row is not None


def _mark_filed(dedup_conn: sqlite3.Connection, route: str, task_id: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    dedup_conn.execute(
        "INSERT OR REPLACE INTO filed_routes (route, task_id, filed_at) VALUES (?, ?, ?)",
        (route, task_id, now),
    )
    dedup_conn.commit()


def file_regression_tasks(regressions: List[Dict[str, object]]) -> List[str]:
    """File kanban bug tasks for visual regressions. Returns list of task IDs created."""
    if not regressions:
        return []

    filed: List[str] = []
    dedup_conn = _ensure_dedup_db()

    try:
        from tools.db.storage import get_connection
        from datetime import datetime, timezone

        conn = get_connection()
        now = datetime.now(timezone.utc).isoformat()

        for reg in regressions:
            route = str(reg.get("route", ""))
            if not route or _already_filed(dedup_conn, route):
                continue

            task_id = f"task-visreg-{uuid.uuid4().hex[:8]}"
            title = f"[VIS-REG] {route} — visual change detected"
            distance = reg.get("distance", "?")
            method = reg.get("method", "?")
            desc = (
                f"Visual regression detected on `{route}`.\n\n"
                f"**Hash distance:** {distance} (threshold: {reg.get('threshold', '?')})\n"
                f"**Method:** {method}\n"
                f"**Baseline:** `{reg.get('baseline_path', '?')}`\n"
                f"**Screenshot:** `{reg.get('screenshot_path', '?')}`\n\n"
                "Steps to remediate:\n"
                "1. Open both images side-by-side and identify the visual change.\n"
                "2. If the change is intentional (CSS update, redesign): update the baseline:\n"
                "   ```\n"
                f"   python tools/testing/visual_regression.py "
                f"--capture-baseline route={route}:screenshot=<path>\n"
                "   ```\n"
                "3. If the change is a regression: identify the commit that introduced it "
                "   and revert or fix the relevant template/CSS.\n"
                "4. Re-run comparison and close this task when it passes.\n"
            )
            conn.execute(
                """
                INSERT INTO kanban_tasks
                    (id, title, description, task_type, priority, status,
                     scheduled_at, created_at, updated_at, dispatch_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (task_id, title, desc, "bug", "medium", "backlog",
                 now, now, now, "visual_regression"),
            )
            conn.commit()
            _mark_filed(dedup_conn, route, task_id)
            filed.append(task_id)
            logger.warning("visual_regression: filed kanban task %s for %s", task_id, route)

    except Exception as exc:
        logger.warning("visual_regression: kanban filing failed: %s", exc)
    finally:
        dedup_conn.close()

    return filed


# ---------------------------------------------------------------------------
# Genesis reflex entry point
# ---------------------------------------------------------------------------

def run(config: dict, state: object) -> dict:
    """Genesis reflex integration.

    config keys:
      routes_and_screenshots: [[route, screenshot_path], ...]
      threshold: int (default 10)
      file_tasks: bool (default true)
    """
    pairs_raw = config.get("routes_and_screenshots", [])
    threshold = int(config.get("threshold", 10))
    file_tasks = bool(config.get("file_tasks", True))

    if not pairs_raw:
        return {"total": 0, "passed": 0, "failed": 0, "regressions": []}

    pairs: List[Tuple[str, str]] = [(str(r), str(s)) for r, s in pairs_raw]
    result = run_regression(pairs, threshold=threshold)

    if file_tasks and result["regressions"]:
        task_ids = file_regression_tasks(result["regressions"])  # type: ignore[arg-type]
        result["kanban_task_ids"] = task_ids  # type: ignore[assignment]

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_pair(s: str) -> Tuple[str, str]:
    """Parse 'route=/foo:screenshot=path.png' → ('/foo', 'path.png')."""
    parts = dict(kv.split("=", 1) for kv in s.split(":") if "=" in kv)
    route = parts.get("route", "")
    screenshot = parts.get("screenshot", "")
    if not route or not screenshot:
        raise argparse.ArgumentTypeError(
            f"Expected 'route=<path>:screenshot=<path>', got: {s!r}"
        )
    return route, screenshot


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ICDEV Visual Regression Tester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--compare",
        metavar="route=R:screenshot=P",
        action="append",
        default=[],
        help="Compare screenshot against baseline (repeatable)",
    )
    parser.add_argument(
        "--capture-baseline",
        metavar="route=R:screenshot=P",
        action="append",
        default=[],
        dest="capture",
        help="Capture/update baseline for route (repeatable)",
    )
    parser.add_argument("--list-baselines", action="store_true")
    parser.add_argument("--threshold", type=int, default=10,
                        help="Hamming distance threshold (default 10 / 64 bits)")
    parser.add_argument("--file-tasks", action="store_true", default=True,
                        help="File kanban bug tasks for regressions (default on)")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    output: dict = {}

    # Capture baselines
    for pair_str in args.capture:
        try:
            route, screenshot = _parse_pair(pair_str)
            saved = capture_baseline(route, screenshot)
            output.setdefault("baselines_captured", []).append(
                {"route": route, "saved_to": str(saved)}
            )
        except Exception as exc:
            output.setdefault("errors", []).append(str(exc))

    # List baselines
    if args.list_baselines:
        output["baselines"] = {slug: str(p) for slug, p in list_baselines().items()}

    # Compare
    if args.compare:
        pairs: List[Tuple[str, str]] = []
        for pair_str in args.compare:
            try:
                pairs.append(_parse_pair(pair_str))
            except Exception as exc:
                output.setdefault("errors", []).append(str(exc))

        if pairs:
            reg_result = run_regression(pairs, threshold=args.threshold)
            if args.file_tasks and reg_result["regressions"]:
                task_ids = file_regression_tasks(reg_result["regressions"])  # type: ignore[arg-type]
                reg_result["kanban_task_ids"] = task_ids  # type: ignore[assignment]
            output["regression"] = reg_result

    if args.as_json:
        print(json.dumps(output, indent=2))
    else:
        if "baselines_captured" in output:
            for item in output["baselines_captured"]:
                print(f"[BASELINE] {item['route']} → {item['saved_to']}")
        if "baselines" in output:
            for slug, path in output["baselines"].items():
                print(f"  {slug}: {path}")
        if "regression" in output:
            r = output["regression"]
            print(f"Visual regression: {r['passed']}/{r['total']} passed, {r['failed']} failed")
            for reg in r.get("regressions", []):
                print(f"  [FAIL] {reg['route']}  distance={reg.get('distance', '?')}  method={reg.get('method', '?')}")

    if not _PILLOW_AVAILABLE:
        logger.warning(
            "visual_regression: Pillow not installed — using byte-diff fallback. "
            "Install Pillow for perceptual hash: pip install Pillow"
        )

    return 0 if not output.get("regression", {}).get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
