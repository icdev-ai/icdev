# CUI // SP-CTI
"""Process identity: is THIS process allowed to run HERE, against THIS database?

Two ICDEV parents run on one machine, share one shell and one PostgreSQL
server, and both read ``<PREFIX>_PG_DATABASE`` / ``<PREFIX>_DATABASE_URL``
from the process environment. An ICDEV[FT] session that inherits ICDEV[IT]'s
``.env`` would open ``icdev`` and write trading rows into it; nothing today
would notice. :func:`assert_identity` is the refusal, called at the start of
every long-lived or state-changing entry point (dashboard, genesis daemon,
``tools/db/migrate.py``, ``tools/kanban/cli.py``).

The check is FAIL-CLOSED ON A DECLARED MISMATCH and NEVER on silence: a
process whose env names no database at all is reported ``unmeasured`` and
allowed through, because SQLite-only deployments, CI and tests legitimately
run without one. A declaration that lists no ``db.databases`` asserts
nothing and is likewise ``unmeasured``.

    python -m icdev.core.context --check          # human summary, exit 1 on mismatch
    python -m icdev.core.context --check --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from icdev.core import paths as core_paths
from icdev.core.domain import Domain, DomainError, load_domain

IDENTITY_GUARD_ENV = "ICDEV_IDENTITY_GUARD"  # =0 -> report only, never refuse


class IdentityMismatch(RuntimeError):
    """The process environment names a database this parent did not declare."""


@dataclass(frozen=True)
class IdentityReport:
    domain_key: str
    domain_name: str
    domain_source: str
    root: str
    database_declared: tuple[str, ...]
    database_observed: str | None
    database_source: str | None  # name_env | dsn_env | None
    verdict: str  # match | mismatch | unmeasured
    enforced: bool
    detail: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["database_declared"] = list(self.database_declared)
        return d


def _database_from_dsn(dsn: str) -> str | None:
    """Return the database name from a libpq URL, or None when it has none."""
    try:
        parts = urlsplit(dsn)
    except ValueError:
        return None
    if parts.scheme not in ("postgresql", "postgres"):
        return None
    name = parts.path.lstrip("/")
    return name or None


def observed_database(domain: Domain, environ: os._Environ | dict | None = None) -> tuple[str | None, str | None]:
    """Return ``(database_name, which_env_var)`` as the process env declares it.

    ``name_env`` wins over ``dsn_env`` because that is the precedence
    ``tools/db/storage.py`` gives ``ICDEV_PG_DATABASE`` when both are set for
    the keyword form; a DSN's path is consulted only when the name is absent.
    """
    env = os.environ if environ is None else environ
    name = (env.get(domain.db.name_env) or "").strip()
    if name:
        return name, domain.db.name_env
    dsn = (env.get(domain.db.dsn_env) or "").strip()
    if dsn:
        parsed = _database_from_dsn(dsn)
        if parsed:
            return parsed, domain.db.dsn_env
    return None, None


def load_env(root: Path | None = None) -> Path | None:
    """Load ``<root>/.env`` (the PARENT's, never cwd's). Returns the path loaded."""
    root = root or core_paths.repo_root()
    env_file = root / ".env"
    if not env_file.is_file():
        return None
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover — dotenv is a declared dependency
        return None
    load_dotenv(env_file, override=False)
    return env_file


def check_identity(
    *,
    anchor: str | os.PathLike[str] | None = None,
    domain: Domain | None = None,
    environ: dict | None = None,
) -> IdentityReport:
    """Compute the identity verdict without raising."""
    dom = domain or load_domain(anchor=anchor)
    observed, which = observed_database(dom, environ)
    declared = tuple(dom.db.databases)
    enforced = os.environ.get(IDENTITY_GUARD_ENV, "1").strip().lower() not in ("0", "false", "no", "monitor")
    if not declared:
        verdict, detail = "unmeasured", "the declaration lists no db.databases, so it asserts nothing"
    elif observed is None:
        verdict, detail = "unmeasured", (
            f"neither {dom.db.name_env} nor {dom.db.dsn_env} names a database in this process"
        )
    elif observed in declared:
        verdict, detail = "match", f"{which}={observed} is declared by {dom.key}"
    else:
        verdict, detail = "mismatch", (
            f"{which} names database {observed!r} but domain {dom.key!r} declares only "
            f"{list(declared)} — this process is running against another parent's database"
        )
    return IdentityReport(
        domain_key=dom.key,
        domain_name=dom.name,
        domain_source=dom.source,
        root=str(dom.root),
        database_declared=declared,
        database_observed=observed,
        database_source=which,
        verdict=verdict,
        enforced=enforced,
        detail=detail,
    )


def assert_identity(
    *,
    anchor: str | os.PathLike[str] | None = None,
    domain: Domain | None = None,
    environ: dict | None = None,
) -> IdentityReport:
    """Refuse (``IdentityMismatch``) on a declared mismatch; return the report otherwise.

    Stand it down with ``ICDEV_IDENTITY_GUARD=0`` (the report is still
    computed and returned, so a caller can log it), never with a shell
    neutraliser.
    """
    report = check_identity(anchor=anchor, domain=domain, environ=environ)
    if report.verdict == "mismatch" and report.enforced:
        raise IdentityMismatch(report.detail)
    return report


def describe(anchor: str | os.PathLike[str] | None = None) -> dict:
    """Everything ``icdev status`` prints about where and who this process is."""
    out: dict = {"paths": core_paths.describe(anchor)}
    try:
        dom = load_domain(anchor=anchor)
    except DomainError as exc:
        out["domain"] = None
        out["error"] = str(exc)
        return out
    out["domain"] = dom.to_dict()
    out["identity"] = check_identity(domain=dom).to_dict()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 on a declared mismatch")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-env", action="store_true", help="do not load <root>/.env first")
    args = ap.parse_args(argv)

    if not args.no_env:
        load_env()
    try:
        info = describe()
    except DomainError as exc:
        print(f"domain declaration error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        d = info.get("domain") or {}
        ident = info.get("identity") or {}
        print(f"domain   : {d.get('key')} ({d.get('name')}) from {d.get('source')}")
        print(f"root     : {info['paths']['root']}  [{info['paths']['source']}]")
        print(f"database : declared {d.get('db', {}).get('databases')} observed "
              f"{ident.get('database_observed')!r} via {ident.get('database_source')}")
        print(f"identity : {ident.get('verdict', 'unknown').upper()} — {ident.get('detail', info.get('error'))}")
    if args.check and (info.get("identity") or {}).get("verdict") == "mismatch":
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
