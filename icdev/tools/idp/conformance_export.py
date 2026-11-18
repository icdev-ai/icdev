# CUI // SP-CTI
"""Export a repo's ``icos-conformance/v1`` bundle for ICDEV[OS] (icx-coe-03).

One bundle per repo (``--repo it|os|ft``, declared in ``args/coe_conformance.yaml``): the engineering standards
and their status against the repo, the scorecard results with evidence, the exemptions that touch it (with
expiry), the delivery metrics (DORA four keys and AI-assisted rework, each with window, sample size and source
digests), the linked ADRs, and the producer, version and time (``tools/idp/conformance.py`` builds it).

Deterministic given its inputs: the same register and exemption log, repo tree, sources and ``--as-of`` give the
same ``content_digest``; ``--at`` pins ``produced_at`` too, so the file is byte for byte the same.

The bundle written is a DRAFT (``publication: null``) unless a person published that exact content: when the
repo's newest published bundle in the ledger has the same ``content_digest``, it is written with its
``publication`` (who, the step, when). ``--published`` writes the newest published bundle as it was published.
Publishing itself is a person's declared step on /idp/conformance; this tool never publishes.

Sources: ``--git``, ``--ci`` and ``--board`` take snapshots (``tools/idp/dora.py`` formats; ``--ci`` also takes a
``gh run list --json headSha,conclusion,updatedAt,headBranch`` export). Without them the declared sources directory
is read, and git live from the declared checkout. ``--snapshot`` writes the git history it read to the sources
directory (``<repo>.git.json``), so a later run can be repeated without the checkout.

    python tools/idp/conformance_export.py --repo it --out <dir> [--as-of 2026-11-17] [--at 2026-11-17T00:00:00Z]
        [--git it.git.json] [--ci it.ci.json] [--board it.board.json]
    python tools/idp/conformance_export.py --repo os --published --out <dir>
    python tools/idp/conformance_export.py --repo ft --snapshot
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# sys.path BOOTSTRAP (the import root, not a data path): run by path, this
# file's own directory is sys.path[0] and the repo root is not.
_BOOTSTRAP_ROOT = Path(__file__).resolve().parents[2]
if str(_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_ROOT))

from tools.idp import conformance, dora  # noqa: E402


def _load(path: Path | None):
    return None if path is None else json.loads(path.read_text(encoding="utf-8"))


def sources_from_args(repo: str, as_of: str, *, git: Path | None, ci: Path | None, board: Path | None) -> dict:
    """The declared sources, with any snapshot named on the command line in place of the declared one."""
    src = conformance.read_sources(repo, as_of)
    if git is not None:
        src["git"], src["read"]["git"] = dora.check_git(_load(git)), "snapshot"
        src["missing"].pop("git", None)
    if ci is not None:
        src["ci"], src["read"]["ci"] = dora.normalise_ci(_load(ci)), "snapshot"
        src["missing"].pop("ci", None)
    if board is not None:
        src["board"], src["read"]["board"] = dora.normalise_board(_load(board)), "snapshot"
        src["missing"].pop("board", None)
    return src


def export(repo: str, *, as_of: str | None = None, at: str | None = None, git: Path | None = None,
           ci: Path | None = None, board: Path | None = None) -> dict:
    """The bundle: a draft, or carrying its publication when a person published this exact content."""
    rc = conformance.repo_config(repo)
    as_of = (as_of or conformance._today())[:10]
    b = conformance.build(rc["key"], as_of=as_of, at=at,
                          sources=sources_from_args(rc["key"], as_of, git=git, ci=ci, board=board))
    pub = conformance.published_bundle(rc["key"])
    if pub is not None and pub.get("content_digest") == b["content_digest"]:
        b["publication"] = pub["publication"]
    return b


def write_snapshot(repo: str, as_of: str | None = None) -> Path:
    """Read the repo's git history live and write it to the sources directory as ``<repo>.git.json``."""
    cfg = conformance.load_config()
    rc = conformance.repo_config(repo, cfg)
    as_of = (as_of or conformance._today())[:10]
    src = conformance.read_sources(rc["key"], as_of, cfg=cfg)
    if src["git"] is None or src["read"].get("git") != "live":
        raise dora.SourceError(src["missing"].get("git") or "the git history was not read live")
    d = conformance.sources_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{rc['key']}.git.json"
    out.write_text(json.dumps(src["git"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export a repo's icos-conformance/v1 bundle for ICDEV[OS]")
    ap.add_argument("--repo", required=True, help="a repo declared in args/coe_conformance.yaml (it, os, ft)")
    ap.add_argument("--out", type=Path, help="write conformance-<repo>.json into this directory (default: stdout)")
    ap.add_argument("--as-of", help="the bundle's as-of date, YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--at", help="produced_at (ISO 8601; default now), so a bundle can be re-made byte for byte")
    ap.add_argument("--git", type=Path, help="git history snapshot (icdev-it/git-history/v1)")
    ap.add_argument("--ci", type=Path, help="CI runs snapshot (icdev-it/ci-runs/v1, or gh run list JSON)")
    ap.add_argument("--board", type=Path, help="board export (icdev-it/board-export/v1)")
    ap.add_argument("--published", action="store_true", help="write the newest PUBLISHED bundle as published")
    ap.add_argument("--snapshot", action="store_true", help="write the git history read live to the sources dir")
    a = ap.parse_args(argv)
    try:
        if a.snapshot:
            print(json.dumps({"status": "ok", "snapshot": str(write_snapshot(a.repo, a.as_of))}))
            return 0
        if a.published:
            b = conformance.published_bundle(a.repo)
            if b is None:
                print(json.dumps({"status": "error", "error": f"no bundle of {a.repo} has been published"}))
                return 1
        else:
            b = export(a.repo, as_of=a.as_of, at=dora._iso(dora._utc(a.at)) if a.at else None,
                       git=a.git, ci=a.ci, board=a.board)
    except (conformance.ConformanceRefused, dora.SourceError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(getattr(exc, "message", exc))}))
        return 1
    text = json.dumps(b, indent=2, sort_keys=True) + "\n"
    if not a.out:
        sys.stdout.write(text)
        return 0
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.out / f"conformance-{b['repo']['key']}.json"
    path.write_text(text, encoding="utf-8")
    m = b["delivery"]["metrics"]
    print(json.dumps({"status": "ok", "out": str(path), "repo": b["repo"]["key"], "schema": b["schema"],
                      "content_digest": b["content_digest"], "published": b["publication"] is not None,
                      "standards": b["summary"]["standards"],
                      "metrics": {k: {"status": v["status"], "value": v["value"], "n": v["sample_size"]}
                                  for k, v in m.items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
