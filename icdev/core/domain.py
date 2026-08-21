# CUI // SP-CTI
"""The ``icdev_domain.yaml`` declaration — what a parent tells the core.

A parent declares its key, env prefix, database, sensitivity model, component
registry, reflex packs, dashboard port, board identity, MCP composition, trust
policy and CI gate set in one file at its repository root. The core reads that
file and nothing else to learn whose domain it is serving.

Two deliberate properties:

* **The key comes from the FILE, never from an env var.** Two parents on one
  machine share a shell; a ``$ICDEV_DOMAIN=ft`` exported once would make the
  IT checkout load FT's database. The file found next to the code (or, for an
  installed kernel, in the current directory) is the only authority.
* **A missing file is NOT an error by default.** The published wheel and every
  project scaffolded by ``icdev init`` have no declaration and must keep
  working byte-for-byte. They receive :data:`BUILTIN_DEFAULT`, which encodes
  ICDEV[IT]'s constants as they stood on 2026-08-21 and reports
  ``source == "builtin_default"`` so ``icdev status`` can say so. Set
  ``ICDEV_REQUIRE_DOMAIN=1`` on a deployment that must refuse to start
  undeclared.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from icdev.core.paths import DOMAIN_FILE, repo_root

REQUIRE_DOMAIN_ENV = "ICDEV_REQUIRE_DOMAIN"
SCHEMA_VERSION = 1

#: ICDEV[IT] as it stood before any parent declared itself. Used when no
#: ``icdev_domain.yaml`` is found; MUST match the checked-in file's values.
BUILTIN_DEFAULT: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "domain": {"key": "it", "name": "ICDEV[IT]", "env_prefix": "ICDEV"},
    "paths": {"data": "data", "forge": ["goals", "args", "context", "hardprompts"]},
    "db": {
        "backend": "postgresql",
        "name_env": "ICDEV_PG_DATABASE",
        "dsn_env": "ICDEV_DATABASE_URL",
        "sqlite_path_env": "ICDEV_DB_PATH",
        "databases": ["icdev"],
        "migrations": ["tools/db/migrations"],
    },
    "sensitivity": {
        "column": "classification",
        "labels_file": "args/classification_profiles.yaml",
        "default": "public",
        "egress_restricted": ["cui", "cui_sp_cti", "secret", "itar"],
        "levels": ["IL2", "IL4", "IL5", "IL6"],
    },
    "components": "args/component_registry.yaml",
    "reflexes": {"packs": []},
    "dashboard": {"port": 5050, "blueprints": "registry"},
    "kanban": {"board": "it", "external_repos": "args/kanban_external_repos.yaml"},
    "mcp": {"servers": ["core", "compliance", "devsecops", "builder", "knowledge", "maintenance"]},
    "trust": {"citation_required": True, "promotion_gates": ["coherence", "gated_tests"]},
    "ci": {
        "gated_lists": "args/ci_test_files",
        "compat_suite": "args/ci_test_files/core_compat.txt",
        "census_dir": "args",
    },
}

_KEY_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")


class DomainError(RuntimeError):
    """The declaration is missing when required, unreadable, or invalid."""


@dataclass(frozen=True)
class DbDeclaration:
    backend: str
    name_env: str
    dsn_env: str
    sqlite_path_env: str
    databases: tuple[str, ...]
    migrations: tuple[str, ...]


@dataclass(frozen=True)
class SensitivityDeclaration:
    column: str
    labels_file: str
    default: str
    egress_restricted: tuple[str, ...]
    levels: tuple[str, ...]


@dataclass(frozen=True)
class Domain:
    key: str
    name: str
    env_prefix: str
    root: Path
    source: str  # "file" | "builtin_default"
    path: Path | None
    db: DbDeclaration
    sensitivity: SensitivityDeclaration
    data_dir: str = "data"
    forge_dirs: tuple[str, ...] = ("goals", "args", "context", "hardprompts")
    components: str = "args/component_registry.yaml"
    reflex_packs: tuple[str, ...] = ()
    dashboard_port: int = 5050
    dashboard_blueprints: str = "registry"
    kanban_board: str = "it"
    kanban_external_repos: str = "args/kanban_external_repos.yaml"
    mcp_servers: tuple[str, ...] = ()
    trust: Mapping[str, Any] = field(default_factory=dict)
    ci: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def env(self, suffix: str) -> str:
        """Name of this parent's env var for ``suffix`` (``env("PG_DATABASE")``)."""
        return f"{self.env_prefix}_{suffix}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "env_prefix": self.env_prefix,
            "root": str(self.root),
            "source": self.source,
            "path": str(self.path) if self.path else None,
            "db": {
                "backend": self.db.backend,
                "name_env": self.db.name_env,
                "dsn_env": self.db.dsn_env,
                "databases": list(self.db.databases),
            },
            "dashboard_port": self.dashboard_port,
            "kanban_board": self.kanban_board,
        }


