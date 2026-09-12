# CUI // SP-CTI

# Phase 80 — XRV: what ICDEV adapts from nine external repositories

**Card:** `xrv` (`args/projects.yaml`, prefix `xrv-`, 10 epics including the
reserved `gate`; **24 rows on the board 2026-09-12**, of which `xrv-cost-05` and
`xrv-docs-02` were added in flight)
**ADRs:** D402–D408 (`docs/reference/adrs.md`, Phase 80)
**Status, read off the live board 2026-09-12:** 24 `xrv-` rows — **19 `done`**,
`xrv-cost-05` `pr_opened`, `xrv-docs-01` (this document) `in_progress`,
`xrv-gate-00` `in_progress` **by design** (it is the manual sentinel, and it is
held open on purpose so the task behind it cannot be dispatched), `xrv-lab-02`
and `xrv-docs-02` `backlog`. Quote the date with the figure: the board moves, and
`xrv-cost-05` and `xrv-docs-02` were both added in flight — found BY the
measurements below rather than planned.

The review question was "what should ICDEV adapt from these nine repositories".
The answer that made the card worth building was not a feature list: **eight of
the nine gaps were already-present ICDEV capabilities that nothing consumed.**
`auto_capture.py` was complete with no hook caller; four budget guards all sat
inside `router.invoke`, which a `claude_cli` dispatch never enters; three
security scanners existed and none had ever been pointed at `.claude/`; the
`--output-format json` envelope was written to a task log and never read back.
That is this repository's signature defect (declared-but-unconsumed), and the
external repositories' value was as a **list of places to look for it**.

---

## Verdict per repository

| Repo | Licence | Verdict | The measured gap |
|---|---|---|---|
| **codeburn** | MIT | ADAPT | `claude_cli.py::_parse_cli_json` already parsed `total_cost_usd` per dispatch and nothing wrote it against a task: `agent_token_usage.task_id` carried **ZERO** rows on the live PG board. No transcript cost reader (`fire_rate_survey` reads `tool_use` blocks only). No one-shot rate, no waste survey. CLAUDE.md measured 2026-09-11 at **352,915 bytes / ~88k tokens**, loaded by every session. |
| **loopx** | Apache-2.0 | ADAPT | Four budget guards exist and **all four sit inside `router.invoke`**; `_dispatch_task` consulted none, so a `claude_cli` dispatch bypassed every USD cap and the budget refused the run's calls MID-RUN instead. Eleven early-return guards with no shared verdict. `handoff_generator.py` orphaned from the kanban loop; `_get_retry_coaching` carried a failure reason only. |
| **claude-mem** | Apache-2.0 | ADAPT (wiring) | No `SessionStart` hook, so the documented Session Start Protocol was a manual step nobody ran. `tools/memory/auto_capture.py` complete with **no hook caller** (`SELECT COUNT(*) FROM memory_buffer` = 0 on the live board). No progressive-disclosure retrieval; no `<private>` exclusion. FTS5 + vector + decay already present. |
| **ECC** | MIT | ADAPT (wiring) | `prompt_injection_detector.scan_project`, `secret_detector.scan`, `mcp_scanner.scan_mcp_servers` all exist; **none** was pointed at `.claude/`, `.agents/`, `.cursor/`, `.mcp.json` or the 10 companion files, and `claude_dir_validator` checked syntax only. NOVA `analyze_patterns` returned a raw `count` behind a `min_count=2` floor, no confidence. Fresh-context review, hooks-as-policy, memory vault: already present, SKIP. |
| **reverse-skill** | MIT (+GPLv3 submodule, unused) | ADAPT | `shutil.which` scattered over 30+ modules with no central index (`health_check.check_tools` probes Python IMPORTS, not PATH); routing tests totalled ~14 cases across four routers and none named a skill; no pin gate (69 unpinned CI supply-chain references, **zero** of 69 third-party `uses:` sha-pinned). Case-init: `tools/agent_case/` is stronger, SKIP. |
| **Ghidra** | Apache-2.0 | ADAPT (two-tier) | **ZERO** binary analysis in the tree — no PE/ELF parser, no strings, no YARA, no decompiler; `analyzer_contract.yaml` + `tools/analyzers/sandbox.py` is a zero-dispatcher-change socket; `slsa_verify` / `attestation_verify` verified metadata about an artifact **without ever opening it**. |
| **PRAXIST** | Fair Source 1.0 | ADAPT shape only | `experiment_engine.py::_PLACEHOLDER_METRICS_NOTE`: the nightly `experiment` reflex evaluated an **identity baseline** (evaluate, change nothing, evaluate again) and decided keep/discard on that delta; `args/autoresearch_config.yaml: enabled: false` had shipped since the engine landed and was read by nothing. Foundry ran every 12 h with **4 of 8 stage modules absent** — including the SEEDER, its only kanban writer — and reported a green cycle emitting 0 tasks. |
| **Obtainium** | Unlicense | ADAPT idea only | Digest pins + cryptographic verify exist for floci ONLY; **nothing asked upstream whether a newer release exists** for any image or binary. The `floci/floci:2.0.1` pin's only ongoing assertion was a test that two files agree with each other — and two files can agree perfectly about a version that shipped a year ago. |
| **floci** | MIT | DONE (`flx`, 21/21) | Pinned 2.0.1 **is** the current upstream (released 2026-09-01). One stale line: `docs/features/phase-flx-floci-emulator.md` still read 16/21 with az/gcp/oci unbuilt; corrected in the card PR (2026-09-11). |

