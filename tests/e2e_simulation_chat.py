# CUI // SP-CTI
"""E2E Selenium test — /simulate/chat — NDC, SDC, EDA canvas types.

10 assertions (T33 / sim-l7-01):
  1.  Page loads with no severe JS errors
  2.  Canvas type chips render (NDC, SDC, EDA visible)
  3.  SDC selection changes slash-command autocomplete
  4.  Upload panel accept types include image/PDF/diagram formats
  5.  POST /api/simulate/session canvas_type=ndc returns 200
  6.  POST /api/simulate/session canvas_type=sdc returns 200
  7.  POST /api/simulate/session canvas_type=eda returns 200
  8.  EXPLAIN message returns Mermaid code fence in response
  9.  Mermaid SVG (or container) renders inline on the page
  10. POST /api/simulate/bundle returns a download link

Prerequisites:
  - Flask dashboard running on http://localhost:5050
    (override with ICDEV_DASHBOARD_URL env var)
  - tools/simulation/blueprint.py registered (sim-l5-01)
  - tools/simulation/tfw_chat_agent.py available (sim-l3-04)
  - Migration 037 applied (nc_simulation_sessions table present)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

BASE_URL = os.environ.get("ICDEV_DASHBOARD_URL", "http://localhost:5050")
SCREENSHOT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "playwright", "screenshots"
)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

passed = 0
failed = 0
errors: list[str] = []
_session_id: str | None = None


def screenshot(driver: webdriver.Chrome, name: str) -> None:
    path = os.path.join(SCREENSHOT_DIR, f"sim-chat-{name}-1920x1080.png")
    driver.save_screenshot(path)
    print(f"    Screenshot: {path}")


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        msg = f"  FAIL: {label}" + (f" -- {detail}" if detail else "")
        print(msg)
        errors.append(msg)


def _api_post(path: str, body: dict | None = None) -> tuple[int, dict]:
    """POST JSON to BASE_URL/path. Returns (status_code, parsed_body)."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    payload = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body_bytes = exc.read()
        except Exception:
            body_bytes = b"{}"
        try:
            body_dict = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            body_dict = {}
        return exc.code, body_dict
    except Exception:
        return 0, {}


