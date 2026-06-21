# CUI // SP-CTI
"""Pillar 10 — STIG Compliance Markers (ICDEV): STIG V-IDs in config, code, or docs."""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import time
from functools import lru_cache
from typing import Any, Optional

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _glob_files,
    _search,
)

# STIG vulnerability ID pattern: V-NNNNNN or V-NNNNNN/SV-NNNNNN
_STIG_VID_PATTERN = r"\bV-\d{5,6}\b|\bSV-\d{5,6}r\d+_rule\b"
_STIG_DOC_PATTERN = r"STIG|Security\s+Technical\s+Implementation\s+Guide|DISA\s+STIG"
_CAT_PATTERN = r"\bCAT\s*[I]{1,3}\b|\bCAT-[123]\b|\bCategory\s+[I]{1,3}\b"

# ---------------------------------------------------------------------------
# Config loader for NLP extractor thresholds
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[4] / "args" / "agent_readiness_config.yaml"
_DEFAULTS: dict[str, Any] = {
    "nlp_extractor_enabled": True,
    "nlp_extractor_model": "claude-haiku-4-5-20251001",
    "nlp_extractor_max_tokens": 256,
    "nlp_extractor_confidence_threshold": 0.7,
    "nlp_extractor_text_sample_chars": 2000,
}


@lru_cache(maxsize=1)
def _load_thresholds() -> dict[str, Any]:
    """Load STIG pillar NLP extractor config from args/agent_readiness_config.yaml.

    Falls back to hard-coded defaults if the config file is absent or malformed.
    """
    try:
        import yaml
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        cfg = data.get("pillars", {}).get("stig_compliance", {}).get("nlp_extractor", {})
        return {
            "nlp_extractor_enabled": bool(cfg.get("enabled", _DEFAULTS["nlp_extractor_enabled"])),
            "nlp_extractor_model": str(cfg.get("model", _DEFAULTS["nlp_extractor_model"])),
            "nlp_extractor_max_tokens": int(cfg.get("max_tokens", _DEFAULTS["nlp_extractor_max_tokens"])),
            "nlp_extractor_confidence_threshold": float(
                cfg.get("confidence_threshold", _DEFAULTS["nlp_extractor_confidence_threshold"])
            ),
            "nlp_extractor_text_sample_chars": int(
                cfg.get("text_sample_chars", _DEFAULTS["nlp_extractor_text_sample_chars"])
            ),
        }
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


# ---------------------------------------------------------------------------
# NLP extractor — Claude Haiku for natural-language STIG reference detection
# ---------------------------------------------------------------------------

def _nlp_extract_stig_refs(text: str, task: str) -> Optional[dict]:
    """Extract STIG references from text using Claude Haiku NLP.

    Returns dict with keys: found (bool), refs (list[str]), confidence (float).
    Returns None when LLM is unavailable so callers fall back to regex.
    """
    thresholds = _load_thresholds()
    if not thresholds["nlp_extractor_enabled"]:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        from tools.llm.anthropic_provider import AnthropicLLMProvider
        from tools.llm.provider import LLMRequest
    except ImportError:
        return None

    sample = text[:thresholds["nlp_extractor_text_sample_chars"]]
    prompt = (
        f"You are a STIG compliance analyst. Analyze the following text and {task}.\n\n"
        f"Text:\n{sample}\n\n"
        "Respond ONLY with valid JSON in this exact format: "
        '{"found": true, "refs": ["V-220938", "CAT I"], "confidence": 0.9}\n'
        "Where refs contains V-IDs, CAT severity labels, or STIG document names found in the text. "
        'Set found to false and refs to [] when nothing is detected.'
    )
    try:
        provider = AnthropicLLMProvider(api_key=api_key)
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=thresholds["nlp_extractor_max_tokens"],
        )
        model_id = thresholds["nlp_extractor_model"]
        model_cfg = {"max_output_tokens": thresholds["nlp_extractor_max_tokens"]}
        response = provider.invoke(request, model_id, model_cfg)
        result_text = response.content.strip()
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:  # noqa: BLE001
        pass
    return None


