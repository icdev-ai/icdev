# CUI // SP-CTI
"""NDC Vendor Stencil Catalog.

Provides catalog listings for each supported vendor.
Cisco catalog is fetched live from cisco.com.
Juniper, AWS, and Azure catalogs are static known-good entries.
Users may also define custom vendors and supply their own URLs.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from html.parser import HTMLParser
from urllib.request import Request, urlopen  # nosec B310

logger = get_logger("icdev.network.stencil_catalog")

CISCO_LISTING_URL = "https://www.cisco.com/c/en/us/products/visio-stencil-listing.html"

# ── Static Cisco catalog (curated fallback — used when live fetch fails) ─────

CISCO_STATIC_CATALOG: list[dict] = [
    # Routers
    {"name": "Cisco Routers — ISR 4000 Series", "category": "Routers", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/routers-cisco-4000-series-isr.zip", "size": "~4 MB", "format": "vss_zip", "updated": ""},
    {"name": "Cisco Routers — ASR 9000 Series", "category": "Routers", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/routers-cisco-asr-9000-series.zip", "size": "~3 MB", "format": "vss_zip", "updated": ""},
    {"name": "Cisco Routers — Catalyst 8000 Series", "category": "Routers", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/routers-cisco-catalyst-8000.zip", "size": "~2 MB", "format": "vss_zip", "updated": ""},
    {"name": "Cisco Routers — ISR 1000 Series", "category": "Routers", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/routers-cisco-1000-series-isr.zip", "size": "~2 MB", "format": "vss_zip", "updated": ""},
    # Switches
    {"name": "Cisco Switches — Catalyst 9000 Series", "category": "Switches", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/switches-cisco-catalyst-9000.zip", "size": "~5 MB", "format": "vss_zip", "updated": ""},
    {"name": "Cisco Switches — Nexus 9000 Series", "category": "Switches", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/switches-cisco-nexus-9000.zip", "size": "~76 MB", "format": "vss_zip", "updated": ""},
    {"name": "Cisco Switches — Nexus 7000 Series", "category": "Switches", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/switches-cisco-nexus-7000.zip", "size": "~8 MB", "format": "vss_zip", "updated": ""},
    {"name": "Cisco Switches — Catalyst 4500-X Series", "category": "Switches", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/switches-cisco-catalyst-4500-x.zip", "size": "~2 MB", "format": "vss_zip", "updated": ""},
    # Security
    {"name": "Cisco Secure Firewall — 1200 Series", "category": "Security", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/cisco-secure-firewall-1200-series.zip", "size": "~2 MB", "format": "vss_zip", "updated": "2026-03-02"},
    {"name": "Cisco Secure Firewall — 6100 Series", "category": "Security", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/cisco-secure-firewall-6100-series.zip", "size": "~3 MB", "format": "vss_zip", "updated": "2026-03-02"},
    {"name": "Cisco ASA 5500-X Series", "category": "Security", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/security-cisco-asa-5500-x.zip", "size": "~2 MB", "format": "vss_zip", "updated": ""},
    {"name": "Cisco Meraki Security", "category": "Security", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/security-meraki.zip", "size": "~3 MB", "format": "vss_zip", "updated": ""},
    # Wireless
    {"name": "Cisco Wireless — Catalyst Access Points", "category": "Wireless", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/wireless-cisco-catalyst-access-points.zip", "size": "~2 MB", "format": "vss_zip", "updated": ""},
    {"name": "Cisco Wireless — Meraki", "category": "Wireless", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/wireless-meraki.zip", "size": "~2 MB", "format": "vss_zip", "updated": ""},
    # Compute
    {"name": "Cisco UCS — Unified Computing System", "category": "Servers / UCS", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/servers-unified-computing.zip", "size": "~144 MB", "format": "vss_zip", "updated": "2025-04-03"},
    # Collaboration
    {"name": "Cisco Collaboration Endpoints", "category": "Collaboration", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/collaboration-endpoints.zip", "size": "~2 MB", "format": "vss_zip", "updated": ""},
    # Interfaces
    {"name": "Cisco Interfaces and Modules", "category": "Interfaces & Modules", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/cisco-interfaces-modules.zip", "size": "~5 MB", "format": "vss_zip", "updated": ""},
    # Optical
    {"name": "Cisco Optical — NCS 4000", "category": "Optical Networking", "url": "https://www.cisco.com/c/dam/assets/prod/visio/visio/optical_networking_ncs_4000.zip", "size": "~41 MB", "format": "vss_zip", "updated": "2025-04-03"},
]

# ── Static catalogs ───────────────────────────────────────────────────────────

JUNIPER_CATALOG: list[dict] = [
    {
        "name": "Juniper Networks — Full Stencil Pack",
        "category": "All Products",
        "url": "https://www.juniper.net/documentation/images/visio-stencils.zip",
        "size": "~15 MB",
        "format": "vssx",
        "updated": "",
    },
]

# AWS Architecture Icons — official free download (size / hash change quarterly)
AWS_CATALOG: list[dict] = [
    {
        "name": "AWS Architecture Icons — Full Pack (Q4 2023)",
        "category": "All Services",
        "url": "https://d1.awsstatic.com/webteam/architecture-icons/q4-2023/Asset-Package_10242023.e47d9fa5db10be08af8ae6e44cee5b7e7b55a59f.zip",
        "size": "~51 MB",
        "format": "svg_pack",
        "updated": "2023-10-24",
    },
]

# Azure Architecture Icons — official free download (version increments with releases)
AZURE_CATALOG: list[dict] = [
    {
        "name": "Azure Architecture Icons — V18",
        "category": "All Services",
        "url": "https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V18.zip",
        "size": "~20 MB",
        "format": "svg_pack",
        "updated": "2024-01",
    },
]


# ── Cisco live catalog (HTML scrape) ──────────────────────────────────────────

class _CiscoHTMLParser(HTMLParser):
    """Extract stencil entries from the Cisco Visio stencil listing page.

    Cisco's page uses any heading level (h2/h3/h4/strong) for categories and
    table rows with <a href="…zip"> links. We track the most recent heading
    text as the current category.
    """

    _HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "strong"}

    def __init__(self) -> None:
        super().__init__()
        self.stencils: list[dict] = []
        self._category = "General"
        self._in_heading = False
        self._in_td = False
        self._cells: list[dict] = []
        self._heading_buf = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        ad = dict(attrs)
        if tag in self._HEADING_TAGS:
            self._in_heading = True
            self._heading_buf = ""
        elif tag == "tr":
            self._cells = []
        elif tag == "td":
            self._in_td = True
            self._cells.append({"text": "", "href": None})
        elif tag == "a":
            href = ad.get("href", "")
            if href and self._in_td and self._cells:
                self._cells[-1]["href"] = href
            # Also catch bare <a href="…zip"> outside tables
            elif href and (href.endswith(".zip") or ("cisco.com" in href and ".zip" in href)):
                self._bare_link(href, ad)

    def _bare_link(self, href: str, attrs: dict) -> None:
        pass  # handled in handle_endtag via cells

    def handle_endtag(self, tag: str) -> None:
        if tag in self._HEADING_TAGS:
            text = self._heading_buf.strip()
            if text and len(text) < 80:
                self._category = text
            self._in_heading = False
        elif tag == "tr":
            self._flush_row()
            self._cells = []
        elif tag == "td":
            self._in_td = False

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if self._in_heading and stripped:
            self._heading_buf += stripped
        elif self._in_td and self._cells and stripped:
            self._cells[-1]["text"] += stripped

    def _flush_row(self) -> None:
        if not self._cells:
            return
        # Find the cell with a .zip href
        zip_cell = next(
            (c for c in self._cells if c.get("href") and ".zip" in c["href"]),
            None,
        )
        if not zip_cell:
            return
        href = zip_cell["href"]
        url = href if href.startswith("http") else f"https://www.cisco.com{href}"
        name = zip_cell["text"].strip() or url.split("/")[-1].replace(".zip", "")
        # updated and size from other cells
        other = [c["text"].strip() for c in self._cells if c is not zip_cell and c["text"].strip()]
        updated = other[0] if other else ""
        size = other[1] if len(other) > 1 else ""
        self.stencils.append({
            "name": name,
            "category": self._category,
            "url": url,
            "size": size,
            "updated": updated,
            "format": "vss_zip",
        })


def get_cisco_catalog() -> list[dict]:
    """Fetch and parse the live Cisco Visio stencil listing page.

    Falls back to CISCO_STATIC_CATALOG on network failure or 403.
    """
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    try:
        req = Request(CISCO_LISTING_URL, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"})
        with urlopen(req, timeout=15) as resp:  # nosec B310
            html = resp.read().decode("utf-8", errors="replace")
        parser = _CiscoHTMLParser()
        parser.feed(html)
        if parser.stencils:
            return parser.stencils
        # Page loaded but no links parsed — fall back to static
        logger.info("Cisco live catalog parsed 0 items; using static fallback")
    except Exception as exc:
        logger.info("Cisco catalog live fetch failed (%s); using static fallback", exc)
    return CISCO_STATIC_CATALOG


# ── Vendor metadata ───────────────────────────────────────────────────────────

VENDOR_META: dict[str, dict] = {
    "cisco": {
        "label": "Cisco",
        "description": "Official Cisco Visio stencils — routers, switches, security, UCS, wireless",
        "logo_char": "C",
        "color": "#049fd9",
        "catalog_url": CISCO_LISTING_URL,
        "catalog_note": "Live from cisco.com — 113 packages across 12 product families",
        "docs_url": "https://www.cisco.com/c/en/us/products/visio-stencil-listing.html",
    },
    "juniper": {
        "label": "Juniper Networks",
        "description": "Official Juniper Networks stencils — MX, EX, SRX, QFX, and vSRX",
        "logo_char": "J",
        "color": "#84b135",
        "catalog_url": "https://www.juniper.net/us/en/contact-us/visio-stencils.html",
        "catalog_note": "Single comprehensive stencil pack (.vssx)",
        "docs_url": "https://www.juniper.net/us/en/contact-us/visio-stencils.html",
    },
    "aws": {
        "label": "Amazon Web Services",
        "description": "AWS Architecture Icons — official SVG/PNG icons for all AWS services (free download)",
        "logo_char": "A",
        "color": "#ff9900",
        "catalog_url": "https://aws.amazon.com/architecture/icons/",
        "catalog_note": "Hundreds of service icons across 20+ categories",
        "docs_url": "https://support.microsoft.com/en-us/office/create-aws-diagrams-in-visio-138206bf-d10f-4583-9f31-885ce706af49",
    },
    "azure": {
        "label": "Microsoft Azure",
        "description": "Azure Architecture Icons — official SVG icons for all Azure services (free download)",
        "logo_char": "Az",
        "color": "#0078d4",
        "catalog_url": "https://learn.microsoft.com/en-us/azure/architecture/icons/",
        "catalog_note": "500+ service icons across 16 categories",
        "docs_url": "https://support.microsoft.com/en-us/office/create-azure-diagrams-in-visio-efbb25e7-c80e-42e1-b1ad-7ef630ff01b7",
    },
    "custom": {
        "label": "Custom Vendor",
        "description": "Import any Visio stencil (.vssx, .vsdx) or SVG/PNG icon pack (.zip) from a custom vendor",
        "logo_char": "+",
        "color": "#8e44ad",
        "catalog_url": "",
        "catalog_note": "Provide a direct download URL or upload a file",
        "docs_url": "",
    },
}

_CATALOGS: dict[str, callable] = {
    "cisco": get_cisco_catalog,
    "juniper": lambda: JUNIPER_CATALOG,
    "aws": lambda: AWS_CATALOG,
    "azure": lambda: AZURE_CATALOG,
    "custom": lambda: [],
}


def get_vendor_list() -> list[dict]:
    """Return metadata for all supported vendors."""
    return [{"id": vid, **meta} for vid, meta in VENDOR_META.items()]


def get_catalog(vendor: str) -> list[dict]:
    """Return the stencil catalog for the given vendor id."""
    fn = _CATALOGS.get(vendor)
    if fn is None:
        return []
    try:
        return fn()
    except Exception as exc:
        logger.warning("Catalog fetch failed for vendor %s: %s", vendor, exc)
        return []
