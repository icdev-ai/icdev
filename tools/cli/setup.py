#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev setup` — terminal UI for turning ICDEV™ components on and off.

Chosen (2026-07-25) as the PRIMARY enable/disable surface because it needs no
browser and no network — a dashboard settings page cannot make that claim on a
fresh air-gap install where the dashboard itself may be disabled.

Everything is driven by ``args/component_registry.yaml`` (the single source of
truth) via ``tools.config.component_registry`` — never a hand-maintained list,
so the menu can never drift from the registry. Notable sub-pages are shown
indented under their parent (DIC → /techwriter, /docdrift, …) so the
"I can't find the page" confusion that triggered this card is structurally
impossible: if a page is missing it is simply its component being off.

Controls (raw mode):
    ↑ / ↓ (or k / j)   move cursor
    SPACE               toggle the highlighted component
    p                   apply an install profile (from core_profiles.yaml)
    w                   write .env
    q                   quit (prompts if there are unsaved changes)

Degrades to a plain numbered-menu mode when stdin/stdout is not a TTY or the
terminal does not support raw key input. STDLIB ONLY — no curses replacement,
no rich/textual. Works on Windows terminals (msvcrt) and POSIX (termios).

Every write logs each changed component via ``log_component_audit()`` per the
CLAUDE.md component-audit rule.

Usage:
    icdev setup                    # interactive TUI on ./.env
    icdev setup --env-file PATH    # operate on a specific .env
    icdev setup --plain            # force the numbered-menu mode
    icdev setup --json             # non-interactive: dump current state as JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Ensure repo root on sys.path when run directly.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.cli.enable import (  # noqa: E402
    _load_env_file,
    _normalize,
    _parse_env,
    _rewrite_flags,
    _save_env_file,
)
from tools.config.component_registry import (  # noqa: E402
    get_registry,
    log_component_audit,
)

# Grouping order + labels (mirrors env_generator so the .env and TUI agree).
KIND_ORDER: list[str] = ["canvas", "feature", "core_extension", "child_app"]
KIND_LABELS: dict[str, str] = {
    "canvas": "CANVASES",
    "feature": "FEATURES",
    "core_extension": "CORE EXTENSIONS",
    "child_app": "CHILD APPS",
}


def _actor() -> str:
    return os.environ.get("USER") or os.environ.get("USERNAME") or "cli"


# ---------------------------------------------------------------------------
# State model (pure, testable — no I/O beyond the initial .env read)
# ---------------------------------------------------------------------------
@dataclass
class Row:
    """One toggleable component in the TUI."""

    key: str
    kind: str
    display_name: str
    env_flag: str
    extra_flags: list[str]
    url_prefix: str
    min_il: str
    sub_pages: list[tuple[str, str]]  # (label, href)
    enabled: bool
    original: bool = field(default=False)

    @property
    def all_flags(self) -> list[str]:
        return [self.env_flag, *self.extra_flags]

    @property
    def dirty(self) -> bool:
        return self.enabled != self.original


def _sub_pages(component) -> list[tuple[str, str]]:
    """Notable sub-pages for a component, from its registry nav.links.

    The parent/overview link (href == url_prefix or its trailing-slash form) is
    skipped so only genuine sub-pages are shown indented under the parent.
    """
    nav = component.nav or {}
    links = nav.get("links") if isinstance(nav, dict) else None
    if not links:
        return []
    prefix = (component.url_prefix or "").rstrip("/")
    overview = {prefix, prefix + "/"}
    pages: list[tuple[str, str]] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        href = str(link.get("href", "")).strip()
        label = str(link.get("label", "")).strip()
        if not href or href in overview:
            continue
        pages.append((label or href, href))
    return pages


