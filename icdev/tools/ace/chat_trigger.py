# CUI // SP-CTI
"""ACE chat trigger — detect trigger conditions in chat messages and launch ACE.

Provides:
    detect_ace_trigger(content) -> 'explicit' | 'implicit' | None
    maybe_launch_ace(context_id, content, user_id, project_id) -> instance_id | None

Trigger conditions
------------------
Explicit  "@team <problem>" at the start of the message.
Implicit  Message is 200+ characters AND matches 4+ distinct RICOAS signal patterns.
"""
from __future__ import annotations

import re
import sys
from typing import Optional

from icdev.tools.ace import controller as _ace_controller

_ORIGINAL_CONTROLLER_CLS = _ace_controller.ACEController

#: Module-level patch point, read by the resolution in ``maybe_launch_ace``.
#: It has to actually exist: ``monkeypatch.setattr(chat_trigger,
#: "ACEController", ...)`` raises AttributeError on a name the module never
#: defines, so the seam the code below documents was unusable — the whole
#: `getattr(sys.modules[__name__], "ACEController", ...)` branch could only
#: ever return its default.
ACEController = _ORIGINAL_CONTROLLER_CLS

# Explicit trigger: message starts with @team
_EXPLICIT_RE = re.compile(r"^\s*@team\b", re.IGNORECASE)

# RICOAS signal patterns (subset of problem_classifier._SIGNALS)
_RICOAS_SIGNAL_RES: list[re.Pattern] = [
    re.compile(r"\bshall\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bmust\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bshould\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bneeds? to\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bhas to\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\brequirement[s]?\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\buser stor(?:y|ies)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bacceptance criteri(?:a|on)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bthe system\b.{0,60}\b(?:shall|should|must|will|needs?)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bfunctional requirement\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:^|\. )(?:create|build|develop|design|implement)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:^|\. )(?:deploy|integrate|generate)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:^|\. )(?:monitor|capture|track|detect|alert)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?:^|\. )(?:analyze|analyse|process|correlate|aggregate)\b", re.IGNORECASE | re.DOTALL),
]

_IMPLICIT_MIN_LEN = 200
_IMPLICIT_MIN_SIGNALS = 4


def count_ricoas_signals(text: str) -> int:
    """Count distinct RICOAS signal patterns matched in text."""
    return sum(1 for p in _RICOAS_SIGNAL_RES if p.search(text))


def detect_ace_trigger(content: str) -> Optional[str]:
    """Classify the trigger type for a chat message.

    Returns:
        'explicit'  if the message starts with '@team'
        'implicit'  if 200+ chars AND 4+ RICOAS signals match
        None        otherwise
    """
    if _EXPLICIT_RE.match(content):
        return "explicit"
    if len(content) >= _IMPLICIT_MIN_LEN and count_ricoas_signals(content) >= _IMPLICIT_MIN_SIGNALS:
        return "implicit"
    return None


def maybe_launch_ace(
    context_id: str,
    content: str,
    user_id: str = "system",
    project_id: str = "",
) -> Optional[str]:
    """Detect trigger and launch ACE if appropriate.

    Returns:
        ACE instance_id if launched, None otherwise.
    """
    trigger_type = detect_ace_trigger(content)
    if trigger_type is None:
        return None

    if trigger_type == "explicit":
        problem_text = _EXPLICIT_RE.sub("", content).strip()
    else:
        problem_text = content

    # Tests patch either the controller class directly or the module-level alias.
    # Prefer a patched controller module, then the module-level alias, then default.
    if _ace_controller.ACEController is not _ORIGINAL_CONTROLLER_CLS:
        _ctrl = _ace_controller.ACEController
    else:
        _ctrl = getattr(sys.modules[__name__], "ACEController", _ORIGINAL_CONTROLLER_CLS)

    return _ctrl.get_instance().launch(
        problem_text=problem_text,
        trigger_source="chat",
        trigger_ref=context_id,
        user_id=user_id,
        project_id=project_id,
    )
