#!/usr/bin/env python3
# CUI // SP-CTI
"""`icdev setup` — guided post-install configuration for Windows, Linux and macOS.

`pip install icdev` installs the package; `icdev init` copies the project
payload out. Neither tells you which LLM to use, which database to point at, or
how to map a volume into a container on your OS. That was left to reading
docs — and the two things people got wrong most often were a Windows Docker
volume path and an LLM key that silently did not work.

This walks through it in order, writing everything to `.env`:

    1. Environment   — OS, Python, Docker, Ollama, PostgreSQL detection
    2. LLM           — primary + fallback chain, with a live probe
    3. Database      — SQLite (zero-config) or PostgreSQL (+pgvector for RAG)
    4. RAG + KG      — embedding provider, dimension, vector store
    5. Docker        — generate a docker-compose.yml matched to the answers
    6. Components    — hand off to the existing component TUI

DESIGN NOTES

Every step is SKIPPABLE and every step is IDEMPOTENT. A user who only wants to
change their LLM should not have to re-answer database questions, and re-running
setup must never destroy a working `.env`.

The LLM probe is bounded and optional. A key that is present but rejected is
worse than a key that is absent: this session traced weeks of degraded retrieval
to a stale OpenAI key that failed over silently. `--no-probe` exists because an
air-gapped install has nothing to probe and should not wait for a timeout.

Compose generation is OS-aware because that is where the real friction is.
Windows bind mounts need a forward-slashed absolute path (`C:/ai/proj/data`),
which is not what `os.path.join` produces and not what most guides show.

STDLIB ONLY — no rich/textual/curses. This has to run on a fresh air-gapped box
before anything optional is installed.

CLI::

    icdev setup                      # guided wizard (default)
    icdev setup --components         # jump straight to the component TUI
    icdev setup --non-interactive    # accept detected defaults, write nothing else
    icdev setup --docker-only        # just (re)generate docker-compose.yml
    icdev setup --json               # machine-readable environment report
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tools.cli import proxy_detect

# --------------------------------------------------------------------------- #
# Environment detection
# --------------------------------------------------------------------------- #

#: Ports we probe for a service that is already running locally.
_PG_DEFAULT_PORT = 5432
_OLLAMA_DEFAULT_PORT = 11434


def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    """True when something is listening. Deliberately short timeout.

    This runs before the user has answered anything; a multi-second stall on a
    closed port makes the wizard feel broken.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class Environment:
    """What we could detect about the machine, before asking anything."""

    os_name: str = ""
    os_release: str = ""
    python_version: str = ""
    is_windows: bool = False
    is_wsl: bool = False
    docker: bool = False
    docker_compose: bool = False
    postgres_local: bool = False
    ollama_local: bool = False
    project_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "os": self.os_name,
            "os_release": self.os_release,
            "python": self.python_version,
            "is_windows": self.is_windows,
            "is_wsl": self.is_wsl,
            "docker": self.docker,
            "docker_compose": self.docker_compose,
            "postgres_local": self.postgres_local,
            "ollama_local": self.ollama_local,
            "project_dir": self.project_dir,
        }


def detect_environment(project_dir: Path | None = None) -> Environment:
    """Probe the host. Never raises — a failed probe is just a False."""
    system = platform.system()
    env = Environment(
        os_name=system,
        os_release=platform.release(),
        python_version=platform.python_version(),
        is_windows=(system == "Windows"),
        project_dir=str((project_dir or Path.cwd()).resolve()),
    )

    # WSL matters: Docker volume paths follow LINUX rules there even though the
    # user is "on Windows", which is a classic source of broken bind mounts.
    if system == "Linux":
        try:
            env.is_wsl = "microsoft" in Path("/proc/version").read_text(
                encoding="utf-8", errors="replace").lower()
        except OSError:
            env.is_wsl = False

    env.docker = shutil.which("docker") is not None
    if env.docker:
        # `docker compose` (v2 plugin) vs the legacy `docker-compose` binary.
        env.docker_compose = shutil.which("docker-compose") is not None or True

    env.postgres_local = _port_open("127.0.0.1", _PG_DEFAULT_PORT)
    env.ollama_local = _port_open("127.0.0.1", _OLLAMA_DEFAULT_PORT)
    return env


# --------------------------------------------------------------------------- #
# LLM providers
# --------------------------------------------------------------------------- #