def build_rows(registry, env_file: Path) -> list[Row]:
    """Build the ordered, grouped list of toggleable rows from the registry."""
    text = _load_env_file(env_file)
    existing = _parse_env(text)

    def _is_on(flags: list[str]) -> bool:
        states = [_normalize(existing.get(f, (None, "false"))[1]) for f in flags]
        return all(states) if states else False

    rows: list[Row] = []
    for kind in KIND_ORDER:
        comps = sorted(
            (c for c in registry.list_all() if c.kind == kind and c.env_flag),
            key=lambda c: c.key,
        )
        for c in comps:
            flags = [c.env_flag, *list(c.extra_env_flags)]
            on = _is_on(flags)
            rows.append(Row(
                key=c.key,
                kind=c.kind,
                display_name=c.display_name or c.key,
                env_flag=c.env_flag,
                extra_flags=list(c.extra_env_flags),
                url_prefix=c.url_prefix or "",
                min_il=c.min_il or "",
                sub_pages=_sub_pages(c),
                enabled=on,
                original=on,
            ))
    return rows


class SetupState:
    """In-memory setup state: rows, toggling, profile apply, and .env write."""

    def __init__(self, registry, env_file: Path):
        self.registry = registry
        self.env_file = env_file
        self.rows: list[Row] = build_rows(registry, env_file)
        self.cursor = 0

    # ---- queries -------------------------------------------------------
    def counts_by_kind(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for r in self.rows:
            en, tot = out.get(r.kind, (0, 0))
            out[r.kind] = (en + (1 if r.enabled else 0), tot + 1)
        return out

    @property
    def dirty(self) -> bool:
        return any(r.dirty for r in self.rows)

    def changed(self) -> list[Row]:
        return [r for r in self.rows if r.dirty]

    def _row_by_key(self, key: str) -> Row | None:
        return next((r for r in self.rows if r.key == key), None)

    # ---- mutations -----------------------------------------------------
    def toggle(self, idx: int) -> Row | None:
        if 0 <= idx < len(self.rows):
            self.rows[idx].enabled = not self.rows[idx].enabled
            return self.rows[idx]
        return None

    def apply_profile(self, name: str) -> dict:
        """Set enablement to a profile's default_enabled_components.

        Advisory: the operator can still toggle afterwards. Returns a summary of
        what changed and any profile keys that no component matched.
        """
        from tools.config.core_profile import get_profile

        profile = get_profile(name)
        if profile is None:
            return {"ok": False, "error": f"unknown profile '{name}'"}
        wanted = {str(k) for k in (profile.get("default_enabled_components") or [])}
        matched: set[str] = set()
        turned_on: list[str] = []
        turned_off: list[str] = []
        for r in self.rows:
            desired = r.key in wanted
            if desired:
                matched.add(r.key)
            if r.enabled != desired:
                r.enabled = desired
                (turned_on if desired else turned_off).append(r.key)
        return {
            "ok": True,
            "profile": name,
            "turned_on": turned_on,
            "turned_off": turned_off,
            "unmatched_keys": sorted(wanted - matched),
        }

    def pending_updates(self) -> dict[str, str]:
        """Flag→value map for every flag owned by a changed component."""
        updates: dict[str, str] = {}
        for r in self.changed():
            val = "true" if r.enabled else "false"
            for f in r.all_flags:
                updates[f] = val
        return updates

    def write(self) -> dict:
        """Persist changes to .env and log an audit event per changed component."""
        changed = self.changed()
        if not changed:
            return {"written": False, "env_file": str(self.env_file),
                    "changed": [], "reason": "no changes"}

        text = _load_env_file(self.env_file)
        new_text = _rewrite_flags(text, self.pending_updates())
        _save_env_file(self.env_file, new_text)

        actor = _actor()
        for r in changed:
            log_component_audit(
                event_type="enable" if r.enabled else "disable",
                actor=actor,
                component_key=r.key,
                details={
                    "flags": r.all_flags,
                    "value": r.enabled,
                    "env_file": str(self.env_file),
                    "surface": "icdev setup",
                },
            )
            r.original = r.enabled  # committed

        return {
            "written": True,
            "env_file": str(self.env_file),
            "changed": [{"key": r.key, "enabled": r.enabled} for r in changed],
        }


# ---------------------------------------------------------------------------
# Raw-key reader (stdlib only; Windows msvcrt + POSIX termios)
# ---------------------------------------------------------------------------
class _Key:
    UP = "UP"
    DOWN = "DOWN"
    SPACE = "SPACE"
    OTHER = "OTHER"


def _supports_raw() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.name == "nt":
        try:
            import msvcrt  # noqa: F401
            return True
        except Exception:
            return False
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
        return True
    except Exception:
        return False


def _read_key_windows() -> str:
    import msvcrt

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):  # arrow/function prefix
        code = msvcrt.getwch()
        return {"H": _Key.UP, "P": _Key.DOWN}.get(code, _Key.OTHER)
    if ch in ("\r", "\n"):
        return "ENTER"
    if ch == " ":
        return _Key.SPACE
    return ch


