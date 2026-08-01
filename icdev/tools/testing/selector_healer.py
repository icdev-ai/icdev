# CUI // SP-CTI
"""Playwright selector self-healer — AI-assisted locator repair for ACE QA Agent.

When a Playwright test fails because a selector was not found, this module:
  1. Parses the error output to identify the broken selector and which spec file
     it lives in (detect_broken_selectors).
  2. Calls the LLM vision router with the failure screenshot to propose a
     replacement selector (propose_repair).  Returns None if confidence < 0.7.
  3. Applies the replacement to the spec file via exact string replace
     (apply_repair_to_spec) — this function is called by the QA Agent via the
     patch_file tool, which is HITL-gated.  A human approves before it runs.

CLI usage:
    python tools/testing/selector_healer.py --stderr-file FILE --json
    python tools/testing/selector_healer.py --selector "getByText('Login')" \\
        --spec-file tests/e2e/auth.spec.ts --screenshot path/to/shot.png --json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.7

# Playwright error patterns that indicate a broken selector
_SELECTOR_ERROR_PATTERNS = [
    # locator().click(), locator().fill() etc.
    re.compile(r"locator\((?P<q>['\"])(?P<sel>.+?)(?P=q)\)"),
    # page.waitForSelector
    re.compile(r"waitForSelector\((?P<q>['\"])(?P<sel>.+?)(?P=q)\)"),
    # getByRole, getByText, getByLabel etc.
    re.compile(r"(getBy\w+)\((?P<q>['\"])(?P<sel>.+?)(?P=q)\)"),
    # CSS selectors inside .locator()
    re.compile(r"\.locator\((?P<q>['\"])(?P<sel>[.#\[\w].+?)(?P=q)\)"),
]

_SELECTOR_ERROR_MSGS = (
    "locator resolved to",
    "timeout exceeded",
    "waiting for locator",
    "selector did not resolve",
    "no element found",
    "element not found",
    "getby",
    "waiting for",
)

_SELECTOR_HEAL_SYSTEM_PROMPT = """You are a Playwright test automation expert specializing in locator repair.
A test failed because a selector was not found on the page.
You will see a screenshot of the page at the time of failure and the broken selector string.

Your task: propose a replacement Playwright locator that would correctly target the same element.