@dataclass
class ProviderChoice:
    """One provider the user can pick for the primary or fallback slot."""

    key: str
    label: str
    env_key: str = ""          # API-key env var; empty for local providers
    local: bool = False
    #: Reaches the model through something that authenticates on our behalf —
    #: a corporate gateway or egress proxy. There is NO key to configure, so a
    #: missing key must not be reported as a misconfiguration.
    keyless: bool = False
    note: str = ""


#: Offered in the wizard. Local providers first when the machine has them —
#: an air-gapped or offline user should not have to scroll past four cloud
#: vendors to find the one that works.
PROVIDERS: tuple[ProviderChoice, ...] = (
    ProviderChoice("anthropic", "Anthropic (Claude)", "ANTHROPIC_API_KEY"),
    ProviderChoice("openai", "OpenAI / OpenAI-compatible", "OPENAI_API_KEY"),
    ProviderChoice("gemini", "Google Gemini", "GOOGLE_API_KEY"),
    ProviderChoice("bedrock", "AWS Bedrock (GovCloud/CUI)", "",
                   note="uses AWS credentials, not an API key"),
    ProviderChoice("ollama", "Ollama (local, air-gap safe)", "", local=True),
    ProviderChoice("gateway", "Corporate LLM gateway / proxy", "",
                   keyless=True,
                   note="no API key — the gateway authenticates upstream"),
)


def provider_by_key(key: str) -> ProviderChoice | None:
    for p in PROVIDERS:
        if p.key == key:
            return p
    return None


def probe_provider(choice: ProviderChoice, env: dict, timeout: float = 6.0) -> dict:
    """Cheap liveness check for one provider.

    Returns ``{ok, detail}``. Never raises: a probe failure is information, not
    an error — the wizard reports it and lets the user continue, because a
    provider can be legitimately unreachable at setup time (VPN down, key not
    issued yet) and still be the right choice.

    A present-but-rejected key is the case worth catching. It fails over
    silently at runtime, which is how a stale OpenAI key degraded retrieval for
    weeks before anyone noticed.
    """
    if choice.local:
        host = env.get("OLLAMA_BASE_URL", "http://localhost:11434")
        port = _OLLAMA_DEFAULT_PORT
        if ":" in host.rsplit("/", 1)[-1]:
            try:
                port = int(host.rsplit(":", 1)[-1].split("/")[0])
            except ValueError:
                port = _OLLAMA_DEFAULT_PORT
        ok = _port_open("127.0.0.1", port, timeout=min(timeout, 1.0))
        return {"ok": ok, "detail": "listening" if ok else f"nothing on :{port}"}

    if choice.key == "gateway":
        url = env.get("ICDEV_LLM_GATEWAY_URL", "")
        if not url:
            return {"ok": False, "detail": "ICDEV_LLM_GATEWAY_URL not set"}
        host, port = _split_host_port(url)
        if not host:
            return {"ok": False, "detail": f"cannot parse {url}"}
        ok = _port_open(host, port, timeout=timeout)
        return {"ok": ok, "detail": f"{'reachable' if ok else 'unreachable'} "
                                    f"at {host}:{port}"}

    # A keyless provider is reached through a gateway or proxy that supplies
    # credentials upstream. Reporting "key not set" there would be reporting
    # the intended configuration as a fault.
    if choice.env_key and not env.get(choice.env_key) and not choice.keyless:
        return {"ok": False, "detail": f"{choice.env_key} not set"}

    # Deliberately does not import provider SDKs: this must work before the
    # user has installed any optional extra. Reachability of the API host is
    # the strongest signal available without a vendor client.
    hosts = {
        "anthropic": "api.anthropic.com",
        "openai": "api.openai.com",
        "gemini": "generativelanguage.googleapis.com",
        "bedrock": "bedrock-runtime.us-gov-west-1.amazonaws.com",
    }
    host = hosts.get(choice.key, "")
    if not host:
        return {"ok": True, "detail": "no probe available"}
    ok = _port_open(host, 443, timeout=timeout)
    if ok:
        return {"ok": True, "detail": "reachable"}

    # Behind a corporate proxy a direct connection to the vendor is SUPPOSED to
    # fail — that is the whole point of the proxy. Calling that FAIL sends the
    # user chasing a network problem they do not have.
    if _proxy_configured(env):
        return {"ok": True,
                "detail": "no direct route (expected — traffic goes via proxy)"}
    return {"ok": False, "detail": "unreachable"}