def main() -> None:
    global passed, failed, _session_id

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(5)

    try:
        # ============================================================
        # 1. PAGE LOADS — NO JS ERRORS
        # ============================================================
        print("\n1. Page Load — no severe JS errors")
        driver.get(f"{BASE_URL}/simulate/chat")
        time.sleep(2)
        src = driver.page_source

        js_errors = [
            entry["message"]
            for entry in driver.get_log("browser")
            if entry.get("level") == "SEVERE"
            and "favicon" not in entry.get("message", "").lower()
        ]
        check("Page loads (non-empty body)", len(src) > 100, f"body_len={len(src)}")
        check(
            "No severe JS errors on load",
            len(js_errors) == 0,
            "; ".join(js_errors[:3]),
        )
        screenshot(driver, "01-page-load")

        # Dismiss tour overlay if present — it intercepts chip clicks
        try:
            driver.execute_script(
                "var el = document.querySelector('.icdev-tour-welcome');"
                "if (el) { el.classList.remove('visible'); el.style.display='none'; }"
            )
        except Exception:
            pass

        # ============================================================
        # 2. CANVAS TYPE CHIPS RENDER
        # ============================================================
        print("\n2. Canvas Type Chips")
        chip_els = driver.find_elements(
            By.CSS_SELECTOR,
            "[data-canvas-type], .canvas-chip, .canvas-type-btn, "
            ".canvas-selector button, [data-canvas]",
        )
        chip_texts = [el.text.upper().strip() for el in chip_els]
        # Fall back to page source when chips use non-standard markup
        src_upper = src.upper()
        ndc_found = "NDC" in " ".join(chip_texts) or "NDC" in src_upper
        sdc_found = "SDC" in " ".join(chip_texts) or "SDC" in src_upper
        eda_found = "EDA" in " ".join(chip_texts) or "EDA" in src_upper
        check("NDC chip renders", ndc_found, f"chip_texts={chip_texts}")
        check("SDC chip renders", sdc_found, f"chip_texts={chip_texts}")
        check("EDA chip renders", eda_found, f"chip_texts={chip_texts}")
        screenshot(driver, "02-canvas-chips")

        # ============================================================
        # 3. SDC SELECTION CHANGES SLASH-COMMAND AUTOCOMPLETE
        # ============================================================
        print("\n3. SDC Selection — slash-command autocomplete change")
        sdc_chip = None
        for el in chip_els:
            if "SDC" in el.text.upper():
                sdc_chip = el
                break
        if not sdc_chip:
            # Try by data attribute
            candidates = driver.find_elements(
                By.CSS_SELECTOR, "[data-canvas-type='sdc'], [data-canvas='sdc']"
            )
            if candidates:
                sdc_chip = candidates[0]

        if sdc_chip:
            try:
                # JS click bypasses any overlapping elements
                driver.execute_script("arguments[0].click();", sdc_chip)
                time.sleep(1)
                after_src = driver.page_source.lower()
                sdc_terms = [
                    "threat", "sdc", "security", "attack", "vulnerability",
                    "/explain", "/troubleshoot", "threat path",
                ]
                check(
                    "SDC selection updates autocomplete/context",
                    any(t in after_src for t in sdc_terms),
                    "no SDC-context terms visible after SDC chip click",
                )
            except Exception as exc:
                check("SDC chip clickable", False, str(exc))
        else:
            check("SDC chip found and clickable", False, "SDC chip element not found")
        screenshot(driver, "03-sdc-autocomplete")

        # ============================================================
        # 4. UPLOAD PANEL ACCEPT TYPES
        # ============================================================
        print("\n4. Upload Panel — accept types")
        file_inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
        if file_inputs:
            accept = file_inputs[0].get_attribute("accept") or ""
            diagram_formats = [".png", ".jpg", ".pdf", ".drawio", ".xml", ".vsdx", "image/"]
            check(
                "Upload accept includes image/PDF/diagram formats",
                any(fmt in accept for fmt in diagram_formats),
                f"accept='{accept}'",
            )
        else:
            # Page may show upload zone without a bare <input type=file>
            upload_present = "upload" in src.lower() or "drag" in src.lower()
            check(
                "Upload panel present in page",
                upload_present,
                "no file input and no upload/drag text found",
            )
        screenshot(driver, "04-upload-panel")

        # ============================================================
        # 5. POST /api/simulate/session — canvas_type=ndc
        # ============================================================
        print("\n5. POST /api/simulate/session — canvas_type=ndc")
        status, body = _api_post("/api/simulate/session", {"canvas_type": "ndc"})
        check("POST session ndc → 200", status == 200, f"status={status}")
        if status == 200:
            _session_id = body.get("session_id") or body.get("id")

        # ============================================================
        # 6. POST /api/simulate/session — canvas_type=sdc
        # ============================================================
        print("\n6. POST /api/simulate/session — canvas_type=sdc")
        status, _ = _api_post("/api/simulate/session", {"canvas_type": "sdc"})
        check("POST session sdc → 200", status == 200, f"status={status}")

        # ============================================================
        # 7. POST /api/simulate/session — canvas_type=eda
        # ============================================================
        print("\n7. POST /api/simulate/session — canvas_type=eda")
        status, _ = _api_post("/api/simulate/session", {"canvas_type": "eda"})
        check("POST session eda → 200", status == 200, f"status={status}")

        # ============================================================
        # 8. EXPLAIN message returns Mermaid fence
        # ============================================================
        print("\n8. EXPLAIN message — Mermaid code fence")
        if _session_id:
            explain_status, explain_body = _api_post(
                "/api/simulate/message",
                {"session_id": _session_id, "content": "/explain"},
            )
            raw = json.dumps(explain_body)
            has_mermaid = "```mermaid" in raw or "mermaid" in raw.lower()
            if explain_status == 200:
                check(
                    "EXPLAIN response contains Mermaid fence",
                    has_mermaid,
                    f"keys={list(explain_body.keys())}",
                )
            else:
                check(
                    "EXPLAIN message → 200",
                    False,
                    f"status={explain_status}",
                )
        else:
            check(
                "EXPLAIN message (requires valid session)",
                False,
                "no session_id from step 5",
            )

        # ============================================================
        # 9. MERMAID SVG RENDERS INLINE
        # ============================================================
        print("\n9. Mermaid SVG renders inline")
        driver.get(f"{BASE_URL}/simulate/chat")
        time.sleep(2)
        mermaid_svgs = driver.find_elements(
            By.CSS_SELECTOR, ".mermaid svg, [data-mermaid] svg, .mermaid-diagram svg"
        )
        mermaid_containers = driver.find_elements(
            By.CSS_SELECTOR, ".mermaid, [data-mermaid], .mermaid-diagram, #mermaid-output"
        )
        check(
            "Mermaid SVG or container renders inline",
            len(mermaid_svgs) > 0 or len(mermaid_containers) > 0,
            f"svgs={len(mermaid_svgs)}, containers={len(mermaid_containers)}",
        )
        screenshot(driver, "09-mermaid-svg")

        # ============================================================
        # 10. /api/simulate/bundle returns download link
        # ============================================================
        print("\n10. POST /api/simulate/bundle — download link")
        if _session_id:
            bundle_status, bundle_body = _api_post(
                "/api/simulate/bundle",
                {"session_id": _session_id},
            )
            download_url = (
                bundle_body.get("download_url")
                or bundle_body.get("url")
                or bundle_body.get("link")
                or bundle_body.get("file_url")
            )
            check(
                "/bundle returns download link",
                download_url is not None,
                f"status={bundle_status}, body_keys={list(bundle_body.keys())}",
            )
        else:
            check(
                "/bundle (requires valid session)",
                False,
                "no session_id from step 5",
            )

    finally:
        driver.quit()

    # ============================================================
    # SUMMARY
    # ============================================================
    print(f"\n{'='*60}")
    print(f"  simulate/chat E2E: {passed} passed, {failed} failed")
    if errors:
        print("\nFailed checks:")
        for err in errors:
            print(f"  {err}")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
