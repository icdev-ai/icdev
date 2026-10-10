#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev omarchy setup|status|uninstall` -- fresh Omarchy (or plain Arch) to a running ICDEV.

ONE command for the steps an operator otherwise runs by hand (omx-setup-01):

  1. detect   -- ``omarchy`` on PATH, and which harnesses are installed.
                 Not on Omarchy is fine: a plain Arch/Linux host gets every
                 step below; only Omarchy desktop integration is skipped.
  2. postgres -- PostgreSQL + pgvector, role + database ``icdev`` (the name
                 ``icdev_domain.yaml`` declares), ``CREATE EXTENSION vector``,
                 then ``icdev-init-db``. Needs root, so it RUNS only with
                 ``--yes``; without it the commands are printed.
  3. env      -- ``ICDEV_DATABASE_URL`` + ``ICDEV_STORAGE_BACKEND=postgresql``
                 in ``.env``. An existing value is KEPT, never overwritten.
                 LLM keys are left to the operator; the providers configured /
                 missing are reported.
  4. mcp      -- ``icdev-unified`` registered with each installed harness, at
                 USER level (opencode / Pi config files via the omx-dx-01
                 generators; claude / codex through their own ``mcp add``).
  5. skill    -- ``icdev skill install`` (omx-dx-02).
  6. guard    -- ``icdev harness install-guard <opencode|pi> --global``
                 (omx-guard-01 / omx-guard-03) for each one installed.
  7. systemd  -- the omx-linux-01 ``--user`` units for ``tools/genesis/launch.py``;
                 enabled + started only with ``--start``.

Every step probes first and reports ``skipped`` when already in place, so a
second run changes nothing. ``--dry-run`` prints the plan and writes nothing.
``status`` probes only. ``uninstall`` reverses 4-7 and NEVER touches the
database or ``.env``.

Every external command goes through one injectable runner (``Host``), which is
how the tests fake pacman / psql / systemctl / the harness CLIs.