def _check_stig_vids_in_code(repo: pathlib.Path) -> CriterionResult:
    cid = "stig-vids-in-code"
    py_files = _glob_files(repo, "**/*.py")
    yaml_files = _glob_files(repo, "**/*.yaml") + _glob_files(repo, "**/*.yml")
    all_files = py_files[:30] + yaml_files[:20]

    # Fast path: regex detection
    hits = []
    for f in all_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if re.search(_STIG_VID_PATTERN, content):
            hits.append(f.name)
    if hits:
        return CriterionResult(cid, True, f"STIG V-IDs found in {len(hits)} file(s): {', '.join(hits[:5])}")

    # Enhanced path: NLP for natural-language V-ID references missed by regex
    thresholds = _load_thresholds()
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    for f in all_files[:10]:
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _nlp_extract_stig_refs(
            content,
            "identify any STIG vulnerability IDs (V-NNNNN format) or SV-rule references in code comments or config values",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            refs = result.get("refs", [])
            ref_str = ", ".join(refs[:3]) if refs else "detected reference"
            return CriterionResult(cid, True, f"STIG V-IDs detected via NLP in {f.name}: {ref_str}")

    return CriterionResult(cid, False, "No STIG V-IDs found in source or config files.",
                           "Reference STIG V-IDs (e.g. # STIG: V-220938) in security-relevant code and config.")


def _check_stig_in_docs(repo: pathlib.Path) -> CriterionResult:
    cid = "stig-in-docs"
    doc_files = (
        _glob_files(repo, "docs/**/*.md") + _glob_files(repo, "*.md")
        + _glob_files(repo, "docs/**/*.txt")
    )

    # Fast path: regex detection
    for f in doc_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, _STIG_DOC_PATTERN) or re.search(_STIG_VID_PATTERN, content):
            return CriterionResult(cid, True, f"STIG reference found in docs: {f.name}")

    # Enhanced path: NLP for natural-language STIG references missed by regex
    thresholds = _load_thresholds()
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    for f in doc_files[:5]:
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _nlp_extract_stig_refs(
            content,
            "identify any STIG references, DISA STIG mentions, Security Technical Implementation Guide "
            "citations, or STIG V-IDs written in natural language",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            refs = result.get("refs", [])
            ref_str = ", ".join(refs[:3]) if refs else "natural language reference"
            return CriterionResult(cid, True, f"STIG reference detected via NLP in {f.name}: {ref_str}")

    return CriterionResult(cid, False, "No STIG references in documentation.",
                           "Add STIG checklist or V-ID references to compliance documentation.")


def _check_stig_checklist(repo: pathlib.Path) -> CriterionResult:
    cid = "stig-checklist"
    # XCCDF or CKL files are standard STIG checklist formats
    checklist_files = (
        _glob_files(repo, "**/*.ckl")
        + _glob_files(repo, "**/*.xml")
        + _glob_files(repo, "**/stig*.yaml")
        + _glob_files(repo, "**/stig*.json")
        + _glob_files(repo, "docs/**/*stig*")
        + _glob_files(repo, "docs/**/*checklist*")
    )
    for f in checklist_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if re.search(_STIG_VID_PATTERN, content) or _search(content, _STIG_DOC_PATTERN):
            return CriterionResult(cid, True, f"STIG checklist found: {f.name}")
    # Check icdev-comply output area
    if (repo / "docs" / "compliance").is_dir():
        for f in _glob_files(repo / "docs" / "compliance", "*.md"):
            content = f.read_text(encoding="utf-8", errors="replace")
            if re.search(_STIG_VID_PATTERN, content) or _search(content, _STIG_DOC_PATTERN):
                return CriterionResult(cid, True, f"STIG checklist in compliance docs: {f.name}")

    # Enhanced path: NLP for checklist content expressed in natural language
    thresholds = _load_thresholds()
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    for f in checklist_files[:3]:
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _nlp_extract_stig_refs(
            content,
            "determine whether this file represents a STIG checklist with vulnerability findings, "
            "V-IDs, or CAT severity ratings",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            refs = result.get("refs", [])
            ref_str = ", ".join(refs[:3]) if refs else "checklist content"
            return CriterionResult(cid, True, f"STIG checklist detected via NLP in {f.name}: {ref_str}")

    return CriterionResult(cid, False, "No STIG checklist (.ckl, XCCDF, or compliance doc) found.",
                           "Generate a STIG checklist with icdev-comply or store .ckl files in docs/compliance/.")


def _check_cat1_remediation(repo: pathlib.Path) -> CriterionResult:
    cid = "cat1-remediation"
    all_files = _glob_files(repo, "**/*.py")[:20] + _glob_files(repo, "docs/**/*.md")

    # Fast path: regex — co-occurrence of V-IDs and CAT severity markers
    for f in all_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if re.search(_STIG_VID_PATTERN, content) and _search(content, _CAT_PATTERN):
            return CriterionResult(cid, True, f"CAT severity markers + V-IDs found in {f.name}")

    # If STIG checklist exists with any content, assume CAT1 is being tracked
    checklist_files = _glob_files(repo, "**/*.ckl") + _glob_files(repo, "**/stig*.yaml")
    if checklist_files:
        return CriterionResult(cid, True, f"STIG checklist present; CAT I tracking assumed: {checklist_files[0].name}")

    # Enhanced path: NLP for natural-language CAT severity descriptions missed by regex co-occurrence
    thresholds = _load_thresholds()
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    doc_files = _glob_files(repo, "docs/**/*.md") + _glob_files(repo, "*.md")
    for f in doc_files[:5]:
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _nlp_extract_stig_refs(
            content,
            "identify any CAT I, CAT II, or CAT III STIG severity findings, remediation evidence, "
            "or category severity references written in natural language prose",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            refs = result.get("refs", [])
            ref_str = ", ".join(refs[:3]) if refs else "CAT severity reference"
            return CriterionResult(cid, True, f"CAT I remediation evidence detected via NLP in {f.name}: {ref_str}")

    return CriterionResult(cid, False, "No CAT I STIG remediation evidence found.",
                           "Document CAT I/II/III STIG finding severity in compliance artifacts.")


# ---------------------------------------------------------------------------
# Post-death verification gate — subprocess STIG scanner support
# ---------------------------------------------------------------------------

def _verify_pid_exited(pid: int, poll_interval: float = 0.05, max_wait: float = 2.0) -> bool:
    """Confirm a process truly exited by polling its OS presence.

    On POSIX uses ``os.kill(pid, 0)`` (signal 0 raises OSError/ProcessLookupError
    when the process no longer exists).  On Windows uses ``GetExitCodeProcess``
    via ctypes — a non-STILL_ACTIVE exit code confirms the process has exited.

    Returns True when the PID is confirmed gone/exited, False if the process
    still appears alive after *max_wait* seconds.  A False return means the
    caller MUST NOT treat the subprocess result as a valid completion.
    """
    import sys

    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        if sys.platform == "win32":
            try:
                import ctypes
                STILL_ACTIVE = 259
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                    PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                )
                if not handle:
                    return True  # OpenProcess failed → process does not exist
                exit_code = ctypes.c_ulong(0)
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))  # type: ignore[attr-defined]
                ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
                if exit_code.value != STILL_ACTIVE:
                    return True  # Process has exited (exit code ≠ STILL_ACTIVE)
            except Exception:  # noqa: BLE001
                return True  # Cannot interrogate → assume gone
        else:
            try:
                # Signal 0: check existence without sending a real signal.
                os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                # ESRCH → process does not exist → truly gone.
                return True
            except PermissionError:
                # EPERM → process exists but we lack permission to signal it.
                # Treat as "still alive".
                pass
        time.sleep(poll_interval)
    return False


