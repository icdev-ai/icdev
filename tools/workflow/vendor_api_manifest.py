# CUI // SP-CTI
"""Committed public-API manifests for modules VENDORED into other repositories.

Why this exists (ctx-enf-01):

``coherence_checker.py::check_vendor_parity`` compares a canonical module against
its vendored copies in the standalone repos. It computes the right answer and it
**cannot enforce**, because those copies live in SEPARATE PRIVATE repositories
that ICDEV's CI never checks out. ``check_vendor_parity`` SKIPS a consumer whose
path is absent (coherence_checker.py, ``_resolve_vendor_path`` → ``skipped``), so
``drift`` stays empty and the gate returns pass. Measured: with the consumer root
absent it passes even when run with ``--gate`` AND
``--changed-files tools/cortex/client.py``.

That is repo topology, not an operating-system problem — ``/home/me/standalone``
skips exactly as ``C:/AI/standalone`` does. Making the path portable does not
fix it. Consequence: ``reason()`` and ``agent()`` were added to
``tools/cortex/client.py`` on 2026-08-09 and both vendored copies were still
missing them days later, with ``last_synced`` reading 2026-08-02.

So the parity signal has to be verifiable from INSIDE this repo alone. This
module writes the canonical public API of each declared vendored source to a
committed manifest under ``args/vendor_api/``. Changing the source without
regenerating the manifest fails — in CI, on any runner, with no external
checkout. It does not prove the consumers are in sync (nothing inside this repo
can); it makes the moment the contract CHANGES impossible to miss, which is the
moment re-vendoring is owed.

The two halves are complementary and neither replaces the other:

    this manifest        — "canonical changed"      : always enforceable here
    check_vendor_parity  — "a copy is behind"       : only where the copy exists

Usage::

    python -m tools.workflow.vendor_api_manifest --check          # exit 1 on drift
    python -m tools.workflow.vendor_api_manifest --write          # regenerate
    python -m tools.workflow.vendor_api_manifest --check --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST_DIR = PROJECT_ROOT / "args" / "vendor_api"
_CONFIG = PROJECT_ROOT / "args" / "vendor_parity.yaml"

_HEADER = (
    "# CUI // SP-CTI\n"
    "# GENERATED — do not hand-edit.\n"
    "#   python -m tools.workflow.vendor_api_manifest --write\n"
    "#\n"
    "# Public API of {source}, which is VENDORED verbatim into out-of-repo\n"
    "# consumers (see args/vendor_parity.yaml). This file is what makes a change\n"
    "# to that contract fail INSIDE this repo: CI never checks out the consumer\n"
    "# repositories, so check_vendor_parity always skips them and can never fail.\n"
    "# If this diff is in your PR, the vendored copies are now owed a re-sync.\n"
)


def manifest_path_for(source: str) -> Path:
    """Deterministic manifest path for a declared vendored source."""
    return MANIFEST_DIR / (source.replace("/", "__").replace(".py", "") + ".api")


def _public_api(source_text: str) -> Set[str]:
    """The SAME surface computation check_vendor_parity uses.

    Imported rather than reimplemented so the manifest and the parity check can
    never disagree about what "the public API" means — two implementations of
    one definition is how the original drift went unnoticed.
    """
    from tools.workflow.coherence_checker import _public_api as _impl

    return _impl(source_text)


def declared_sources() -> List[str]:
    try:
        import yaml
    except ImportError:  # pragma: no cover - PyYAML is an ICDEV requirement
        return []
    if not _CONFIG.is_file():
        return []
    data = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    out = []
    for entry in data.get("vendored_copies") or []:
        if isinstance(entry, dict) and entry.get("source"):
            out.append(str(entry["source"]).strip())
    return out


def render(source: str) -> str:
    """Manifest text for *source*: header plus its sorted public API."""
    path = PROJECT_ROOT / source
    api = sorted(_public_api(path.read_text(encoding="utf-8")))
    return _HEADER.format(source=source) + "\n" + "\n".join(api) + "\n"


def _committed(source: str) -> str:
    path = manifest_path_for(source)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def check() -> Dict:
    """Compare every declared source against its committed manifest."""
    drift, verified, missing_source = [], [], []
    for source in declared_sources():
        if not (PROJECT_ROOT / source).is_file():
            missing_source.append(source)
            continue
        expected, actual = render(source), _committed(source)
        if not actual:
            drift.append(f"{source}: no manifest at {manifest_path_for(source).relative_to(PROJECT_ROOT)}")
        elif expected != actual:
            exp_api = set(expected.splitlines()) - set(_HEADER.format(source=source).splitlines())
            act_api = set(actual.splitlines()) - set(_HEADER.format(source=source).splitlines())
            added = sorted(x for x in exp_api - act_api if x.strip())
            removed = sorted(x for x in act_api - exp_api if x.strip())
            detail = []
            if added:
                detail.append(f"added: {', '.join(added)}")
            if removed:
                detail.append(f"removed: {', '.join(removed)}")
            drift.append(f"{source}: manifest is stale ({'; '.join(detail) or 'header changed'})")
        else:
            verified.append(source)
    return {
        "ok": not drift and not missing_source,
        "verified": verified,
        "drift": drift,
        "missing_source": missing_source,
    }


def write() -> List[str]:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for source in declared_sources():
        if not (PROJECT_ROOT / source).is_file():
            continue
        target = manifest_path_for(source)
        target.write_text(render(source), encoding="utf-8", newline="")
        written.append(str(target.relative_to(PROJECT_ROOT)))
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if any manifest is stale")
    ap.add_argument("--write", action="store_true", help="regenerate every manifest")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.write:
        written = write()
        print(json.dumps({"written": written}, indent=2) if args.json
              else "\n".join(f"wrote {w}" for w in written) or "nothing to write")
        return 0

    result = check()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for s in result["verified"]:
            print(f"  ok    {s}")
        for d in result["drift"]:
            print(f"  DRIFT {d}")
        for m in result["missing_source"]:
            print(f"  GONE  {m}: declared in args/vendor_parity.yaml but not on disk")
        if not result["ok"]:
            print(
                "\nThe vendored API contract changed. Regenerate with:\n"
                "    python -m tools.workflow.vendor_api_manifest --write\n"
                "and re-sync the out-of-repo copies (args/vendor_parity.yaml lists them).",
                file=sys.stderr,
            )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
