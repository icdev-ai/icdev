#!/usr/bin/env python3
# CUI // SP-CTI
"""tools/sharepoint/selectors — Centralized DOM selectors for SharePoint Server classic UI.

All CSS selectors and XPath expressions for browser-based fallback ingestion live
here.  Keeping them in one place means a single DOM drift fix propagates to every
caller instead of requiring a grep across multiple files.

**Last verified against:** SharePoint Server Subscription Edition (SE) / 2019 / 2016
classic view (``_layouts/15/`` family).  Modern/SPFx pages use a React shell;
detect them with ``IS_MODERN_PAGE`` before relying on classic selectors.

Usage::

    from tools.sharepoint.selectors import LIST_ITEMS_TABLE, DOCUMENT_LINK_ROW

    rows = driver.find_elements(By.CSS_SELECTOR, DOCUMENT_LINK_ROW)

All values are plain strings so they are compatible with Selenium
``find_element(By.CSS_SELECTOR, …)`` and ``find_element(By.XPATH, …)``.
XPath alternatives are noted in comments where the CSS selector alone is
insufficient.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Authentication / session
# ---------------------------------------------------------------------------

# Main ASP.NET form present on every page — used as a readiness probe after
# navigation to confirm the page is an authenticated SharePoint page and not
# a login redirect.
AUTH_ASPNET_FORM = "form#aspnetForm"

# Forms-based authentication (FBA) login form action contains /_layouts/
AUTH_FBA_FORM = "form[action*='/_layouts/']"

# FBA username and password fields (standard SharePoint FBA layout).
AUTH_USERNAME_INPUT = "#ctl00_PlaceHolderMain_signInControl_UserName"
AUTH_PASSWORD_INPUT = "#ctl00_PlaceHolderMain_signInControl_password"
AUTH_SUBMIT_BUTTON = "#ctl00_PlaceHolderMain_signInControl_login"

# Hidden request-digest field — CSRF token for REST POST calls made from a
# browser session.  Extract its value before issuing write operations.
REQUEST_DIGEST = "#__REQUESTDIGEST"

# Redirect detection: a page that is NOT a SharePoint page (pure login
# redirect or IDP hand-off) will lack this element.
AUTH_REDIRECT_FORM = AUTH_ASPNET_FORM

# ---------------------------------------------------------------------------
# Page-type detection
# ---------------------------------------------------------------------------

# Present on SPFx / modern pages.  If found, classic list selectors will NOT
# match; fall back to the REST API instead of attempting DOM scraping.
IS_MODERN_PAGE = "#spPageChromeAppDiv"

# Classic placeholder that wraps all main page content in 2016/2019/SE.
CLASSIC_PAGE_CONTENT = "#DeltaPlaceHolderMain, #ctl00_PlaceHolderMain"

# ---------------------------------------------------------------------------
# List view — items table
# ---------------------------------------------------------------------------

# The outer <table> that contains all list rows in a classic list view.
# XPath alt: //table[contains(@class,'ms-listviewtable')]
LIST_ITEMS_TABLE = "table.ms-listviewtable"

# Every data row inside LIST_ITEMS_TABLE.  SharePoint renders both
# ms-itmhover (odd) and ms-alternating (even striped) variants.
LIST_ITEM_ROW = "tr.ms-itmhover, tr.ms-alternating"

# Column header cells — useful for mapping column index → field name.
LIST_COLUMN_HEADER = "th.ms-vh-div, th.ms-vh2"

# The checkbox cell in each item row used for multi-select.
LIST_ROW_CHECKBOX = "td.ms-vb-itmcbx input[type='checkbox']"

# ---------------------------------------------------------------------------
# Document library
# ---------------------------------------------------------------------------

# A row in a document library view that contains a file link.
# Equivalent to LIST_ITEM_ROW but scoped inside a document library container.
# XPath alt: //tr[contains(@class,'ms-itmhover')]//a[contains(@href,'._layouts')]
DOCUMENT_LINK_ROW = "tr.ms-itmhover"

# The clickable file-name link inside a DOCUMENT_LINK_ROW.
# href points to the file's server-relative URL; title attribute holds the
# display name.
DOCUMENT_LINK_ANCHOR = "td.ms-vb-title a, td.ms-cellstyle.ms-vb-title a"

# Fallback anchor pattern for older SharePoint 2016 builds where the td class
# is ms-vb2 instead of ms-vb-title.
DOCUMENT_LINK_ANCHOR_LEGACY = "td.ms-vb2 a[href]"

# File-type icon image — its src attribute encodes the MIME type and extension
# when no explicit file-type column is available.
DOCUMENT_TYPE_ICON = "td.ms-vb-itmcbx ~ td img.ms-ftIcon, td img[src*='/_layouts/15/images/ic']"

# Modified-date cell — second to last <td> in most default document library views.
DOCUMENT_MODIFIED_CELL = "td.ms-vb-user"

# ---------------------------------------------------------------------------
# List view — navigation and pagination
# ---------------------------------------------------------------------------

# "Next page" link rendered by SharePoint's built-in pager.
PAGER_NEXT = "a[id*='NextPage'], .ms-paging a[href*='PageFirstRow']"

# "Previous page" link.
PAGER_PREV = "a[id*='PrevPage']"

# Total items count text node — not always present; availability depends on
# the view threshold setting.
PAGER_ITEM_COUNT = ".ms-paging"

# ---------------------------------------------------------------------------
# Toolbar / ribbon actions
# ---------------------------------------------------------------------------

# New item / upload button in document libraries (Ribbon tab: Documents > New).
TOOLBAR_UPLOAD_BUTTON = (
    "#Ribbon\\.Documents\\.New\\.AddDocument-Large, "
    "#Ribbon\\.Documents\\.New\\.AddDocument"
)

# Check Out / Check In buttons.
TOOLBAR_CHECKOUT = "#Ribbon\\.Documents\\.Manage\\.CheckOut-Large"
TOOLBAR_CHECKIN = "#Ribbon\\.Documents\\.Manage\\.CheckIn-Large"

# Generic "New item" button on a standard list.
TOOLBAR_NEW_ITEM = "#Ribbon\\.ListForm\\.Edit\\.Commit\\.Publish-Large"

# View selector drop-down (switch between All Items / My Items / etc.).
VIEW_SELECTOR_MENU = ".ms-viewSelectorMenu"

# ---------------------------------------------------------------------------
# Site navigation
# ---------------------------------------------------------------------------

# Left-hand quick-launch navigation container.
NAV_QUICK_LAUNCH = "#sideNavBox, .ms-core-navigation"

# Top / global navigation bar.
NAV_GLOBAL = "#globalNavBox, .ms-core-globalNavMenu"

# Suite bar (top-right user / app links).
NAV_SUITE_BAR = "#suiteBarLeft, #suiteBar"

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

# The main search input on the SharePoint search centre page.
SEARCH_BOX = "#ms-srch-sbq, input[id*='ctl00_PlaceHolderSearchArea']"

# Search results container.
SEARCH_RESULTS_CONTAINER = "#ResultsCenter, .ms-srch-results"

# Individual result item.
SEARCH_RESULT_ITEM = ".ms-srch-item, .ms-srch-result"

# Result title link.
SEARCH_RESULT_TITLE = ".ms-srch-item-title a, .ms-srch-result-title a"

# ---------------------------------------------------------------------------
# Error / status messages
# ---------------------------------------------------------------------------

# Inline error label rendered by SharePoint web parts.
ERROR_LABEL = ".ms-error, #ctl00_PlaceHolderMain_LabelMessage"

# Access-denied page heading — indicates the current session lacks permission
# to the requested resource.
ACCESS_DENIED_HEADING = "h1.ms-accentText, .ms-error h2"

# Page-level status bar (used for soft warnings).
STATUS_BAR = "#ms-status-bar"

# ---------------------------------------------------------------------------
# Form / item display form
# ---------------------------------------------------------------------------

# Field labels in a standard display/edit form.
FORM_FIELD_LABEL = "td.ms-formlabel h3.ms-standardheader"

# Field values in a display form.
FORM_FIELD_VALUE = "td.ms-formbody"

# Save button on an edit/new item form.
FORM_SAVE_BUTTON = "input[id$='_ButtonSave'], input[value='Save']"

# Cancel link on an edit/new item form.
FORM_CANCEL_LINK = "a[id$='_GoBackLink']"
