# Store publisher (icx-ent-04)

ICDEV[IT]'s side of ICDEV[OS]'s private, curated agent store (osx-ent-11, `docs/store.md` there). It replaces the
retired marketplace publish and review tools with a **packager** that prepares a signed store package and exports it,
with its publication beside it, for ICDEV[OS]'s one shared bundle import. Code: `tools/store_publisher/`; page
`/store-publisher/` (Canvases); config `args/store_publisher.yaml`; declared step `icos-store-publish`; ledger
`sp_events`; MCP `store_publisher_status` (read); twin canvas `store_publisher`.

## 1. A package

One `icos-store-package/v1` (ICDEV[OS]'s contract, `context/icos/store-package.schema.json`, vendored verbatim and
pinned by SHA-256) per package declared in the config: an **agent** (`prompt.md`), **crew** (`crew.json`), **skill**
(`instructions.md`), **MCP server** (`tools.json` and its sources) or **plugin** (`manifest.json`, `config.json`, the
distribution's SHA-256 and its sources). Every kind also carries `sbom.cdx.json` (CycloneDX 1.6/1.7),
`dependency-scan.json` (the factory scanner's findings over that SBOM) and `redteam.json` (the publisher's OWASP
Agentic ASI01-10 / LLM01-10 results). File names are fixed per kind and read only under `package_root`, never through
a symlink or a hard link (`tools/common/confined_read.py`).

| Part | How it is made |
|---|---|
| `publisher` | the config's `publisher` (`icdev-it`): the id ICDEV[OS] pins in its `args/store.yaml` (the owner's reviewed change there) |
| `agent_card` | an A2A Agent Card signed with JWS, ES384 over the RFC 8785 canonical card without `signatures` and default-valued fields (`card.py`, the exact form ICDEV[OS]'s `icdev_os/a2a/card_sign.py` verifies). **It binds the package**: `metadata["icos.store/package_digest"]` is the package's digest (every field but the card and `content_digest`); its name and version are the package's; its skills are EXACTLY the declared tools |
| key | the publisher's P-384 key from the OS keyring (`signing.key: keyring:<service>/<name>`; null until the site sets it, so every package is refused `no_key`). `signing.kid`, when set, must be the key id ICDEV[OS] pins (`key_mismatch`). Never an operator's, the owner's or a release key; never printed |
| `dependencies` | the factory scan over exactly this SBOM (`sbom_sha256`); its findings must name SBOM components |
| `marking`, `marking_ceiling`, `egress_ceiling` | `public` or `internal` only: a ceiling of `cui` or above needs an accreditation, which is the owner's |
| `scan` | ICDEV's seven marketplace scanner gates, run on the package itself (section 2): the publisher's own report |
| `content_digest` | SHA-256 of the canonical JSON of everything but itself (`tools.idp.dora.digest`, the same form as ICDEV[OS]'s `canon.digest`) |

Before it is recorded the package is validated against the pinned schema and checked again as ICDEV[OS] will check it
(`package.self_check`: its digest, the card's signature under the publisher's key, the binding, name, version, skills).

## 2. The seven gates

`gates.py` writes the package once into a fresh private directory and runs `tools/marketplace/asset_scanner.py`'s gates
on those bytes. Each says `pass`, `fail`, `warning`, `unverified` or `not_applicable`; **a gate that cannot run, or runs
on nothing it needs, is unverified, never a pass**.

| Gate | Here |
|---|---|
| `sast_scan` | the scanner's `sast_findings` over the source of what runs: bandit under this interpreter, hardened so the analysed code cannot switch it off (`--ignore-nosec`, an empty `--ini` of ours so no `.bandit` the sources carry is read, `-x` matching nothing so no default exclude hides a file), AND the AST checks always; the worse result stands. A Python file not in bandit's report, a file nothing can parse, or any source that is not Python is `unverified`, named. Source paths may have no dot-leading component. An agent, crew or skill carries no code: `not_applicable` |
| `secret_detection` | the scanner's `secret_findings` over every file of the package; the report names the file, line and kind, never the match |
| `dependency_audit` | the dependency report: over this SBOM, at most 30 days old (parsed, never compared as text; a future time is refused), naming only SBOM components; a critical or high finding fails, an unrated one is unverified. CISA KEV is checked by ICDEV[OS], not here |
| `cui_marking_validation` | every marking public or internal, and no CUI banner (the scanner's `cui_banner_files`) in a package marked below CUI |
| `sbom_generation` | the SBOM the package carries: CycloneDX 1.6/1.7, every component named, versioned and with a package URL (an AI-BOM names a machine-learning model) |
| `supply_chain_provenance` | an https source and a 40-hex commit; a component not pinned to an exact version is a warning (the scanner's own rule) |
| `digital_signature` | the publisher's key signs a trial card over this package and it verifies (and is the pinned kid, when set); the real card is verified again after signing |

Two of the scanner's gates are not called as written: its `dependency_audit` runs `pip-audit` over a requirements file,
which resolves and installs the requirements into a virtual environment (an untrusted build on the host) and reaches the
network; its `sbom_generation` writes an SBOM rather than checking one. The scanner's SAST gate also read an empty bandit
report (bandit not on `PATH`) as zero findings, a pass; it now always runs the AST checks too and calls an unparsed or
unanalysed file unverified (`test_the_scanner_sast_gate_never_passes_when_bandit_cannot_run`, `test_m1_*`).

ICDEV[OS] re-runs its own nine gates on the imported package and shows this report only as claimed.

## 3. Prepare, publish, export

* **Prepare** (`POST /store-publisher/api/packages/<id>/prepare`): a signed-in person in `packager_roles`; an agent, a
  service key or a bootstrap principal never makes the publisher's key sign (`agent_cannot_sign`). It records the whole
  package at its content digest (`sp_events`, `prepared`) and decides nothing.
* **Publish** (`.../publish`): the declared step `icos-store-publish` (`args/workflow_hitl_templates/store-publish.yaml`;
  its id is the one ICDEV[OS]'s store contract pins as the publication's `template`), held by admin, isso or reviewer,
  never the preparer (`separate_from: builders`), with a reason, bound to the digest read. Before the step the recorded
  package is checked again, and its key must be the publisher's current key by public identity (`signing.kid`,
  else the configured key); with neither configured the publication records `key_currency: unverified` and the twin
  shows `key_unverified` (ICDEV[OS] verifies the card against its own pin either way).
* **Export** (`.../export`): `{"bundle": <icos-store-package/v1>, "publication": <icos-store-publication/v1>}`, only
  for a person who packages or publishes (never an agent, a service key or a bootstrap principal), and only for a
  published version whose declared step is recorded that way, by that person, naming the package's org (`step_missing`
  otherwise). If the ledger write after a step is lost, the step is voided (`withdrawn`), so no surface shows it
  published. The
  publication names the preparer and the publisher; it is validated against the pinned publication schema.
* **Import** is a third person's act in ICDEV[OS] (its Store page), and certification a reviewer's who is none of them.
  Nothing here publishes by itself, no agent publishes, and a new version is a new package and a new step.

A package is shown, prepared, published or exported only for a reader of its org cleared for its marking (`internal`
needs CUI); anything else is "no such package", byte for byte, and a list counts what it holds back. An agent (MCP) is in
no org and sees synthetic packages only.

## 4. Twin, MCP, console

The twin (`tools/twin_core/adapters/store_publisher.py`) observes `store_publisher.signed_packages`: a package with no
published version is unverified; findings `chain_broken`, `awaiting_publish`, `self_published`, `step_missing`,
`gate_not_passed` and `redteam_not_passed` each thread to their `sp_events` row (`store_publisher:<event>`); an
observation replays. A thread resolves only through the console's reader-filtered route (the generic resolver does
not name store rows). `store_publisher_status` only reads, and never returns the bundle. The page lists packages, prepares and publishes by endpoint id,
and shows each gate, the card's skills and binding, the ceilings, the red-team summary and the self-check re-run on read.

## 5. The retired tools

`tools/marketplace/publish_pipeline.py` and `review_queue.py` (deprecated 2026-05-09) were thin shims (deleted at icx-ent-13) that refused
(`superseded`, `tools/marketplace/superseded.py`) and point here; the MCP marketplace server's `publish_asset`,
`review_asset`, `list_pending` and `openclaw_promote` refuse the same way, and so do the paths that had the same effect
with no person: `catalog_manager.py --update-status --status published` and `--promote`, and
`federation_sync.py --promote`. They are removed with the other deprecated duplicates once nothing
names them (osx-ent-13).

## 6. Cross-repo proof

A bundle exported here was imported by ICDEV[OS] (gitlab/main ba4e0a56, a read-only worktree, a scratch data dir and
store config pinning a throwaway test publisher key): both synthetic packages were accepted and all nine of ICDEV[OS]'s
store-review gates passed on them; a copy whose content changed under its digest was refused `tampered`, a re-digested
copy with an added tool `card`, one published by its preparer or by an agent `bad_publication`, one with no publication
`unpublished`, and one from an unpinned publisher `publisher`.

## 7. Facts and what was not verified here

The JWS form (A2A v1.0 section 8.4, RFC 7515 over RFC 8785) and the OWASP ids each kind must answer are taken from
ICDEV[OS]'s merged contract and verifier (`icdev_os/a2a/card_sign.py`, `args/store.yaml`), which the cross-repo proof
exercises; they were not re-read from the A2A specification or OWASP at this card.

## 8. Owner and site steps

1. Set the publisher's key in the OS keyring and name it in `signing.key` (a reviewed change); optionally `signing.kid`.
   No environment variable or key file stands in for it (the e2e seed signs in its own process, in memory).
2. ICDEV[OS]'s owner pins the publisher in its `args/store.yaml` (id `icdev-it`, its org, the public JWK, and the
   accounts of the people who prepare and publish here, none of whom may certify).