**PRAXIST is Fair Source 1.0, so only its shape was adapted, never its code.**

## Deliberately NOT adopted, and why

| Not adopted | Reason |
|---|---|
| **ECC cross-platform hook adapter** | Only `claude_code` declares `capabilities.hooks: true` in `args/companion_registry.yaml`; Cursor's hook API is unstable and `companion.py --sync` already churns those configs on every run. A hook abstraction over one implementation is an abstraction over nothing. |
| **codeburn's LiteLLM price feed** | A network dependency for an air-gap product. Prices stay DECLARED in `args/llm_config.yaml`, and an unknown model prices to `None` under `unpriced` — never a live lookup and never $0. |
| **PRAXIST parallel peers / quality-diversity archive** | Premature while the loop mutates nothing. A QD archive over an identity baseline archives 0 distinct behaviours; build the mutation first (`xrv-lab-02`, gated), measure, then decide. |
| **reverse-skill's 44-rule unified router** | ICDEV keeps **four** deliberately separate deterministic routers and the boundary note atop `tools/cortex/intent_router.py` states why. `xrv-route-02` added a 195-case regression corpus over all four and **no fifth router**. |
| **Obtainium's Android/APK content** | Irrelevant to this platform. The adapted idea is one sentence — *ask upstream whether the pin is still the newest one* — and none of the implementation. |
| **Ghidra in `requirements.txt`** | JDK 21+ and a ~400 MB install. It is an OPTIONAL backend that reports `unavailable` on a default install, which is the correct reading (D406). |
| **A dedicated MCP tool for `tool_index`** | The 8-point checklist asks for one, and `capability_liveness` already fails at `mcp_dispatch_tool` 468 over a grandfathered budget of 467. A 469th never-dispatched entry makes a documented breach worse to satisfy a checklist. |

---

## What shipped, by epic, with every number

Each figure below is dated and re-derivable with the command beside it. Where a
live corpus moved between two readings, **both readings are quoted** — one figure
off a moving corpus is not a measurement.

### `cost` — where the spend goes, and did it ship (codeburn)

| card | what shipped |
|---|---|
| `xrv-cost-01` | `tools/cost/session_cost.py` — the transcript cost reader. Reuses `fire_rate_survey.transcript_root/iter_transcripts`; prices through `cost_intelligence._model_pricing`. A message is counted ONCE across the several JSONL lines Claude Code writes it on. |
| `xrv-cost-02` | `tools/cost/task_attribution.py` — ONE `agent_token_usage` row per reaped `claude_cli` dispatch with `task_id` set, written at reap from the envelope the log already carried. `--task <id>` joins the rows to the outcome through `landed_check` + `pr_linker`: `shipped \| reverted \| abandoned \| in_flight \| unmeasurable`. |
| `xrv-cost-03` | `tools/cost/waste_survey.py` — one-shot rate, re-reads, ghost definitions, CLAUDE.md tokens/day, MCP usage. Report only. |
| `xrv-cost-04` | A **Spend by Card** panel on the EXISTING `/cache-savings` page + `GET /api/cache-savings/spend`. Not a new page, so the 8-point page gate does not apply. |

**Measured.** `--since-days 7 --project ICDev`, 2026-09-11: 194 transcripts, 193
with usage, 18,791 messages, **all `unpriced`** — the models the transcripts name
(`claude-fable-5-1`, `claude-opus-5`) are absent from `args/llm_config.yaml` and
`claude-opus` / `claude-sonnet-cli` carry 0.0/0.0. The reader reports that as
`unpriced_models`, **never $0.00**.

Waste survey, 30-day window, 2026-09-12 (two runs eight minutes apart returned
1600 and 1599 sessions; the second is tabled):

