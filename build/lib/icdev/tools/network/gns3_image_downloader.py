"""
GNS3 Free Router Image Downloader
Downloads open/free router images into data/gns3-vendor-images/.

Usage:
    python tools/network/gns3_image_downloader.py --list
    python tools/network/gns3_image_downloader.py --download vyos
    python tools/network/gns3_image_downloader.py --download mikrotik-chr
    python tools/network/gns3_image_downloader.py --download all
    python tools/network/gns3_image_downloader.py --json
"""

import argparse
import json
import pathlib
import sys
import urllib.request
import urllib.error
import zipfile

DEST = pathlib.Path(__file__).resolve().parents[2] / "data" / "gns3-vendor-images"

IMAGES = {
    "vyos": {
        "name": "VyOS (rolling)",
        "description": "Juniper-like CLI — BGP, OSPF, MPLS, VRF. Fully open-source.",
        "closest_to": "Juniper vMX (CLI parity)",
        "license": "Free / GPLv2",
        "gns3_appliance": "vyos-rolling.gns3a",
        "format": "iso",
        "download_type": "github_release",
        "github_repo": "vyos/vyos-nightly-build",
        "asset_pattern": ".iso",
        "notes": (
            "ISO downloaded. In GNS3: New Template → Qemu VMs → "
            "RAM 512MB, 2 CPUs → attach ISO as CD-ROM → boot and run 'install image' → "
            "export disk as qcow2 for reuse. Or use 'vyos' appliance template."
        ),
    },
    "mikrotik-chr": {
        "name": "MikroTik CHR",
        "description": "Full RouterOS — BGP, OSPF, MPLS, VRRP, firewall. 1 Mbps free.",
        "closest_to": "Cisco IOS (routing features)",
        "license": "Free (1 Mbps cap) / commercial license for full speed",
        "gns3_appliance": "mikrotik-chr.gns3a",
        "format": "img",
        "download_type": "mikrotik",
        "version_url": "https://download.mikrotik.com/routeros/LATEST.7",
        "download_template": "https://download.mikrotik.com/routeros/{version}/chr-{version}.img.zip",
        "notes": "GNS3 appliance: Appliances → Add → search 'MikroTik'",
    },
    "arista-veos": {
        "name": "Arista vEOS-lab",
        "description": "Full Arista EOS — BGP, EVPN, VXLAN, OpenConfig. Free for labs.",
        "closest_to": "Arista hardware (identical NOS)",
        "license": "Free with arista.com account (no cost)",
        "gns3_appliance": "arista-veos.gns3a",
        "format": "vmdk / qcow2",
        "download_type": "manual",
        "manual_url": "https://www.arista.com/en/support/software-download",
        "notes": (
            "1. Register free at arista.com\n"
            "2. Downloads → vEOS-lab → choose version → download .qcow2\n"
            "3. Drop into data/gns3-vendor-images/\n"
            "4. GNS3 appliance: Appliances → Add → search 'Arista'"
        ),
    },
    "nokia-srlinux": {
        "name": "Nokia SR Linux",
        "description": "Modern containerised NOS — BGP, ISIS, SR-MPLS, EVPN. Free.",
        "closest_to": "Nokia SR OS (protocol stack)",
        "license": "Free / Apache 2.0",
        "gns3_appliance": "nokia-sr-linux.gns3a",
        "format": "Docker container (ghcr.io/nokia/srlinux)",
        "download_type": "docker",
        "docker_image": "ghcr.io/nokia/srlinux:latest",
        "notes": (
            "Run: docker pull ghcr.io/nokia/srlinux:latest\n"
            "GNS3 must have Docker integration enabled.\n"
            "Appliance: Appliances → Add → search 'SR Linux'"
        ),
    },
    "frrouting": {
        "name": "FRRouting (FRR)",
        "description": "Open-source routing suite — BGP, OSPF, ISIS, LDP, MPLS, PIM.",
        "closest_to": "Protocol engine only (no vendor CLI)",
        "license": "Free / GPLv2",
        "gns3_appliance": "frrouting.gns3a",
        "format": "Docker container (frrouting/frr)",
        "download_type": "docker",
        "docker_image": "frrouting/frr:latest",
        "notes": (
            "Run: docker pull frrouting/frr:latest\n"
            "GNS3 must have Docker integration enabled.\n"
            "Appliance: Appliances → Add → search 'FRRouting'"
        ),
    },
}


def _progress(block_num, block_size, total):
    downloaded = block_num * block_size
    if total > 0:
        pct = min(downloaded * 100 / total, 100)
        bar = "#" * int(pct / 2)
        print(f"\r  [{bar:<50}] {pct:5.1f}%", end="", flush=True)


