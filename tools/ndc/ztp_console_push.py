"""ZTP Console Push — NDC Workflow Step 4 (GNS3 console variant).

Connects to each MikroTik router via its GNS3 telnet console port
(no SSH, no network routing required) and pushes the generated .rsc
ZTP config line by line.

Why console instead of SSH/Ansible:
  - Router IPs are internal to GNS3's virtual network and not routable
    from the host until configs are applied
  - Console ports (e.g. localhost:5000) ARE reachable from the host
  - This is the authentic ZTP pattern: configure before the device has an IP

Usage:
  python tools/ndc/ztp_console_push.py [--server http://localhost:3080] [--dry-run] [--json]

Reads ZTP .rsc scripts from data/studio_artifacts/ndc/ztp/ matching
the device names discovered in GNS3.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_CANVAS = "ndc"
_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / _CANVAS

# Commands that need extra delay (RouterOS reloads firewall on commit)
_SLOW_PREFIXES = ("/ip firewall", "/interface", "/system")

# RouterOS prompt patterns
_PROMPTS = [b"] > ", b"]> ", b"> "]

# Per-line send delay (seconds) — avoids overwhelming the console buffer
_LINE_DELAY = 0.15
_SLOW_DELAY = 0.5


def _telnet_push(host: str, port: int, device_name: str,
                 commands: list[str], dry_run: bool = False,
                 timeout: float = 30.0) -> dict:
    """Open telnet to host:port, send commands, capture output."""
    import telnetlib  # noqa: PLC0415 # nosec B401

    output_lines: list[str] = []
    errors: list[str] = []

    if dry_run:
        return {
            "device": device_name,
            "status": "dry_run",
            "lines_sent": len(commands),
            "output": [f"[DRY-RUN] {c}" for c in commands[:5]] + ["..."],
        }

    try:
        tn = telnetlib.Telnet(host, port, timeout=timeout)  # nosec: B312

        # Wait for RouterOS login prompt or shell prompt
        time.sleep(1.5)
        banner = tn.read_very_eager().decode("utf-8", errors="replace")
        output_lines.append(f"[banner] {banner.strip()[:200]}")

        # If RouterOS presents a login prompt, send admin/<empty>
        if "Login:" in banner or "login:" in banner:
            tn.write(b"admin\n")
            time.sleep(0.5)
            resp = tn.read_very_eager().decode("utf-8", errors="replace")
            if "Password:" in resp or "password:" in resp:
                tn.write(b"\n")  # blank password
                time.sleep(0.5)
                tn.read_very_eager()

        # Send each command
        for cmd in commands:
            stripped = cmd.strip()
            if not stripped or stripped.startswith("#"):
                continue

            delay = _SLOW_DELAY if any(stripped.startswith(p) for p in _SLOW_PREFIXES) else _LINE_DELAY

            tn.write((stripped + "\n").encode("utf-8"))
            time.sleep(delay)

            raw = tn.read_very_eager()
            line_out = raw.decode("utf-8", errors="replace").strip()
            if line_out:
                output_lines.append(f"[{stripped[:50]}] => {line_out[:150]}")

            # Detect error responses
            if "bad command" in line_out.lower() or "error" in line_out.lower():
                errors.append(f"CMD: {stripped[:80]} => {line_out[:100]}")

        # Final flush
        time.sleep(0.5)
        tn.write(b"\n")
        tail = tn.read_very_eager().decode("utf-8", errors="replace")
        if tail.strip():
            output_lines.append(f"[tail] {tail.strip()[:200]}")

        tn.close()

    except ConnectionRefusedError:
        return {"device": device_name, "status": "unreachable",
                "error": f"Connection refused to {host}:{port}"}
    except TimeoutError:
        return {"device": device_name, "status": "unreachable",
                "error": f"Timeout connecting to {host}:{port}"}
    except Exception as exc:
        return {"device": device_name, "status": "error", "error": str(exc)}

    return {
        "device":     device_name,
        "status":     "error" if errors else "success",
        "lines_sent": len(commands),
        "errors":     errors,
        "output":     output_lines,
    }


def _discover_console_ports(server: str) -> dict[str, int]:
    """Query GNS3 API for console ports of all nodes. Returns {name: port}."""
    try:
        from tools.network.adapters.gns3_adapter import GNS3Adapter
        g = GNS3Adapter(server)
        projects = g.list_projects()
        if not projects:
            return {}
        pid = projects[0]["project_id"]
        nodes = g._get_list(f"/v2/projects/{pid}/nodes")
        return {
            n["name"]: n["console"]
            for n in nodes
            if n.get("console") and n.get("console_type") == "telnet"
        }
    except Exception:
        return {}


def _find_rsc(device_name: str) -> Path | None:
    """Find the most recent ZTP .rsc script for this device."""
    ztp_dir = _ARTIFACTS_DIR / "ztp"
    candidates = sorted(ztp_dir.glob(f"ztp_{device_name}.rsc"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def run(server: str = "http://localhost:3080",
        dry_run: bool = False) -> dict:
    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    console_ports = _discover_console_ports(server)

    if not console_ports:
        return {
            "gate":  "FAIL",
            "error": f"GNS3 server unreachable or no nodes at {server}",
        }

    results = []
    gate = "PASS"

    for device_name, port in console_ports.items():
        rsc = _find_rsc(device_name)
        if rsc is None:
            results.append({
                "device": device_name,
                "status": "skipped",
                "reason": "no .rsc config found",
            })
            continue

        commands = [
            line for line in rsc.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

        result = _telnet_push("127.0.0.1", port, device_name,
                               commands, dry_run=dry_run)
        results.append(result)

        if result["status"] in ("error", "unreachable"):
            gate = "WARN"  # non-fatal — other devices may succeed

    # Write push report
    uid = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    report_lines = [
        "# ZTP Console Push Report",
        f"**Generated:** {ts}  ",
        f"**Mode:** {'DRY-RUN' if dry_run else 'LIVE'}  ",
        f"**Gate:** {'✓ PASS' if gate == 'PASS' else '⚠ WARN'}",
        "",
    ]
    for r in results:
        icon = {"success": "✓", "skipped": "–", "dry_run": "○",
                "unreachable": "✗", "error": "✗"}.get(r["status"], "?")
        report_lines.append(f"## {icon} {r['device']} — {r['status'].upper()}")
        if r.get("error"):
            report_lines.append(f"> ERROR: {r['error']}")
        if r.get("errors"):
            for e in r["errors"]:
                report_lines.append(f"> CMD ERROR: {e}")
        if r.get("lines_sent"):
            report_lines.append(f"Lines sent: {r['lines_sent']}")
        if r.get("output"):
            report_lines.append("```")
            for line in r["output"][:20]:
                report_lines.append(line)
            if len(r.get("output", [])) > 20:
                report_lines.append(f"... ({len(r['output']) - 20} more lines)")
            report_lines.append("```")
        report_lines.append("")

    rpt_path = _ARTIFACTS_DIR / f"ztp_push_report_{uid}.md"
    rpt_path.write_text("\n".join(report_lines), encoding="utf-8", newline="")

    # Update DB ztp_status
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            new_status = "pushed" if gate == "PASS" else "push_partial"
            conn.execute(
                "UPDATE ndc_topologies SET ztp_status=%s, updated_at=%s "
                "WHERE source='gns3' ORDER BY updated_at DESC LIMIT 1",
                (new_status, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass

    return {
        "gate":      gate,
        "canvas":    _CANVAS,
        "dry_run":   dry_run,
        "devices":   len(results),
        "pushed":    sum(1 for r in results if r["status"] == "success"),
        "skipped":   sum(1 for r in results if r["status"] == "skipped"),
        "failed":    sum(1 for r in results if r["status"] in ("error", "unreachable")),
        "results":   results,
        "artifacts": [
            {"name": "ZTP Push Report",
             "path": rpt_path.relative_to(_ROOT).as_posix(),
             "type": "md"},
        ],
    }


def main() -> None:
    # telnetlib is deprecated in 3.11 but still available in 3.14 — suppress warning
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="telnetlib")

    parser = argparse.ArgumentParser(description="ZTP Console Push — NDC GNS3")
    parser.add_argument("--server",  default="http://localhost:3080")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show commands without connecting")
    parser.add_argument("--json",    action="store_true")
    args = parser.parse_args()

    result = run(args.server, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        mode = "DRY-RUN" if args.dry_run else "LIVE"
        print(f"Gate   : {result['gate']} ({mode})")
        print(f"Devices: {result['devices']}  Pushed: {result['pushed']}  "
              f"Skipped: {result['skipped']}  Failed: {result['failed']}")
        for r in result.get("results", []):
            icon = "OK" if r["status"] == "success" else r["status"].upper()
            print(f"  [{icon}] {r['device']}", end="")
            if r.get("error"):
                print(f"  ERROR: {r['error']}", end="")
            print()
        if result.get("artifacts"):
            print(f"\nReport : {result['artifacts'][0]['path']}")


if __name__ == "__main__":
    main()
