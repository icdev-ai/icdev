# CUI // SP-CTI
"""AgentShield-style scan of the AGENT CONFIG SURFACE (xrv-shield-01).

WHY. Three scanners already exist and NONE of them was ever pointed at the
files that configure the agents:

* ``tools/security/prompt_injection_detector.py::scan_project`` -- 22 patterns
  in 5 categories over ``.md/.json/.yaml/.toml/.sh/.ps1``, and its ``SKIP_DIRS``
  does not exclude ``.claude/``, ``.agents/`` or ``.cursor/``. It simply was
  never asked about them.
* ``tools/security/secret_detector.py::scan`` -- detect-secrets first, builtin
  patterns as the air-gap fallback.
* ``tools/mcp/mcp_scanner.py::scan_mcp_servers`` -- unauthenticated transport,
  plaintext HTTP, wildcard tool names, privileged tools, missing classification.

``tools/testing/claude_dir_validator.py`` checks hook SYNTAX and file
REFERENCES, and ``tools/marketplace/asset_scanner.py`` runs its 10 gates on
marketplace assets and never on the local tree. So the surface that tells every
agent in this repo what to do -- ``.claude/``, ``.agents/``, ``.cursor/``,
``CLAUDE.md``, the ten companion instruction files, ``.mcp.json`` -- had no
scanner pointed at it at all.

THIS MODULE WIRES, IT DOES NOT RE-IMPLEMENT. No injection pattern, no secret
pattern and no MCP check is spelled here; each check imports the scanner and
calls its entry point. ``tests/testing/test_claude_dir_shield_checks.py`` reads
this file's AST and refuses a string literal equal to any pattern those scanners
declare, which is the one test shape that can see a future copy-paste.

FOUR VERDICTS, and ``unmeasurable`` is never folded into ``pass``:

    pass          a scanner RAN over at least one present target and the
                  findings do not move the verdict
    warn          findings below the fail bar (reported in full), or a target
                  that could not be measured beside ones that could
    fail          a CRITICAL finding, or the wrapped scanner's OWN policy says
                  the config did not pass
    unmeasurable  the scanner raised, the config could not be parsed, or there
                  was NOTHING PRESENT to scan. An absent target is not a clean
                  one -- ``scan_mcp_servers`` returns ``passed: True`` with
                  ``error: no MCP config file found``, and reading that as a
                  pass is the fabricated-clean-bill defect this repo keeps
                  finding.

THE FAIL BAR IS ``critical``, AND THAT IS A MEASUREMENT, NOT A PREFERENCE.
Surveyed on the live tree 2026-09-11
(docs/audits/xrv-shield-01-agent-config-survey.md): the config surface carries
36 medium/high findings and ZERO are real -- 31 ``encoded_base64_block`` hits
are 40-char content hashes and long slash-joined paths, 1
``encoded_invisible_chars`` is a UTF-8 BOM, and 4 MCP mediums are
``unauthenticated_transport`` + ``missing_classification`` on the two LOCAL
STDIO servers in ``.mcp.json`` (stdio has no transport to authenticate, and
Claude Code's config format has no ``classification`` field). Failing at medium
would be a check that is red on the day it ships, which is the born-red defect
``tools/ci/born_red_survey.py`` exists to measure. They are REPORTED -- every
one, with its severity -- and they do not move the verdict.

THE MCP CHECK ADOPTS THE SCANNER'S OWN POLICY rather than inventing a second
one: ``scan_mcp_servers`` declares ``passed = high_count == 0``, so a high
finding (plaintext HTTP, wildcard tool names, a privileged exec tool) fails here
too. The card's sketch asked for an unauthenticated server to FAIL; it ships as
``warn`` because that check is the scanner's ``medium`` and the live
``.mcp.json`` trips it twice -- the deviation is recorded in the survey rather
than hidden behind a threshold nobody can see.

REPORT ONLY. Nothing here edits a config, a setting or a hook. The coherence
registration caps the status at ``warn``
(``coherence_checker.check_agent_config_shield``); this CLI exits 1 on a
critical finding and 2 when the report could not be produced, which is never the
same as a clean scan.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from icdev.core.paths import repo_root
from icdev.tools.dx.ai_platforms import AI_PLATFORM_FILES, CLAUDE_FILES
from icdev.tools.mcp import mcp_scanner as _mcp
from icdev.tools.security import prompt_injection_detector as _injection
from icdev.tools.security import secret_detector as _secrets

ROOT = repo_root(__file__)

#: Directories that configure an agent. Scanned whole.
CONFIG_DIRS: Tuple[str, ...] = (".claude", ".agents", ".cursor")

#: MCP config files this repo owns, beyond whatever the companion registry
#: declares. ``args/mcp_config.yaml`` is `mcp_scanner`'s own first candidate.
MCP_CONFIG_FILES: Tuple[str, ...] = (".mcp.json", "args/mcp_config.yaml")

COMPANION_REGISTRY = "args/companion_registry.yaml"
CLAUDE_SETTINGS = ".claude/settings.json"

#: The ONE blocking hook. CLAUDE.md: a PreToolUse hook signals "block" with
#: exit 2, so a shell neutraliser on it makes every refusal return 0. Matched on
#: a path boundary so ``coordination.py --event pre_tool_use`` is not mistaken
#: for the blocking entry.
BLOCKING_HOOK_EVENT = "PreToolUse"
BLOCKING_HOOK_SCRIPT = "pre_tool_use.py"
BLOCKING_HOOK_RE = re.compile(r"(?:^|[/\\\s])pre_tool_use\.py\b")

#: Shell constructs that discard a hook's exit status.
NEUTRALISERS: Tuple[str, ...] = (
    "|| true",
    "||true",
    "; true",
    ";true",
    "|| exit 0",
    "|| :",
)

#: Fetch-and-execute: ``curl ... | sh``, ``wget ... | bash``, ``iwr ... | iex``.
PIPE_TO_SHELL = re.compile(
    r"(?i)\b(?:curl|wget|iwr|invoke-webrequest)\b[^|]*\|\s*"
    r"(?:sudo\s+)?(?:(?:ba|z|da|k)?sh\b|iex\b|invoke-expression\b)"
)

#: A POSIX or Windows absolute path. Used to ask whether a hook command names a
#: file outside this checkout -- never to classify the command.
ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|(?<![\w$])/)[^\s\"';|&]+")

VERDICTS: Tuple[str, ...] = ("pass", "warn", "fail", "unmeasurable")

#: Severities that fail. Everything else found is still REPORTED in full.
FAIL_SEVERITIES: Tuple[str, ...] = ("critical",)


# ---------------------------------------------------------------------------
# The declared surface
# ---------------------------------------------------------------------------


def config_files() -> Tuple[str, ...]:
    """Loose instruction files: CLAUDE.md plus every AI platform file.

    DERIVED from `dx.ai_platforms`, never a second list -- that module exists
    because this list previously lived in four places that had no way to
    disagree loudly.
    """
    return tuple(CLAUDE_FILES) + tuple(rel for _p, rel in AI_PLATFORM_FILES)


def _count_files(path: Path) -> Optional[int]:
    """How many files sit under *path*. None when it cannot be read.

    This is the DENOMINATOR, not a scan: a scanner reporting zero findings over
    zero files is not a measured zero, and without a count the two read the
    same.
    """
    if not path.is_dir():
        return None
    try:
        return sum(1 for p in path.rglob("*") if p.is_file())
    except OSError:
        return None


def surface(root: Optional[Path] = None) -> Dict[str, Any]:
    """The declared config surface, with what is actually on disk."""
    base = Path(root) if root is not None else ROOT
    dirs = [
        {
            "path": d,
            "present": (base / d).is_dir(),
            "files_present": _count_files(base / d),
        }
        for d in CONFIG_DIRS
    ]
    files = [{"path": f, "present": (base / f).is_file()} for f in config_files()]
    return {
        "root": str(base),
        "dirs": dirs,
        "files": files,
        "dirs_present": sum(1 for d in dirs if d["present"]),
        "files_present": sum(1 for f in files if f["present"]),
    }


def mcp_config_targets(root: Optional[Path] = None) -> List[str]:
    """Every MCP config path this repo or a companion declares.

    Read from `args/companion_registry.yaml`, so a companion added there is
    scanned with no edit here.
    """
    base = Path(root) if root is not None else ROOT
    out: List[str] = list(MCP_CONFIG_FILES)
    reg = base / COMPANION_REGISTRY
    if not reg.is_file():
        return out
    try:
        import yaml  # noqa: PLC0415 -- optional dependency, required only here

        data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
    except Exception:
        return out
    for _cid, cfg in sorted((data.get("companions") or {}).items()):
        if not isinstance(cfg, dict):
            continue
        rel = cfg.get("mcp_config_file")
        if isinstance(rel, str) and rel and rel not in out:
            out.append(rel)
    return out


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ShieldCheck:
    """One wired scan over the agent config surface."""

    check_id: str
    check_name: str
    verdict: str
    scanner: str
    message: str
    findings: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    severity_counts: Dict[str, int] = dataclasses.field(default_factory=dict)
    targets_measured: List[str] = dataclasses.field(default_factory=list)
    targets_absent: List[str] = dataclasses.field(default_factory=list)
    targets_unmeasurable: List[Dict[str, str]] = dataclasses.field(default_factory=list)
    advisory: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    detail: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.get("severity") in FAIL_SEVERITIES)


def _severity_counts(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for f in findings:
        key = str(f.get("severity", "unknown"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _verdict(
    findings: List[Dict[str, Any]],
    measured: List[str],
    unmeasurable: List[Dict[str, str]],
    scanner_refused: bool = False,
) -> str:
    """fail > unmeasurable > warn > pass, in that order of precedence.

    A critical finding (or the wrapped scanner's own refusal) fails. Nothing
    measured is UNMEASURABLE -- never pass -- whatever the empty finding list
    says, because an empty list over an empty denominator is not a measurement.
    """
    if any(f.get("severity") in FAIL_SEVERITIES for f in findings) or scanner_refused:
        return "fail"
    if not measured:
        return "unmeasurable"
    if findings or unmeasurable:
        return "warn"
    return "pass"


def _message(
    verdict: str,
    findings: List[Dict[str, Any]],
    counts: Dict[str, int],
    measured: List[str],
    denominator: int,
    noun: str,
    unit: str = "file(s)",
) -> str:
    if verdict == "unmeasurable":
        return f"nothing measurable: no declared target could be scanned (0 {unit} read)"
    shape = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "no findings"
    return f"{len(findings)} {noun} over {len(measured)} target(s) / {denominator} {unit} — {shape}"


def _rel(path: Any) -> str:
    text = str(path).replace("\\", "/")
    root = str(ROOT).replace("\\", "/")
    return text[len(root) + 1 :] if text.startswith(root + "/") else text


# ---------------------------------------------------------------------------
# Check 1 — prompt injection in the config surface
# ---------------------------------------------------------------------------


def check_config_injection(root: Optional[Path] = None) -> ShieldCheck:
    """`prompt_injection_detector` over the config dirs and instruction files.

    Directories go through ``scan_project`` (its own walk, its own extension
    set); loose files go through ``scan_file``, which bypasses the extension
    filter -- ``.clinerules``, ``.goosehints`` and ``.cursor/rules/icdev.mdc``
    carry no scannable suffix, so the directory walk cannot see them.
    """
    base = Path(root) if root is not None else ROOT
    findings: List[Dict[str, Any]] = []
    measured: List[str] = []
    absent: List[str] = []
    unmeasurable: List[Dict[str, str]] = []
    files_scanned = 0

    try:
        detector = _injection.PromptInjectionDetector()
    except Exception as exc:
        return ShieldCheck(
            check_id="config_injection",
            check_name="Agent Config — Prompt Injection",
            verdict="unmeasurable",
            scanner="prompt_injection_detector.scan_project",
            message=f"detector could not be constructed: {exc}",
            targets_unmeasurable=[{"target": "(all)", "error": str(exc)}],
        )

    for rel in CONFIG_DIRS:
        target = base / rel
        if not target.is_dir():
            absent.append(rel)
            continue
        try:
            result = detector.scan_project(str(target))
        except Exception as exc:
            unmeasurable.append({"target": rel, "error": str(exc)})
            continue
        measured.append(rel)
        files_scanned += int(result.get("files_scanned") or 0)
        for file_result in result.get("file_results", []):
            findings.extend(_injection_findings(rel, file_result))

    for rel in config_files():
        target = base / rel
        if not target.is_file():
            absent.append(rel)
            continue
        try:
            result = detector.scan_file(str(target), source=f"config:{rel}")
        except Exception as exc:
            unmeasurable.append({"target": rel, "error": str(exc)})
            continue
        if result.get("error"):
            unmeasurable.append({"target": rel, "error": str(result["error"])})
            continue
        measured.append(rel)
        files_scanned += 1
        if result.get("detected"):
            findings.extend(_injection_findings(rel, result))

    verdict = _verdict(findings, measured, unmeasurable)
    counts = _severity_counts(findings)
    return ShieldCheck(
        check_id="config_injection",
        check_name="Agent Config — Prompt Injection",
        verdict=verdict,
        scanner="prompt_injection_detector.scan_project",
        message=_message(
            verdict, findings, counts, measured, files_scanned, "injection finding(s)"
        ),
        findings=findings,
        severity_counts=counts,
        targets_measured=measured,
        targets_absent=absent,
        targets_unmeasurable=unmeasurable,
        detail={"files_scanned": files_scanned},
    )


def _injection_findings(target: str, file_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten one scanner file result. Carries its verdict, never re-derives it."""
    out: List[Dict[str, Any]] = []
    where = file_result.get("file_path") or file_result.get("source") or target
    for f in file_result.get("findings", []):
        out.append(
            {
                "target": target,
                "file": _rel(where),
                "severity": f.get("severity"),
                "pattern": f.get("pattern_name"),
                "category": f.get("category"),
                "match": str(f.get("match", ""))[:80],
                "description": f.get("description"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Check 2 — secrets in the config surface
# ---------------------------------------------------------------------------


def check_config_secrets(root: Optional[Path] = None) -> ShieldCheck:
    """`secret_detector.scan` over the same surface -- BOTH of its arms.

    ``scan`` takes a project ROOT and walks it, so the loose instruction files
    are STAGED: copied into a temporary directory at their real relative paths,
    scanned there, and every reported path mapped back. The alternatives were
    worse -- scanning the repo root to reach ``CLAUDE.md`` would scan the whole
    tree, and a per-file loop here would copy the patterns this module exists
    not to copy.

    BOTH ARMS RUN, AND THAT IS A DEFECT WORKAROUND, NOT BELT AND BRACES.
    ``_run_detect_secrets`` passes ``--exclude-files
    '(venv|node_modules|\\.git|__pycache__|build|dist|\\.lock)'`` -- UNANCHORED,
    so the substring ``build`` excludes any path containing it. MEASURED
    2026-09-11 while writing this check's own test: a planted GitHub token in
    ``.agents/skills/icdev-build/SKILL.md`` was found under
    ``skills/x/SKILL.md`` and NOT under ``skills/icdev-build/SKILL.md``, same
    file, same token. On the live tree that silently drops every file under
    ``.claude/skills/icdev-build/`` and ``.agents/skills/icdev-build/`` -- a
    clean bill of health over files nothing read. The builtin arm's walk matches
    directory names EXACTLY (``item.name in SKIP_DIRS``) and has no such hole,
    so the two arms are unioned and the findings deduplicated on
    (file, line, type). Fixing the regex in `secret_detector` would widen what
    every one of its ~20 consumers reports and needs its own survey; this check
    refuses to inherit the hole meanwhile. Recorded in
    docs/audits/xrv-shield-01-agent-config-survey.md.
    """
    base = Path(root) if root is not None else ROOT
    findings: List[Dict[str, Any]] = []
    measured: List[str] = []
    absent: List[str] = []
    unmeasurable: List[Dict[str, str]] = []
    tools_used: List[str] = []
    files_present = 0

    for rel in CONFIG_DIRS:
        target = base / rel
        if not target.is_dir():
            absent.append(rel)
            continue
        count = _count_files(target)
        if not count:
            unmeasurable.append({"target": rel, "error": "no files present to scan"})
            continue
        found, tools, errors = _scan_both_arms(str(target), rel, prefix=rel)
        unmeasurable.extend(errors)
        if not tools:
            continue
        measured.append(rel)
        files_present += count
        tools_used.extend(tools)
        findings.extend(found)

    loose = [rel for rel in config_files() if (base / rel).is_file()]
    absent.extend(rel for rel in config_files() if not (base / rel).is_file())
    if loose:
        staged, error = _stage(base, loose)
        if staged is None:
            unmeasurable.extend({"target": rel, "error": error or "staging failed"} for rel in loose)
        else:
            try:
                found, tools, errors = _scan_both_arms(
                    str(staged), "(instruction files)", prefix=""
                )
                for err in errors:
                    unmeasurable.extend({"target": rel, "error": err["error"]} for rel in loose)
                if tools:
                    measured.extend(loose)
                    files_present += len(loose)
                    tools_used.extend(tools)
                    findings.extend(found)
            finally:
                shutil.rmtree(staged, ignore_errors=True)

    verdict = _verdict(findings, measured, unmeasurable)
    counts = _severity_counts(findings)
    return ShieldCheck(
        check_id="config_secrets",
        check_name="Agent Config — Secrets",
        verdict=verdict,
        scanner="secret_detector.scan",
        message=_message(verdict, findings, counts, measured, files_present, "secret(s)"),
        findings=findings,
        severity_counts=counts,
        targets_measured=measured,
        targets_absent=absent,
        targets_unmeasurable=unmeasurable,
        detail={"files_present": files_present, "tools": sorted(set(tools_used))},
    )


def _scan_both_arms(
    path: str, target: str, prefix: str
) -> Tuple[List[Dict[str, Any]], List[str], List[Dict[str, str]]]:
    """`secret_detector.scan` twice -- detect-secrets, then the builtin walk.

    Returns (deduplicated findings, tools that ANSWERED, per-arm errors). A
    target with no answering arm is the caller's `unmeasurable`; one arm
    answering is a MEASURED but PARTIAL scan, which the errors carry so the
    verdict degrades to `warn` rather than reading clean.
    """
    findings: List[Dict[str, Any]] = []
    tools: List[str] = []
    errors: List[Dict[str, str]] = []
    seen: set = set()
    for use_builtin in (False, True):
        arm = "builtin" if use_builtin else "detect-secrets"
        try:
            result = _secrets.scan(path, use_builtin=use_builtin)
        except Exception as exc:
            errors.append({"target": target, "error": f"{arm} arm raised: {exc}"})
            continue
        if not result.get("success", False):
            errors.append(
                {"target": target, "error": f"{arm} arm did not finish: "
                                            f"{str(result.get('raw_output', ''))[:160]}"}
            )
            continue
        tools.append(str(result.get("tool")))
        for f in _secret_findings(target, result, prefix=prefix):
            key = (f["file"], f.get("line"), f.get("pattern"))
            if key in seen:
                continue
            seen.add(key)
            findings.append(f)
    return findings, tools, errors


def _stage(base: Path, relatives: List[str]) -> Tuple[Optional[Path], Optional[str]]:
    """Copy *relatives* into a temp dir at their own relative paths."""
    staged: Optional[Path] = None
    try:
        staged = Path(tempfile.mkdtemp(prefix="icdev-shield-"))
        for rel in relatives:
            dest = staged / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base / rel, dest)
        return staged, None
    except OSError as exc:
        if staged is not None:
            shutil.rmtree(staged, ignore_errors=True)
        return None, f"could not stage instruction files: {exc}"


def _secret_findings(
    target: str, result: Dict[str, Any], prefix: str
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for f in result.get("findings", []):
        reported = str(f.get("file", "")).replace("\\", "/")
        while reported.startswith("./"):
            reported = reported[2:]
        path = f"{prefix}/{reported}" if prefix else reported
        out.append(
            {
                "target": target,
                "file": path,
                "line": f.get("line"),
                "severity": f.get("severity"),
                "pattern": f.get("type"),
                "tool": result.get("tool"),
                "match": f.get("match_preview", ""),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Check 3 — MCP server configuration
# ---------------------------------------------------------------------------


def check_mcp_config(root: Optional[Path] = None) -> ShieldCheck:
    """`mcp_scanner.scan_mcp_servers` over every declared MCP config.

    ``scan_mcp_servers`` answers an ABSENT config with ``passed: True`` and an
    ``error`` key. That is the fabricated-clean shape, so an absent target is
    recorded as absent and a present one that yields that error is recorded as
    UNMEASURABLE -- neither is ever a pass. ``.codex/config.toml`` is a real
    example of unmeasurable BY FORMAT: the loader reads YAML and JSON only, so a
    TOML config raises rather than reporting zero servers.
    """
    base = Path(root) if root is not None else ROOT
    findings: List[Dict[str, Any]] = []
    measured: List[str] = []
    absent: List[str] = []
    unmeasurable: List[Dict[str, str]] = []
    servers = 0
    refused = False

    targets = mcp_config_targets(base)
    for rel in targets:
        target = base / rel
        if not target.is_file():
            absent.append(rel)
            continue
        try:
            result = _mcp.scan_mcp_servers(target)
        except Exception as exc:
            unmeasurable.append({"target": rel, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if result.get("error"):
            unmeasurable.append({"target": rel, "error": str(result["error"])})
            continue
        measured.append(rel)
        servers += int(result.get("servers_scanned") or 0)
        if not result.get("passed", True):
            refused = True
        for f in result.get("findings", []):
            findings.append(
                {
                    "target": rel,
                    "file": rel,
                    "severity": f.get("severity"),
                    "pattern": f.get("check"),
                    "server": f.get("server_id"),
                    "cwe": f.get("cwe"),
                    "description": f.get("description"),
                }
            )

    verdict = _verdict(findings, measured, unmeasurable, scanner_refused=refused)
    counts = _severity_counts(findings)
    message = _message(
        verdict, findings, counts, measured, servers, "MCP finding(s)", unit="server(s)"
    )
    if refused:
        message += " — mcp_scanner's own policy (high_count == 0) refuses this config"
    return ShieldCheck(
        check_id="mcp_config",
        check_name="Agent Config — MCP Servers",
        verdict=verdict,
        scanner="mcp_scanner.scan_mcp_servers",
        message=message,
        findings=findings,
        severity_counts=counts,
        targets_measured=measured,
        targets_absent=absent,
        targets_unmeasurable=unmeasurable,
        detail={
            "servers_scanned": servers,
            "targets_declared": targets,
            "scanner_refused": refused,
        },
    )


# ---------------------------------------------------------------------------
# Check 4 — hook command strings
# ---------------------------------------------------------------------------


def check_hook_commands(root: Optional[Path] = None) -> ShieldCheck:
    """What `.claude/settings.json` actually tells the harness to execute.

    There is no existing scanner for this, and that is why the rules are written
    here: `claude_dir_validator` checks that a hook's FILE exists and that its
    Python PARSES, and neither question reaches the command string that runs it.

    FOUR RULES, and the first is CLAUDE.md's:

      blocking_hook_neutralised   a shell neutraliser on the BLOCKING PreToolUse
          ``pre_tool_use.py`` entry. A PreToolUse hook signals "block" with exit
          2; ``|| true`` makes the shell return 0 whatever the hook decided, so
          all eleven checks print ``BLOCKED:`` and block nothing. CRITICAL.
      pipe_to_shell               fetch-and-execute in a hook command. CRITICAL.
      absolute_path_outside_repo  the command names a file on this machine
          outside the checkout. HIGH -- a hook is supposed to run this repo's
          code.
      hook_script_not_project_rooted   a hook script reached by a bare relative
          path. MEDIUM: ``$CLAUDE_PROJECT_DIR`` is the only spelling that does
          not depend on the harness's working directory.

    A neutraliser on a NON-blocking hook is ADVISORY, not a finding: those hooks
    report and enrich, they do not refuse, so their exit status is not load
    bearing. They are listed by name so a reader can see that they were seen.

    A missing blocking entry is HIGH rather than critical: a minimal or
    companion-scaffolded settings file may legitimately declare no PreToolUse
    hook, whereas a NEUTRALISED one is an actively disabled control.
    """
    base = Path(root) if root is not None else ROOT
    settings = base / CLAUDE_SETTINGS
    scanner = "(this module — no scanner exists for hook command strings)"
    if not settings.is_file():
        return ShieldCheck(
            check_id="hook_commands",
            check_name="Agent Config — Hook Commands",
            verdict="unmeasurable",
            scanner=scanner,
            message=f"{CLAUDE_SETTINGS} is not present; nothing to measure",
            targets_absent=[CLAUDE_SETTINGS],
        )
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ShieldCheck(
            check_id="hook_commands",
            check_name="Agent Config — Hook Commands",
            verdict="unmeasurable",
            scanner=scanner,
            message=f"{CLAUDE_SETTINGS} could not be read: {exc}",
            targets_unmeasurable=[{"target": CLAUDE_SETTINGS, "error": str(exc)}],
        )

    findings: List[Dict[str, Any]] = []
    advisory: List[Dict[str, Any]] = []
    commands = 0
    blocking_seen = False

    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    for event, entries in sorted(hooks.items()):
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("hooks")
            for hook in inner if isinstance(inner, list) else []:
                if not isinstance(hook, dict):
                    continue
                command = hook.get("command")
                if not isinstance(command, str) or not command.strip():
                    continue
                commands += 1
                is_blocking = event == BLOCKING_HOOK_EVENT and bool(
                    BLOCKING_HOOK_RE.search(command)
                )
                blocking_seen = blocking_seen or is_blocking
                findings.extend(
                    _hook_findings(base, event, command, is_blocking, advisory)
                )

    if commands and not blocking_seen:
        findings.append(
            {
                "target": CLAUDE_SETTINGS,
                "file": CLAUDE_SETTINGS,
                "severity": "high",
                "pattern": "blocking_hook_absent",
                "event": BLOCKING_HOOK_EVENT,
                "command": "",
                "description": (
                    f"no {BLOCKING_HOOK_EVENT} hook runs {BLOCKING_HOOK_SCRIPT} — the only "
                    "ICDEV control that sees a tool call inside a spawned session"
                ),
            }
        )

    measured = [CLAUDE_SETTINGS] if commands else []
    unmeasurable: List[Dict[str, str]] = (
        []
        if commands
        else [{"target": CLAUDE_SETTINGS, "error": "settings.json declares no hook commands"}]
    )
    verdict = _verdict(findings, measured, unmeasurable)
    counts = _severity_counts(findings)
    return ShieldCheck(
        check_id="hook_commands",
        check_name="Agent Config — Hook Commands",
        verdict=verdict,
        scanner=scanner,
        message=_message(
            verdict, findings, counts, measured, commands, "hook finding(s)", unit="command(s)"
        ),
        findings=findings,
        severity_counts=counts,
        targets_measured=measured,
        targets_absent=[],
        targets_unmeasurable=unmeasurable,
        advisory=advisory,
        detail={"commands": commands, "blocking_hook_present": blocking_seen},
    )


def _hook_findings(
    base: Path,
    event: str,
    command: str,
    is_blocking: bool,
    advisory: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def finding(severity: str, pattern: str, description: str) -> Dict[str, Any]:
        return {
            "target": CLAUDE_SETTINGS,
            "file": CLAUDE_SETTINGS,
            "severity": severity,
            "pattern": pattern,
            "event": event,
            "command": command[:200],
            "description": description,
        }

    neutralisers = [n for n in NEUTRALISERS if n in command]
    if neutralisers:
        if is_blocking:
            out.append(
                finding(
                    "critical",
                    "blocking_hook_neutralised",
                    f"the blocking {event} hook is wrapped in {neutralisers!r}: a hook signals "
                    "block with exit 2 and the shell returns 0, so every refusal is discarded",
                )
            )
        else:
            advisory.append(
                {
                    "event": event,
                    "command": command[:200],
                    "note": (
                        f"non-blocking hook neutralised by {neutralisers!r} — reporting hook, "
                        "its exit status is not load bearing"
                    ),
                }
            )

    if PIPE_TO_SHELL.search(command):
        out.append(
            finding(
                "critical",
                "pipe_to_shell",
                "hook command fetches and executes remote content "
                "(curl/wget/iwr piped to a shell)",
            )
        )

    absolute = list(ABSOLUTE_PATH.finditer(command))
    for match in absolute:
        raw = match.group().rstrip(".,;:")
        if _outside_repo(base, raw):
            out.append(
                finding(
                    "high",
                    "absolute_path_outside_repo",
                    f"hook command names {raw!r}, which is outside this checkout",
                )
            )
            break

    if (
        ".py" in command
        and "CLAUDE_PROJECT_DIR" not in command
        and not absolute
    ):
        out.append(
            finding(
                "medium",
                "hook_script_not_project_rooted",
                "hook script is reached by a bare relative path; $CLAUDE_PROJECT_DIR is the "
                "only spelling that does not depend on the harness's working directory",
            )
        )
    return out


def _outside_repo(base: Path, raw: str) -> bool:
    """Is *raw* an absolute path that does not live under this checkout?

    Fail-OPEN on anything unresolvable: a path this cannot resolve is not
    evidence of a path outside the repo.
    """
    if "$" in raw or "%" in raw:
        return False
    try:
        candidate = Path(raw).resolve()
        root = Path(base).resolve()
    except (OSError, ValueError):
        return False
    try:
        candidate.relative_to(root)
        return False
    except ValueError:
        return True


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


CHECKS: Dict[str, Callable[..., ShieldCheck]] = {
    "config-injection": check_config_injection,
    "config-secrets": check_config_secrets,
    "mcp-config": check_mcp_config,
    "hook-commands": check_hook_commands,
}


def run(selected: Optional[List[str]] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    """Run the selected (or all four) checks and aggregate.

    ``overall_verdict`` is the worst of the four with ``unmeasurable`` kept
    apart: fail > unmeasurable > warn > pass. A run where something could not be
    measured can never read as a clean pass.
    """
    names = list(CHECKS) if not selected else [n for n in CHECKS if n in selected]
    checks = [CHECKS[n](root=root) for n in names]
    verdicts = [c.verdict for c in checks]
    if "fail" in verdicts:
        overall = "fail"
    elif "unmeasurable" in verdicts or not checks:
        overall = "unmeasurable"
    elif "warn" in verdicts:
        overall = "warn"
    else:
        overall = "pass"
    return {
        "overall_verdict": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "surface": surface(root),
        "checks_run": names,
        "critical_findings": sum(c.fail_count for c in checks),
        "total_findings": sum(len(c.findings) for c in checks),
        "by_verdict": {v: sum(1 for c in checks if c.verdict == v) for v in VERDICTS},
        "checks": [c.to_dict() for c in checks],
    }


def human(report: Dict[str, Any]) -> str:
    lines = [
        "Agent config shield (xrv-shield-01)",
        f"  root {report['surface']['root']}",
        f"  surface: {report['surface']['dirs_present']}/{len(CONFIG_DIRS)} dir(s), "
        f"{report['surface']['files_present']}/{len(config_files())} instruction file(s) present",
        f"  overall {report['overall_verdict'].upper()} — "
        f"{report['critical_findings']} critical of {report['total_findings']} finding(s)",
        "",
    ]
    for check in report["checks"]:
        lines.append(f"  [{check['verdict'].upper():<12}] {check['check_name']}")
        lines.append(f"       scanner: {check['scanner']}")
        lines.append(f"       {check['message']}")
        for f in check["findings"][:12]:
            lines.append(
                f"         - {str(f.get('severity')):<8} "
                f"{str(f.get('pattern')):<32} {f.get('file')}"
            )
        if len(check["findings"]) > 12:
            lines.append(f"         ... and {len(check['findings']) - 12} more")
        for t in check["targets_unmeasurable"]:
            lines.append(f"         ? unmeasurable: {t['target']} — {str(t['error'])[:100]}")
        for a in check["advisory"]:
            lines.append(f"         i advisory: {a.get('event', '')} {str(a.get('note', ''))[:120]}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="AgentShield-style scan of the agent config surface (xrv-shield-01)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--check",
        action="append",
        choices=sorted(CHECKS),
        help="run one check (repeatable); default is all four",
    )
    parser.add_argument(
        "--surface", action="store_true", help="print the declared surface and exit"
    )
    parser.add_argument("--root", default=None, help="scan another checkout")
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else None
    if args.surface:
        print(json.dumps(surface(root), indent=2))
        return 0

    try:
        report = run(args.check, root=root)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "overall_verdict": "unmeasurable"}, indent=2))
        return 2

    print(json.dumps(report, indent=2, default=str) if args.json else human(report))
    return 1 if report["critical_findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
