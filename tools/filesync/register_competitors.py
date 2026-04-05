#!/usr/bin/env python3
# CUI // SP-CTI
"""Register file synchronization competitors in the Creative Engine.

One-time setup script to populate the creative_competitors table with
known file sync competitors and confirm them for scanning.

Usage:
    python tools/filesync/register_competitors.py
"""

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.creative.competitor_discoverer import (  # noqa: E402
    confirm_competitor,
    get_competitors,
    store_competitors,
)

DOMAIN = "file synchronization"
CONFIRMED_BY = "admin@icdev.local"

COMPETITORS = [
    {
        "name": "Syncthing",
        "source": "manual",
        "source_url": "https://syncthing.net",
        "features": ["peer-to-peer sync", "open source", "decentralized", "cross-platform"],
        "metadata": {"github": "syncthing/syncthing", "type": "open_source"},
    },
    {
        "name": "Resilio Sync",
        "source": "manual",
        "source_url": "https://www.resilio.com",
        "features": ["peer-to-peer sync", "enterprise features", "WAN acceleration"],
        "metadata": {"type": "commercial"},
    },
    {
        "name": "rclone",
        "source": "manual",
        "source_url": "https://rclone.org",
        "features": ["cloud storage sync", "CLI tool", "multi-cloud support", "encryption"],
        "metadata": {"github": "rclone/rclone", "type": "open_source"},
    },
    {
        "name": "Dropbox",
        "source": "manual",
        "source_url": "https://www.dropbox.com",
        "features": ["cloud sync", "file sharing", "collaboration", "smart sync"],
        "metadata": {"type": "commercial_saas"},
    },
    {
        "name": "OneDrive/SharePoint",
        "source": "manual",
        "source_url": "https://onedrive.live.com",
        "features": ["cloud sync", "Microsoft 365 integration", "enterprise DLP", "co-authoring"],
        "metadata": {"type": "commercial_saas"},
    },
    {
        "name": "Box",
        "source": "manual",
        "source_url": "https://www.box.com",
        "features": ["cloud content management", "compliance", "FedRAMP authorized", "workflow automation"],
        "metadata": {"type": "commercial_saas"},
    },
    {
        "name": "Google Drive",
        "source": "manual",
        "source_url": "https://drive.google.com",
        "features": ["cloud sync", "Google Workspace integration", "real-time collaboration", "search"],
        "metadata": {"type": "commercial_saas"},
    },
    {
        "name": "Nextcloud",
        "source": "manual",
        "source_url": "https://nextcloud.com",
        "features": ["self-hosted", "file sync", "collaboration", "end-to-end encryption"],
        "metadata": {"github": "nextcloud/server", "type": "open_source"},
    },
    {
        "name": "ownCloud",
        "source": "manual",
        "source_url": "https://owncloud.com",
        "features": ["self-hosted", "file sync", "enterprise features", "LDAP integration"],
        "metadata": {"github": "owncloud/core", "type": "open_source"},
    },
    {
        "name": "Seafile",
        "source": "manual",
        "source_url": "https://www.seafile.com",
        "features": ["self-hosted", "file sync", "encryption", "versioning"],
        "metadata": {"github": "haiwen/seafile", "type": "open_source"},
    },
    {
        "name": "FileRun",
        "source": "manual",
        "source_url": "https://filerun.com",
        "features": ["self-hosted", "file management", "WebDAV", "photo management"],
        "metadata": {"type": "commercial"},
    },
    {
        "name": "FreeFileSync",
        "source": "manual",
        "source_url": "https://freefilesync.org",
        "features": ["local sync", "batch sync", "real-time sync", "mirror sync"],
        "metadata": {"type": "open_source"},
    },
]


def main():
    print("=" * 60)
    print("  CREATIVE ENGINE — File Sync Competitor Registration")
    print("=" * 60)

    # Step 1: Store competitors
    result = store_competitors(COMPETITORS, DOMAIN)
    print(f"\nStored: {result['stored']}, Duplicates: {result['duplicates']}")

    # Step 2: Fetch all discovered competitors for this domain
    all_comps = get_competitors(domain=DOMAIN)
    print(f"Total competitors in DB: {len(all_comps)}\n")

    # Step 3: Confirm all that are in 'discovered' status
    confirmed_count = 0
    for comp in all_comps:
        if comp["status"] == "discovered":
            res = confirm_competitor(comp["id"], CONFIRMED_BY)
            if res.get("confirmed"):
                confirmed_count += 1
                print(f"  [+] Confirmed: {comp['name']} ({comp['id']})")
            else:
                print(f"  [!] Failed to confirm {comp['name']}: {res.get('error')}")
        elif comp["status"] == "confirmed":
            print(f"  [=] Already confirmed: {comp['name']} ({comp['id']})")

    print(f"\nNewly confirmed: {confirmed_count}")

    # Step 4: Final listing
    final = get_competitors(domain=DOMAIN, status="confirmed")
    print(f"\nFinal confirmed competitors ({len(final)}):")
    print("-" * 60)
    for c in final:
        features_str = ", ".join(c.get("features", [])[:3])
        gh = c.get("metadata", {}).get("github", "")
        gh_str = f" | GitHub: {gh}" if gh else ""
        print(f"  {c['name']}")
        print(f"    URL: {c['source_url']}{gh_str}")
        print(f"    Features: {features_str}")
        print()

    print("=" * 60)
    print("  Registration complete. Run scans with:")
    print("  python tools/creative/creative_engine.py --scan --all --json")
    print("=" * 60)

    return {"registered": result["stored"], "confirmed": confirmed_count, "total": len(final)}


if __name__ == "__main__":
    result = main()
    print(f"\nResult: {json.dumps(result, indent=2)}")