| measure | value | the caveat that ships with it |
|---|---|---|
| one-shot rate | **81.91%** (9,180 edits, 1,661 retries) | 3 of the top 5 retried paths are CLAUDE.md and memory files — append-shaped documents edited in a loop with a check between each edit. The rule behaving correctly, not 1,661 failed code edits. |
| re-read offenders (same path Read >= 3x in a session) | **96** | A **LOWER BOUND**: 2,050 `Read` calls against 62,377 shell calls. This deployment's own harness instruction steers sessions to `cat`/`sed -n`, and a file read that way names no path. Roughly one file read in thirty is visible. |
| definitions not invoked in the window | **91 of 93 (97.85%)** — commands 68/70, skills 23/23 | Verdict is `not_invoked_in_window`, **never `dead`**. All three detectors fire (`tool_use` 166, `command_tag` 83, `slash_line` 32) — the positive control that makes 97.85% a finding rather than a broken scanner. `.claude/agents` is `absent` with `declared: None`, never zero ghosts. |
| CLAUDE.md | **357,077 bytes / ~89,078 tokens**, approx **4.75 M tokens loaded per day** over 1,599 sessions | `token_basis: chars_div_4` on every field. An approximation; must not be quoted as billed tokens. The 2026-09-11 reading was 352,915 bytes — the file gains a block per card, so it is dated on every run. |
| MCP tools reached | transcripts **3 of 472**; Studio audit **1 of 472** | Two different callers, **never merged**. `studio_mcp_dispatch_audit` has exactly two writers, both Studio-internal; nothing under `tools/mcp/` writes a dispatch row, so every MCP call from a Claude Code session is invisible to `capability_consumption --class mcp_dispatch_tool`. Filed as `xrv-cost-05`. |

Spend panel, live board 2026-09-12: **5 attributed cards, $110.36, all `shipped`,
0 `unpriced`, 0 `unmeasurable`** — a measured 100.0% share, not a fabricated one.

**A defect found and fixed at the ledger seam:** `record_task_cost` fell back to
`0.0` when the envelope carried no cost field, so an ABSENT price reached the
ledger as a zero and `attributed_totals` summed it as one — understating the bill
in the direction that makes work look cheap. `row_is_unpriced` now names that
class, and the count is carried **apart from every verdict**: a dispatch that
reported no dollars still shipped or was still abandoned, so folding it into
`unmeasurable` would make the verdict answer a question about the price instead
of about the outcome.

**A survey defect found and fixed:** the first MCP attribution matched a
transcript tool name against `TOOL_REGISTRY` **bare** and reported 6 tools
reached. Three of the six were **Playwright's** — the registry declares
`browser_click`/`browser_navigate`/`browser_type` and so does the Playwright MCP
server, so Playwright's 50 clicks and 176 navigations were credited to ICDEV's
registry, doubling the one number that section exists to produce. Attribution is
now server-qualified, derived from the checkout's own `.mcp.json`.

### `run` — one should-run verdict before a token is spent (loopx)

`tools/kanban/should_run.py` — one `assess(task)` folding **ten** checks the
dispatcher already consulted plus the missing **budget rung** through one pure
`classify`, re-implementing none (AST-pinned). One log line per dispatch.
`xrv-run-02` adds the evidence-backed handoff record in the existing
`kanban_tasks.last_run_metadata` (no migration), rendered into the next prompt by
`_get_retry_coaching`.

**Measured** (`docs/audits/xrv-run-01-should-run-survey.md`), 7,072 scheduler
dispatches 2026-06-11 to 2026-09-11, replayed through the SHIPPED `classify`:

| verdict | dispatches | share |
|---|---:|---:|
| proceed | 5,084 | 71.89% |
| **wait** | **1,988** | **28.11%** |
| ask / refuse / unmeasurable | 0 | 0.00% |

By month: 2026-06 and 2026-07 **0.00%**; 2026-08 **63.00%** (first wait
2026-08-07T23:13Z); 2026-09 **100.00%**. 30-day window: 1,621 of 1,621 `wait`.

**Every fire is the module arm's TOKEN cap and not one is USD** —
`module_budget_periods.spent_usd` is 0.0 in every month and `ai_telemetry`'s
13,995 rows sum to $0.00, because every call routes to a $0/1k provider.
Corroborated from the writer side: `icdev.budget.module_budget.ndjson` carries
**567 `budget exceeded` refusals in four hours** on 2026-09-11 across eight
modules, with 31,608 more in the rotated file. Those are the mid-run refusals the
card names: the dispatch went ahead and every router call inside it was refused.

`report` is the default and **`enforce` stays off** (D403).

### `mem` — memory that captures and recalls itself (claude-mem)

