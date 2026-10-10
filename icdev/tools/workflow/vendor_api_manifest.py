# CUI // SP-CTI
"""Generate / verify the committed public-API manifest for VENDORED sources (ctx-enf-01).

WHY THIS EXISTS. ``coherence_checker.py::check_vendor_parity`` (cxo-doc-03)
compares ``tools/cortex/client.py`` against the copies standalone apps vendor
into their own repositories. It computes the drift correctly and it can never
block in CI: compass and idea_lab are separate PRIVATE repositories the ICDEV
runner does not check out, so every consumer path resolves to nothing, is
SKIPPED (correctly — an absent repo is not evidence of drift), and the finding
list stays empty. The root cause is repo TOPOLOGY, not the operating system;
``/srv/standalone`` skips exactly as ``C:/AI/standalone`` does, so making the
path portable fixes nothing.

WHAT THIS DOES. It commits the fact that CI *can* check: a snapshot of each
declared source's public API, in this repo, next to the code. Changing a
vendored source's public API without regenerating the manifest fails
``tests/workflow/test_vendor_api_manifest.py`` on a runner with no standalone
checkout — which is the whole point — and makes re-vendoring a deliberate step
rather than something you remember.

It reuses ``coherence_checker._public_api()`` rather than re-deriving the API, so
the manifest and the check can never disagree about what "the public API" is.

    python tools/workflow/vendor_api_manifest.py            # verify, exit 1 on drift
    python tools/workflow/vendor_api_manifest.py --write     # regenerate
    python tools/workflow/vendor_api_manifest.py --json

This is a COMPLEMENT, not a replacement: run the consumer-side parity check in
compass/idea_lab CI, which do have the vendored copy checked out.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.workflow.coherence_checker import (  # noqa: E402
    _VENDOR_API_MANIFEST,
    PROJECT_ROOT,
    render_vendor_api_manifest,
    vendor_manifest_drift,
    vendor_parity_sources,
)


def manifest_path() -> Path:
    """Absolute path of the committed manifest."""
    return PROJECT_ROOT / _VENDOR_API_MANIFEST


def write_manifest() -> bool:
    """Regenerate the manifest. Returns True when the file changed on disk."""
    path = manifest_path()
    rendered = render_vendor_api_manifest()
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" — this file is committed and the repo is LF; Path.write_text
    # otherwise emits CRLF on Windows and every regeneration is a whole-file diff.
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def verify_manifest() -> dict:
    """Machine-readable verification result for the current tree."""
    drift = vendor_manifest_drift()
    return {
        "manifest": _VENDOR_API_MANIFEST,
        "manifest_present": manifest_path().exists(),
        "sources": vendor_parity_sources(),
        "drift": drift,
        "in_sync": not drift,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write", action="store_true", help="regenerate the manifest from the current tree"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify only (the default); exit 1 when the manifest is stale",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.write:
        changed = write_manifest()
        result = verify_manifest()
        result["written"] = True
        result["changed"] = changed
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            state = "updated" if changed else "already up to date"
            print(f"{_VENDOR_API_MANIFEST} {state} ({len(result['sources'])} source(s))")
        return 0

    result = verify_manifest()
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["in_sync"]:
        print(
            f"{_VENDOR_API_MANIFEST} matches {len(result['sources'])} declared "
            "vendored source(s)"
        )
    else:
        for line in result["drift"]:
            print(f"DRIFT: {line}")
        print(
            "Regenerate with `python tools/workflow/vendor_api_manifest.py --write`, "
            "then re-vendor the file into each consumer repo."
        )
    return 0 if result["in_sync"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
