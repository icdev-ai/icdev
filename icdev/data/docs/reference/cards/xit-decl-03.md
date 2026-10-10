# A module that computes the REPO ROOT from its own location (xit-decl-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/self_root_census.py --check                  # the gate; exit 1 on a NEW site
python tools/ci/self_root_census.py --changed tools/foo.py --check
python tools/ci/self_root_census.py --fix tools/foo.py       # the simple module-level form only
python tools/ci/self_root_census.py --json
python tools/ci/self_root_census.py --prune
```

    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    CFG = BASE_DIR / "args" / "x.yaml"
1,369 such sites in 1,315 files (measured 2026-08-21). Each is a private,
hard-coded claim about where the file sits; true today, silently wrong the
moment the file moves -- which is what the ICDEV[domain] split does to every
kernel package. THE FIX: `from icdev.core.paths import repo_root;
BASE_DIR = repo_root(__file__)`. NOT a site, because correct: a module-local
path (`Path(__file__).parent / "templates"` moves WITH the module), the
sys.path BOOTSTRAP idiom (`_REPO_ROOT = parents[2]; sys.path.insert(...)`
resolves the IMPORT root, same before and after a move), and a marker walk.
Climbing PAST the root is `overwalk` (26 today) -- a bug, not a self-root.
Grandfathered BY NAME in args/self_root_census.txt; `self_root_max` in
args/self_root_gate.yaml may only go DOWN. A kernel package must reach ZERO
sites before it physically moves (xit-core-*).
