# CUI // SP-CTI
"""tools/sharepoint/browser_fallback.py — Selenium fallback for classic SharePoint pages.

Phase F / P4.2: When the REST API cannot render a classic web part / SPFx page,
this module drives Edge/Chrome via ``tools.browser.driver_manager.get_driver()``
to extract structured rows as plain dicts.

Gated by ``args/sharepoint.yaml`` key ``sharepoint.fallback_enabled`` (default
``false``).  Raises :class:`FallbackDisabledError` when the gate is off so
callers can decide whether to surface a warning or skip silently.

Public surface::

    from tools.sharepoint.browser_fallback import fetch_classic_page, FallbackDisabledError

    rows = fetch_classic_page(url, {"Title": "td.ms-vb-title", "Modified": "td.ms-vb-datetime"})
    # -> [{"Title": "...", "Modified": "..."}, ...]
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from pathlib import Path
from typing import Any, Dict, List

import yaml

logger = get_logger("icdev.sharepoint.browser_fallback")

# ── Config ────────────────────────────────────────────────────────────────────

_ARGS_FILE = Path(__file__).resolve().parent.parent.parent / "args" / "sharepoint.yaml"


def _load_sp_config() -> Dict[str, Any]:
    """Return the ``sharepoint:`` block from args/sharepoint.yaml, or {} if absent."""
    if not _ARGS_FILE.exists():
        logger.warning("browser_fallback: %s not found; treating fallback as disabled", _ARGS_FILE)
        return {}
    raw = yaml.safe_load(_ARGS_FILE.read_text(encoding="utf-8")) or {}
    return raw.get("sharepoint", {})


# ── Exception ─────────────────────────────────────────────────────────────────


class FallbackDisabledError(Exception):
    """Raised when ``sharepoint.fallback_enabled`` is ``false`` in args/sharepoint.yaml.

    The caller should handle this gracefully — either surface a configuration
    warning or skip Selenium extraction entirely.  This is not a hard failure;
    it is an intentional configuration gate.
    """


# ── Public API ────────────────────────────────────────────────────────────────


def fetch_classic_page(url: str, selectors: Dict[str, str]) -> List[Dict[str, str]]:
    """Navigate to *url* with a Selenium driver and extract rows.

    Parameters
    ----------
    url:
        Fully-qualified URL of the classic SharePoint page to render.  Must be
        reachable from the ICDEV host with whatever auth the current session
        provides (NTLM SSO, Kerberos, etc.).
    selectors:
        Mapping of ``{field_name: css_selector}``.  For every field the driver
        collects the ``text`` property of all matching elements.  Results are
        zipped by position to produce row dicts — if a selector matches fewer
        elements than the maximum, shorter columns are padded with ``""``.

    Returns
    -------
    list[dict[str, str]]
        One dict per extracted row, keyed by the names in *selectors*.
        Returns an empty list when selectors match nothing.

    Raises
    ------
    FallbackDisabledError
        When ``sharepoint.fallback_enabled`` is ``false`` (the default) in
        ``args/sharepoint.yaml``.
    """
    cfg = _load_sp_config()
    if not cfg.get("fallback_enabled", False):
        raise FallbackDisabledError(
            "sharepoint.fallback_enabled is false in args/sharepoint.yaml. "
            "Set it to true to enable Selenium-based classic-page extraction."
        )

    # Lazy import — avoids Selenium startup cost when fallback is disabled.
    from tools.browser.driver_manager import get_driver  # noqa: PLC0415

    driver = get_driver()
    try:
        logger.info("browser_fallback: navigating to %s", url)
        driver.get(url)

        # Collect element texts per field
        columns: Dict[str, List[str]] = {}
        for field, selector in selectors.items():
            elements = driver.find_elements("css selector", selector)
            columns[field] = [el.text for el in elements]
            logger.debug(
                "browser_fallback: selector %r matched %d element(s)",
                selector,
                len(elements),
            )

        if not columns:
            return []

        max_rows = max(len(vals) for vals in columns.values())
        if max_rows == 0:
            return []

        rows: List[Dict[str, str]] = []
        for i in range(max_rows):
            row = {
                field: (vals[i] if i < len(vals) else "")
                for field, vals in columns.items()
            }
            rows.append(row)

        logger.info("browser_fallback: extracted %d row(s) from %s", len(rows), url)
        return rows

    finally:
        try:
            driver.quit()
        except Exception:  # pragma: no cover — quit errors are non-fatal
            pass
