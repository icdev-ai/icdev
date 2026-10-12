#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev omarchy setup|status|uninstall` -- fresh Omarchy (or plain Arch) to a running ICDEV.

ONE command for the steps an operator otherwise runs by hand (omx-setup-01):

  1. detect   -- ``omarchy`` on PATH, and which harnesses are installed.
                 Not on Omarchy is fine: a plain Arch/Linux host gets every
                 step below; only Omarchy desktop integration is skipped.
  2. postgres -- PostgreSQL + pgvector, role + database ``icdev`` (the name
                 ``icdev_domain.yaml`` declares), ``CREATE EXTENSION vector``,
                 then the schema (``bootstrap_pg``). Ready means pgvector AND a
                 finished schema, so a failed load is retried, never `skipped`.
                 Needs root, so it RUNS only with ``--yes``; without it the
                 commands are printed.
  3. env      -- ``ICDEV_DATABASE_URL`` + ``ICDEV_STORAGE_BACKEND=postgresql``
                 in ``.env``. An existing value is KEPT, never overwritten.
                 On a package install (pacman/pipx) that ``.env`` is NOT in the
                 root-owned package dir: see ``project_root()``.
                 LLM keys are left to the operator; the providers configured /
                 missing are reported.
  4. mcp      -- ``icdev-unified`` registered with each installed harness, at
                 USER level (opencode / Pi config files via the omx-dx-01
                 generators; claude / codex through their own ``mcp add``).
  5. skill    -- ``icdev skill install`` (omx-dx-02).
  6. guard    -- ``icdev harness install-guard <opencode|pi> --global``
                 (omx-guard-01 / omx-guard-03) for each one installed.
  7. systemd  -- the omx-linux-01 ``--user`` units for ``tools/genesis/launch.py``;
                 enabled + started only with ``--start``. A package install
                 has no checkout to run: ``n/a``, and ``--start`` refuses.
  8. panel    -- Omarchy only (omx-panel-01): the ``omarchy-agent-usage-icdev``
                 collector into ``~/.local/bin``, the ``icdev.svg`` mark into the
                 agents plugin's ``assets/``, and a ``systemd --user`` timer that
                 refreshes the record. Read-only, so enabled without ``--start``.

OPTIONAL, only with ``--ghidra`` (rea-eng-02):

  ghidra    -- NSA Ghidra 12 (Apache-2.0) + JDK 21 from pacman, and an
               interpreter that can ``import pyghidra`` -- what
               ``tools/analyzers/ghidra_headless.py`` needs to decompile. Opt-in
               because it is a ~1 GB install most hosts never use; the pacman
               half needs root, so it RUNS only with ``--yes``. Not reversed by
               ``uninstall``: it is a system package. Windows: see
               docs/install/ghidra.md.

Every step probes first and reports ``skipped`` when already in place, so a
second run changes nothing. ``--dry-run`` prints the plan and writes nothing.
``status`` probes only. ``uninstall`` reverses 4-8 and NEVER touches the
database or ``.env``.

Every external command goes through one injectable runner (``Host``), which is
how the tests fake pacman / psql / systemctl / the harness CLIs.

Usage:
    icdev omarchy setup [--dry-run] [--yes] [--start] [--ghidra] [--json]
    icdev omarchy status [--ghidra] [--json]
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
#: Why --start refuses on a package install (omx-setup-02); deploy/aur/icdev.install says the same.
GENESIS_NEEDS_CHECKOUT = (
    "the genesis supervisor (tools/genesis/launch.py) needs a source checkout: it writes its "
    "pid lock, logs and data under its own root, which a package install keeps root-owned. "
    "Run it from a clone: git clone https://github.com/icdev-ai/icdev && cd icdev && "
    "python -m tools.cli omarchy setup --yes --start")


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


#: What `icdev init` writes into every project it scaffolds (omx-setup-02).
INIT_PROJECT_MARKERS = ("AGENTS.md", ".env.template")


def is_source_checkout(root: Path) -> bool:
    from icdev.core.paths import SOURCE_MARKERS
    return any((root / m).exists() for m in SOURCE_MARKERS)