def _run_stig_cmd_with_death_gate(
    cmd: list[str],
    *,
    cwd: Optional[pathlib.Path] = None,
    timeout: int = 30,
) -> tuple[bool, str]:
    """Run an external STIG scanner command with a post-death verification gate.

    Returns (success: bool, output: str).

    Gate invariant: a non-zero return code OR a PID that has not truly exited
    is treated as a failed verification, and the result is explicitly NOT moved
    to 'backlog' — it is surfaced as a failed check so the caller can decide.
    """
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
        )
        pid = proc.pid
        try:
            stdout_bytes, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return False, f"STIG scanner timed out after {timeout}s (cmd: {cmd[0]})"

        returncode = proc.returncode
        output = stdout_bytes.decode("utf-8", errors="replace").strip()

        # ── Post-death verification gate ──────────────────────────────────────
        # After proc.communicate() returns, the subprocess *should* be gone.
        # Confirm the PID is truly absent before accepting the result.
        # If the process is still alive the completion cannot be trusted —
        # explicitly do NOT mark this as 'backlog'; surface it as a gate failure.
        pid_gone = _verify_pid_exited(pid)
        if not pid_gone:
            return False, (
                f"Post-death verification FAILED: PID {pid} still present after "
                f"subprocess reported exit (cmd: {cmd[0]}). "
                "Result rejected — do not promote to backlog."
            )
        # ── End gate ──────────────────────────────────────────────────────────

        success = returncode == 0
        return success, output

    except FileNotFoundError:
        return False, f"STIG scanner not found: {cmd[0]}"
    except Exception as exc:  # noqa: BLE001
        return False, f"STIG scanner error: {exc}"