def _read_key_posix() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # escape sequence
            seq = sys.stdin.read(2)
            return {"[A": _Key.UP, "[B": _Key.DOWN}.get(seq, _Key.OTHER)
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == " ":
            return _Key.SPACE
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _read_key() -> str:
    return _read_key_windows() if os.name == "nt" else _read_key_posix()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _clear() -> None:
    # ANSI clear + home. Falls back silently on terminals that ignore it.
    sys.stdout.write("\x1b[2J\x1b[H")


def render(state: SetupState, cursor: int, message: str = "") -> str:
    """Render the current state to a string (pure — testable)."""
    lines: list[str] = []
    total_on = sum(1 for r in state.rows if r.enabled)
    dirty = "  *unsaved changes*" if state.dirty else ""
    lines.append(f"ICDEV setup — {state.env_file}   ({total_on}/{len(state.rows)} on){dirty}")
    lines.append("[↑/↓ move] [SPACE toggle] [p profile] [w write] [q quit]")
    lines.append("")

    counts = state.counts_by_kind()
    idx = 0
    for kind in KIND_ORDER:
        kind_rows = [r for r in state.rows if r.kind == kind]
        if not kind_rows:
            continue
        en, tot = counts.get(kind, (0, 0))
        lines.append(f"── {KIND_LABELS.get(kind, kind.upper())}  ({en}/{tot}) ──")
        for r in kind_rows:
            pointer = ">" if idx == cursor else " "
            box = "[x]" if r.enabled else "[ ]"
            il = f"  min_il:{r.min_il}" if r.min_il else ""
            url = f"  {r.url_prefix}" if r.url_prefix else ""
            lines.append(f"{pointer} {box} {r.display_name}  ({r.env_flag}){url}{il}")
            for label, href in r.sub_pages:
                lines.append(f"        ↳ {label}  {href}")
            idx += 1
        lines.append("")
    if message:
        lines.append(message)
    return "\n".join(lines)


def _prompt_profile_name() -> str | None:
    from tools.config.core_profile import load_profiles

    profiles = load_profiles()
    if not profiles:
        return None
    names = list(profiles)
    sys.stdout.write("\nApply profile:\n")
    for i, n in enumerate(names, 1):
        cnt = len(profiles[n].get("default_enabled_components") or [])
        sys.stdout.write(f"  {i}. {n} ({cnt} components)\n")
    sys.stdout.write("Number or name (blank to cancel): ")
    sys.stdout.flush()
    try:
        raw = input().strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(names):
        return names[int(raw) - 1]
    return raw if raw in profiles else None