def project_root(root: Path, environ: Dict[str, str], home: Path, cwd: Path) -> Path:
    """Where this host's ``.env`` lives (omx-setup-02).

    A source checkout is its own project. A PACKAGE install (pacman, pipx, pip)
    resolves ``root`` to the installed ``icdev`` package -- root-owned under
    /opt/icdev for the AUR -- so ``.env`` goes to, in order: $ICDEV_PROJECT_ROOT;
    the ``icdev init`` project enclosing the working directory; else
    ``$XDG_CONFIG_HOME/icdev``, which the user always owns.
    """
    declared = (environ.get("ICDEV_PROJECT_ROOT") or "").strip()
    if declared:
        return Path(declared).expanduser().resolve()
    if is_source_checkout(root):
        return root
    for d in (cwd, *cwd.parents):
        if all((d / m).is_file() for m in INIT_PROJECT_MARKERS):
            return d
    return Path(environ.get("XDG_CONFIG_HOME") or home / ".config") / "icdev"


@dataclass
class Host:
    root: Path
    home: Path
    environ: Dict[str, str]
    platform: str
    python: str
    which: Callable[[str], Optional[str]] = shutil.which
    run: Callable[..., Result] = _real_run
    #: where .env lives; None = root (a source checkout). See project_root().
    project: Optional[Path] = None

    @classmethod
    def real(cls) -> "Host":
        from icdev.core.paths import repo_root
        root, home, environ = repo_root().resolve(), Path.home(), dict(os.environ)
        return cls(root=root, home=home, environ=environ, platform=_host_platform(),
                   python=sys.executable, project=project_root(root, environ, home, Path.cwd()))

    @property
    def project_dir(self) -> Path:
        return self.project or self.root

    @property
    def env_file(self) -> Path:
        return self.project_dir / ".env"

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


#: Ready = pgvector AND a schema bootstrap_pg finished (omx-setup-02). The
#: extension alone is created BEFORE the schema step, so probing only it reported
#: `skipped` forever after a schema load that failed. bootstrap_pg creates
#: schema_migrations last-but-one and drops its partial-load sentinel last.
READY_SQL = ("SELECT 1 FROM pg_extension WHERE extname = 'vector' "
             "AND to_regclass('public.schema_migrations') IS NOT NULL "
             "AND to_regclass('public.schema_bootstrap_partial') IS NULL")