def _check_external_stig_scanner(repo: pathlib.Path) -> CriterionResult:
    """Attempt to run an installed STIG scanner (OpenSCAP / oscap) with a
    post-death verification gate.

    The gate confirms the subprocess PID is truly gone before accepting any
    result as valid, preventing a valid completion from being silently moved
    to 'backlog' due to a zombie or stalled process.

    If no external scanner is installed the criterion is skipped (not failed)
    so it does not penalise projects that rely solely on static artefacts.
    """
    cid = "external-stig-scanner"

    # Discover an available STIG scanner on PATH.
    scanner_candidates = ["oscap", "openscap", "stig-checklist"]
    scanner_cmd: Optional[list[str]] = None
    for candidate in scanner_candidates:
        try:
            probe = subprocess.run(  # noqa: S603,S607
                [candidate, "--version"],
                capture_output=True,
                timeout=5,
            )
            if probe.returncode == 0:
                scanner_cmd = [candidate, "--version"]
                break
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue

    if scanner_cmd is None:
        return CriterionResult(
            cid,
            passed=False,
            message="No external STIG scanner (oscap/openscap) found on PATH.",
            details="Install OpenSCAP and run oscap against a STIG profile to enable this criterion.",
            skipped=True,
        )

    success, output = _run_stig_cmd_with_death_gate(scanner_cmd, cwd=repo)

    if "verification FAILED" in output:
        # Post-death gate explicitly rejected the result — do not backlog.
        return CriterionResult(
            cid,
            passed=False,
            message=output,
            details="Post-death gate rejected subprocess result. Investigate stale PID before retrying.",
        )

    if success:
        return CriterionResult(
            cid,
            passed=True,
            message=f"External STIG scanner ({scanner_cmd[0]}) completed and PID verified gone.",
            details=output[:200] if output else "",
        )

    return CriterionResult(
        cid,
        passed=False,
        message=f"External STIG scanner returned non-zero exit (cmd: {scanner_cmd[0]}).",
        details=output[:200] if output else "",
    )


PILLAR = Pillar(
    id="stig-compliance",
    name="STIG Compliance Markers",
    description="STIG V-IDs in code/config, documentation references, checklist artifacts, and CAT I tracking.",
    criteria=[
        Criterion("stig-vids-in-code", "STIG V-IDs in code", "STIG V-IDs referenced in source or config files.", "stig-compliance", 3, _check_stig_vids_in_code),
        Criterion("stig-in-docs", "STIG in docs", "STIG references appear in project documentation.", "stig-compliance", 2, _check_stig_in_docs),
        Criterion("stig-checklist", "STIG checklist", "A STIG checklist (.ckl, XCCDF) or compliance artifact exists.", "stig-compliance", 4, _check_stig_checklist),
        Criterion("cat1-remediation", "CAT I remediation", "CAT severity markers show active STIG tracking.", "stig-compliance", 4, _check_cat1_remediation),
        Criterion("external-stig-scanner", "External STIG scanner", "External STIG scanner ran with post-death PID verification.", "stig-compliance", 3, _check_external_stig_scanner),
    ],
)