def _require(mapping: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise DomainError(f"{where}: missing required field {key!r}")
    return mapping[key]


def _tuple(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not hasattr(value, "__iter__"):
        raise DomainError(f"{where}: expected a list, got {type(value).__name__}")
    return tuple(str(v) for v in value)


def parse_domain(data: Mapping[str, Any], *, root: Path, source: str, path: Path | None) -> Domain:
    """Validate a declaration mapping and build a :class:`Domain`."""
    if not isinstance(data, Mapping):
        raise DomainError(f"{path or source}: top level must be a mapping")
    version = data.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise DomainError(
            f"{path or source}: schema_version {version!r} is not supported "
            f"(this core reads {SCHEMA_VERSION})"
        )
    dom = _require(data, "domain", str(path or source))
    key = str(_require(dom, "key", "domain")).strip()
    if not key or set(key) - _KEY_CHARS or key[0].isdigit():
        raise DomainError(
            f"domain.key {key!r} must be lowercase [a-z0-9_] and start with a letter"
        )
    env_prefix = str(_require(dom, "env_prefix", "domain")).strip()
    if not env_prefix.isidentifier() or env_prefix != env_prefix.upper():
        raise DomainError(f"domain.env_prefix {env_prefix!r} must be an UPPERCASE identifier")

    db_raw = _require(data, "db", str(path or source))
    db = DbDeclaration(
        backend=str(db_raw.get("backend", "postgresql")),
        name_env=str(db_raw.get("name_env", f"{env_prefix}_PG_DATABASE")),
        dsn_env=str(db_raw.get("dsn_env", f"{env_prefix}_DATABASE_URL")),
        sqlite_path_env=str(db_raw.get("sqlite_path_env", f"{env_prefix}_DB_PATH")),
        databases=_tuple(db_raw.get("databases"), "db.databases"),
        migrations=_tuple(db_raw.get("migrations"), "db.migrations"),
    )
    sens_raw = data.get("sensitivity") or {}
    sensitivity = SensitivityDeclaration(
        column=str(sens_raw.get("column", "classification")),
        labels_file=str(sens_raw.get("labels_file", "")),
        default=str(sens_raw.get("default", "public")),
        egress_restricted=_tuple(sens_raw.get("egress_restricted"), "sensitivity.egress_restricted"),
        levels=_tuple(sens_raw.get("levels"), "sensitivity.levels"),
    )
    paths = data.get("paths") or {}
    dashboard = data.get("dashboard") or {}
    kanban = data.get("kanban") or {}
    port = dashboard.get("port", 5050)
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        raise DomainError(f"dashboard.port {port!r} must be an integer in 1..65535")
    return Domain(
        key=key,
        name=str(dom.get("name", key)),
        env_prefix=env_prefix,
        root=root,
        source=source,
        path=path,
        db=db,
        sensitivity=sensitivity,
        data_dir=str(paths.get("data", "data")),
        forge_dirs=_tuple(paths.get("forge", BUILTIN_DEFAULT["paths"]["forge"]), "paths.forge"),
        components=str(data.get("components", "args/component_registry.yaml")),
        reflex_packs=_tuple((data.get("reflexes") or {}).get("packs"), "reflexes.packs"),
        dashboard_port=port,
        dashboard_blueprints=str(dashboard.get("blueprints", "registry")),
        kanban_board=str(kanban.get("board", key)),
        kanban_external_repos=str(kanban.get("external_repos", "args/kanban_external_repos.yaml")),
        mcp_servers=_tuple((data.get("mcp") or {}).get("servers"), "mcp.servers"),
        trust=dict(data.get("trust") or {}),
        ci=dict(data.get("ci") or {}),
        raw=dict(data),
    )


def _read_yaml(path: Path) -> Mapping[str, Any]:
    import yaml  # local import: keep module import free of third-party deps

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DomainError(f"{path}: cannot read declaration ({exc})") from exc
    if loaded is None:
        raise DomainError(f"{path}: declaration is empty")
    return loaded


def load_domain(
    path: str | os.PathLike[str] | None = None,
    *,
    anchor: str | os.PathLike[str] | None = None,
    require: bool | None = None,
) -> Domain:
    """Load the declaration that governs this process.

    ``path`` forces a specific file (tests, tooling). Otherwise the file is
    ``<repo_root(anchor)>/icdev_domain.yaml``. When absent, the builtin IT
    default is returned unless ``require`` (or ``$ICDEV_REQUIRE_DOMAIN``) says
    to refuse.
    """
    if path is not None:
        p = Path(path).resolve()
        if not p.is_file():
            raise DomainError(f"{p}: declaration not found")
        return parse_domain(_read_yaml(p), root=p.parent, source="file", path=p)

    root = repo_root(anchor)
    p = root / DOMAIN_FILE
    if p.is_file():
        return parse_domain(_read_yaml(p), root=root, source="file", path=p)

    if require is None:
        require = os.environ.get(REQUIRE_DOMAIN_ENV, "").strip().lower() in ("1", "true", "yes")
    if require:
        raise DomainError(
            f"no {DOMAIN_FILE} at {root} and {REQUIRE_DOMAIN_ENV} is set — "
            "this deployment refuses to run undeclared"
        )
    return parse_domain(BUILTIN_DEFAULT, root=root, source="builtin_default", path=None)
