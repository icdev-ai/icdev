# [TEMPLATE: CUI // SP-CTI]
"""
UserPromptSubmit hook — logs user prompts and runs prompt injection detection.

Modes:
    Default: Logs prompt to DB + runs prompt injection scan (warn-only).
    Exit code 0 = allow prompt, exit code 2 = block prompt.
"""

import json
import re
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HOOKS_DIR.parent.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------- lightweight prompt injection patterns (subset of prompt_injection_detector) ----------

INJECTION_PATTERNS = [
    # Role hijacking
    (r"ignore\s+(all\s+)?previous\s+instructions", "role_hijacking"),
    (r"you\s+are\s+now\s+(a|an)\s+", "role_hijacking"),
    (r"disregard\s+(all\s+)?prior", "role_hijacking"),
    (r"forget\s+(everything|all)\s+(you|that)", "role_hijacking"),
    # Delimiter attacks
    (r"```\s*(system|admin|root)", "delimiter_attack"),
    (r"<\s*/?\s*(system|prompt|instruction)", "delimiter_attack"),
    # Instruction injection
    (r"new\s+instructions?\s*:", "instruction_injection"),
    (r"override\s+(previous\s+)?instructions?", "instruction_injection"),
    (r"execute\s+the\s+following\s+(instead|command)", "instruction_injection"),
    # Data exfiltration
    (r"(print|output|show|reveal|display)\s+(your|the|all)\s+(system\s+)?prompt", "data_exfiltration"),
    (r"(print|output|show|reveal)\s+(your|the)\s+(instructions|rules|config)", "data_exfiltration"),
    # Encoded payloads
    (r"base64\s*[:\-]\s*[A-Za-z0-9+/=]{20,}", "encoded_payload"),
]


def scan_for_injection(prompt: str) -> list:
    """Quick scan for prompt injection patterns. Returns list of (category, pattern)."""
    findings = []
    prompt_lower = prompt.lower()
    for pattern, category in INJECTION_PATTERNS:
        if re.search(pattern, prompt_lower):
            findings.append({"category": category, "pattern": pattern})
    return findings


def capture_decision(prompt: str, session_id: str):
    """A user turn beginning ``decision:`` -> one auto_capture buffer row (xrv-mem-02).

    Deterministic: the first sentence after the tag, ``<private>`` spans
    dropped before capture, per-session cap honoured. Returns the capture
    verdict or None when the turn is not a decision. Never raises.
    """
    if not isinstance(prompt, str) or not prompt.lstrip()[:9].lower().startswith("decision:"):
        return None
    try:
        from tools.hooks.observation_capture import capture_prompt_event

        return capture_prompt_event(prompt, session_id)
    except Exception:
        return None


def main():
    try:
        input_data = json.loads(sys.stdin.read())
        session_id = input_data.get("session_id", "")
        prompt = input_data.get("prompt", "")

        from send_event import get_session_id, store_event

        sid = session_id or get_session_id()

        # Scan for prompt injection
        findings = scan_for_injection(prompt) if prompt else []

        payload = {
            "prompt_length": len(prompt),
            "prompt_preview": prompt[:200] if prompt else "",
            "injection_findings": findings,
            "injection_detected": len(findings) > 0,
        }
        # xrv-mem-02: a turn beginning `decision:` is captured (first sentence,
        # <private> spans stripped) into the auto_capture buffer. The payload
        # carries the verdict, never the sentence.
        decision = capture_decision(prompt, sid)
        if decision:
            payload["memory_capture"] = {
                "kind": decision.get("kind"),
                "status": decision.get("status"),
                "private_stripped": decision.get("private_stripped", False),
            }

        store_event(
            session_id=sid,
            hook_type="user_prompt_submit",
            payload=payload,
        )

        # Warn-only mode: log but don't block.
        # To enable blocking, uncomment:
        # if findings:
        #     print(f"BLOCKED: Prompt injection detected — {findings[0]['category']}", file=sys.stderr)
        #     sys.exit(2)

    except Exception:
        pass  # Never block user input on hook failure

    sys.exit(0)


if __name__ == "__main__":
    main()
