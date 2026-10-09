# CUI // SP-CTI

# Pin census — adoption survey (xrv-route-03)

Measured 2026-09-12 on `kanban/xrv-route-03` off `origin/main` at `b0c16c286`,
with the SHIPPED predicate (`tools/ci/pin_census.py`) and never a second copy.
Re-derive every number below with:

```bash
python tools/ci/pin_census.py --json
python tools/ci/pin_census.py --seed        # the census file, from scratch
```

## 1. The finding

Four things in this repository decide, at run time, which bytes a build
executes, and nothing checked any of them. Each is a **reference that does not
name the bytes it resolves to**, and each sends a reader to a different repair —
so they are four kinds and never one "unpinned" bucket.

| kind | sites | what it is |
|---|---:|---|
| `tag_pinned_action` | 34 | a `uses:` pinned to a mutable git TAG rather than a 40-hex commit sha |
| `unpinned_install`  | 26 | a pip / `npm install -g` package literal with no `==` |
| `undigested_image`  | 7  | a compose `image:` whose repo has no measured digest in `vendor/images/*.txt` |
| `unpinned_script`   | 2  | `curl … \| sh`, unpinned by construction |
| **total**           | **69** | |

Three of the four are exactly what the `reverse-skill` external review objects
to; the fourth (`undigested_image`) is the same mutability one layer down and
already has a home in this tree, because `vendor/images/images-floci.txt`
records a MEASURED digest for `floci/floci` and nothing for its three siblings.

### 69 third-party `uses:` occurrences collapse to 34 sites

There are 70 `uses:` lines across the 10 workflow files; 1 is a local
`./.github/workflows/...` reference and the other **69 are third-party, of which
ZERO are sha-pinned**. Only 10 distinct actions are involved. The census reports
34 because the key is per `(file, action)` and carries **no ref**: a workflow that
checks out in four jobs took one decision once, and `actions/checkout@v4 -> @v5`
is a routine bump and the SAME unpinned decision. Keying on the ref would fail
`--check` on every bump and demand a census edit that says nothing new — the
merge-conflict-generator failure `undeclared_import_census` avoids by leaving
line numbers out. Occurrences and sites are different quantities and are quoted
separately.

## 2. What the four predicates buy, measured

Each predicate is RE-DERIVED every run rather than kept as an exemption list. An
exemption list is a claim a reviewer must check; a predicate is one the scanner
proves.

| predicate | what it removes | measured |
|---|---|---|
| a compose service carrying `build:` is BUILT here, never pulled | 21 of the 29 services in `docker-compose.yml` | 29 image references → **8**, of which 1 (`floci/floci`) is already digest-vendored → **7 sites** |
| `pip install -r <file>` / `-e .` / `dist/*.whl` names no package literal | 38 `-r`, 6 `-e`, 1 `dist/*.whl` across the 10 workflows, the GitLab config and 20 Dockerfiles | 0 sites |
| `npm ci` / bare `npm install` resolve through `package-lock.json` | 4 `npm ci` and 1 bare `npm install` fallback | 0 sites |
| `uses: ./…` is a path into this repository | 1 (`./.github/workflows/interface_validation_steps.yaml`) | 0 sites |

The compose predicate is the load-bearing one: without it the census would carry
29 image entries of which 21 describe an image this repository builds itself,
where a registry digest has no meaning. **8 real sites against 29 mostly-noise
ones** — a census whose first two thirds were noise would be ignored inside a
week.

## 3. Fire rate — the gate refuses nothing that exists today

The census is a **ratchet**, so the rate that matters is not "how many sites are
there" (69, all grandfathered BY NAME in `args/pin_census.txt`) but "how often
does `--check` refuse a commit that adds nothing new".

```
python tools/ci/pin_census.py --check   ->  exit 0
  69 site(s) seen, 69 registered, 0 unregistered | census 69 (ceiling 69)
```

**0 refusals on the tree as committed**, by construction: the census was seeded
from the shipped predicate's own output, so every current site is named. The only
commit it can refuse is one that ADDS an unpinned reference, which is that
commit's own doing and repairable at the site.

A planted site was verified to fail, and the planted line restored:

```
+    - pip install planted-unpinned-package
->  exit 1,  NEW  .gitlab-ci.yml:71  [unpinned_install] planted-unpinned-package
```

### Why the coherence check is WARN and not FAIL

`coherence_checker --check pin_census` reports **warn**. That is deliberate and it
is the reason no broader fire-rate survey is owed:

* The standalone tool's `--check` DOES exit 1, so the refusal exists and is
  opt-in. Nothing in CI runs it yet; wiring it into a required job is a separate
  decision with its own survey.
