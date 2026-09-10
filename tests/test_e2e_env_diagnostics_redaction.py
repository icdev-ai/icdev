# CUI // SP-CTI
"""The E2E env-diagnostics snapshot masks credentials in VALUES (qa-fail-0992fb60b78c0b2e).

``globalSetup.ts`` writes ``.tmp/test_runs/e2e-env-diagnostics*.json`` on every
Playwright run, and CI uploads it. Its ``display()`` redacted by KEY NAME only,
so ``ICDEV_DATABASE_URL=postgresql://<user>:<password>@<host>/<db>`` -- a key no
secret pattern matches -- landed in the snapshot with the password verbatim.

The behaviour is proved by ``tests/e2e/env_diagnostics_redaction.spec.ts``,
which writes a real snapshot (run RED first against the key-name-only
``display()``). That spec needs Node, and the CI pytest jobs have none, so this
file pins the WIRING in the gated suite: the redaction lives in one helper,
``globalSetup.ts`` uses it for every value it prints or writes, and no
key-name-only rendering is left behind.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_SETUP = REPO_ROOT / "globalSetup.ts"
HELPER = REPO_ROOT / "tests" / "e2e" / "fixtures" / "env_redaction.ts"
SPEC = REPO_ROOT / "tests" / "e2e" / "env_diagnostics_redaction.spec.ts"


def _function_body(source: str, signature: str) -> str:
    """The text of one top-level TypeScript function, up to its closing brace."""
    assert signature in source, f"{signature!r} not found"
    return source.split(signature, 1)[1].split("\n}\n", 1)[0]


def test_display_masks_embedded_credentials_after_the_key_check():
    src = HELPER.read_text(encoding="utf-8")
    redact = _function_body(src, "export function redactValue(")
    assert "isSecretKey(key, value)" in redact
    assert "maskEmbeddedCredentials(value)" in redact, (
        "a value whose key names no secret must still have embedded credentials masked"
    )
    display = _function_body(src, "export function display(")
    assert "redactValue(key, value)" in display


def test_the_helper_masks_url_userinfo_and_keyword_passwords():
    src = HELPER.read_text(encoding="utf-8")
    mask = _function_body(src, "export function maskEmbeddedCredentials(")
    assert "URL_USERINFO_PASSWORD_RE" in mask
    assert "KEYWORD_PASSWORD_RE" in mask


def test_global_setup_renders_every_value_through_the_helper():
    src = GLOBAL_SETUP.read_text(encoding="utf-8")
    assert re.search(
        r"import\s*\{[^}]*\bdisplay\b[^}]*\}\s*from\s*'\./tests/e2e/fixtures/env_redaction'", src
    ), "globalSetup.ts must take display() from the shared helper"
    # No local copy of the key-name-only rule survives beside the import.
    assert "function display(" not in src
    assert "function isSecret(" not in src
    assert "SECRET_KEY_RE" not in src


def test_the_baseline_diff_uses_the_helpers_comparison():
    src = GLOBAL_SETUP.read_text(encoding="utf-8")
    body = _function_body(src, "function diffEnv(")
    assert "classifyBaselineValue(key, ciValue, value)" in body, (
        "a baseline carrying the MASKED local value must read `redacted`, not `differs`"
    )
    assert "ciValue === REDACTED" not in body


def test_the_snapshot_url_field_is_masked_too():
    src = GLOBAL_SETUP.read_text(encoding="utf-8")
    assert "dashboardUrl: maskEmbeddedCredentials(" in src


def test_the_behavioural_proof_exercises_a_url_password():
    src = SPEC.read_text(encoding="utf-8")
    assert "logEnvironmentDiagnostics(" in src, "the spec must write a real snapshot"
    assert "ICDEV_DATABASE_URL" in src
    assert "not.toContain(SENTINEL)" in src
# CUI // SP-CTI