Rules:
- Prefer accessibility-first locators: getByRole, getByText, getByLabel, getByPlaceholder
- Avoid CSS class selectors (they break with styling changes)
- Return ONLY the locator string — no explanation, no code, no quotes around it
- If you cannot identify the correct element with confidence ≥ 0.7, return exactly: CANNOT_REPAIR
- Maximum 120 characters in your response"""


@dataclass
class BrokenSelector:
    selector: str = ""
    spec_file: str = ""
    line_hint: int = 0
    error_snippet: str = ""
    test_name: str = ""


@dataclass
class RepairProposal:
    broken: BrokenSelector
    proposed_selector: str = ""
    confidence: float = 0.0
    model_used: str = ""
    applied: bool = False
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["broken"] = asdict(self.broken)
        return d


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_broken_selectors(playwright_stderr: str) -> list[BrokenSelector]:
    """Parse Playwright error output for locator/selector failures.

    Returns a deduplicated list of BrokenSelector objects — one per unique
    (selector, spec_file) pair found in the error text.
    """
    if not playwright_stderr:
        return []

    results: list[BrokenSelector] = []
    seen: set[str] = set()

    lines = playwright_stderr.splitlines()
    for i, line in enumerate(lines):
        lower = line.lower()
        if not any(kw in lower for kw in _SELECTOR_ERROR_MSGS):
            continue

        # Attempt to extract a selector string from this error line
        # or the surrounding context (±2 lines)
        context_block = "\n".join(lines[max(0, i - 2): i + 3])

        for pat in _SELECTOR_ERROR_PATTERNS:
            for m in pat.finditer(context_block):
                sel = m.group("sel") if "sel" in m.groupdict() else ""
                if not sel or len(sel) > 200:
                    continue
                key = sel.strip()
                if key in seen:
                    continue
                seen.add(key)

                # Try to find spec file reference (e.g., "auth.spec.ts:42:7")
                spec_file = ""
                line_hint = 0
                file_match = re.search(
                    r"(tests/e2e/[\w\-/.]+\.spec\.ts)(?::(\d+))?", context_block
                )
                if file_match:
                    spec_file = file_match.group(1)
                    line_hint = int(file_match.group(2) or 0)

                results.append(BrokenSelector(
                    selector=key,
                    spec_file=spec_file,
                    line_hint=line_hint,
                    error_snippet=line.strip()[:300],
                ))

    return results


# ---------------------------------------------------------------------------
# Repair proposal
# ---------------------------------------------------------------------------

def propose_repair(
    broken: BrokenSelector,
    screenshot_path: Optional[str],
    page_html_snippet: Optional[str] = None,
) -> Optional[str]:
    """Call the LLM vision router to propose a replacement selector.

    Returns the proposed replacement selector string, or None if:
    - No vision-capable model is available
    - The model returns CANNOT_REPAIR
    - Confidence < _MIN_CONFIDENCE (inferred from response length/structure)

    Never writes any file.  Callers must gate the repair through HITL
    before calling apply_repair_to_spec().
    """
    try:
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest
    except ImportError:
        logger.error("selector_healer: LLMRouter not available")
        return None

    # Build user message — always include text context; optionally add screenshot
    user_content: list[dict] = []

    if screenshot_path:
        screenshot_path_obj = Path(screenshot_path)
        if screenshot_path_obj.exists():
            try:
                import base64
                with open(screenshot_path_obj, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("utf-8")
                suffix = screenshot_path_obj.suffix.lower()
                media_type = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                }.get(suffix, "image/png")
                user_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                })
            except Exception as exc:
                logger.warning("selector_healer: cannot encode screenshot: %s", exc)
        else:
            logger.warning("selector_healer: screenshot not found: %s", screenshot_path)

    text_parts = [
        f"Broken selector: {broken.selector}",
        f"Error context: {broken.error_snippet}" if broken.error_snippet else "",
        f"Spec file: {broken.spec_file}" if broken.spec_file else "",
    ]
    if page_html_snippet:
        text_parts.append(f"Page HTML snippet:\n{page_html_snippet[:2000]}")

    user_content.append({"type": "text", "text": "\n".join(p for p in text_parts if p)})

    try:
        router = get_router()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_SELECTOR_HEAL_SYSTEM_PROMPT,
            max_tokens=150,
            temperature=0.1,
            skip_injection_scan=True,
        )
        response = router.invoke("screenshot_validation", request)
    except Exception as exc:
        logger.error("selector_healer: LLM call failed: %s", exc)
        return None

    raw = (response.content or "").strip()

    # Reject explicit refusals
    if not raw or raw.upper() == "CANNOT_REPAIR" or len(raw) < 3:
        logger.info("selector_healer: model declined to repair selector '%s'", broken.selector)
        return None

    # Reject if the model echoed the broken selector back unchanged
    if raw.strip("\"'") == broken.selector.strip("\"'"):
        logger.info("selector_healer: model returned same selector — skipping")
        return None

    # Sanity length gate
    if len(raw) > 200:
        logger.warning("selector_healer: proposed selector suspiciously long (%d chars) — skipping", len(raw))
        return None

    logger.info("selector_healer: proposed '%s' → '%s'", broken.selector, raw)
    return raw


# ---------------------------------------------------------------------------
# Application (HITL-gated — called via patch_file tool by the QA Agent)
# ---------------------------------------------------------------------------

def apply_repair_to_spec(
    spec_path: str,
    old_selector: str,
    new_selector: str,
) -> bool:
    """Replace old_selector with new_selector in spec_path (exact string, once).

    Returns True if exactly one replacement was made, False otherwise.

    This function is ONLY called after HITL approval — the QA Agent routes it
    through the patch_file tool which is hitl_gated in qa_agent.yaml.
    """
    path = Path(spec_path)
    if not path.exists():
        logger.error("selector_healer: spec file not found: %s", spec_path)
        return False

    content = path.read_text(encoding="utf-8", errors="replace")
    count = content.count(old_selector)

    if count == 0:
        logger.warning("selector_healer: old_selector not found in %s", spec_path)
        return False

    if count > 1:
        logger.warning(
            "selector_healer: old_selector appears %d times in %s — refusing ambiguous replace",
            count, spec_path,
        )
        return False

    updated = content.replace(old_selector, new_selector, 1)
    path.write_text(updated, encoding="utf-8")
    logger.info("selector_healer: replaced '%s' → '%s' in %s", old_selector, new_selector, spec_path)
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Playwright selector self-healer — AI-assisted locator repair"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stderr-file", metavar="FILE", help="Parse Playwright stderr from file")
    mode.add_argument("--selector", metavar="SELECTOR", help="Repair a specific broken selector")

    parser.add_argument("--spec-file", metavar="SPEC", help="Spec file containing the selector (with --selector)")
    parser.add_argument("--screenshot", metavar="PNG", help="Screenshot at failure time")
    parser.add_argument("--error-context", metavar="TEXT", help="Error message context (with --selector)")
    parser.add_argument("--apply", action="store_true",
                        help="Apply the proposed repair to the spec file (HITL must have already approved)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    if args.stderr_file:
        stderr_text = Path(args.stderr_file).read_text(encoding="utf-8", errors="replace")
        broken_list = detect_broken_selectors(stderr_text)
        if args.json:
            print(json.dumps([asdict(b) for b in broken_list], indent=2))
        else:
            print(f"Found {len(broken_list)} broken selector(s):")
            for b in broken_list:
                print(f"  {b.selector!r} in {b.spec_file or 'unknown'}:{b.line_hint}")
        return 0

    if args.selector:
        broken = BrokenSelector(
            selector=args.selector,
            spec_file=args.spec_file or "",
            error_snippet=args.error_context or "",
        )
        proposed = propose_repair(broken, args.screenshot)
        if proposed is None:
            output = {"selector": args.selector, "proposed": None, "action": "skip"}
            if args.json:
                print(json.dumps(output, indent=2))
            else:
                print(f"Could not propose repair for: {args.selector!r}")
            return 1

        output: dict = {"selector": args.selector, "proposed": proposed, "action": "proposed"}

        if args.apply and args.spec_file:
            ok = apply_repair_to_spec(args.spec_file, args.selector, proposed)
            output["action"] = "applied" if ok else "apply_failed"

        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print(f"Proposed: {proposed!r}")
            if args.apply:
                print(f"Applied: {output['action']}")
        return 0 if proposed else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