def plan_postgres(host: Host) -> Plan:
    _check_db_name()
    ready = host.run(["psql", "-h", "localhost", "-U", DB_ROLE, "-d", DB_NAME, "-tAc", READY_SQL])
    if ready.rc == 0 and ready.stdout.strip() == "1":
        return Plan(done=True, detail=f"database {DB_NAME} reachable with pgvector and the ICDEV schema")
    # The schema load (and ICDEV itself, once .env says postgresql) needs the
    # driver IN THIS interpreter -- the [postgresql] extra, not a base dependency.
    if host.run([host.python, "-c", "import psycopg2"]).rc != 0:
        return Plan(done=False, state="failed",
                    detail=f"the PostgreSQL driver (psycopg2) is not installed for {host.python}: "
                           "reinstall with the extra -- `omarchy-mise-install 'pipx:icdev[extras=postgresql]' icdev` "
                           "(mise), `pipx install --force 'icdev[postgresql]'` or "
                           "`pip install 'icdev[postgresql]'` -- then re-run")

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
        # NOT icdev-init-db: on postgresql it prints a pointer and creates
        # nothing. bootstrap_pg loads the consolidated snapshot, runs the newer
        # migrations, and exits non-zero on a partial load.
        Action("load the ICDEV schema (bootstrap_pg)",
               [host.python, "-m", "icdev.tools.db.bootstrap_pg"],
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
    if host.project_dir != host.root:
        detail = (f"package install: .env is {host.env_file} (set ICDEV_PROJECT_ROOT to "
                  f"{host.project_dir} so ICDEV reads it); " + detail)
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

    # The genesis units run tools/genesis/launch.py from a SOURCE CHECKOUT; the
    # wheel/AUR package ships neither the templates nor a launch.py at the root,
    # so a unit written there would crash-loop. Measured by the Arch Package
    # Smoke job (omx-ci-01): this step reported `failed` on every package install.
    # A REFUSAL, not a gap (omx-setup-02): launch.py keeps its pid lock, logs and
    # data under its own root, and a package install keeps that root-owned.
    if not (host.root / "scripts" / "systemd").is_dir():
        return Plan(done=not start, state="failed" if start else "n/a",
                    detail=f"{host.root} is a package install -- {GENESIS_NEEDS_CHECKOUT}")
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
# Step 8 -- the Omarchy agents-panel record (omx-panel-01)
# --------------------------------------------------------------------------- #

PANEL_BIN = "omarchy-agent-usage-icdev"
PANEL_TIMER = "icdev-agent-usage.timer"
PANEL_UNITS = ("icdev-agent-usage.service", PANEL_TIMER)
PANEL_MARKS = ("icdev.svg", "icdev-light.svg")


def _data_file(host: Host, rel: str) -> Path:
    """A packaged ``icdev/data`` file, in a checkout or the wheel."""
    for data in (host.root / "icdev" / "data", host.root / "data"):
        if (data / rel).is_file():
            return data / rel
    raise FileNotFoundError(f"packaged {rel} not found under {host.root}")


def render_collector(host: Host) -> str:
    """The collector with this interpreter and checkout root filled in."""
    text = _data_file(host, f"bin/{PANEL_BIN}").read_text(encoding="utf-8")
    # A shebang cannot carry whitespace; fall back to PATH's python3 then.
    shebang = f"#!{host.python}" if not any(c.isspace() for c in host.python) else "#!/usr/bin/env python3"
    _, _, rest = text.partition("\n")
    return (shebang + "\n" + rest).replace(
        '_INSTALLED_ROOT = ""', f"_INSTALLED_ROOT = {json.dumps(host.root.as_posix())}", 1)


def panel_assets_dir(host: Host) -> Path:
    base = host.environ.get("OMARCHY_PATH") or host.home / ".local" / "share" / "omarchy"
    return Path(base) / "shell" / "plugins" / "agents" / "assets"


def panel_usage_record(host: Host) -> Path:
    from tools.cli.omarchy_panel import AGENT_ID, usage_dir
    return usage_dir(host.environ, host.home) / f"{AGENT_ID}.json"


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def _marks_skipped(host: Host) -> bool:
    """A packaged Omarchy keeps the agents plugin under root-owned /usr/share/omarchy
    (omx-panel-02): the mark is optional, so it is skipped there -- never sudo-written."""
    assets = panel_assets_dir(host)
    return assets.is_dir() and not _writable(assets)


def _panel_files(host: Host) -> List[tuple]:
    """(target, content, executable) for everything the panel step writes."""
    files = [(host.home / ".local" / "bin" / PANEL_BIN, render_collector(host), True)]
    assets = panel_assets_dir(host)
    # The mark is optional: no agents plugin, or one the user cannot write, means no mark.
    if assets.is_dir() and not _marks_skipped(host):
        files += [(assets / m, _data_file(host, f"omarchy/{m}").read_text(encoding="utf-8"), False)
                  for m in PANEL_MARKS]
    if host.platform == "linux":
        files += [(host.unit_dir() / u, _data_file(host, f"omarchy/{u}").read_text(encoding="utf-8"), False)
                  for u in PANEL_UNITS]
    return files


def plan_panel(host: Host, det: Detected, remove: bool = False) -> Plan:
    if not det.omarchy:
        return Plan(done=True, state="n/a", detail="not on Omarchy: no agents panel")
    systemctl = host.platform == "linux" and bool(host.which("systemctl"))
    actions: List[Action] = []
    if remove:
        targets = [host.home / ".local" / "bin" / PANEL_BIN, panel_usage_record(host)]
        targets += [panel_assets_dir(host) / m for m in PANEL_MARKS]
        targets += [host.unit_dir() / u for u in PANEL_UNITS]
        # unlink needs a writable DIRECTORY: a mark in a root-owned packaged plugin was
        # never ours to write (omx-panel-02), so it is not ours to remove (omx-panel-03).
        present = [p for p in targets
                   if p.is_file() and os.access(p.parent, os.W_OK | os.X_OK)]
        units_present = any(p.parent == host.unit_dir() for p in present)
        if units_present and systemctl:
            actions.append(Action(f"disable {PANEL_TIMER}",
                                  ["systemctl", "--user", "disable", "--now", PANEL_TIMER]))
        actions += [Action(f"remove {p}", fn=p.unlink) for p in present]
        if units_present and systemctl:
            actions.append(Action("systemctl --user daemon-reload", ["systemctl", "--user", "daemon-reload"]))
        return Plan(done=not actions, actions=actions)

    units_changed = False
    for path, content, executable in _panel_files(host):
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            continue
        units_changed = units_changed or path.parent == host.unit_dir()

        def _write(path=path, content=content, executable=executable):
            path.parent.mkdir(parents=True, exist_ok=True)
            # newline="\n": a shebang / unit file with CRLF does not run on Linux.
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            if executable:
                path.chmod(0o755)
        actions.append(Action(f"write {path}", fn=_write))
    if units_changed and systemctl:
        actions.append(Action("systemctl --user daemon-reload", ["systemctl", "--user", "daemon-reload"]))
    # Enabled without --start, unlike genesis: the timer only READS the board
    # and the ledger, and a record nothing refreshes is a frozen panel.
    if systemctl and not (host.ok(["systemctl", "--user", "is-enabled", "--quiet", PANEL_TIMER])
                          and host.ok(["systemctl", "--user", "is-active", "--quiet", PANEL_TIMER])):
        actions.append(Action(f"enable + start {PANEL_TIMER}",
                              ["systemctl", "--user", "enable", "--now", PANEL_TIMER]))
    detail = f"record: {panel_usage_record(host)}"
    if _marks_skipped(host):
        detail += f"; mark skipped: {panel_assets_dir(host)} is not writable"
    if not systemctl:
        detail += f"; no systemd --user: run `{PANEL_BIN} --write` to refresh it"
    return Plan(done=not actions, actions=actions, detail=detail)


# --------------------------------------------------------------------------- #
# Optional -- Ghidra 12 + PyGhidra (rea-eng-02)
# --------------------------------------------------------------------------- #

GHIDRA_PACKAGES = ("ghidra", "jdk21-openjdk")
#: Where Arch's ``ghidra`` package installs. ICDEV_GHIDRA_HOME outranks it.
GHIDRA_DEFAULT_HOME = "/opt/ghidra"
#: PyPI fallback when the install carries no bundled wheel. 3.x is the line
#: that matches Ghidra 12 ("PyGhidra 3.0.0 (compatible with Ghidra 12.0 and later)").
PYGHIDRA_REQUIREMENT = "pyghidra>=3.0"


def _pyghidra_python(host: Host) -> Optional[str]:
    """The first interpreter that can ``import pyghidra``: ICDEV_GHIDRA_PYTHON,
    this ICDEV's own, then ``python3`` on PATH (Arch's ``ghidra`` package puts
    ``pyghidra`` in the system python)."""
    from tools.analyzers.ghidra_headless import ENV_PYTHON
    seen: List[str] = []
    for cand in (host.environ.get(ENV_PYTHON), host.python, host.which("python3")):
        if not cand or cand in seen:
            continue
        seen.append(cand)
        if host.ok([cand, "-c", "import pyghidra"]):
            return cand
    return None


def plan_ghidra(host: Host) -> Plan:
    from tools.analyzers.ghidra_headless import (
        ENV_GHIDRA_HOME, MIN_GHIDRA_VERSION, _version_tuple, ghidra_version)
    home = host.environ.get(ENV_GHIDRA_HOME) or GHIDRA_DEFAULT_HOME
    version = ghidra_version(home)
    parsed = _version_tuple(version)
    current = parsed is not None and parsed[:2] >= MIN_GHIDRA_VERSION
    python = _pyghidra_python(host)
    want = "%s.%s" % MIN_GHIDRA_VERSION
    if current and python:
        return Plan(done=True, detail=f"Ghidra {version} at {home}; pyghidra via {python}")

    actions: List[Action] = []
    needs_yes = False
    if not current:
        if host.platform != "linux" or not host.which("pacman"):
            return Plan(done=False, state="pending",
                        detail=(f"Ghidra {version or 'not found'} at {home}; {want}+ is required. "
                                "No pacman here: install the release zip + a JDK 21 and set "
                                f"{ENV_GHIDRA_HOME} -- docs/install/ghidra.md"))
        actions.append(Action(f"install {' + '.join(GHIDRA_PACKAGES)}",
                              ["sudo", "pacman", "-S", "--needed", "--noconfirm", *GHIDRA_PACKAGES]))
        needs_yes = True
    if not python:
        dist = Path(home) / "Ghidra" / "Features" / "PyGhidra" / "pypkg" / "dist"
        source = (["--no-index", "-f", str(dist), "pyghidra"] if dist.is_dir()
                  else [PYGHIDRA_REQUIREMENT])
        # Skipped when the pacman step just put pyghidra into the system python.
        probe = host.which("python3") or "python3"
        actions.append(Action(f"install pyghidra into {host.python}",
                              [host.python, "-m", "pip", "install", *source],
                              unless=[probe, "-c", "import pyghidra"], unless_output=False))
    detail = (f"Ghidra {version} at {home}" if current
              else f"Ghidra {version or 'not found'} at {home}; {want}+ is required")
    detail += "; pyghidra " + ("ok" if python else "not importable")
    return Plan(done=False, actions=actions, needs_yes=needs_yes, detail=detail)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

STEPS = ("postgres", "env", "mcp", "skill", "guard", "systemd", "panel")
REVERSIBLE = ("mcp", "skill", "guard", "systemd", "panel")
#: Run only when asked for. Listed in every setup report so the offer is seen.
OPTIONAL_STEPS = {"ghidra": "--ghidra: Ghidra 12 + JDK 21 + PyGhidra, the optional decompiler"}


def _plans(host: Host, det: Detected, *, start: bool = False, remove: bool = False,
           ghidra: bool = False) -> Dict[str, Callable[[], Plan]]:
    plans: Dict[str, Callable[[], Plan]] = {
        "mcp": lambda: plan_mcp(host, det, remove),
        "skill": lambda: plan_skill(host, det, remove),
        "guard": lambda: plan_guard(host, det, remove),
        "systemd": lambda: plan_systemd(host, start, remove),
        "panel": lambda: plan_panel(host, det, remove),
    }
    if not remove:
        plans = {"postgres": lambda: plan_postgres(host), "env": lambda: plan_env(host), **plans}
        if ghidra:
            plans["ghidra"] = lambda: plan_ghidra(host)
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
        start: bool = False, ghidra: bool = False) -> Dict:
    det = detect(host)
    remove = command == "uninstall"
    mode = "status" if command == "status" else ("dry-run" if dry_run else "apply")
    report = {"command": command, "mode": mode, "omarchy": det.omarchy, "harnesses": det.harnesses,
              "host": ("Omarchy" if det.omarchy else
                       "not on Omarchy: plain Arch/Linux, Omarchy desktop bits skipped"),
              "steps": [],
              "optional": [] if remove or ghidra else list(OPTIONAL_STEPS.values())}
    for name, make in _plans(host, det, start=start, remove=remove, ghidra=ghidra).items():
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
    for offer in report.get("optional", []):
        print(f"  optional: {offer}")