def _github_latest_asset(repo: str, pattern: str) -> tuple[str, str]:
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    for asset in data.get("assets", []):
        if pattern in asset["name"]:
            return asset["browser_download_url"], asset["name"]
    raise RuntimeError(f"No asset matching '{pattern}' in {repo} latest release")


def _mikrotik_latest() -> tuple[str, str]:
    with urllib.request.urlopen(IMAGES["mikrotik-chr"]["version_url"], timeout=10) as r:
        raw = r.read().decode().strip()
    # Response format: "7.x.y <unix-timestamp>" — take only the version token
    version = raw.split()[0]
    url = IMAGES["mikrotik-chr"]["download_template"].format(version=version)
    return url, f"chr-{version}.img.zip"


def download_image(key: str) -> dict:
    meta = IMAGES[key]
    DEST.mkdir(parents=True, exist_ok=True)

    if meta["download_type"] == "manual":
        return {
            "status": "manual",
            "message": f"{meta['name']} requires manual download.",
            "instructions": meta["notes"],
            "url": meta.get("manual_url", ""),
        }

    if meta["download_type"] == "docker":
        import subprocess
        img = meta["docker_image"]
        print(f"Pulling Docker image: {img}")
        result = subprocess.run(["docker", "pull", img], capture_output=True, text=True)
        if result.returncode != 0:
            return {"status": "error", "message": result.stderr.strip()}
        return {"status": "ok", "image": img, "notes": meta["notes"]}

    # File-based downloads
    try:
        if meta["download_type"] == "github_release":
            url, filename = _github_latest_asset(
                meta["github_repo"], meta["asset_pattern"]
            )
        elif meta["download_type"] == "mikrotik":
            url, filename = _mikrotik_latest()
        else:
            return {"status": "error", "message": f"Unknown download_type for {key}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    dest_file = DEST / filename
    if dest_file.exists():
        return {"status": "exists", "path": str(dest_file)}

    print(f"Downloading {meta['name']}...")
    print(f"  URL: {url}")
    print(f"  Destination: {dest_file}")
    try:
        urllib.request.urlretrieve(url, dest_file, _progress)
        print()
    except urllib.error.URLError as exc:
        return {"status": "error", "message": str(exc)}

    # Unzip MikroTik .img.zip
    if filename.endswith(".zip"):
        print("  Extracting...")
        with zipfile.ZipFile(dest_file, "r") as zf:
            zf.extractall(DEST)
        dest_file.unlink()
        extracted = [f for f in DEST.iterdir() if f.suffix == ".img" and "chr" in f.name]
        dest_file = extracted[0] if extracted else dest_file

    return {
        "status": "ok",
        "path": str(dest_file),
        "size_mb": round(dest_file.stat().st_size / 1_048_576, 1) if dest_file.exists() else 0,
        "notes": meta["notes"],
    }


def main():
    parser = argparse.ArgumentParser(description="GNS3 free router image downloader")
    parser.add_argument("--list", action="store_true", help="List available images")
    parser.add_argument("--download", metavar="KEY", help="Download image (key or 'all')")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.list or (not args.download):
        rows = []
        for key, meta in IMAGES.items():
            rows.append({
                "key": key,
                "name": meta["name"],
                "closest_to": meta["closest_to"],
                "license": meta["license"],
                "format": meta["format"],
            })
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(f"\n{'Key':<18} {'Name':<22} {'Closest to':<30} {'License'}")
            print("-" * 100)
            for r in rows:
                print(f"{r['key']:<18} {r['name']:<22} {r['closest_to']:<30} {r['license']}")
            print(f"\nDrop images into: {DEST}")
        return

    keys = list(IMAGES.keys()) if args.download == "all" else [args.download]
    results = {}
    for key in keys:
        if key not in IMAGES:
            results[key] = {"status": "error", "message": f"Unknown image key '{key}'"}
            continue
        print(f"\n{'='*60}")
        print(f"  {IMAGES[key]['name']}")
        print(f"{'='*60}")
        results[key] = download_image(key)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for key, result in results.items():
            status = result.get("status", "?")
            if status == "ok":
                print(f"\n[OK] {key}: {result.get('path', result.get('image', ''))}")
                if "notes" in result:
                    print(f"     Next: {result['notes'].splitlines()[0]}")
            elif status == "exists":
                print(f"\n[SKIP] {key}: already exists at {result['path']}")
            elif status == "manual":
                print(f"\n[MANUAL] {key}: {result['message']}")
                print(f"         {result.get('url', '')}")
            else:
                print(f"\n[ERR] {key}: {result.get('message')}", file=sys.stderr)


if __name__ == "__main__":
    main()