Usage:
    icdev omarchy setup [--dry-run] [--yes] [--start] [--json]
    icdev omarchy status [--json]
    icdev omarchy uninstall [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 -- fixed argv lists, never a shell
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

HARNESSES = ("opencode", "claude", "codex", "pi")
GUARDED_HARNESSES = ("opencode", "pi")
MCP_NAME = "icdev-unified"
DB_NAME = "icdev"
DB_ROLE = "icdev"
DSN = f"postgresql://{DB_ROLE}@localhost:5432/{DB_NAME}"
PG_DATA = "/var/lib/postgres/data"
GENESIS_TIMER = "icdev-genesis.timer"


# --------------------------------------------------------------------------- #
# The host: every probe of the outside world goes through here.
# --------------------------------------------------------------------------- #

@dataclass
class Result:
    rc: int
    stdout: str = ""
    stderr: str = ""


def _real_run(argv: Sequence[str], env: Optional[Dict[str, str]] = None) -> Result:
    try:
        r = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8",  # nosec B603
                           errors="replace", timeout=600,
                           env={**os.environ, **env} if env else None)
        return Result(r.returncode, r.stdout or "", r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Result(127, "", str(exc))


def _host_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


@dataclass
class Host:
    root: Path
    home: Path
    environ: Dict[str, str]
    platform: str
    python: str
    which: Callable[[str], Optional[str]] = shutil.which
    run: Callable[..., Result] = _real_run

    @classmethod
    def real(cls) -> "Host":
        from icdev.core.paths import repo_root
        return cls(root=repo_root().resolve(), home=Path.home(), environ=dict(os.environ),
                   platform=_host_platform(), python=sys.executable)

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    def xdg_config(self) -> Path:
        return Path(self.environ.get("XDG_CONFIG_HOME") or self.home / ".config")

    def pi_agent_dir(self) -> Path:
        return Path(self.environ.get("PI_CODING_AGENT_DIR") or self.home / ".pi" / "agent")

    def unit_dir(self) -> Path:
        return self.xdg_config() / "systemd" / "user"

    def ok(self, argv: Sequence[str]) -> bool:
        return self.run(list(argv)).rc == 0


@dataclass
class Detected:
    omarchy: bool
    harnesses: List[str]


def detect(host: Host) -> Detected:
    return Detected(omarchy=bool(host.which("omarchy")),
                    harnesses=[h for h in HARNESSES if host.which(h)])


# --------------------------------------------------------------------------- #
# Actions: a description plus EITHER an argv OR a python callable.
# --------------------------------------------------------------------------- #

@dataclass
class Action:
    describe: str
    argv: Optional[List[str]] = None
    fn: Optional[Callable[[], None]] = None
    # argv probe: when it exits 0 (with non-empty stdout, if unless_output), it is already done.
    unless: Optional[List[str]] = None
    unless_output: bool = True
    env: Optional[Dict[str, str]] = None

    def apply(self, host: Host) -> Optional[str]:
        """Run it. None on success, else the error text."""
        if self.unless:
            probe = host.run(self.unless)
            if probe.rc == 0 and (probe.stdout.strip() or not self.unless_output):
                return None
        if self.fn is not None:
            try:
                self.fn()
            except Exception as exc:  # noqa: BLE001 -- reported per step, never swallowed
                return f"{type(exc).__name__}: {exc}"
            return None
        r = host.run(self.argv, env=self.env) if self.env else host.run(self.argv)
        return None if r.rc == 0 else f"{' '.join(self.argv)} -> rc {r.rc} {r.stderr.strip()}".rstrip()


@dataclass
class Plan:
    """What a step found. ``done`` means nothing to do; ``state`` overrides it."""
    done: bool
    actions: List[Action] = field(default_factory=list)
    detail: str = ""
    state: Optional[str] = None      # "n/a" / "pending" when actions cannot run as-is
    needs_yes: bool = False


# --------------------------------------------------------------------------- #
# Step 2 -- PostgreSQL
# --------------------------------------------------------------------------- #

def _check_db_name() -> None:
    """The database we create must be one this parent's icdev_domain.yaml declares."""
    try:
        from icdev.core.context import check_identity
        report = check_identity(environ={"ICDEV_DATABASE_URL": DSN})
    except Exception:  # noqa: BLE001 -- no declaration found: nothing to assert against
        return
    if report.verdict == "mismatch":
        raise RuntimeError(report.detail)


def _psql_su(sql: str, db: str = "postgres") -> List[str]:
    return ["sudo", "-u", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-d", db, "-tAc", sql]


def plan_postgres(host: Host) -> Plan:
    _check_db_name()
    ready = host.run(["psql", "-h", "localhost", "-U", DB_ROLE, "-d", DB_NAME, "-tAc",
                      "SELECT 1 FROM pg_extension WHERE extname = 'vector'"])
    if ready.rc == 0 and ready.stdout.strip() == "1":
        return Plan(done=True, detail=f"database {DB_NAME} reachable with pgvector")

    installed = bool(host.which("psql"))
    if not installed and not host.which("pacman"):
        return Plan(done=False, state="pending",
                    detail="PostgreSQL is not installed and pacman is absent: install PostgreSQL "
                           f"+ pgvector, create role/database {DB_NAME}, then re-run")
    actions: List[Action] = []
    if not installed:
        actions.append(Action("install postgresql + pgvector",
                              ["sudo", "pacman", "-S", "--needed", "--noconfirm", "postgresql", "pgvector"]))
    if not installed or not host.ok(["pg_isready", "-q"]):
        actions += [
            Action("initialise the cluster (unless it exists)",
                   ["sudo", "-u", "postgres", "initdb", "--locale=C.UTF-8", "--encoding=UTF8",
                    "-D", PG_DATA],
                   unless=["sudo", "test", "-f", f"{PG_DATA}/PG_VERSION"], unless_output=False),
            Action("enable + start postgresql", ["sudo", "systemctl", "enable", "--now", "postgresql"]),
        ]
    actions += [
        Action(f"create role {DB_ROLE}", _psql_su(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{DB_ROLE}') "
            f"THEN CREATE ROLE {DB_ROLE} LOGIN; END IF; END $$")),
        Action(f"create database {DB_NAME}",
               ["sudo", "-u", "postgres", "createdb", "-O", DB_ROLE, DB_NAME],
               unless=_psql_su(f"SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}'")),
        Action("create extension vector", _psql_su("CREATE EXTENSION IF NOT EXISTS vector", DB_NAME)),
        Action("initialise the ICDEV schema (icdev-init-db)",
               [host.python, "-m", "icdev.tools.db.init_icdev_db"],
               env={"ICDEV_DATABASE_URL": DSN, "ICDEV_STORAGE_BACKEND": "postgresql"}),
    ]
    return Plan(done=False, actions=actions, needs_yes=True,
                detail="PostgreSQL absent" if not installed else f"database {DB_NAME} not ready")


# --------------------------------------------------------------------------- #
# Step 3 -- .env
# --------------------------------------------------------------------------- #

def llm_provider_report(host: Host) -> Dict[str, List[str]]:
    """Providers in args/llm_config.yaml whose api_key_env is set / unset (.env or process)."""
    from tools.cli.setup_wizard import read_env
    try:
        import yaml
        cfg = yaml.safe_load((host.root / "args" / "llm_config.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {"configured": [], "missing": []}
    env = {**read_env(host.env_file), **host.environ}
    out: Dict[str, List[str]] = {"configured": [], "missing": []}
    for name, p in sorted((cfg.get("providers") or {}).items()):
        key = (p or {}).get("api_key_env")
        if key:
            out["configured" if env.get(key) else "missing"].append(name)
    return out


def plan_env(host: Host) -> Plan:
    from tools.cli.setup_wizard import db_env_updates, read_env, update_env
    current = read_env(host.env_file)
    wanted = db_env_updates("postgresql", dsn=DSN)
    missing = {k: v for k, v in wanted.items() if not current.get(k)}
    kept = [k for k in wanted if current.get(k) and current[k] != wanted[k]]
    llm = llm_provider_report(host)
    detail = (f"LLM providers configured: {', '.join(llm['configured']) or 'none'}; "
              f"missing keys: {', '.join(llm['missing']) or 'none'}")
    if kept:
        detail = f"kept existing {', '.join(kept)}; " + detail
    if not missing:
        return Plan(done=True, detail=detail)
    return Plan(done=False, detail=detail, actions=[Action(
        f"write {', '.join(sorted(missing))} to {host.env_file}",
        fn=lambda: update_env(host.env_file, missing))])


# --------------------------------------------------------------------------- #
# Step 4 -- MCP registration (user level)
# --------------------------------------------------------------------------- #

def mcp_server(host: Host) -> Dict:
    """The icdev-unified entry, in .mcp.json (Claude Code) shape, for THIS checkout."""
    return {"command": host.python,
            "args": [(host.root / "tools" / "mcp" / "unified_server.py").as_posix()],
            "env": {"ICDEV_PROJECT_ROOT": host.root.as_posix()}}


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="")


def _mcp_file(host: Host, harness: str) -> tuple:
    """(config path, container key, entry) for the file-configured harnesses."""
    from tools.dx.mcp_config_generator import OPENCODE_SCHEMA, _to_opencode_server
    srv = mcp_server(host)
    if harness == "opencode":
        return host.xdg_config() / "opencode" / "opencode.json", "mcp", _to_opencode_server(srv), \
            {"$schema": OPENCODE_SCHEMA}
    return host.pi_agent_dir() / "mcp.json", "mcpServers", srv, {}


def _mcp_cli_add(host: Host, harness: str) -> List[str]:
    srv = mcp_server(host)
    # Name BEFORE the env flags: claude's -e is variadic and would swallow it.
    scope = ["--scope", "user"] if harness == "claude" else []
    flag = "-e" if harness == "claude" else "--env"
    envs = [x for k, v in srv["env"].items() for x in (flag, f"{k}={v}")]
    return [harness, "mcp", "add", *scope, MCP_NAME, *envs, "--", srv["command"], *srv["args"]]


def plan_mcp(host: Host, det: Detected, remove: bool = False) -> Plan:
    if not det.harnesses:
        return Plan(done=True, state="n/a", detail="no supported harness installed")
    actions: List[Action] = []
    for h in det.harnesses:
        if h in ("opencode", "pi"):
            path, key, entry, base = _mcp_file(host, h)
            data = _read_json(path)
            present = (data.get(key) or {}).get(MCP_NAME)
            if remove:
                if present is not None:
                    def _rm(path=path, key=key):
                        d = _read_json(path)
                        d.get(key, {}).pop(MCP_NAME, None)
                        _write_json(path, d)
                    actions.append(Action(f"{h}: remove {MCP_NAME} from {path}", fn=_rm))
            elif present != entry:
                def _add(path=path, key=key, entry=entry, base=base):
                    d = {**base, **_read_json(path)}
                    d.setdefault(key, {})[MCP_NAME] = entry
                    _write_json(path, d)
                actions.append(Action(f"{h}: register {MCP_NAME} in {path}", fn=_add))
        else:
            registered = host.ok([h, "mcp", "get", MCP_NAME])
            if remove and registered:
                scope = ["--scope", "user"] if h == "claude" else []
                actions.append(Action(f"{h}: remove {MCP_NAME}", [h, "mcp", "remove", *scope, MCP_NAME]))
            elif not remove and not registered:
                actions.append(Action(f"{h}: register {MCP_NAME}", _mcp_cli_add(host, h)))
    return Plan(done=not actions, actions=actions,
                detail=f"harnesses: {', '.join(det.harnesses)}")


# --------------------------------------------------------------------------- #
# Step 5 -- the cross-harness skill
# --------------------------------------------------------------------------- #

def plan_skill(host: Host, det: Detected, remove: bool = False) -> Plan:
    from tools.dx import skill_install as si
    entries = si.status(host.home)["entries"]
    if remove:
        if not entries:
            return Plan(done=True, detail="not installed")
        return Plan(done=False, actions=[Action("icdev skill uninstall",
                                                fn=lambda: si.uninstall(host.home))])
    wanted = [h for h in det.harnesses if h in si.HARNESS_SKILL_DIRS]
    have = {e["harness"] for e in entries if e["present"]}
    if "agents" in have and set(wanted) <= have:
        return Plan(done=True, detail=f"linked into: {', '.join(sorted(have - {'agents'})) or 'none'}")
    dirs = ",".join(wanted) or "auto"
    return Plan(done=False, actions=[Action(f"icdev skill install --dirs {dirs}",
                                            fn=lambda: si.install(dirs, host.home))])


# --------------------------------------------------------------------------- #
# Step 6 -- guard plugins
# --------------------------------------------------------------------------- #

def plan_guard(host: Host, det: Detected, remove: bool = False) -> Plan:
    targets = [h for h in GUARDED_HARNESSES if h in det.harnesses]
    if not targets:
        return Plan(done=True, state="n/a", detail="neither opencode nor pi is installed")
    try:
        from tools.hooks.harness_guard import PLUGIN_FILENAME, install_guard, plugin_dir, render_plugin
    except ImportError:
        return Plan(done=True, state="n/a", detail="harness guard installer not available in this build")
    actions: List[Action] = []
    for h in targets:
        path = plugin_dir(h, global_=True) / PLUGIN_FILENAME
        if remove:
            if path.is_file():
                actions.append(Action(f"{h}: remove guard {path}", fn=path.unlink))
        elif not (path.is_file() and path.read_text(encoding="utf-8") == render_plugin(h)):
            actions.append(Action(f"icdev harness install-guard {h} --global",
                                  fn=lambda h=h: install_guard(h, global_=True)))
    return Plan(done=not actions, actions=actions, detail=f"guarded: {', '.join(targets)}")


# --------------------------------------------------------------------------- #
# Step 7 -- systemd --user units
# --------------------------------------------------------------------------- #

def plan_systemd(host: Host, start: bool = False, remove: bool = False) -> Plan:
    if host.platform != "linux":
        return Plan(done=True, state="n/a", detail=f"systemd units are Linux-only (host: {host.platform})")
    from tools.genesis.install_units import GENESIS_UNITS, plan as unit_plan
    unit_dir = host.unit_dir()
    systemctl = bool(host.which("systemctl"))
    actions: List[Action] = []
    if remove:
        present = [unit_dir / n for _, n in GENESIS_UNITS if (unit_dir / n).is_file()]
        if present and systemctl:
            actions.append(Action(f"disable {GENESIS_TIMER}",
                                  ["systemctl", "--user", "disable", "--now", GENESIS_TIMER]))
        actions += [Action(f"remove {p}", fn=p.unlink) for p in present]
        if present and systemctl:
            actions.append(Action("systemctl --user daemon-reload",
                                  ["systemctl", "--user", "daemon-reload"]))
        return Plan(done=not actions, actions=actions)

    try:
        units = unit_plan(host.root, host.python)
    except (OSError, ValueError) as exc:
        return Plan(done=False, state="failed", detail=f"cannot render units: {exc}")
    for u in units:
        path = unit_dir / u["name"]
        if not (path.is_file() and path.read_text(encoding="utf-8") == u["content"]):
            def _write(path=path, content=u["content"]):
                path.parent.mkdir(parents=True, exist_ok=True)
                # newline="\n": systemd reads LF; Windows text mode would emit CRLF.
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(content)
            actions.append(Action(f"write {path}", fn=_write))
    if actions and systemctl:
        actions.append(Action("systemctl --user daemon-reload", ["systemctl", "--user", "daemon-reload"]))
    detail = "start with --start" if not start else ""
    if start:
        if not systemctl:
            return Plan(done=False, state="failed", actions=actions,
                        detail="--start given but systemctl is not on PATH")
        if not (host.ok(["systemctl", "--user", "is-enabled", "--quiet", GENESIS_TIMER])
                and host.ok(["systemctl", "--user", "is-active", "--quiet", GENESIS_TIMER])):
            actions.append(Action(f"enable + start {GENESIS_TIMER}",
                                  ["systemctl", "--user", "enable", "--now", GENESIS_TIMER]))
    return Plan(done=not actions, actions=actions, detail=detail)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

STEPS = ("postgres", "env", "mcp", "skill", "guard", "systemd")
REVERSIBLE = ("mcp", "skill", "guard", "systemd")


def _plans(host: Host, det: Detected, *, start: bool = False, remove: bool = False) -> Dict[str, Callable[[], Plan]]:
    plans: Dict[str, Callable[[], Plan]] = {
        "mcp": lambda: plan_mcp(host, det, remove),
        "skill": lambda: plan_skill(host, det, remove),
        "guard": lambda: plan_guard(host, det, remove),
        "systemd": lambda: plan_systemd(host, start, remove),
    }
    if not remove:
        plans = {"postgres": lambda: plan_postgres(host), "env": lambda: plan_env(host), **plans}
    return plans


def _execute(host: Host, name: str, plan: Plan, *, mode: str, yes: bool) -> Dict:
    """mode: 'status' (probe only), 'dry-run' (plan only) or 'apply'."""
    rec = {"step": name, "detail": plan.detail, "actions": [a.describe for a in plan.actions]}
    if plan.done and plan.state is None:
        rec["state"] = "skipped" if mode != "status" else "ok"
        return rec
    if plan.state in ("n/a", "failed") or (plan.state == "pending" and not plan.actions):
        rec["state"] = plan.state
        return rec
    if mode == "status":
        rec["state"] = "missing"
        return rec
    if mode == "dry-run":
        rec["state"] = "planned"
        rec["commands"] = [" ".join(a.argv) for a in plan.actions if a.argv]
        return rec
    if plan.needs_yes and not yes:
        rec["state"] = "pending"
        rec["detail"] = (plan.detail + "; needs root -- re-run with --yes, or run:").lstrip("; ")
        rec["commands"] = [" ".join(a.argv) for a in plan.actions if a.argv]
        return rec
    for a in plan.actions:
        err = a.apply(host)
        if err:
            rec.update(state="failed", error=err)
            return rec
    rec["state"] = "done"
    return rec


def run(command: str, host: Host, *, dry_run: bool = False, yes: bool = False,
        start: bool = False) -> Dict:
    det = detect(host)
    remove = command == "uninstall"
    mode = "status" if command == "status" else ("dry-run" if dry_run else "apply")
    report = {"command": command, "mode": mode, "omarchy": det.omarchy, "harnesses": det.harnesses,
              "host": ("Omarchy" if det.omarchy else
                       "not on Omarchy: plain Arch/Linux, Omarchy desktop bits skipped"),
              "steps": []}
    for name, make in _plans(host, det, start=start, remove=remove).items():
        try:
            plan = make()
        except Exception as exc:  # noqa: BLE001 -- one broken probe must not hide the others
            report["steps"].append({"step": name, "state": "failed", "error": f"{type(exc).__name__}: {exc}",
                                    "actions": []})
            continue
        report["steps"].append(_execute(host, name, plan, mode=mode, yes=yes))
    report["exit"] = 1 if any(s["state"] == "failed" for s in report["steps"]) else 0
    return report


def _print(report: Dict) -> None:
    print(f"icdev omarchy {report['command']} ({report['mode']})")
    print(f"  host: {report['host']}")
    print(f"  harnesses: {', '.join(report['harnesses']) or 'none found'}")
    for s in report["steps"]:
        line = f"  [{s['state']:<8}] {s['step']}"
        if s.get("detail"):
            line += f" -- {s['detail']}"
        print(line)
        if s["state"] in ("planned", "pending", "done", "missing"):
            for a in s["actions"]:
                print(f"      - {a}")
        for c in s.get("commands", []):
            print(f"      $ {c}")
        if s.get("error"):
            print(f"      error: {s['error']}")


def main(argv: Optional[List[str]] = None, host: Optional[Host] = None) -> int:
    ap = argparse.ArgumentParser(prog="icdev omarchy", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("setup", help="Bring this host to a running ICDEV (idempotent).")
    sp.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    sp.add_argument("--yes", action="store_true", help="run the root (sudo) PostgreSQL steps")
    sp.add_argument("--start", action="store_true", help="enable + start the genesis systemd timer")
    sp.add_argument("--json", action="store_true")
    st = sub.add_parser("status", help="Report each step's state.")
    st.add_argument("--json", action="store_true")
    up = sub.add_parser("uninstall", help="Reverse steps 4-7 (never drops the database).")
    up.add_argument("--dry-run", action="store_true")
    up.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = run(args.cmd, host or Host.real(), dry_run=getattr(args, "dry_run", False),
                 yes=getattr(args, "yes", False), start=getattr(args, "start", False))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print(report)
    return int(report["exit"])


if __name__ == "__main__":
    sys.exit(main())
