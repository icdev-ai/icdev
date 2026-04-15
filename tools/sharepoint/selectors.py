# CUI // SP-CTI
"""tools/sharepoint/selectors — Centralized DOM selectors for SharePoint Server.

One place for every CSS selector used by the Selenium fallback path
(``tools/sharepoint/browser_fallback.py``, Phase F / P4.2).  When Microsoft
ships a SharePoint CU that reshuffles the classic markup, update this file and
the breakage is fixed everywhere.

Last verified against:
    SharePoint Server Subscription Edition (SE) — Feature Update January 2024
    SharePoint Server 2019 (RTM + CU October 2023)
    SharePoint Server 2016 (RTM + CU November 2023)

Selector format
---------------
All constants are CSS selector strings compatible with Selenium's
``By.CSS_SELECTOR`` and ``driver.find_element(By.CSS_SELECTOR, CONST)``.
XPath alternatives are provided as ``*_XPATH`` siblings only where CSS cannot
express the required predicate (e.g., text-based or sibling relationships that
CSS4 ``:has()`` does not yet cover in older WebDriver implementations).

SharePoint experience model
---------------------------
Classic experience (all three versions):
    Rendered server-side; selectors rely on ``ms-*`` CSS classes and ASP.NET
    control-ID patterns like ``ctl00_PlaceHolderMain_*``.

Modern / SPFx experience (2019 and SE only):
    Client-side SPFx; selectors use ``data-automation-id`` attributes which
    Microsoft commits to as stable across CUs.  Avoid class-name selectors for
    SPFx — they are hashed and change with every build.

Usage
-----
    from selenium.webdriver.common.by import By
    from tools.sharepoint.selectors import LIST_ITEMS_TABLE, ITEM_ROW

    table = driver.find_element(By.CSS_SELECTOR, LIST_ITEMS_TABLE)
    rows  = table.find_elements(By.CSS_SELECTOR, ITEM_ROW)
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Version annotation stored at runtime (consumed by browser_fallback.py and
# tests to surface the baseline in log output).
# ---------------------------------------------------------------------------

LAST_VERIFIED_VERSION: str = (
    "SharePoint Server SE (Jan-2024 CU) / 2019 (Oct-2023 CU) / 2016 (Nov-2023 CU)"
)

# ===========================================================================
# Classic experience — List View (allitems.aspx / dispform.aspx)
# ===========================================================================

# The outer ``<table>`` rendered by the classic List View web part.
# Multiple web parts can appear on one page; use ``find_elements`` and filter
# by context if needed.
LIST_ITEMS_TABLE: str = "table.ms-listviewtable"

# Every data row in the list view.  Header rows carry class ``ms-viewheadertr``;
# they are excluded here — ITEM_ROW matches only hoverable content rows.
ITEM_ROW: str = "tr.ms-itmhover"

# Header row (column titles) within the list view table.
HEADER_ROW: str = "tr.ms-viewheadertr"

# Title / name anchor in a list item row.  This is the primary navigable link
# for both list items and document names in a library.
ITEM_TITLE_ANCHOR: str = "a.ms-listlink"

# Standard value cell (``<td>`` containing a field value).
ITEM_VALUE_CELL: str = "td.ms-vb2"

# Checkbox selection cell (leftmost column in multi-select lists).
ITEM_CHECKBOX_CELL: str = "td.ms-vb-itmcbx"

# ===========================================================================
# Classic experience — Document Library
# ===========================================================================

# Row containing a document file (same row class as ITEM_ROW; kept as a
# separate named constant so intent is self-documenting at call sites).
DOCUMENT_LINK_ROW: str = "tr.ms-itmhover"

# File-type icon cell (the column immediately to the left of the filename).
# Presence of this cell distinguishes a document library row from a plain
# list row when both are rendered on the same page.
DOCUMENT_ICON_CELL: str = "td.ms-vb-icon"

# The ``<a>`` element that opens a document.  Use with ``find_element``
# *within* a DOCUMENT_LINK_ROW to get only the anchor of that row.
DOCUMENT_LINK_ANCHOR: str = "a.ms-listlink"

# Full name cell (combines the icon sibling and the styled text cell).
DOCUMENT_NAME_CELL: str = "td.ms-cellstyle.ms-vb2"

# ===========================================================================
# Classic experience — Pagination
# ===========================================================================

# Container ``<table>`` for the prev/next pager bar below the list.
PAGER: str = "table.ms-SPPageDataPager"

# "Next" page navigation link.  The id includes ``NextPage`` as a substring;
# CSS substring matching (``*=``) handles the ctl00_... prefix variation.
NEXT_PAGE_LINK: str = "a[id*='NextPage']"

# "Previous" page navigation link.
PREV_PAGE_LINK: str = "a[id*='PrevPage']"

# ===========================================================================
# Forms-Based Authentication (FBA) — /_forms/default.aspx
# ===========================================================================

# The ``<form>`` that SharePoint renders when FBA is configured and the REST
# client is not pre-authenticated.  The browser_fallback.py driver detects
# this form to decide whether a credentials submission is required before
# scraping can begin.
AUTH_REDIRECT_FORM: str = "form[action*='/_forms/default.aspx']"

# Username text input on the FBA login page.
# Control ID is stable across SP 2016/2019/SE for the default FBA provider.
AUTH_USERNAME_INPUT: str = "#ctl00_PlaceHolderMain_signInControl_UserName"

# Password input on the FBA login page.
AUTH_PASSWORD_INPUT: str = "#ctl00_PlaceHolderMain_signInControl_password"

# Submit / Sign In button on the FBA login page.
AUTH_LOGIN_BUTTON: str = "input[id*='signInControl'][type='submit']"

# ASP.NET hidden fields required for form submissions (ViewState, EventValidation).
# Must be included in any POST that mimics a server-side postback.
VIEWSTATE_INPUT: str = "input[type='hidden'][name='__VIEWSTATE']"
EVENTVALIDATION_INPUT: str = "input[type='hidden'][name='__EVENTVALIDATION']"

# Validation / error message area on the FBA login page (wrong credentials).
AUTH_ERROR_LABEL: str = ".ms-formvalidation"

# ===========================================================================
# Classic experience — Page Chrome (shared across all classic pages)
# ===========================================================================

# Root ASP.NET ``<form>`` that wraps the entire classic SharePoint page.
# Required as the WebDriver root when traversing page-scoped elements.
MAIN_FORM: str = "#aspnetForm"

# The main scrollable work area below the ribbon.
WORKSPACE: str = "#s4-workspace"

# Horizontal ribbon bar (the Office-style command ribbon).
RIBBON_ROW: str = "#s4-ribbonrow"

# Inner container of the ribbon (holds tab/button DOM).
RIBBON_CONTAINER: str = "#RibbonContainer"

# Quick Launch / left navigation panel.
LEFT_NAV_PANEL: str = "#s4-leftpanel"

# Title bar row (breadcrumb + site actions area).
TITLE_ROW: str = "#s4-titlerow"

# Site logo / icon link in the title row.
SITE_ICON_LINK: str = "a.ms-siteicon-a"

# ===========================================================================
# Classic experience — Web Part infrastructure
# ===========================================================================

# A web part zone ``<div>`` on the page (one per zone; multiple on wiki/webpart pages).
WEBPART_ZONE: str = "div.ms-webpart-zone"

# The content body ``<div>`` inside an individual web part chrome.
WEBPART_BODY: str = "div.ms-WPBody"

# ===========================================================================
# Classic experience — Search (/_layouts/15/osssearchresults.aspx)
# ===========================================================================

# Keyword search input box in the site search area (classic search center).
SEARCH_INPUT: str = "#ctl00_PlaceHolderSearchArea_ctl01_S_InputKeywords"

# Search submit button.
SEARCH_BUTTON: str = "#ctl00_PlaceHolderSearchArea_ctl01_S_SearchBtn"

# Individual result item row on the classic search results page.
SEARCH_RESULT_ROW: str = "div.ms-srch-item"

# ===========================================================================
# Modern (SPFx) experience — SharePoint Server 2019 / SE only
# ===========================================================================
# These selectors use ``data-automation-id`` attributes, which Microsoft
# commits to as stable test hooks.  Do NOT use class names for SPFx elements
# (they are build-hashed and change with every cumulative update).

# Root container of the SPFx List View web part.
MODERN_LIST_VIEW: str = "[data-automation-id='list-view']"

# Column header cells in the modern list view.
MODERN_LIST_HEADER_CELL: str = "[data-automation-id='FieldRenderer-name']"

# Individual cell in the modern list (wraps one field value).
MODERN_LIST_CELL: str = ".ms-List-cell"

# Title / name link inside a modern list row.
MODERN_ITEM_TITLE_LINK: str = "[data-automation-id='name-column-link-span']"

# Command bar at the top of a modern list / library.
MODERN_COMMAND_BAR: str = "[data-automation-id='commandBar']"

# "New" button in the modern command bar.
MODERN_NEW_BUTTON: str = "[data-automation-id='newCommand']"

# Breadcrumb navigation in the modern experience.
MODERN_BREADCRUMB: str = "[data-automation-id='breadcrumb']"

# ===========================================================================
# XPath alternatives (CSS cannot express these queries portably)
# ===========================================================================

# All ITEM_ROW elements whose title anchor text matches a given prefix.
# Use: driver.find_elements(By.XPATH, ITEM_ROW_BY_TITLE_XPATH % title)
ITEM_ROW_BY_TITLE_XPATH: str = (
    "//tr[contains(@class,'ms-itmhover')]"
    "[.//a[contains(@class,'ms-listlink') and contains(text(),'%s')]]"
)

# The FBA redirect form detected by action URL substring (XPath alternative).
AUTH_REDIRECT_FORM_XPATH: str = "//form[contains(@action,'/_forms/default.aspx')]"

# ===========================================================================
# Public surface
# ===========================================================================

__all__: list[str] = [
    "LAST_VERIFIED_VERSION",
    # Classic list view
    "LIST_ITEMS_TABLE",
    "ITEM_ROW",
    "HEADER_ROW",
    "ITEM_TITLE_ANCHOR",
    "ITEM_VALUE_CELL",
    "ITEM_CHECKBOX_CELL",
    # Document library
    "DOCUMENT_LINK_ROW",
    "DOCUMENT_ICON_CELL",
    "DOCUMENT_LINK_ANCHOR",
    "DOCUMENT_NAME_CELL",
    # Pagination
    "PAGER",
    "NEXT_PAGE_LINK",
    "PREV_PAGE_LINK",
    # Forms-based authentication
    "AUTH_REDIRECT_FORM",
    "AUTH_USERNAME_INPUT",
    "AUTH_PASSWORD_INPUT",
    "AUTH_LOGIN_BUTTON",
    "VIEWSTATE_INPUT",
    "EVENTVALIDATION_INPUT",
    "AUTH_ERROR_LABEL",
    # Page chrome
    "MAIN_FORM",
    "WORKSPACE",
    "RIBBON_ROW",
    "RIBBON_CONTAINER",
    "LEFT_NAV_PANEL",
    "TITLE_ROW",
    "SITE_ICON_LINK",
    # Web part infrastructure
    "WEBPART_ZONE",
    "WEBPART_BODY",
    # Search
    "SEARCH_INPUT",
    "SEARCH_BUTTON",
    "SEARCH_RESULT_ROW",
    # Modern (SPFx) — 2019/SE only
    "MODERN_LIST_VIEW",
    "MODERN_LIST_HEADER_CELL",
    "MODERN_LIST_CELL",
    "MODERN_ITEM_TITLE_LINK",
    "MODERN_COMMAND_BAR",
    "MODERN_NEW_BUTTON",
    "MODERN_BREADCRUMB",
    # XPath alternatives
    "ITEM_ROW_BY_TITLE_XPATH",
    "AUTH_REDIRECT_FORM_XPATH",
]