def _split_host_port(url: str, default_port: int = 443) -> tuple:
    """Host and port from a URL, tolerating a bare `host:port`."""
    from urllib.parse import urlparse

    parsed = urlparse(url if "//" in url else f"//{url}", scheme="https")
    host = parsed.hostname or ""
    port = parsed.port or (80 if parsed.scheme == "http" else default_port)
    return host, port


def _proxy_configured(env: dict) -> bool:
    """True when this machine routes egress through a proxy."""
    keys = ("ICDEV_LLM_PROXY", "ICDEV_LLM_PROXY_CMD", "HTTPS_PROXY",
            "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY")
    if any((env.get(k) or "").strip() for k in keys):
        return True
    return proxy_detect.detect_proxy().found


# --------------------------------------------------------------------------- #
# docker-compose generation
# --------------------------------------------------------------------------- #

def compose_volume_path(project_dir: Path, env: Environment) -> str:
    """Bind-mount source path in the form Docker actually accepts.

    THIS is the step people get wrong. On Windows, Docker Desktop wants a
    forward-slashed absolute path (`C:/ai/proj/data`) — not the backslashes
    `os.path` produces, and not the `/c/ai/...` form some guides show. Under
    WSL the rules are the LINUX rules even though the user thinks of the
    machine as Windows, so WSL is detected separately.
    """
    data = (project_dir / "data").resolve()
    if env.is_windows and not env.is_wsl:
        return str(data).replace("\\", "/")
    return "./data"


def render_compose(env: Environment, *, use_postgres: bool, project_dir: Path,
                   dashboard_port: int = 5050, pg_password: str = "icdev",
                   pg_port: int = _PG_DEFAULT_PORT) -> str:
    """Produce a docker-compose.yml matched to the answers.

    pgvector rather than stock postgres: ICDEV stores embeddings in a `vector`
    column, so a plain `postgres:16` image cannot host the RAG schema at all.
    """
    vol = compose_volume_path(project_dir, env)
    lines = [
        "# Generated by `icdev setup`. Safe to edit and re-generate.",
        f"# Host: {env.os_name} {env.os_release}"
        + (" (WSL)" if env.is_wsl else ""),
        "#",
        "# Volume source is written in the form Docker accepts on THIS host:",
        f"#   {vol}",
        "",
        "services:",
    ]

    if use_postgres:
        lines += [
            "  postgres:",
            "    # pgvector, not stock postgres: ICDEV stores embeddings in a",
            "    # `vector` column, so plain postgres:16 cannot host the RAG schema.",
            "    image: pgvector/pgvector:pg16",
            "    environment:",
            "      POSTGRES_DB: icdev",
            "      POSTGRES_USER: icdev",
            f"      POSTGRES_PASSWORD: {pg_password}",
            "    ports:",
            # host:container. Only the HOST side moves when 5432 is taken —
            # postgres inside the container always listens on 5432.
            f'      - "{pg_port}:{_PG_DEFAULT_PORT}"',
            "    volumes:",
            f"      - {vol}/postgres:/var/lib/postgresql/data",
            "    healthcheck:",
            '      test: ["CMD-SHELL", "pg_isready -U icdev"]',
            "      interval: 5s",
            "      retries: 10",
            "",
        ]

    if use_postgres and pg_port != _PG_DEFAULT_PORT:
        lines.insert(len(lines) - 1,
                     f"# NOTE: published on host port {pg_port} because "
                     f"{_PG_DEFAULT_PORT} was already in use.")

    lines += [
        "  icdev:",
        "    image: python:3.11-slim",
        "    working_dir: /app",
        "    command: >-",
        '      bash -c "pip install --no-cache-dir icdev &&',
        '      icdev init --force && icdev-init-db && icdev-dashboard"',
        "    ports:",
        f'      - "{dashboard_port}:5050"',
        "    volumes:",
        f"      - {vol}:/app/data",
        "    environment:",
    ]
    if use_postgres:
        lines += [
            "      ICDEV_STORAGE_BACKEND: postgresql",
            "      # 'postgres' is the compose service name, not localhost —",
            "      # inside the network localhost is the icdev container itself.",
            f"      ICDEV_DATABASE_URL: postgresql://icdev:{pg_password}@postgres:5432/icdev",
            "    depends_on:",
            "      postgres:",
            "        condition: service_healthy",
        ]
    else:
        lines += [
            "      ICDEV_STORAGE_BACKEND: sqlite",
            "      ICDEV_DB_PATH: /app/data/icdev.db",
        ]
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# .env writing
# --------------------------------------------------------------------------- #

