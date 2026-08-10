# CUI // SP-CTI
"""AGOV CASE (agov-case-03) — bundle verification names WHICH records failed.

These tests build real bundles on disk and tamper with exactly one thing at a
time. No database is touched: a case bundle must verify on a machine that never
had the source database, which is the whole point of a portable bundle.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from tools.agent_case import bundle_format as bf
from tools.agent_case import bundle_verifier as bv

REAL_SECRET = "operator-set-secret-not-the-default"
OTHER_SECRET = "rotated-since-capture"

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Fixture bundle
# ---------------------------------------------------------------------------

def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=2, sort_keys=True))


def _events(secret=REAL_SECRET):
    raw = [
        (1, "pre_tool_use", "Read", {"file_path": "/repo/.env"}),
        (2, "pre_tool_use", "Bash", {"command": "curl https://example.test"}),
        (3, "post_tool_use", "Bash", {"exit_code": 0}),
    ]
    records = []
    for event_id, hook_type, tool, payload in raw:
        payload_str = json.dumps(payload)
        records.append({
            "id": event_id,
            "session_id": "sess-agov-case-03",
            "hook_type": hook_type,
            "tool_name": tool,
            "payload": payload_str,
            "classification": "CUI",
            "signature": bf.compute_event_hmac(payload_str, secret),
            "created_at": f"2026-08-09T20:0{event_id}:00+00:00",
        })
    return {"table": "hook_events", "records": records}


def _audit_rows():
    base = [
        {"id": 1, "project_id": "proj-agov", "event_type": "agent_action", "actor": "agent",
         "action": "read_env", "details": "read .env", "classification": "CUI",
         "ip_address": "127.0.0.1", "session_id": "sess-agov-case-03"},
        {"id": 2, "project_id": "proj-agov", "event_type": "agent_action", "actor": "agent",
         "action": "egress_attempt", "details": "POST example.test", "classification": "CUI",
         "ip_address": "127.0.0.1", "session_id": "sess-agov-case-03"},
        {"id": 3, "project_id": "proj-agov", "event_type": "gate_decision", "actor": "approval_gate",
         "action": "deny", "details": "unattended", "classification": "CUI",
         "ip_address": "127.0.0.1", "session_id": "sess-agov-case-03"},
        {"id": 4, "project_id": "proj-agov", "event_type": "session_end", "actor": "agent",
         "action": "stop", "details": "", "classification": "CUI",
         "ip_address": "127.0.0.1", "session_id": "sess-agov-case-03"},
    ]
    prev = bf.GENESIS_HASH
    for row in base:
        row["previous_hash"] = prev
        row["hash"] = bf.compute_audit_row_hash(row)
        prev = row["hash"]
    return {"table": "audit_trail", "records": base}


def make_bundle(tmp_path, secret=REAL_SECRET, name="bundle") -> Path:
    bundle = tmp_path / name
    _write_json(bundle / bf.HOOK_EVENTS_MEMBER, _events(secret))
    _write_json(bundle / bf.AUDIT_TRAIL_MEMBER, _audit_rows())
    _write_json(bundle / "timeline.json", {"session_id": "sess-agov-case-03", "steps": 7})
    bf.write_manifest(bundle, bf.build_manifest(bundle, bundle_id="case-0001",
                                                session_id="sess-agov-case-03",
                                                created_at="2026-08-09T20:10:00+00:00"))
    return bundle


def reseal(bundle: Path) -> None:
    """Recompute the manifest — an attacker who also re-sealed the bundle."""
    bf.write_manifest(bundle, bf.build_manifest(bundle, bundle_id="case-0001"))


def _mutate_member(bundle: Path, member: str, fn):
    path = bundle / member
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    _write_json(path, data)


def _env(secret=REAL_SECRET):
    return {bf.HMAC_SECRET_ENV: secret} if secret else {}


# ---------------------------------------------------------------------------
# Clean bundle
# ---------------------------------------------------------------------------

def test_clean_bundle_passes_every_layer(tmp_path):
    report = bv.verify_bundle(make_bundle(tmp_path), env=_env())
    assert report["status"] == bv.PASSED, report["findings"]
    assert report["exit_code"] == 0
    for layer in ("manifest", "hmac", "chain"):
        assert report["layers"][layer]["status"] == bv.PASSED
        assert report["layers"][layer]["failed"] == 0
    assert report["layers"]["hmac"]["verified"] == 3
    assert report["layers"]["chain"]["verified"] == 4


def test_default_secret_caveat_is_always_reported(tmp_path):
    report = bv.verify_bundle(make_bundle(tmp_path), env=_env())
    assert any(bf.DEFAULT_HOOK_HMAC_SECRET in c for c in report["caveats"])


# ---------------------------------------------------------------------------
# Layer 1 — manifest
# ---------------------------------------------------------------------------

def test_tampered_member_is_reported_by_path_with_expected_and_actual_digest(tmp_path):
    bundle = make_bundle(tmp_path)
    sealed = {m["path"]: m["sha256"] for m in bf.read_manifest(bundle)["members"]}

    _mutate_member(bundle, "timeline.json", lambda d: d.update({"steps": 999}))
    actual_digest = bf.sha256_file(bundle / "timeline.json")

    layer = bv.verify_manifest_layer(bundle)
    assert layer["status"] == bv.FAILED
    hits = [f for f in layer["findings"] if f["kind"] == "member_digest_mismatch"]
    assert len(hits) == 1
    assert hits[0]["member"] == "timeline.json"
    assert hits[0]["expected"] == sealed["timeline.json"]
    assert hits[0]["actual"] == actual_digest
    assert hits[0]["expected"] != hits[0]["actual"]
    # the untouched members are still counted as verified, not swept up
    assert layer["verified"] == len(sealed) - 1


def test_missing_and_unmanifested_members_are_named(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "timeline.json").unlink()
    _write_json(bundle / "records" / "smuggled.json", {"planted": True})

    layer = bv.verify_manifest_layer(bundle)
    assert layer["status"] == bv.FAILED
    missing = [f for f in layer["findings"] if f["kind"] == "member_missing"]
    extra = [f for f in layer["findings"] if f["kind"] == "unmanifested_file"]
    assert [f["member"] for f in missing] == ["timeline.json"]
    assert [f["member"] for f in extra] == ["records/smuggled.json"]


def test_missing_manifest_fails_the_layer(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / bf.MANIFEST_NAME).unlink()
    layer = bv.verify_manifest_layer(bundle)
    assert layer["status"] == bv.FAILED
    assert [f["kind"] for f in layer["findings"]] == ["manifest_missing"]


@pytest.mark.parametrize("evil", ["../../etc/passwd", "/etc/passwd", "C:\\Windows\\win.ini",
                                  "records/../../escape.json"])
def test_manifest_member_path_traversal_is_refused_not_followed(tmp_path, evil):
    bundle = make_bundle(tmp_path)
    manifest = bf.read_manifest(bundle)
    manifest["members"].append({"path": evil, "sha256": "0" * 64, "bytes": 1})
    bf.write_manifest(bundle, manifest)

    layer = bv.verify_manifest_layer(bundle)
    unsafe = [f for f in layer["findings"] if f["kind"] == "unsafe_member_path"]
    assert [f["member"] for f in unsafe] == [evil]
    assert layer["status"] == bv.FAILED


def test_unsupported_digest_algorithm_is_not_verified_not_passed(tmp_path):
    bundle = make_bundle(tmp_path)
    manifest = bf.read_manifest(bundle)
    manifest["algorithm"] = "md5"
    bf.write_manifest(bundle, manifest)

    layer = bv.verify_manifest_layer(bundle)
    assert layer["status"] == bv.NOT_VERIFIED
    assert layer["complete"] is False


# ---------------------------------------------------------------------------
# Layer 2 — per-event HMAC
# ---------------------------------------------------------------------------

def test_tampered_event_payload_is_reported_by_id_while_others_pass(tmp_path):
    bundle = make_bundle(tmp_path)

    def tamper(data):
        # rewrite the command but keep the captured signature
        data["records"][1]["payload"] = json.dumps({"command": "curl https://evil.test"})

    _mutate_member(bundle, bf.HOOK_EVENTS_MEMBER, tamper)
    reseal(bundle)  # attacker re-sealed the manifest; only the HMAC layer can see it

    report = bv.verify_bundle(bundle, env=_env())
    assert report["layers"]["manifest"]["status"] == bv.PASSED
    hmac_layer = report["layers"]["hmac"]
    assert hmac_layer["status"] == bv.FAILED
    assert hmac_layer["failed"] == 1
    assert hmac_layer["verified"] == 2          # the other two events still pass

    hit = [f for f in hmac_layer["findings"] if f["kind"] == "hmac_mismatch"]
    assert len(hit) == 1
    assert hit[0]["event_id"] == 2
    assert hit[0]["tool_name"] == "Bash"
    assert hit[0]["expected"] != hit[0]["actual"]
    assert report["exit_code"] == 1


def test_no_configured_secret_reports_not_verified_never_passed(tmp_path):
    bundle = make_bundle(tmp_path)
    report = bv.verify_bundle(bundle, env={})

    layer = report["layers"]["hmac"]
    assert layer["status"] == bv.NOT_VERIFIED
    assert layer["status"] != bv.PASSED
    assert layer["verified"] == 0
    assert layer["failed"] == 0
    assert layer["not_verified"] == 3
    assert bf.HMAC_SECRET_ENV in layer["reason"]
    assert {f["event_id"] for f in layer["findings"]} == {1, 2, 3}
    assert report["exit_code"] == 2


def test_verifier_never_falls_back_to_the_shipped_default_secret(tmp_path):
    """A bundle signed with the shipped default must NOT report as verified."""
    bundle = make_bundle(tmp_path, secret=bf.DEFAULT_HOOK_HMAC_SECRET)

    # (a) secret absent — no silent fallback the way send_event.py does when writing
    assert bv.verify_hmac_layer(bundle, env={})["status"] == bv.NOT_VERIFIED

    # (b) secret present but IS the public default — mathematically it matches,
    #     and it is still not evidence of anything
    layer = bv.verify_hmac_layer(bundle, env=_env(bf.DEFAULT_HOOK_HMAC_SECRET))
    assert layer["status"] == bv.NOT_VERIFIED
    assert layer["verified"] == 0
    assert "public" in layer["reason"]


def test_all_signatures_mismatching_is_not_verified_rather_than_failed(tmp_path):
    """A rotated secret is indistinguishable from wholesale tampering — say so."""
    bundle = make_bundle(tmp_path)
    layer = bv.verify_hmac_layer(bundle, env=_env(OTHER_SECRET))

    assert layer["status"] == bv.NOT_VERIFIED
    assert layer["failed"] == 0
    assert "rotated" in layer["reason"]
    # the records are still named — indeterminate is not the same as silent
    assert {f["event_id"] for f in layer["findings"]} == {1, 2, 3}


def test_unsigned_event_is_not_verified_and_named(tmp_path):
    bundle = make_bundle(tmp_path)
    _mutate_member(bundle, bf.HOOK_EVENTS_MEMBER,
                   lambda d: d["records"][0].update({"signature": None}))
    reseal(bundle)

    layer = bv.verify_hmac_layer(bundle, env=_env())
    assert layer["status"] == bv.PASSED       # the other two verified
    assert layer["complete"] is False
    unsigned = [f for f in layer["findings"] if f["kind"] == "event_unsigned"]
    assert [f["event_id"] for f in unsigned] == [1]


def test_hmac_recipe_matches_send_event_hook_exactly():
    """Pins the duplicated recipe to .claude/hooks/send_event.py::compute_hmac."""
    hook_path = REPO_ROOT / ".claude" / "hooks" / "send_event.py"
    spec = importlib.util.spec_from_file_location("agov_send_event_probe", hook_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.compute_hmac("", "s") == bf.compute_event_hmac("", "s")
    assert module.compute_hmac('{"a": 1}', REAL_SECRET) == bf.compute_event_hmac('{"a": 1}', REAL_SECRET)
    # the writer signs "" when payload is NULL; the verifier must agree
    assert bf.compute_event_hmac(None, REAL_SECRET) == module.compute_hmac("", REAL_SECRET)


def test_default_secret_literal_matches_the_hook():
    """If send_event.py ever changes its fallback literal, this fails loudly."""
    source = (REPO_ROOT / ".claude" / "hooks" / "send_event.py").read_text(encoding="utf-8")
    assert f'"{bf.DEFAULT_HOOK_HMAC_SECRET}"' in source or \
           f"'{bf.DEFAULT_HOOK_HMAC_SECRET}'" in source


# ---------------------------------------------------------------------------
# Layer 3 — audit hash chain (migration 149)
# ---------------------------------------------------------------------------

def test_broken_chain_link_is_reported_by_row(tmp_path):
    bundle = make_bundle(tmp_path)

    def tamper(data):
        data["records"][2]["previous_hash"] = "f" * 64

    _mutate_member(bundle, bf.AUDIT_TRAIL_MEMBER, tamper)
    reseal(bundle)

    layer = bv.verify_chain_layer(bundle)
    assert layer["status"] == bv.FAILED
    broken = [f for f in layer["findings"] if f["kind"] == "chain_link_broken"]
    assert len(broken) == 1
    assert broken[0]["row_id"] == 3
    assert broken[0]["actual"] == "f" * 64
    assert broken[0]["expected"] != broken[0]["actual"]
    assert layer["verified"] == 4   # all four row hashes still recompute


def test_edited_audit_row_is_reported_by_row_with_both_digests(tmp_path):
    bundle = make_bundle(tmp_path)

    def tamper(data):
        data["records"][1]["action"] = "egress_allowed"   # rewrite history

    _mutate_member(bundle, bf.AUDIT_TRAIL_MEMBER, tamper)
    reseal(bundle)

    layer = bv.verify_chain_layer(bundle)
    assert layer["status"] == bv.FAILED
    mismatch = [f for f in layer["findings"] if f["kind"] == "row_hash_mismatch"]
    assert [f["row_id"] for f in mismatch] == [2]
    assert mismatch[0]["expected"] != mismatch[0]["actual"]
    assert mismatch[0]["action"] == "egress_allowed"
    # rows 1, 3 and 4 still hash correctly — the report is per-record
    assert layer["verified"] == 3


def test_unpopulated_hash_chain_is_not_verified_not_passed(tmp_path):
    """The real-world case: migration 149 added the columns, nothing writes them."""
    bundle = make_bundle(tmp_path)

    def strip(data):
        for row in data["records"]:
            row["hash"] = None
            row["previous_hash"] = None

    _mutate_member(bundle, bf.AUDIT_TRAIL_MEMBER, strip)
    reseal(bundle)

    layer = bv.verify_chain_layer(bundle)
    assert layer["status"] == bv.NOT_VERIFIED
    assert layer["failed"] == 0
    assert layer["verified"] == 0
    assert "never populated" in layer["reason"]
    assert any("migration 149" in c for c in layer["caveats"])
    assert {f["row_id"] for f in layer["findings"] if f["kind"] == "row_hash_absent"} == {1, 2, 3, 4}


def test_predecessor_outside_the_slice_is_not_verifiable_unless_anchored(tmp_path):
    bundle = make_bundle(tmp_path)

    def slice_off_head(data):
        data["records"] = data["records"][2:]   # keep rows 3 and 4 only

    _mutate_member(bundle, bf.AUDIT_TRAIL_MEMBER, slice_off_head)
    reseal(bundle)

    layer = bv.verify_chain_layer(bundle)
    gaps = [f for f in layer["findings"] if f["kind"] == "chain_link_not_verifiable"]
    assert [f["row_id"] for f in gaps] == [3]
    assert "outside this bundle slice" in gaps[0]["detail"]
    assert layer["complete"] is False

    # ...and an anchor closes the gap
    row2_hash = _audit_rows()["records"][1]["hash"]
    _mutate_member(bundle, bf.AUDIT_TRAIL_MEMBER,
                   lambda d: d.update({"chain_context": {"2": row2_hash}}))
    reseal(bundle)
    layer = bv.verify_chain_layer(bundle)
    assert [f["kind"] for f in layer["findings"]] == []
    assert layer["status"] == bv.PASSED


# ---------------------------------------------------------------------------
# CLI / exit codes
# ---------------------------------------------------------------------------

def test_cli_exit_code_is_nonzero_when_a_layer_fails(tmp_path, monkeypatch, capsys):
    bundle = make_bundle(tmp_path)
    monkeypatch.setenv(bf.HMAC_SECRET_ENV, REAL_SECRET)
    _mutate_member(bundle, "timeline.json", lambda d: d.update({"steps": 0}))

    code = bv.main(["--bundle", str(bundle), "--json"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == bv.FAILED
    assert payload["layers"]["manifest"]["findings"][0]["member"] == "timeline.json"


def test_cli_exit_code_zero_on_a_clean_bundle(tmp_path, monkeypatch, capsys):
    bundle = make_bundle(tmp_path)
    monkeypatch.setenv(bf.HMAC_SECRET_ENV, REAL_SECRET)
    assert bv.main(["--bundle", str(bundle)]) == 0
    out = capsys.readouterr().out
    assert "OVERALL: PASSED" in out


def test_cli_exit_code_two_when_only_indeterminate(tmp_path, monkeypatch, capsys):
    bundle = make_bundle(tmp_path)
    monkeypatch.delenv(bf.HMAC_SECRET_ENV, raising=False)
    code = bv.main(["--bundle", str(bundle)])
    assert code == 2
    out = capsys.readouterr().out
    assert "NOT_VERIFIED" in out and "OVERALL: INCOMPLETE" in out


def test_cli_reports_a_missing_bundle_rather_than_crashing(tmp_path, capsys):
    code = bv.main(["--bundle", str(tmp_path / "nope"), "--json"])
    assert code == 3
    assert json.loads(capsys.readouterr().out)["status"] == bv.FAILED


def test_single_layer_selection(tmp_path):
    bundle = make_bundle(tmp_path)
    report = bv.verify_bundle(bundle, layers=["manifest"], env=_env())
    assert set(report["layers"]) == {"manifest"}
    assert report["exit_code"] == 0


def test_human_output_names_every_failing_record(tmp_path):
    bundle = make_bundle(tmp_path)
    _mutate_member(bundle, bf.HOOK_EVENTS_MEMBER,
                   lambda d: d["records"][0].update({"payload": '{"file_path": "/repo/secrets"}'}))
    _mutate_member(bundle, bf.AUDIT_TRAIL_MEMBER,
                   lambda d: d["records"][3].update({"previous_hash": "0" * 64}))
    reseal(bundle)

    text = bv.format_report(bv.verify_bundle(bundle, env=_env()))
    assert "event 1" in text
    assert "audit row 4" in text
    assert "hmac_mismatch" in text and "chain_link_broken" in text