| card | what shipped |
|---|---|
| `xrv-mem-01` | `.claude/hooks/session_start.py` + `tools/hooks/session_context.py`. Measured live: **0.48–0.54 s wall, 425 tokens, 20 entries**, capped at 1,500 tokens and WITHHELD past a 1 s wall clock. `hook_events.hook_type` now derives from ONE tuple; **found on the way** — `user_prompt_submit` and `pre_compact` had been written by two shipped hooks since they were authored and admitted by NO constraint (0 rows on either backend beside 42,569 / 341,173 `post_tool_use` rows). Both join. |
| `xrv-mem-02` | `.claude/hooks/post_tool_use.py` -> `auto_capture.capture()`, four closed kinds (`edit`, `commit`, `test_summary`, `decision`), `<private>` spans dropped BEFORE capture. |
| `xrv-mem-03` | `hybrid_search.py --layer index\|timeline\|detail`, every layered response carrying `approx_tokens`. The timeline REUSES the dashboard's chronology — the `audit_trail UNION ALL hook_events` query moved into `tools/dashboard/api/activity_query.py` and an AST test asserts the UNION is spelled as SQL in exactly one file. |

**Measured capture rate** (`docs/audits/xrv-mem-02-capture-rate.md`), replay of
the shipped predicate over 7 days of transcripts (197 read, 20,258 `tool_use`
events): **144 rows on the last full UTC day, median 156/day over 10 measured
days**, mean 232.5. *Quote the median* — the mean is dragged by two
multi-session build days (the spread is 7 to 563, which is the spread of the
operator's day and not of the predicate). Live buffer during the building
session: 0 -> 5 -> 9 rows. Cost per tool call: **+10 ms** for a non-capturing
event, **+280 ms** when an event yields an observation (16.6% of events).

**A defect the survey found before it shipped:** the first replay read **4** test
summaries against 116 commits. The pytest regex demanded the
`===== N passed =====` bars and this repo runs `-q`, which prints the bare
`N passed in 1.2s`. Both shapes read now (**1,140** summaries), and the DURATION
is stripped from the stored observation — the outcome is the fact, and
`24 passed in 1.13s` versus `1.19s` was every re-run of one suite as a fresh row.

### `shield` — scan the agent config surface (ECC AgentShield)

`xrv-shield-01` gives `claude_dir_validator.py` four checks, each **WIRING an
existing scanner**: `config-injection`, `config-secrets`, `mcp-config`,
`hook-commands`, plus `tools/security/agent_config_shield.py` and a
`coherence_checker --check agent_config_shield` registration at **warn**.
`xrv-shield-02` puts confidence 0..1 on NOVA learned patterns, copying
`learning_collector`'s clamped-confidence + 0.7 block / 0.5 demote precedent.

**Surface measured 2026-09-11**
(`docs/audits/xrv-shield-01-agent-config-survey.md`): 3 of 3 config directories
present, 11 of 11 instruction files, **204** files reached by the injection walk,
**227** files under the three directories, 2 of 6 declared MCP configs present,
**11** hook commands.

| check | verdict | findings |
|---|---|---|
| `config-injection` | warn | 1 high, 32 medium, **0 critical** — all 33 are scanner artefacts (40-char content hashes matching `encoded_base64_block`; one UTF-8 BOM) |
| `config-secrets` | **pass** | 0 over 227 files — a MEASURED zero, both scanner arms answered |
| `mcp-config` | warn | 8 medium, 0 high, structural for stdio configs |
| `hook-commands` | **pass** | 0; ten `\|\| true` advisories, all on reporting hooks |

**THREE REAL DEFECTS the wiring found**, each invisible to the scanner's own
tests because those tests feed it fixtures rather than this repository:

1. **`role_hijack_jailbreak` matched the substring `dan` — 9,929 false
   criticals.** The pattern had no word boundaries, so every `dan` inside a
   longer word was a CRITICAL jailbreak finding. Over the 18,730 scannable files:
   **10,019 matches as shipped -> 90 with `\b(...)\b`, 9,929 removed (99.10%)**.
   Top enclosing tokens: `guidance` 2,826, `accordance` 541, `dangerous` 389,
   `redundancy` 356, `pydantic` 186. Everything kept is the real vocabulary
   (`jailbreak` 43, standalone `dan` 33, `do anything now` 6). On the config
   surface alone: 67 criticals -> 0. **FIXED** — a strict narrowing (adding `\b`
   cannot create a match), with the existing `test_jailbreak_dan` fixture still
   matching, asserted.
2. **`secret_detector`'s detect-secrets arm silently skips any path containing
   `build` or `dist`.** The `--exclude-files` regex is unanchored, so it excludes
   on a *substring* of the path. Reproduced as a controlled pair — same file,
   same token, only the directory name differs: `.agents/skills/x/SKILL.md` ->
   **1** finding, `.agents/skills/icdev-build/SKILL.md` -> **0**. **NOT FIXED
   HERE, AND NAMED**: `scan` has ~20 in-repo consumers including the
   `domain_leak_gate` CI gate, so anchoring it widens what every one of them
   reports and owes its own fire-rate survey. `config-secrets` instead runs
   **both arms** and unions the findings deduplicated on `(file, line, type)`, so
   its live `pass` is a measured zero over all 227 files.
3. **`scan_mcp_servers` answers an ABSENT config with `passed: True`.** A caller
   reading `passed` gets a clean bill of health for a config it never found.
   `mcp-config` treats an `error` key as **unmeasurable** and an absent file as
   **absent** — never as a pass.

Pattern confidence, measured 2026-09-11: 40 hits in ONE session scored 0.7375 and
cleared the propose bar **on frequency alone**, so a declared
`single_session_ceiling` (0.65) caps a measured single-session pattern below the
0.7 bar. A ceiling rather than a re-weighting, because every weighting that
refuses 40-in-one-session also refuses 5-across-5-sessions-this-week, which is
the case worth proposing. **REPETITION IS NOT CORROBORATION.**

### `route` — deterministic, verifiable routing (reverse-skill)

| card | what shipped |
|---|---|
| `xrv-route-01` | `args/tool_index.yaml` (37 declared + 4 declared ABSENCES with reasons) + `tools/dx/tool_index.py::which(name)`, the ONE lookup new code calls; it REFUSES an undeclared name, because an index that silently answers for anything can never grow. Consumed by `health_check.check_external_tools`. Live on this host: **36 declared, 18 present, 18 absent, 0 unmeasurable, ~6 s**. |
| `xrv-route-02` | `tests/routing/corpus.yaml` — **195 cases over the four routers** (chat_canvas 72, cortex_facade 48, rag_shape 40, skill_category 35) + `tools/routing/corpus_survey.py`, plus optional `prerequisites:` frontmatter on a SKILL.md. **No fifth router.** |
| `xrv-route-03` | `tools/ci/pin_census.py` — a CI reference that does not name the bytes it resolves to. |

**`present` requires the binary to ANSWER its `version_cmd`**, because a
`present` verdict that never ran the binary is a claim about a file NAME.
Measured: **two binaries called `helm` are on PATH** — a real WinGet `helm.EXE`
and an extension-less Python console script that dies in `ModuleNotFoundError`.
Git Bash's `command -v` returns the broken one and `shutil.which` returns the
working one; only running it settles which you got (`present 4.2.3`). And
**argv[0] must be the resolved path**, measured not assumed:
`shutil.which("npx")` returns `npx.CMD`, `subprocess.run([resolved, ...])`
succeeds, and `subprocess.run(["npx", ...])` raises `FileNotFoundError` — the
naive spelling fails on this platform.

`xrv-route-02` found **four routing defects**, all report-only, each an
`xfail(strict)` case so it is red while the defect stands and red the moment it
is fixed and the declaration goes stale. The sharpest: `skill_selector`'s OWN
documented example, `fix the login tests`, matches **nothing** —
`match_keywords` tests keyword membership in a token set with no stemming, so
`test` misses `tests` and the safety fallback injects all nine categories.

Pin census, measured 2026-09-12
(`docs/audits/xrv-route-03-pin-census-survey.md`):

| kind | sites | what it is |
|---|---:|---|
| `tag_pinned_action` | 34 | a `uses:` pinned to a mutable git TAG — **69 third-party `uses:` lines, ZERO sha-pinned** |
| `unpinned_install` | 26 | a pip / `npm install -g` package literal with no `==` |
| `undigested_image` | 7 | a compose `image:` with no measured digest in `vendor/images/` |
| `unpinned_script` | 2 | `curl ... \| sh`, unpinned by construction |
| **total** | **69** | all grandfathered BY NAME in `args/pin_census.txt`; `pin_max` may only go DOWN |

Fire rate **0 refusals on the tree as committed**, by construction — the census
was seeded from the shipped predicate's own output, so the only commit it can
refuse is one that ADDS an unpinned reference. A planted unpinned install was
verified to fail and restored. The compose predicate is the load-bearing one:
without it the census would carry 29 image entries of which **21 describe an
image this repository builds itself**, where a registry digest has no meaning —
8 real sites against 29 mostly-noise ones. `requirements.txt` (46 declarations,
45 ranged, 1 direct reference) is **SURVEYED, not gated**: the install-time pin
for a deployment is the vendored wheel set, and refusing 45 ranges would refuse
routine work. **Three parse defects were found by running it**, each a
*fabricated* finding rather than a missed one and each now pinned by a test:
cutting the command text at the first `>` turned `pip install "boto3>=1.34"`
into a package called `boto`; a bare `2` survived from `2>/dev/null`; and a
PEP 508 direct reference contains a URL, so the path heuristic discarded the one
real direct reference in the tree.

### `bin` — a binary is an observable (Ghidra)

Two tiers, two modules, two declarations (D406):

| card | what shipped |
|---|---|
| `xrv-bin-01` | `observable_types.binary` + `tools/analyzers/binary_triage.py` — pure-Python: magic-based format (PE/ELF/Mach-O/unknown), sha256, sections + per-section entropy, imports, printable strings. **Executes NOTHING**, asserted from its AST. `docs/security/sandbox-coverage.md` Gap 71. |
| `xrv-bin-02` | `tools/analyzers/ghidra_headless.py` — the OPTIONAL backend. Five statuses: `ok \| truncated \| unavailable \| timeout \| error`. |
| `xrv-bin-03` | `tools/compliance/binary_components.py` (SBOM hints) + `tools/devsecops/artifact_corroboration.py`, consumed by `attestation_verify --artifact` and `slsa_verify --artifact`. |

`sections` / `imports` / `functions` / `strings` are **`None` — never `[]`** when
nothing looked, with the reason named. An `[]` there would read as "Ghidra looked
at this binary and found no functions": a claim about the ARTIFACT from a run
that never opened it. A MEASURED `[]` survives as `[]`, and both directions are
asserted.

**THE TOKEN IS NOT THE VERSION.** `_VERSION_TOKEN` ends at a word boundary, so
the seam's hint for `OpenSSL 1.1.1k` is **`1.1`** — a *wrong* version, not a
vague one, and a CVE lookup against it answers about a release the artifact does
not contain. The token is extended over the version characters that follow it in
the same string, bounded at 40 chars, and **both values ride on the component**
with `version_basis` naming which rule produced it. Measured residual, reported
and deliberately **not** repaired here because it belongs to `binary_triage`:
that same `\b` means `v1.1.1k` yields **no hint at all**. A second regex in the
SBOM seam would make the SBOM and `binary_triage --json` disagree about one
artifact, so the zero is reported and a test pins it. Every hinted component is
labelled one — `evidence: binary_strings`, `confidence: unasserted` (there is no
second value), and the RAW string it came from so the reader can check. No purl
is invented.

Corroboration is **reported beside the signature verdict and never folded into
it**: `attach()` returns a NEW mapping with every existing key unchanged and
REFUSES to overwrite, so a triage that cannot load `pefile` can never downgrade
a sound verification — and an adversary who can make the triage unmeasurable
cannot move the verdict. Asserted as equality over the WHOLE result on both
verifiers, not over one field. The SBOM axis is deliberately asymmetric: only a
VERSION CONTRADICTION on a name both sides carry reaches `disagrees`, because
static linking leaves no trace and a version string can name a protocol or
somebody else's requirement.

Two Ghidra decisions worth carrying. **The two timeouts are not the same
timeout:** `-analysisTimeoutPerFile` is set BELOW the wall budget, so an
over-running analysis still runs the postScript and yields a PARTIAL export this
module can label `truncated`, instead of a killed process with no export to
label at all. And **the process TREE is killed, not the process** —
`analyzeHeadless` is a `.bat`/`sh` wrapping a JVM, so `proc.kill()` reaps the
wrapper and leaves the JVM holding the pipes `communicate()` is blocked on
(`reflexes/kanban._kill_process_tree` is imported, never re-implemented, and
lazily, because only a timing-out run should pay its ~0.5 s).

### `lab` — the research loop stops reporting a measurement it did not make (PRAXIST)

`xrv-lab-01`. **The master switch was declared and read by nothing** —
`args/autoresearch_config.yaml: enabled: false` had shipped since the engine
landed and the nightly reflex ran the loop regardless.
`experiment_engine.autoresearch_enabled()` is now the ONE reading of it, with the
env override outranking the config **in both directions** (it is the per-host
knob for an opt-in loop that ships off) and an unreadable config **fail-closed**
with `basis: config_unreadable` — so "declared off" and "could not tell" stay
apart.

**And the loop measured an identity baseline.** `run_loop` evaluated the domain,
created an experiment, ran it, evaluated AGAIN with nothing changed and decided
keep/discard on that delta — which is why every result carried
`placeholder_metrics: True`. Any placeholder domain now makes the WHOLE run
`unmeasurable`: `metric_value` None, `total_kept` / `total_discarded` /
`acceptance_rate` None, **no GKP export**. A placeholder domain's keep count
rides as `kept_unmeasured`, never `kept` — a total summed across a measured and
an unmeasured domain is not a total. Two more empty denominators, both previously
a confident 0.0, are now `unmeasurable` with reasons (`no_experiments_run`,
`no_measured_domain`); a MEASURED 0.0 over real experiments still reports 0.0.

**The 0.0 was deleting its own record.** Measured on the live board: **73 runs,
73 successes, `last_metric_value` 0.0 — and 73 `reflex.started` rows with NOT ONE
`reflex.completed`**, because `base.run_reflex` suppresses the audit row when
`metric_value == 0 and not details.get("tasks")`. `None == 0` is False, so an
unmeasurable run is now recorded. Two daemon seams had coerced the refusal back
to a number (`evaluate_metric(cfg, None)` raised `TypeError`; the ORANGE path did
`float(metric or 0.0)` and staged a `pending_review` GKP for a run that did not
run); both fixed, and `_NO_PROPOSAL_STATUSES` is `{disabled, unmeasurable}` and
never `ok`.

**The Foundry has no stage that could emit.** 4 of 8 stage modules do not
exist — synthesizer, scorer, deliberator and **SEEDER, the only writer of a
kanban row** — so `tasks_emitted: 0` was never the gate verdict it reads as.
`run_cycle` now returns `stages_missing` / `stages_present` (which modules
IMPORT) and `stages_ran` (which were INVOKED — a present stage still does not run
under the circuit breaker or the rate limit), and the reflex reports
`unmeasurable` while `success` stays True, because an unbuilt stage is a gap and
not a failing cycle.

Standing claim (`autonomy-lrn-01`): **`experiment_loop_measures_a_change`** —
reported = the loop's own keep count; derived = DISTINCT experiment ids whose
`pre_metric != post_metric`, sharing no column, no reduction and no author. Live
verdict `agrees`, **reported 0 / derived 0 over 9 rows, every one of which has
`pre_metric == post_metric` exactly.**

`experiment_programs` is created in BOTH schemas and read and written by
**NOTHING** (every program load resolves to
`args/experiment_programs/<domain>.yaml`), so it is registered under
`substrates:` and probes `empty`. Named rather than dropped — deleting a table
two schemas declare is its own decision with its own migration.

### `pin` — is the pinned artifact still current (Obtainium)

`xrv-pin-01`. `args/pinned_artifacts.yaml` enumerates **16** artifacts (the two
floci pin files, the four compose services, testcontainers-floci) and is a
DECLARATION, never a second copy of a pin: each entry names the file the pin
actually lives in and the survey reads it through `image_vendor.parse_pin`, so
there is no second parser to drift from. A `pin_source` that disagrees with the
manifest is `unmeasurable` rather than resolved in favour of one side.

    AGREEMENT IS NOT CURRENCY.

**Measured on this host 2026-09-12, first live survey: 16 declared, 10 current,
6 behind, 0 unmeasurable.** postgres `16.3-alpine -> 18.6-alpine`, mysql
`8.0.36 -> 26.7.0`, valkey `8 -> 9`, opensearch `2.19.5 -> 3.8.0`, amazonlinux
`2023 -> 2027`, registry `2 -> 3`. **Three also carry digest drift**
(lambda-python, elasticache-valkey, ec2-amazonlinux): the pinned tag no longer
serves the digest the pin file names, so an air-gap bundle re-cut from the tag
today would not contain what the pin says. Both `:latest` pins measured UNMOVED
since 2026-09-05 — the positive control for the digest lane.

Two bases for `behind`, and which one decided is recorded: `version_tag` where
the pinned tag carries an ordering (same SUFFIX and component count only, so
`16.4-alpine` supersedes `16.3-alpine` and `16.4` does not), and `digest` where
it does not (`redpanda:latest` and `k3s:latest` are pinned by a MUTABLE tag, so
"newer" for them is knowable only as the tag having MOVED). ONE code path serves
every registry: the host comes from the ref and the bearer token from the
registry's OWN 401 challenge, so Docker Hub, `public.ecr.aws` and `ghcr.io` need
no per-registry table.

The `artifact_freshness` reflex (24 h, green) was dispatched once through the
daemon: **6 findings, 5 cards filed, `ecr-registry` deferred by name**;
`capability_consumption --class reflex` then read **83 declared / 83 consumed, 0
never-run**. It **never pulls and never writes a pin**, structurally (D407).

---

## Found by the measurements, not by the plan

| finding | where it went |
|---|---|
| Every MCP call made from a Claude Code session is invisible to `capability_consumption --class mcp_dispatch_tool` (3 vs 1 of 472 on this host) | `xrv-cost-05`, `pr_opened` |
| CLAUDE.md is 357 KB / ~89k tokens and costs approx 4.75 M tokens/day across the fleet | `xrv-docs-02`, `backlog` — the proposal is to move the card essays out to a `docs/reference/cards/` tree (which does not exist yet) and keep the rules here |
| `user_prompt_submit` / `pre_compact` written by shipped hooks, admitted by no CHECK constraint | fixed in `xrv-mem-01` (migration `20260911220746`) |
| `role_hijack_jailbreak` matched the substring `dan` — 9,929 false criticals | fixed in `xrv-shield-01` |
| `secret_detector`'s unanchored `--exclude-files` drops every path containing `build`/`dist` | named in `xrv-shield-01`, unfixed — ~20 consumers, owes its own survey |
| `scan_mcp_servers` returns `passed: True` for an absent config | handled at the caller in `xrv-shield-01` |
| On SQLite `audit_trail`/`hook_events` hold BOTH timestamp spellings, and a `T`-form bound silently excluded every space-form row of the same day | fixed at the shared query builder in `xrv-mem-03` |
| `record_task_cost` turned an ABSENT price into a `0.0` ledger row | fixed in `xrv-cost-04` |
| 73 `reflex.started` rows with zero `reflex.completed`, because a fabricated 0.0 suppressed the audit row | fixed in `xrv-lab-01` |
| `experiment_programs` created in both schemas, read and written by nothing | registered as a substrate; dropping it is its own decision |
| `skill_selector`'s own documented example matches no category (no stemming) | `xfail(strict)` in the routing corpus |
| `tools/manifest/memory-system.md` named `scoped_provider.py` and `init_memory_db.py`, neither of which exists | rows removed in `xrv-mem-01` |
| `docs/features/phase-flx-floci-emulator.md` still read 16/21 | corrected in the card PR |
| `companion.py --sync --write` silently DELETES the optional `prerequisites:` frontmatter `xrv-route-02` shipped — the skill translator rebuilds the block from a fixed field list, so the ONE skill declaring it (`icdev-secure`) lost it on the next sync and `invoke.py` read the skill as having no prerequisites | found by running the documented sync step in `xrv-docs-01`; REVERTED there, unfixed — the translator's field list is its own card |

## The honesty rails every card in this phase obeys

1. **Every rate is `None` over an empty denominator** — never `0.0` and never
   `100.0` (`args/perfect_score_gate.yaml`, ratcheted to 0). A MEASURED zero
   stays a real red bar, and both directions are asserted.
2. **`unmeasurable` is its own verdict and never folds into `ok` or `clean`.** An
   air-gapped freshness survey is `unmeasurable`, never `current`; an absent MCP
   config is `unmeasurable`, never `pass`; a placeholder experiment domain is
   `unmeasurable`, never a green cycle.
3. **`None`, never `[]`.** An empty list from a run that never looked is a claim
   about the artifact. Every such field names why it is empty.
4. **Report only unless a fire-rate survey is recorded.** `should_run` ships
   `report`; `pin_census` and `agent_config_shield` register at `warn`; four
   surveys carry no `--gate` at all, because a survey with a `--gate` earns
   itself a `|| true`. **Exit 2 = the report could not be produced, which is
   never the same as a clean report.**
5. **Wire an existing seam before writing a new one** — and prove it with an AST
   test, because a behavioural test over today's callers still passes the day
   somebody threads a private copy through.

## Not built, and named

- **`xrv-lab-02`** — real autonomous code mutation for one domain, held by the
  `xrv-gate-00` manual gate. Until it opens, every experiment run is honestly
  `unmeasurable`; nothing in `xrv-lab-01` makes the loop apply a code change.
- **`enforce` for `should_run`** — the ledger it would enforce is a cost proxy,
  not a cost. Arming it today would park every task on the board until
  2026-10-01: correct against the ledger, and a stall against the estate. The
  repair is the token cap moved after routing or windowed, then re-survey.
- **Anchoring `secret_detector`'s exclude regex** — ~20 consumers, its own survey.
- **`binary_triage`'s `v1.1.1k` blind spot** — reported, pinned by a test, and
  repaired in the triage seam or nowhere.
- **An MCP-server-side dispatch audit** — a new writer to an append-only table
  (`xrv-cost-05`).
- **`heartbeat_daemon.check_memory_maintenance` flushes `data/memory.db`**, a
  file the PG board does not use; delivery there is `memory_maintenance_reflex`.
  Not repaired by `xrv-mem-02`.

---

## Re-derive everything

```bash
python -m tools.cost.session_cost --survey --since-days 7 --project ICDev --json
python -m tools.cost.session_cost --survey --by-verdict --json
python -m tools.cost.waste_survey --since-days 30 --project ICDev --json
python -m tools.cache_savings.spend --json
python -m tools.kanban.should_run --survey --json
python -m tools.hooks.session_context --json
python -m tools.hooks.observation_capture --survey --since-days 7 --json
python tools/memory/hybrid_search.py --query "budget" --layer index --json
python -m tools.security.agent_config_shield --json
python tools/nova/skill_generator.py --analyze --json
python -m tools.dx.tool_index --refresh --json
python -m tools.routing.corpus_survey --json
python tools/ci/pin_census.py --json
python -m tools.analyzers.binary_triage <path> --json
python -m tools.analyzers.ghidra_headless <path> --json
python -m tools.compliance.binary_components <artifact> --json
python -m tools.devsecops.artifact_corroboration --artifact <path> --attestation <att.json> --json
python -m tools.airgap.artifact_freshness --survey --json
```

Surveys: `docs/audits/xrv-cost-03-waste-survey.md`,
`docs/audits/xrv-mem-02-capture-rate.md`,
`docs/audits/xrv-route-03-pin-census-survey.md`,
`docs/audits/xrv-run-01-should-run-survey.md`,
`docs/audits/xrv-shield-01-agent-config-survey.md`.