# ---------------------------------------------------------------------------
# Interactive raw-mode loop
# ---------------------------------------------------------------------------
def run_interactive(state: SetupState) -> int:
    cursor = 0
    message = ""
    while True:
        _clear()
        sys.stdout.write(render(state, cursor, message) + "\n")
        sys.stdout.flush()
        message = ""
        try:
            key = _read_key()
        except (EOFError, KeyboardInterrupt):
            key = "q"

        if key in (_Key.UP, "k"):
            cursor = (cursor - 1) % len(state.rows)
        elif key in (_Key.DOWN, "j"):
            cursor = (cursor + 1) % len(state.rows)
        elif key == _Key.SPACE:
            r = state.toggle(cursor)
            if r:
                message = f"{'enabled' if r.enabled else 'disabled'}: {r.key}"
        elif key == "p":
            name = _prompt_profile_name()
            if name:
                res = state.apply_profile(name)
                if res.get("ok"):
                    message = (f"profile '{name}': +{len(res['turned_on'])} "
                               f"-{len(res['turned_off'])}")
                else:
                    message = res.get("error", "profile error")
            else:
                message = "profile: cancelled"
        elif key == "w":
            res = state.write()
            message = ("wrote .env: "
                       f"{len(res['changed'])} component(s) changed"
                       if res["written"] else "no changes to write")
        elif key in ("q", "\x03"):
            if state.dirty:
                sys.stdout.write("\nUnsaved changes. Write before quit? [y/N/cancel] ")
                sys.stdout.flush()
                try:
                    ans = input().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    ans = "n"
                if ans in ("c", "cancel"):
                    continue
                if ans in ("y", "yes"):
                    state.write()
            return 0


# ---------------------------------------------------------------------------
# Plain numbered-menu mode (non-TTY / --plain fallback)
# ---------------------------------------------------------------------------
def run_plain(state: SetupState, in_stream=None, out_stream=None) -> int:
    inp = in_stream or sys.stdin
    out = out_stream or sys.stdout

    def _emit() -> None:
        out.write("\n" + render(state, cursor=-1) + "\n")
        out.write("Enter a number to toggle, 'p <profile>' to apply a profile, "
                  "'w' to write, 'q' to quit: ")
        out.flush()

    while True:
        _emit()
        line = inp.readline()
        if not line:  # EOF
            return 0
        cmd = line.strip()
        if not cmd:
            continue
        if cmd in ("q", "quit"):
            return 0
        if cmd in ("w", "write"):
            res = state.write()
            out.write(f"\n{'wrote .env' if res['written'] else 'no changes'}: "
                      f"{len(res.get('changed', []))} changed\n")
            continue
        if cmd.startswith("p"):
            parts = cmd.split(None, 1)
            if len(parts) == 2:
                res = state.apply_profile(parts[1].strip())
                out.write(f"\n{res}\n")
            else:
                out.write("\nusage: p <profile-name>\n")
            continue
        if cmd.isdigit():
            idx = int(cmd) - 1
            r = state.toggle(idx)
            out.write(f"\n{'toggled ' + r.key if r else 'out of range'}\n")
            continue
        out.write(f"\nunrecognized: {cmd}\n")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="icdev setup",
        description=__doc__.split("\n\n")[0],
    )
    parser.add_argument("--env-file", default=".env",
                        help="Path to .env (default: ./.env)")
    parser.add_argument("--plain", action="store_true",
                        help="Force plain numbered-menu mode (no raw keys)")
    parser.add_argument("--json", action="store_true",
                        help="Print current component state as JSON and exit")
    args = parser.parse_args(argv)

    env_file = Path(args.env_file).resolve()
    state = SetupState(get_registry(), env_file)

    if args.json:
        payload = {
            "env_file": str(env_file),
            "counts": {k: {"enabled": e, "total": t}
                       for k, (e, t) in state.counts_by_kind().items()},
            "components": [
                {"key": r.key, "kind": r.kind, "env_flag": r.env_flag,
                 "enabled": r.enabled, "url_prefix": r.url_prefix,
                 "min_il": r.min_il,
                 "sub_pages": [{"label": lbl, "href": h} for lbl, h in r.sub_pages]}
                for r in state.rows
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0

    if args.plain or not _supports_raw():
        if not (args.plain):
            print("(terminal does not support raw input — plain menu mode)\n")
        return run_plain(state)

    return run_interactive(state)


if __name__ == "__main__":
    sys.exit(main())