* This is a brand-new gate over a 69-site surface nobody has drained. Arming a
  hard refusal on day one over a set that large is how a check earns itself a
  `|| true` — the failure `.claude/settings.json` already recorded once.
* Promote it to `fail` with a survey over the population it would then refuse,
  never with an edit to this file.

It is registered in `HEAVY_CHECKS` on **SCOPE and not cost** (the whole scan is
0.198 s, measured): the census is a claim about the CI surface, and that surface
does not change because a diff touched an unrelated module, so running it on
every per-task gate would re-report the same 69 grandfathered sites to sessions
that cannot act on them. The full tier always runs it; the fast tier re-adds it
exactly when the diff touches a workflow, `.gitlab-ci.yml`, a Dockerfile, a
compose file, `vendor/images/`, or the gate/census pair. Verified:

```
--tier fast --changed-files tools/foo.py      ->  pin_census NOT selected
--tier fast --changed-files .gitlab-ci.yml    ->  pin_census selected
--tier full                                   ->  pin_census selected
```

## 4. Surveyed, NOT gated: `requirements.txt`

```
46 declarations -- 0 pinned, 45 ranged (`>=`), 1 direct reference, 0 unspecified
100.0% not pinned   (a MEASURED 100, not an empty denominator)
```

Reported under `surveyed_not_gated` with its reason and **never gated**. The
install-time pin for a deployment is the vendored wheel set
(`tools/airgap/wheel_vendor.py`); refusing 45 ranges would refuse routine work,
the defect the PreToolUse fire-rate survey found. Quoting the number is what lets
a later card decide with evidence instead of by feel.

A **range** and a **direct reference** are counted apart, because they resolve
differently: an index resolves `pyyaml>=6.0` against a published release, while
`icdev-core @ git+https://github.com/icdev-ai/icdev-core@v0.2.0` is resolved by
whoever owns that repository and can move `v0.2.0` to any commit. That one is the
single direct reference in the file, and the same reference appears as a census
site at `.github/workflows/icdev-ci.yml` where the workflow installs it inline.

## 5. Three parse defects found by RUNNING it

Every one produced a **fabricated finding or a silent miss**, not a missed
refusal, and every one is now pinned by a test in
`tests/ci/test_pin_census.py`. They are recorded because each is the same class
of error: reading shell text with a regex where the operands contain the
operators.

1. **`boto3>=1.34` was reported as a package called `boto`.** The first version
   truncated the raw command text at the first shell operator, and `>` in a
   version specifier is the same character as a redirection. Truncation is now on
   TOKENS (`shlex`, quotes respected), so a quoted requirement survives whole.
   Pinned by `test_a_version_RANGE_is_not_a_pin`, which asserts the REFERENCE is
   `boto3>=1.34` and not merely that a site was found.
2. **`pip install -e . 2>/dev/null || true` was reported as a package called
   `2`.** Same cut, at the `>` of the file descriptor, leaving the `2` behind.
   Pinned by `test_a_redirection_is_never_read_as_a_package`.
3. **The one PEP 508 direct reference in the tree was silently dropped as "a
   path".** `pkg @ git+https://…@v0.2.0` contains a `/`, and the path heuristic
   that correctly discards `dist/*.whl` discarded it too. This is the quiet miss a
   census exists to prevent — the other two were loud. Pinned in both directions:
   `test_a_pep508_direct_reference_pinned_to_a_git_TAG_is_a_site` and
   `…_pinned_to_a_sha_is_not_a_site`.

## 6. Unmeasurable is its own verdict

Three ways this census can fail to look, each named on the report and each
making `ok` False rather than reading as clean:

* an `image:` file that will not parse (`docker-compose.override.yml` parses
  today; a future one may not);
* `vendor/images/` absent or unreadable — reading that as "no image is pinned"
  would invent a finding for every image on any deployment without a vendor
  tree, which is a fabricated finding the size of the real set;
* a CI file that will not decode.

`--prune` **refuses** against an unmeasurable scan, because pruning against a
partial read deletes a live entry — the one direction that loses information.

## 7. What this card does NOT do

It pins nothing. All 69 sites are grandfathered BY NAME and `pin_max` is set to
exactly 69 — today's count, not a round number above it, because headroom is
permission. The ceiling may only go DOWN.

The cheapest cohort to drain is the images: three of the seven are the
`floci/*` siblings whose AWS sibling already carries a measured digest, so the
repair is `docker image inspect <ref> --format '{{index .RepoDigests 0}}'` on a
connected host plus one line per image in `vendor/images/`. The action cohort is
the largest and the most mechanical: repin to the 40-hex sha, keep the tag in a
trailing comment.

`args/pin_gate.yaml` ships with `exclude: []`. Every site in this tree is debt to
be drained, not a case for an excuse.