def main(argv: Optional[List[str]] = None, host: Optional[Host] = None) -> int:
    ap = argparse.ArgumentParser(prog="icdev omarchy", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("setup", help="Bring this host to a running ICDEV (idempotent).")
    sp.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    sp.add_argument("--yes", action="store_true", help="run the root (sudo) PostgreSQL steps")
    sp.add_argument("--start", action="store_true", help="enable + start the genesis systemd timer")
    sp.add_argument("--ghidra", action="store_true",
                    help="also install Ghidra 12 + JDK 21 + PyGhidra (optional decompiler)")
    sp.add_argument("--json", action="store_true")
    st = sub.add_parser("status", help="Report each step's state.")
    st.add_argument("--ghidra", action="store_true", help="also probe the optional Ghidra step")
    st.add_argument("--json", action="store_true")
    up = sub.add_parser("uninstall", help="Reverse steps 4-8 (never drops the database).")
    up.add_argument("--dry-run", action="store_true")
    up.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = run(args.cmd, host or Host.real(), dry_run=getattr(args, "dry_run", False),
                 yes=getattr(args, "yes", False), start=getattr(args, "start", False),
                 ghidra=getattr(args, "ghidra", False))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print(report)
    return int(report["exit"])


if __name__ == "__main__":
    sys.exit(main())
