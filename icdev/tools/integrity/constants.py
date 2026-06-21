# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessment — Constants.

Single source of truth for every enum that drives a SQL CHECK constraint in
``tools/integrity/db/init_db.py``. Never hardcode these strings in SQL — always
reference the ``CHECK_*`` fragments derived at the bottom of this module so the
Python enums and the database stay in lock-step.

Enum domains:
  * CAPABILITY_TYPES  — what a piece of code can *do* (network, fs, exec, …)
  * SOURCE_TYPES      — where an artifact came from (local / git / unc / uri)
  * ASSESS_MODES      — provenance-aware vs. blind vs. auto-select
  * VERDICTS          — final disposition (allow / review / quarantine)
  * ASSESS_STATUS     — assessment lifecycle state
  * FINDING_SCANNERS  — which analyzer produced a finding
  * FINDING_TYPES     — finding taxonomy
  * SEVERITY          — finding severity scale

Plus RISK_WEIGHTS (per capability_type + per severity) and the
``ICDEV_INTEGRITY_ENABLED`` feature flag.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #
# Env var (truthy => SIPA assessment active). Engine/routes are no-ops when off.
FEATURE_FLAG = "ICDEV_INTEGRITY_ENABLED"

# --------------------------------------------------------------------------- #
# Capability taxonomy — what code can do
# --------------------------------------------------------------------------- #
CAPABILITY_TYPES = [
    "network_egress",   # outbound network / DNS / sockets
    "filesystem",       # file read/write/delete outside sandbox
    "process_exec",     # spawning subprocesses / shell-out
    "dynamic_code",     # eval/exec/compile, importlib, runtime codegen
    "crypto",           # cryptographic primitives / hashing / signing
    "env_secret",       # environment / secret material access
    "serialization",    # pickle/marshal/yaml.load and friends
    "obfuscation",      # base64/hex/zlib-packed or otherwise hidden logic
]

# --------------------------------------------------------------------------- #
# Source provenance — where an artifact was ingested from
# --------------------------------------------------------------------------- #
SOURCE_TYPES = [
    "local",   # local working tree / disk path
    "git",     # cloned git repository / ref
    "unc",     # UNC network share (\\host\share\...)
    "uri",     # remote URI (http/https/registry pull)
]

# --------------------------------------------------------------------------- #
# Assessment mode — provenance handling strategy
# --------------------------------------------------------------------------- #
ASSESS_MODES = [
    "provenance_aware",   # reconcile declared (disclosed) vs. exercised behavior
    "provenance_blind",   # judge exercised behavior only; no disclosure manifest
    "auto",               # engine selects aware vs. blind based on availability
]

# --------------------------------------------------------------------------- #
# Verdict — final disposition of an assessment
# --------------------------------------------------------------------------- #
VERDICTS = [
    "allow",        # cleared for use
    "review",       # human-in-the-loop review required
    "quarantine",   # blocked pending remediation
]

# --------------------------------------------------------------------------- #
# Assessment lifecycle status
# --------------------------------------------------------------------------- #
ASSESS_STATUS = [
    "quarantine",   # ingested, isolated, not yet cleared
    "assessed",     # scanners run, findings recorded
    "approved",     # reviewer approved
    "rejected",     # reviewer rejected
]

# --------------------------------------------------------------------------- #
# Finding scanners — which analyzer emitted a finding
# --------------------------------------------------------------------------- #
FINDING_SCANNERS = [
    "sast",            # static application security testing
    "secrets",         # secret / credential detection
    "deps",            # dependency / SCA vulnerability scan
    "formal",          # formal / property verification (SQLi / dangerous-pattern / input-validation)
    "container",       # container image / Dockerfile scan (trivy + dockerfile analyzer)
    "semgrep",         # semgrep rule matches
    "capability",      # capability extraction / exercise analysis
    "reconciliation",  # disclosed-vs-exercised reconciliation
    "tamper",          # tamper / integrity (hash/signature) checks
]

# --------------------------------------------------------------------------- #
# Finding taxonomy
# --------------------------------------------------------------------------- #
FINDING_TYPES = [
    "dangerous_api",            # use of a high-risk API
    "secret",                   # embedded secret / credential
    "vuln_dependency",          # known-vulnerable dependency
    "unauthorized_capability",  # capability exercised beyond policy allowance
    "undisclosed_capability",   # capability exercised but not disclosed
    "tamper_mismatch",          # hash / signature mismatch vs. expected
    "known_bad_signature",      # matches a known-bad / malware signature
]

# --------------------------------------------------------------------------- #
# Severity scale
# --------------------------------------------------------------------------- #
SEVERITY = [
    "critical",
    "high",
    "medium",
    "low",
    "info",
]

# --------------------------------------------------------------------------- #
# Risk weights — feed the weighted risk score that drives the verdict
# --------------------------------------------------------------------------- #
# Per-capability inherent risk (0.0–1.0). Higher == more dangerous to exercise.
RISK_WEIGHTS_CAPABILITY: dict[str, float] = {
    "network_egress": 0.90,
    "filesystem":     0.70,
    "process_exec":   0.95,
    "dynamic_code":   1.00,
    "crypto":         0.40,
    "env_secret":     0.85,
    "serialization":  0.80,
    "obfuscation":    0.90,
}

# Per-severity multiplier applied to a finding's contribution to the risk score.
RISK_WEIGHTS_SEVERITY: dict[str, float] = {
    "critical": 1.00,
    "high":     0.70,
    "medium":   0.40,
    "low":      0.20,
    "info":     0.05,
}

# Convenience aggregate so callers can grab both weight maps from one symbol.
RISK_WEIGHTS: dict[str, dict[str, float]] = {
    "capability": RISK_WEIGHTS_CAPABILITY,
    "severity":   RISK_WEIGHTS_SEVERITY,
}

# --------------------------------------------------------------------------- #
# SQL CHECK fragments — derived from the lists above so SQL and Python stay in
# sync. db/init_db.py consumes these directly, e.g.:
#     f"... CHECK ({CHECK_CAPABILITY_TYPES})"
# Each fragment is a ready-to-embed predicate of the form
#     <column> IN ('a', 'b', ...)
# --------------------------------------------------------------------------- #
_cap   = "', '".join(CAPABILITY_TYPES)
_src   = "', '".join(SOURCE_TYPES)
_mode  = "', '".join(ASSESS_MODES)
_vrd   = "', '".join(VERDICTS)
_astat = "', '".join(ASSESS_STATUS)
_scan  = "', '".join(FINDING_SCANNERS)
_ftype = "', '".join(FINDING_TYPES)
_sev   = "', '".join(SEVERITY)

CHECK_CAPABILITY_TYPES = f"capability_type IN ('{_cap}')"
CHECK_SOURCE_TYPES     = f"source_type IN ('{_src}')"
CHECK_ASSESS_MODES     = f"assess_mode IN ('{_mode}')"
CHECK_VERDICTS         = f"verdict IN ('{_vrd}')"
CHECK_ASSESS_STATUS    = f"status IN ('{_astat}')"
CHECK_FINDING_SCANNERS = f"scanner IN ('{_scan}')"
CHECK_FINDING_TYPES    = f"finding_type IN ('{_ftype}')"
CHECK_SEVERITY         = f"severity IN ('{_sev}')"
