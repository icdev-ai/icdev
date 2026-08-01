# CUI // SP-CTI
"""`icdev skills` — list / search / install / update skills (sag-skl-02).

CLI wiring ONLY — no new hub. It composes what ICDEV already ships:

- ``icdev skills list``   → local skills via :mod:`tools.skills.registry`
  (parseable by the SAG runtime's ``/skills`` command).
- ``icdev skills search`` → the Federated FORGE Asset Marketplace catalog
  (:mod:`tools.marketplace.catalog_manager`).
- ``icdev skills install`` / ``icdev skills update`` → the EXISTING marketplace
  install path (:mod:`tools.marketplace.install_manager`), so every install runs
  through the marketplace's security pipeline (sandbox verification, IL
  compatibility, publish gate, audit). We never raw-fetch → write into
  ``.agents/skills``.

See goals/marketplace.md + tools/manifest/marketplace.md for the asset model.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    start = Path(__file__).resolve().parent
    for parent in [start, *start.parents]:
        if any((parent / s).exists() for s in ("pyproject.toml", ".git", "CLAUDE.md")):
            return parent
    return start


def _default(env: str, fallback: str) -> str:
    return os.environ.get(env) or fallback


# ---------------------------------------------------------------------------
# list (local)
# ---------------------------------------------------------------------------


def _skills_list(args: argparse.Namespace) -> int:
    from tools.skills.registry import load_registry

    reg = load_registry(rebuild=args.rebuild)
    skills = reg.get("skills", {})
    if args.json:
        print(json.dumps({"count": len(skills), "skills": skills}, indent=2, default=str))
        return 0
    if not skills:
        print("No local skills found.")
        return 0
    print(f"{len(skills)} local skill(s):")
    for name in sorted(skills):
        entry = skills[name] or {}
        desc = (entry.get("description") or "").strip().replace("\n", " ")
        print(f"  {name:<28} {desc[:72]}")
    return 0


# ---------------------------------------------------------------------------
# search (marketplace catalog)
# ---------------------------------------------------------------------------


def _skills_search(args: argparse.Namespace) -> int:
    query = " ".join(args.query).strip().lower()
    try:
        from tools.marketplace.catalog_manager import list_assets

        assets = list_assets(asset_type="skill", limit=args.limit)
    except Exception as exc:  # noqa: BLE001 — marketplace may be unprovisioned
        print(f"icdev skills search: marketplace catalog unavailable ({exc})", file=sys.stderr)
        return 1

    def _matches(a: dict[str, Any]) -> bool:
        if not query:
            return True
        hay = " ".join(
            str(a.get(k, "")) for k in ("name", "slug", "description", "tags")
        ).lower()
        return query in hay

    matches = [a for a in assets if _matches(a)]
    if args.json:
        print(json.dumps({"count": len(matches), "results": matches}, indent=2, default=str))
        return 0
    if not matches:
        print(f"No marketplace skills match {query!r}.")
        return 0
    print(f"{len(matches)} marketplace skill(s):")
    for a in matches:
        slug = a.get("slug") or a.get("id", "")
        ver = a.get("current_version", "")
        tier = a.get("catalog_tier", "")
        desc = (a.get("description") or "").strip().replace("\n", " ")
        print(f"  {slug:<26} v{ver:<8} [{tier}] {desc[:56]}")
    print("Install with: icdev skills install <slug>")
    return 0


# ---------------------------------------------------------------------------
# install (marketplace install path — 7-gate pipeline)
# ---------------------------------------------------------------------------


def _resolve_version_id(asset: dict[str, Any], version: str | None = None) -> str | None:
    """Pick a version id: the named/current version, else the newest."""
    versions = asset.get("versions") or []
    if not versions:
        return None
    target = version or asset.get("current_version")
    if target:
        for v in versions:
            if v.get("version") == target:
                return v.get("id")
    # get_asset orders versions newest-first.
    return versions[0].get("id")


def _skills_install(args: argparse.Namespace) -> int:
    try:
        from tools.marketplace.catalog_manager import get_asset
        from tools.marketplace.install_manager import install_asset
    except Exception as exc:  # noqa: BLE001
        print(f"icdev skills install: marketplace unavailable ({exc})", file=sys.stderr)
        return 1

    try:
        asset = get_asset(slug=args.ref)
    except Exception as exc:  # noqa: BLE001
        print(f"icdev skills install: lookup failed ({exc})", file=sys.stderr)
        return 1
    if not asset:
        print(f"icdev skills install: no marketplace skill named {args.ref!r}", file=sys.stderr)
        return 2
    if asset.get("asset_type") not in (None, "skill"):
        print(f"icdev skills install: {args.ref!r} is a {asset.get('asset_type')}, not a skill", file=sys.stderr)
        return 2

    version_id = _resolve_version_id(asset, args.version)
    if not version_id:
        print(f"icdev skills install: {args.ref!r} has no installable version", file=sys.stderr)
        return 2

    install_path = args.path or str(_repo_root() / ".agents" / "skills")
    tenant_id = _default("ICDEV_TENANT_ID", args.tenant or "default")
    project_id = args.project or "skills"
    installed_by = _default("ICDEV_USER_ID", args.user or "default")

    try:
        result = install_asset(
            asset["id"], version_id, tenant_id, project_id, installed_by, install_path
        )
    except (ValueError, FileNotFoundError) as exc:
        # IL-incompatible / not published / already installed / missing files —
        # the marketplace pipeline rejected it. Surface cleanly, no traceback.
        print(f"icdev skills install: rejected — {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"icdev skills install: failed — {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(
            f"Installed {args.ref!r} (installation {result.get('installation_id', '?')}, "
            f"status {result.get('status', '?')}) into {install_path}"
        )
    return 0


# ---------------------------------------------------------------------------
# update (marketplace update path)
# ---------------------------------------------------------------------------


def _skills_update(args: argparse.Namespace) -> int:
    try:
        from tools.marketplace.catalog_manager import get_asset
        from tools.marketplace.install_manager import check_updates, update_asset
    except Exception as exc:  # noqa: BLE001
        print(f"icdev skills update: marketplace unavailable ({exc})", file=sys.stderr)
        return 1

    tenant_id = _default("ICDEV_TENANT_ID", args.tenant or "default")
    try:
        report = check_updates(tenant_id)
    except Exception as exc:  # noqa: BLE001
        print(f"icdev skills update: check failed ({exc})", file=sys.stderr)
        return 1

    updates = report.get("updates_available", []) if isinstance(report, dict) else []
    # Only skills — filter out non-skill assets when the type is present.
    updates = [u for u in updates if u.get("asset_type") in (None, "skill")]

    if not updates:
        print("All skills are up to date.")
        return 0

    if args.dry_run:
        print(f"{len(updates)} update(s) available (dry run):")
        for u in updates:
            print(f"  {u.get('asset_name', u.get('asset_id'))}: "
                  f"{u.get('installed_version')} -> {u.get('current_version')}")
        print("Re-run without --dry-run to apply.")
        return 0

    applied, failed = [], []
    updated_by = _default("ICDEV_USER_ID", args.user or "default")
    for u in updates:
        try:
            asset = get_asset(asset_id=u.get("asset_id"))
            new_version_id = _resolve_version_id(asset or {}, u.get("current_version"))
            if not new_version_id:
                failed.append((u.get("asset_name"), "no target version id"))
                continue
            update_asset(u["installation_id"], new_version_id, updated_by)
            applied.append(u.get("asset_name", u.get("asset_id")))
        except Exception as exc:  # noqa: BLE001
            failed.append((u.get("asset_name", u.get("asset_id")), str(exc)))

    if args.json:
        print(json.dumps({"applied": applied, "failed": failed}, indent=2, default=str))
    else:
        print(f"Applied {len(applied)} update(s): {', '.join(applied) or '(none)'}")
        for name, why in failed:
            print(f"  failed: {name} — {why}", file=sys.stderr)
    return 0 if not failed else 1


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="icdev skills")
    subs = parser.add_subparsers(dest="cmd")

    lp = subs.add_parser("list", help="List locally installed icdev-* skills.")
    lp.add_argument("--json", action="store_true")
    lp.add_argument("--rebuild", action="store_true", help="Rebuild the local registry first.")

    sp = subs.add_parser("search", help="Search the marketplace catalog for skills.")
    sp.add_argument("query", nargs="*", help="Search terms (empty lists all).")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--json", action="store_true")

    ip = subs.add_parser("install", help="Install a skill via the marketplace pipeline.")
    ip.add_argument("ref", help="Marketplace skill slug.")
    ip.add_argument("--version", default=None, help="Specific version (default: current).")
    ip.add_argument("--tenant", default=None)
    ip.add_argument("--project", default=None)
    ip.add_argument("--user", default=None)
    ip.add_argument("--path", default=None, help="Install dir (default: <repo>/.agents/skills).")
    ip.add_argument("--json", action="store_true")

    up = subs.add_parser("update", help="Update installed skills to their latest versions.")
    up.add_argument("--tenant", default=None)
    up.add_argument("--user", default=None)
    up.add_argument("--dry-run", action="store_true", help="Show available updates without applying.")
    up.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    if args.cmd == "list":
        return _skills_list(args)
    if args.cmd == "search":
        return _skills_search(args)
    if args.cmd == "install":
        return _skills_install(args)
    if args.cmd == "update":
        return _skills_update(args)

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
