# CUI // SP-CTI
"""Register the Genesis supervisor to start at logon -- on the host's own scheduler.

WHY THIS EXISTS (omx-linux-01). Every logon registration for
`tools/genesis/launch.py` was a Windows Task Scheduler artifact
(`install_scheduled_task.ps1`, `register_startup.ps1`, `icdev_genesis_task.xml`,
`start_daemon.bat`). A Linux host had no way to start the supervisor at logon
and restart it on failure, so it was a second-class host by construction.

The Linux equivalent is a systemd ``--user`` service + timer, committed under
``scripts/systemd/``. Those files are VALID AS-IS for a checkout at ``~/icdev``
(``%h`` is systemd's own home specifier, not a placeholder); this module renders
them for the checkout it runs from -- ``%h/icdev`` becomes the real root and
``/usr/bin/env python3`` the interpreter running the installer (your venv) --
and writes them to ``$XDG_CONFIG_HOME/systemd/user``.

The Windows path is UNCHANGED: ``--platform windows`` prints the existing
PowerShell registration and writes nothing. macOS has no unit here yet; it is
reported, exit 2, never silently skipped.

Exit codes: 0 written/planned · 1 a systemctl step failed · 2 nothing done
(unsupported platform, missing template).

Usage:
    python -m tools.genesis.install_units --platform linux --dry-run
    python -m tools.genesis.install_units --platform linux            # write units
    python -m tools.genesis.install_units --platform linux --enable   # + systemctl --user enable --now
    python -m tools.genesis.install_units --platform linux --production   # also icdev.service
    python -m tools.genesis.install_units --platform linux --uninstall
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 -- systemctl only, fixed argv, no shell
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from icdev.core.paths import repo_root

#: (template path relative to the repo root, installed unit name)
GENESIS_UNITS = (
    ("scripts/systemd/icdev-genesis.service", "icdev-genesis.service"),
    ("scripts/systemd/icdev-genesis.timer", "icdev-genesis.timer"),
)
PRODUCTION_UNIT = ("scripts/icdev.service.template", "icdev.service")

#: What the committed files assume, and what each is rewritten to.
DEFAULT_ROOT_TOKEN = "%h/icdev"
DEFAULT_PYTHON_TOKEN = "/usr/bin/env python3"

WINDOWS_INSTRUCTIONS = (
    "Windows uses Task Scheduler (unchanged):\n"
    "  powershell -ExecutionPolicy Bypass -File tools\\genesis\\register_startup.ps1\n"
    "  (or, as Administrator: tools\\genesis\\install_scheduled_task.ps1)"
)


def host_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def default_unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def render_unit(text: str, root: Path, python: str) -> str:
    """The committed unit with the checkout root and interpreter filled in.

    systemd does not split ExecStart on quotes the way a shell does, so a root
    containing whitespace cannot be expressed safely -- refused, not mangled.
    """
    root_s = Path(root).as_posix()
    if any(c.isspace() for c in root_s + python):
        raise ValueError(f"systemd units cannot carry whitespace in paths: {root_s!r} / {python!r}")
    return text.replace(DEFAULT_ROOT_TOKEN, root_s).replace(DEFAULT_PYTHON_TOKEN, python)


def plan(root: Path, python: str, production: bool = False) -> List[Dict[str, str]]:
    """[{name, source, content}] for every unit to install. Raises on a missing template."""
    units = list(GENESIS_UNITS) + ([PRODUCTION_UNIT] if production else [])
    out = []
    for rel, name in units:
        src = Path(root) / rel
        text = src.read_text(encoding="utf-8")
        out.append({"name": name, "source": rel, "content": render_unit(text, root, python)})
    return out


def _systemctl(*args: str) -> Dict[str, Any]:
    argv = ["systemctl", "--user", *args]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",  # nosec B603
                           errors="replace", timeout=60)
        return {"argv": " ".join(argv), "rc": r.returncode, "stderr": (r.stderr or "").strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": " ".join(argv), "rc": 1, "stderr": str(exc)}


def run(platform: str, unit_dir: Path, root: Path, python: str, *, dry_run: bool = False,
        enable: bool = False, uninstall: bool = False, production: bool = False) -> Dict[str, Any]:
    report: Dict[str, Any] = {"platform": platform, "unit_dir": str(unit_dir), "root": str(root),
                              "dry_run": dry_run, "units": [], "systemctl": [], "exit": 0}
    if platform == "windows":
        report["message"] = WINDOWS_INSTRUCTIONS
        return report
    if platform != "linux":
        report["message"] = (f"no {platform} scheduler unit is provided; run "
                             "`python tools/genesis/launch.py` from a login item")
        report["exit"] = 2
        return report

    names = [n for _, n in GENESIS_UNITS] + ([PRODUCTION_UNIT[1]] if production else [])
    if uninstall:
        if not dry_run and shutil.which("systemctl"):
            report["systemctl"].append(_systemctl("disable", "--now", "icdev-genesis.timer"))
        for name in names:
            path = unit_dir / name
            report["units"].append({"name": name, "path": str(path), "removed": path.exists()})
            if not dry_run and path.exists():
                path.unlink()
    else:
        try:
            units = plan(root, python, production)
        except (OSError, ValueError) as exc:
            report["message"] = f"cannot render units: {exc}"
            report["exit"] = 2
            return report
        if not dry_run:
            unit_dir.mkdir(parents=True, exist_ok=True)
        for u in units:
            path = unit_dir / u["name"]
            report["units"].append({"name": u["name"], "path": str(path), "source": u["source"]})
            if not dry_run:
                # newline="\n": systemd reads LF; write_text on Windows would emit CRLF.
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(u["content"])

    if not dry_run and shutil.which("systemctl"):
        report["systemctl"].append(_systemctl("daemon-reload"))
        if enable and not uninstall:
            report["systemctl"].append(_systemctl("enable", "--now", "icdev-genesis.timer"))
    elif enable and not dry_run:
        report["message"] = "systemctl not found on PATH: units written, nothing enabled"
        report["exit"] = 1
    if any(s["rc"] != 0 for s in report["systemctl"]):
        report["exit"] = 1
    if not enable and not uninstall and "message" not in report:
        report["message"] = ("enable with: systemctl --user enable --now icdev-genesis.timer"
                             "  (boot without logon: loginctl enable-linger \"$USER\")")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Install the Genesis supervisor's logon units")
    ap.add_argument("--platform", choices=["auto", "linux", "windows", "macos"], default="auto")
    ap.add_argument("--unit-dir", type=Path, default=None, help="default: $XDG_CONFIG_HOME/systemd/user")
    ap.add_argument("--root", type=Path, default=None, help="checkout to run from (default: this repo)")
    ap.add_argument("--python", default=None, help="interpreter for ExecStart (default: this one)")
    ap.add_argument("--production", action="store_true", help="also install scripts/icdev.service.template")
    ap.add_argument("--enable", action="store_true", help="systemctl --user enable --now the timer")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="plan only; write nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    platform = host_platform() if args.platform == "auto" else args.platform
    report = run(platform, args.unit_dir or default_unit_dir(), (args.root or repo_root()).resolve(),
                 args.python or sys.executable, dry_run=args.dry_run, enable=args.enable,
                 uninstall=args.uninstall, production=args.production)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        verb = "would " if args.dry_run else ""
        for u in report["units"]:
            action = "remove" if args.uninstall else "write"
            print(f"{verb}{action} {u['path']}")
        for s in report["systemctl"]:
            print(f"{s['argv']} -> rc {s['rc']} {s['stderr']}".rstrip())
        if report.get("message"):
            print(report["message"])
    return int(report["exit"])


if __name__ == "__main__":
    sys.exit(main())