def read_env(env_file: Path) -> dict:
    """Parse a .env into a dict. Comments and blanks ignored."""
    out: dict[str, str] = {}
    if not env_file.is_file():
        return out
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def update_env(env_file: Path, updates: dict, *, dry_run: bool = False) -> dict:
    """Set each key in ``updates``, preserving comments, order and unrelated keys.

    Rewriting the file from a dict would discard the extensive commentary
    `icdev init` writes into `.env` — which is where users learn what a flag
    does. Existing keys are edited in place; new ones are appended.
    """
    lines = (env_file.read_text(encoding="utf-8", errors="replace").splitlines()
             if env_file.is_file() else [])
    remaining = dict(updates)

    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    if remaining:
        lines.append("")
        lines.append("# ── Added by `icdev setup` ──────────────────────────────")
        for k, v in remaining.items():
            lines.append(f"{k}={v}")

    if not dry_run:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"written": not dry_run, "keys": sorted(updates), "path": str(env_file)}


def llm_env_updates(primary: str, fallback: str, keys: dict) -> dict:
    """.env keys for the chosen chain.

    Both slots are written even when they are the same provider: an explicit
    fallback that equals the primary is a deliberate statement ("no fallback"),
    whereas an empty one reads as "not configured yet".
    """
    out = {"ICDEV_LLM_PROVIDER": primary, "ICDEV_LLM_FALLBACK_PROVIDER": fallback}
    for env_key, value in keys.items():
        if value:
            out[env_key] = value
    if "ollama" in (primary, fallback):
        out.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
    return out


def db_env_updates(backend: str, *, dsn: str = "", db_path: str = "") -> dict:
    out: dict[str, str] = {"ICDEV_STORAGE_BACKEND": backend}
    if backend == "postgresql" and dsn:
        out["ICDEV_DATABASE_URL"] = dsn
    if backend == "sqlite" and db_path:
        out["ICDEV_DB_PATH"] = db_path
    return out


def rag_env_updates(*, enabled: bool, embed_dim: int = 768) -> dict:
    """RAG + KG toggles.

    Dimension is a property of the EMBEDDING provider, not the chat model —
    768 matches every air-gap provider (nomic / gemini-004 / ibm-slate) while
    1536 matches only cloud OpenAI. Defaulting to 768 keeps an air-gapped
    install working without a schema migration.
    """
    return {
        "ICDEV_RAG_ENABLED": "true" if enabled else "false",
        "ICDEV_KG_ENABLED": "true" if enabled else "false",
        "ICDEV_EMBEDDING_DIM": str(embed_dim),
    }


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

@dataclass
class SetupReport:
    environment: Environment = field(default_factory=Environment)
    steps: list = field(default_factory=list)
    env_file: str = ""
    compose_file: str = ""

    def to_dict(self) -> dict:
        return {
            "environment": self.environment.to_dict(),
            "steps": self.steps,
            "env_file": self.env_file,
            "compose_file": self.compose_file,
        }


