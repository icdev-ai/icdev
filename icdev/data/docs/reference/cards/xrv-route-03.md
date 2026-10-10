# A CI reference that does NOT NAME THE BYTES it resolves to (xrv-route-03)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/pin_census.py --check                         # the gate; exit 1 on a NEW site
python tools/ci/pin_census.py --json
python tools/ci/pin_census.py --changed .gitlab-ci.yml --check
python tools/ci/pin_census.py --staged
python tools/ci/pin_census.py --prune                         # drop entries whose site is gone
python tools/ci/pin_census.py --seed                          # re-derive the census; writes nothing
python tools/workflow/coherence_checker.py --check pin_census --json
```

FOUR KINDS, NEVER MERGED, because each sends a reader to a DIFFERENT repair:
  unpinned_install   a pip / `npm install -g` package literal with no `==`.
                     .gitlab-ci.yml:148 installs `llm-sandbox docker pyyaml`
                     unversioned -- the shape reverse-skill fails CI on, and a
                     job that installs today's llm-sandbox is a job whose
                     behaviour changes when somebody else publishes a release.
  tag_pinned_action  a `uses:` pinned to a mutable git TAG. Of 70 `uses:` lines
                     in .github/workflows, 69 are third party and ZERO are
                     sha-pinned: whoever owns the action
                     repository can move `v4` to any commit, and every workflow
                     here runs it with the token the job was given.
  undigested_image   a compose `image:` whose repo has NO measured digest in
                     vendor/images/*.txt -- three of the four floci/* siblings,
                     while the AWS one has had one since flx-ci-01.
  unpinned_script    `curl … | sh`, unpinned by construction. Two sites.
THE FINDING IS THE REFERENCE, NOT THE INSTALL, and four predicates keep it
high-signal -- each RE-DERIVED every run rather than kept as an exemption list
(an exemption list is a claim a reviewer must check; a predicate is one the
scanner proves):
  * `pip install -r requirements.txt` / `-e .` / `dist/*.whl` names no package
    literal at all. The pin lives in the declared file.
  * `npm ci` and a bare `npm install` resolve through package-lock.json, which
    IS the pin -- `npm ci` refuses to run without one. Only the `-g` form has
    no lockfile behind it.
  * A compose service carrying `build:` is BUILT from a Dockerfile in this tree
    and never pulled, so a registry digest cannot describe it. MEASURED: 21 of
    the 29 services in docker-compose.yml, which is the difference between 8
    real sites and 29 mostly-noise ones.
  * `uses: ./.github/...` is a path into THIS repository, pinned by the commit
    under review.
ONE SPELLING OF "WHAT IS A DIGEST PIN": `tools/airgap/image_vendor.parse_pin`
is IMPORTED for the image half, never re-implemented, or the vendor and the
gate come to disagree about a fact neither of them changed.
THE KEY CARRIES NO LINE NUMBER AND NO REF. Line numbers churn on every edit
above the site (undeclared_import_census's reason). The REF is omitted because
`actions/checkout@v4 -> @v5` is a routine bump and the SAME unpinned decision:
keying on it would fail --check on every bump and demand a census edit that
says nothing new. The ref is REPORTED, so a reader sees it; it is not identity.
So `.gitlab-ci.yml` installing ruff at two lines is ONE site.
SURVEYED, NOT GATED: requirements.txt declares 46 distributions, 45 with `>=`
and 1 a git-TAG direct reference (`icdev-core @ git+...@v0.2.0`). Those ride
under `surveyed_not_gated` with their reason -- the install-time pin for a
deployment is the vendored wheel set (tools/airgap/wheel_vendor.py), and
refusing 45 ranges would refuse routine work. A range and a direct reference
are counted APART: an index resolves a range against a published release, a
mutable git tag is resolved by whoever owns the repository.
UNMEASURABLE IS ITS OWN VERDICT. An unparseable compose file, an absent
vendor/images, an undecodable CI file -- each is named and makes `ok` False,
never a clean bill of health. Reading an absent vendor tree as "no image is
pinned" would invent a finding for every image on any deployment without one.
`--prune` REFUSES against an unmeasurable scan: that is the one direction that
deletes a live entry.
THREE PARSE DEFECTS FOUND BY RUNNING IT, each a fabricated finding rather than
a missed one, and each now pinned by a test:
  * cutting the raw command text at the first `>` turned `pip install
    "boto3>=1.34"` into a package called `boto`. Truncation is on TOKENS now,
    because a version specifier contains the characters a redirection does.
  * the same cut left a bare `2` behind from `pip install -e . 2>/dev/null`,
    reported as a package named "2".
  * a PEP 508 direct reference contains a URL, so the path heuristic discarded
    the ONE real direct reference in the tree as "a path" -- the quiet miss a
    census exists to prevent.
69 sites grandfathered BY NAME in args/pin_census.txt (34 action, 26 install,
7 image, 2 script); `pin_max` in args/pin_gate.yaml may only go DOWN. The
cheapest cohort to drain is the images: `docker image inspect <ref> --format
'{{index .RepoDigests 0}}'` on a connected host, one line per image.
WARN in coherence_checker, and the tool's own `--check` exits 1. A brand-new
gate over a 69-site surface nobody has drained cannot arm a hard refusal
without a fire-rate survey -- that is how a check earns itself a `|| true`.
Promote it to `fail` with a survey, never with an edit. It is a HEAVY_CHECKS
entry on SCOPE and not cost (the whole scan is under a second): the census is
a claim about the CI surface, which does not change because a diff touched an
unrelated module, so the full tier always runs it and the fast tier re-adds it
exactly when the diff touches a workflow, .gitlab-ci.yml, a Dockerfile, a
compose file, vendor/images/ or the gate/census pair.