def os_guidance(env: Environment) -> list:
    """Per-OS notes shown before anything is asked.

    Each line exists because it is a step someone actually got stuck on, not
    because the OS deserves a paragraph.
    """
    tips = []
    if env.is_windows and not env.is_wsl:
        tips.append("Windows: Docker volume paths must use forward slashes "
                    "(C:/ai/proj/data). The generated compose file does this for you.")
        tips.append("Windows: run the dashboard from a terminal with the venv "
                    "activated, or use the icdev-dashboard.exe shim in Scripts/.")
    elif env.is_wsl:
        tips.append("WSL detected: Docker volume paths follow LINUX rules here. "
                    "Keep the project inside the WSL filesystem (~/...) — bind "
                    "mounts from /mnt/c are slow and break file watching.")
    elif env.os_name == "Darwin":
        tips.append("macOS: Docker Desktop must have file sharing enabled for "
                    "the project directory (Settings → Resources → File Sharing).")
    elif env.os_name == "Linux":
        tips.append("Linux: if `docker` needs sudo, add yourself to the docker "
                    "group (`sudo usermod -aG docker $USER`) and re-login.")
    if not env.docker:
        tips.append("Docker not found — SQLite works with no container at all. "
                    "Choose SQLite unless you specifically need PostgreSQL.")
    if not env.ollama_local and not env.docker:
        tips.append("No local Ollama detected: a cloud LLM key will be needed, "
                    "or install Ollama for an air-gap-safe local model.")
    return tips


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _ask(prompt: str, default: str = "", choices: list | None = None) -> str:
    """One question. Returns the default when stdin is not a TTY."""
    if not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            ans = input(f"{prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return default
        if not ans:
            return default
        if choices and ans not in choices:
            print(f"  choose one of: {', '.join(choices)}")
            continue
        return ans


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="icdev setup",
        description="Guided post-install configuration for Windows, Linux and macOS.")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--components", action="store_true",
                    help="Skip the wizard; open the component enable/disable TUI.")
    ap.add_argument("--docker-only", action="store_true",
                    help="Only (re)generate docker-compose.yml.")
    ap.add_argument("--non-interactive", action="store_true",
                    help="Accept detected defaults; ask nothing.")
    ap.add_argument("--no-probe", action="store_true",
                    help="Skip LLM reachability probes (air-gapped installs).")
    ap.add_argument("--postgres", action="store_true", help="Assume PostgreSQL.")
    ap.add_argument("--provision-db", action="store_true",
                    help="Create the database and vector store if they do not exist.")
    ap.add_argument("--dry-run", action="store_true", help="Write nothing.")
    ap.add_argument("--json", action="store_true", help="Machine-readable report.")
    args = ap.parse_args(argv)

    if args.components:
        from tools.cli.setup import main as components_main

        return components_main(["--env-file", args.env_file])

    project_dir = Path.cwd()
    env_file = Path(args.env_file).resolve()
    env = detect_environment(project_dir)
    report = SetupReport(environment=env, env_file=str(env_file))
    existing = read_env(env_file)

    if args.json:
        report.steps.append({"step": "detect", "guidance": os_guidance(env)})
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    use_pg = args.postgres or (env.postgres_local and not args.non_interactive)

    if args.docker_only:
        compose = project_dir / "docker-compose.yml"
        text = render_compose(env, use_postgres=use_pg, project_dir=project_dir)
        if not args.dry_run:
            compose.write_text(text, encoding="utf-8")
        report.compose_file = str(compose)
        print(f"docker-compose.yml {'would be' if args.dry_run else ''} written: {compose}")
        return 0

    print("=" * 68)
    print("  ICDEV setup")
    print("=" * 68)
    print(f"  OS      : {env.os_name} {env.os_release}"
          + (" (WSL)" if env.is_wsl else ""))
    print(f"  Python  : {env.python_version}")
    print(f"  Docker  : {'yes' if env.docker else 'no'}")
    print(f"  Postgres: {'listening on :5432' if env.postgres_local else 'not detected'}")
    print(f"  Ollama  : {'listening on :11434' if env.ollama_local else 'not detected'}")
    for tip in os_guidance(env):
        print(f"  - {tip}")
    print()

    # ── LLM ────────────────────────────────────────────────────────────────
    default_primary = "ollama" if env.ollama_local else "anthropic"
    if args.non_interactive:
        primary, fallback = default_primary, "ollama"
        keys: dict = {}
    else:
        print("LLM providers:")
        for p in PROVIDERS:
            mark = " (detected)" if p.local and env.ollama_local else ""
            print(f"  {p.key:<10} {p.label}{mark}")
        primary = _ask("Primary provider", default_primary,
                       [p.key for p in PROVIDERS])
        fallback = _ask("Fallback provider", "ollama", [p.key for p in PROVIDERS])
        keys = {}
        for key in (primary, fallback):
            ch = provider_by_key(key)
            if ch and ch.env_key and not existing.get(ch.env_key):
                val = _ask(f"  {ch.env_key} (blank to skip)", "")
                if val:
                    keys[ch.env_key] = val

    updates = llm_env_updates(primary, fallback, keys)

    # ── Gateway ────────────────────────────────────────────────────────────
    if "gateway" in (primary, fallback):
        url = existing.get("ICDEV_LLM_GATEWAY_URL", "")
        if not url and not args.non_interactive:
            url = _ask("  Gateway base URL (OpenAI-compatible, e.g. "
                       "https://llm.corp.example/v1)", "")
        if url:
            updates["ICDEV_LLM_GATEWAY_URL"] = url
        print("  Gateway providers need no API key — ICDEV sends the "
              "placeholder 'not-needed' and the gateway authenticates upstream.")

    # ── Proxy ──────────────────────────────────────────────────────────────
    # Adopting the proxy the machine already uses, rather than asking the user
    # to retype it, is the difference between setup working first try in an
    # enterprise and appearing to hang on an unreachable API host.
    proxy = proxy_detect.detect_proxy()
    print()
    if proxy.found:
        shown = proxy.url or proxy.pac_url or "(resolved per call)"
        print(f"Proxy detected: {shown}  [{proxy.source}]")
    else:
        print("Proxy: none detected")
    for tip in proxy_detect.guidance(proxy):
        print(f"  - {tip}")

    proxy_cmd = ""
    if not args.non_interactive:
        if not proxy.found:
            manual = _ask("Proxy URL (blank for direct connection)", "")
            if manual:
                proxy = proxy_detect.ProxyInfo(
                    url=manual, source="manual", detail="entered at setup")
        if proxy.found or proxy.source == "manual":
            # The single question that decides whether this config survives the
            # next rotation. A command is re-run per call; a URL is not.
            rotates = _ask("  Does this proxy rotate or change? (y/n)",
                           "y" if proxy.rotating else "n", ["y", "n"]) == "y"
            if rotates and proxy.source not in ("env", "icdev-command"):
                proxy_cmd = _ask(
                    "  Command that prints the CURRENT proxy URL "
                    "(blank to re-read the OS environment each call)", "")

    updates.update(proxy_detect.proxy_env_updates(proxy, command=proxy_cmd,
                                                  ttl_seconds=60 if proxy_cmd else 0))
    report.steps.append({"step": "proxy", "source": proxy.source,
                         "found": proxy.found, "rotating": proxy.rotating,
                         "command": bool(proxy_cmd)})

    if not args.no_probe:
        merged = {**existing, **updates}
        for slot, key in (("primary", primary), ("fallback", fallback)):
            ch = provider_by_key(key)
            if not ch:
                continue
            r = probe_provider(ch, merged)
            print(f"  probe {slot:<8} {key:<10} {'OK' if r['ok'] else 'FAIL'} — {r['detail']}")
            report.steps.append({"step": "probe", "slot": slot, "provider": key, **r})

    # ── Database ───────────────────────────────────────────────────────────
    if not args.non_interactive:
        use_pg = _ask("Database (sqlite/postgresql)",
                      "postgresql" if use_pg else "sqlite",
                      ["sqlite", "postgresql"]) == "postgresql"
    backend = "postgresql" if use_pg else "sqlite"
    dsn = existing.get("ICDEV_DATABASE_URL", "")
    if use_pg and not dsn:
        dsn = "postgresql://icdev:icdev@localhost:5432/icdev"
    updates.update(db_env_updates(
        backend, dsn=dsn, db_path=str((project_dir / "data" / "icdev.db"))))

    # ── RAG + KG ───────────────────────────────────────────────────────────
    updates.update(rag_env_updates(enabled=True, embed_dim=768))

    res = update_env(env_file, updates, dry_run=args.dry_run)
    report.steps.append({"step": "env", **res})

    # ── Docker ─────────────────────────────────────────────────────────────
    if env.docker:
        compose = project_dir / "docker-compose.yml"
        text = render_compose(env, use_postgres=use_pg, project_dir=project_dir)
        if not args.dry_run:
            compose.write_text(text, encoding="utf-8")
        report.compose_file = str(compose)
        report.steps.append({"step": "compose", "path": str(compose)})

    # ── Database + vector store ────────────────────────────────────────────
    # Writing a DSN does not make a database exist. On a fresh machine the
    # first real failure is a connection refused, or a `CREATE EXTENSION vector`
    # that cannot work because the running image has no pgvector at all.
    if args.provision_db:
        from tools.cli.provision_db import check_sqlite, provision

        if use_pg:
            pres = provision(dsn, use_docker=env.docker,
                             compose_file=project_dir / "docker-compose.yml",
                             dry_run=args.dry_run)
        else:
            st = check_sqlite(project_dir / "data" / "icdev.db")
            pres = {"ok": st.ready, "steps": [], "status": st.to_dict()}
        report.steps.append({"step": "provision-db", **pres})
        s = pres["status"]
        print(f"  database ready : {s['ready']}"
              + (f"  (missing: {', '.join(s['missing'])})" if s["missing"] else ""))
        if pres.get("hint"):
            print(f"  {pres['hint']}")

    print()
    print("=" * 68)
    print(f"  .env {'(dry run)' if args.dry_run else 'written'}: {env_file}")
    if report.compose_file:
        print(f"  docker-compose.yml: {report.compose_file}")
    print("=" * 68)
    print("Next:")
    print("  icdev setup --components   # turn canvases/features on")
    print("  icdev-init-db              # create the database")
    print("  icdev-dashboard            # http://localhost:5050")
    if report.compose_file:
        print("  docker compose up -d       # or run it all in containers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
