# CUI // SP-CTI
"""tests/airgap/test_image_vendor — the air-gap container-image vendor (flx-airgap-01).

The fixtures here build a REAL OCI-layout tar — content-addressed blobs plus an
``index.json`` naming the manifest digest — rather than mocking ``verify_bundle``.
Mocking it would prove the function is deterministic, which was never in
question; the defect worth catching is a verifier that reports ``verified`` for
a tar it did not actually check.
"""
from __future__ import annotations

import ast
import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from tools.airgap import image_vendor as iv


# ---------------------------------------------------------------------------
# Fixtures: build genuine tars, so the verifier has something real to refuse
# ---------------------------------------------------------------------------


def _blob(payload: bytes) -> tuple[str, bytes]:
    return hashlib.sha256(payload).hexdigest(), payload


def _write_tar(path: Path, members: dict[str, bytes]) -> None:
    import io

    with tarfile.open(path, "w") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def make_oci_tar(path: Path, *, corrupt_blob: bool = False) -> str:
    """Write a minimal but VALID OCI-layout image tar; return its manifest digest."""
    config_hex, config = _blob(b'{"architecture":"amd64","os":"linux"}')
    layer_hex, layer = _blob(b"layer-bytes-for-test")
    manifest_doc = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": f"sha256:{config_hex}", "size": len(config)},
        "layers": [{"digest": f"sha256:{layer_hex}", "size": len(layer)}],
    }, sort_keys=True).encode()
    manifest_hex, manifest = _blob(manifest_doc)

    index = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{"digest": f"sha256:{manifest_hex}", "size": len(manifest)}],
    }).encode()

    stored_layer = b"TAMPERED-" + layer if corrupt_blob else layer
    _write_tar(path, {
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
        "index.json": index,
        f"blobs/sha256/{config_hex}": config,
        f"blobs/sha256/{layer_hex}": stored_layer,
        f"blobs/sha256/{manifest_hex}": manifest,
    })
    return f"sha256:{manifest_hex}"


def make_legacy_tar(path: Path) -> None:
    """A pre-OCI ``docker-v1`` tar: no index.json, so no recorded manifest digest."""
    _write_tar(path, {
        "manifest.json": json.dumps([{"Config": "c.json", "Layers": ["l/layer.tar"]}]).encode(),
        "c.json": b"{}",
    })


# ---------------------------------------------------------------------------
# A pin is a DIGEST, never a tag
# ---------------------------------------------------------------------------


DIGEST = "sha256:" + "ab" * 32
GOOD_PIN = f"floci/floci@{DIGEST}"


def test_parse_pin_accepts_a_digest_reference():
    pin = iv.parse_pin(GOOD_PIN)
    assert pin["repo"] == "floci/floci"
    assert pin["digest"] == DIGEST


@pytest.mark.parametrize("ref", [
    "floci/floci:2.0.1",                       # the mutable tag this exists to refuse
    "floci/floci",                             # bare repo
    "floci/floci@sha256:tooshort",             # malformed digest
    "floci/floci@md5:" + "ab" * 16,            # wrong algorithm
])
def test_parse_pin_refuses_anything_that_is_not_a_sha256_digest(ref):
    with pytest.raises(ValueError):
        iv.parse_pin(ref)


def test_parse_pin_tag_error_tells_the_operator_how_to_resolve_it():
    with pytest.raises(ValueError, match="RepoDigests"):
        iv.parse_pin("floci/floci:2.0.1")


def test_pin_with_registry_host_and_port_is_accepted():
    pin = iv.parse_pin(f"registry.internal:5000/floci/floci@{DIGEST}")
    assert pin["repo"] == "registry.internal:5000/floci/floci"


def test_read_pins_skips_comments_and_blanks(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path)
    (tmp_path / "images-floci.txt").write_text(
        f"# a comment\n\n{GOOD_PIN}   # trailing comment\n", encoding="utf-8"
    )
    pins = iv.read_pins("floci")
    assert [p["ref"] for p in pins] == [GOOD_PIN]


def test_read_pins_fails_the_whole_file_on_one_tag_line(tmp_path, monkeypatch):
    """A partial bundle whose gaps nobody notices is worse than a refusal."""
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path)
    (tmp_path / "images-floci.txt").write_text(
        f"{GOOD_PIN}\nfloci/floci:2.0.1\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="images-floci.txt:2"):
        iv.read_pins("floci")


def test_read_pins_missing_file_names_the_path(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="images-nope.txt"):
        iv.read_pins("nope")


def test_tar_name_keeps_two_digests_of_one_repo_apart():
    a = iv.tar_name_for(iv.parse_pin(f"floci/floci@{'sha256:' + 'ab' * 32}"))
    b = iv.tar_name_for(iv.parse_pin(f"floci/floci@{'sha256:' + 'cd' * 32}"))
    assert a != b
    assert "/" not in a


# ---------------------------------------------------------------------------
# verify_bundle — the proof that does not need a daemon
# ---------------------------------------------------------------------------


def test_verify_bundle_verifies_a_real_oci_tar_against_its_pin(tmp_path):
    tar = tmp_path / "img.tar"
    digest = make_oci_tar(tar)
    result = iv.verify_bundle(tar, digest)
    assert result["status"] == iv.STATUS_VERIFIED
    assert result["ok"] is True
    assert result["layout"] == "oci"
    assert result["blobs_checked"] == 3
    assert result["blobs_corrupt"] == []
    assert result["manifest_digest_verified"] is True


def test_verify_bundle_catches_a_single_altered_byte(tmp_path):
    """Content addressing is the whole point — a tampered layer must not pass."""
    tar = tmp_path / "img.tar"
    digest = make_oci_tar(tar, corrupt_blob=True)
    result = iv.verify_bundle(tar, digest)
    assert result["status"] == iv.STATUS_FAILED
    assert result["ok"] is False
    assert len(result["blobs_corrupt"]) == 1


def test_verify_bundle_refuses_a_tar_holding_a_different_image(tmp_path):
    tar = tmp_path / "img.tar"
    make_oci_tar(tar)
    result = iv.verify_bundle(tar, "sha256:" + "99" * 32)
    assert result["status"] == iv.STATUS_FAILED
    assert result["manifest_digest_verified"] is False


def test_legacy_layout_is_unmeasured_and_never_verified(tmp_path):
    """A tar that cannot record the pin must not report that it matched one."""
    tar = tmp_path / "legacy.tar"
    make_legacy_tar(tar)
    result = iv.verify_bundle(tar, DIGEST)
    assert result["layout"] == "docker-v1"
    assert result["manifest_digest_verified"] is None
    assert result["status"] == iv.STATUS_UNMEASURED
    assert result["ok"] is False
    assert "docker-v1" in result["manifest_digest_reason"]


def test_missing_tar_is_unmeasured_not_verified(tmp_path):
    result = iv.verify_bundle(tmp_path / "absent.tar", DIGEST)
    assert result["exists"] is False
    assert result["status"] == iv.STATUS_UNMEASURED
    assert result["ok"] is False


def test_blobless_tar_proves_nothing(tmp_path):
    """An existence check is not a verification."""
    tar = tmp_path / "empty.tar"
    _write_tar(tar, {"index.json": json.dumps({"manifests": []}).encode()})
    result = iv.verify_bundle(tar, DIGEST)
    assert result["blobs_checked"] == 0
    assert result["status"] != iv.STATUS_VERIFIED


def test_unreadable_tar_is_failed_not_verified(tmp_path):
    bad = tmp_path / "notatar.tar"
    bad.write_bytes(b"this is not a tar archive at all")
    result = iv.verify_bundle(bad, DIGEST)
    assert result["status"] == iv.STATUS_FAILED


# ---------------------------------------------------------------------------
# It never pulls — asserted structurally, not by convention
# ---------------------------------------------------------------------------


def test_pull_is_not_an_allowed_docker_command():
    for forbidden in ("pull", "run", "rmi", "tag", "push"):
        assert forbidden not in iv.ALLOWED_DOCKER_COMMANDS


def test_docker_helper_refuses_a_non_allowlisted_subcommand():
    with pytest.raises(RuntimeError, match="never pulls"):
        iv._docker(["pull", "floci/floci:2.0.1"])


def test_subprocess_is_reached_only_from_the_one_allowlisted_door():
    """A second subprocess call site would step around the allowlist entirely.

    Read from the AST rather than by behaviour: the failure mode is a future
    edit adding a `subprocess.run(["docker", "pull", ...])` somewhere else,
    which every behavioural test in this file would still pass.
    """
    source = Path(iv.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            ) and node.name != "_docker":
                offenders.append(f"{node.name}:{inner.lineno}")

    assert offenders == [], f"subprocess reached outside _docker(): {offenders}"


# ---------------------------------------------------------------------------
# No docker -> unmeasured. Never a clean bundle.
# ---------------------------------------------------------------------------


@pytest.fixture
def no_docker(monkeypatch):
    monkeypatch.setattr(
        iv, "docker_basis",
        lambda: {"available": False, "reason": "docker CLI not found on PATH"},
    )


def test_save_on_a_host_with_no_docker_reports_unmeasured(tmp_path, monkeypatch, no_docker):
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path)
    (tmp_path / "images-floci.txt").write_text(GOOD_PIN + "\n", encoding="utf-8")

    out = iv.save("floci")
    assert out["status"] == iv.STATUS_UNMEASURED
    assert out["ok"] is False
    assert out["saved"] == []
    assert "NOT" in out["note"]


def test_load_on_a_host_with_no_docker_reports_unmeasured(tmp_path, monkeypatch, no_docker):
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path)
    bucket = tmp_path / "floci"
    bucket.mkdir()
    tar = bucket / "img.tar"
    digest = make_oci_tar(tar)
    (bucket / "MANIFEST.json").write_text(json.dumps({"images": [
        {"ref": GOOD_PIN, "digest": digest, "tar_name": "img.tar",
         "tar_sha256": hashlib.sha256(tar.read_bytes()).hexdigest()}
    ]}), encoding="utf-8")

    out = iv.load("floci")
    assert out["status"] == iv.STATUS_UNMEASURED
    assert out["ok"] is False
    assert out["loaded"] == []


def test_verify_still_proves_the_tars_with_no_daemon(tmp_path, monkeypatch, no_docker):
    """The tar proof is authoritative; the daemon probe only corroborates."""
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path)
    bucket = tmp_path / "floci"
    bucket.mkdir()
    tar = bucket / "img.tar"
    digest = make_oci_tar(tar)
    (bucket / "MANIFEST.json").write_text(json.dumps({"images": [
        {"ref": GOOD_PIN, "digest": digest, "tar_name": "img.tar",
         "tar_sha256": hashlib.sha256(tar.read_bytes()).hexdigest()}
    ]}), encoding="utf-8")

    out = iv.verify("floci")
    image = out["images"][0]
    assert image["bundle"]["status"] == iv.STATUS_VERIFIED
    assert image["tar_sha256_matches_manifest"] is True
    # Unknown, never False: nobody asked a daemon.
    assert image["in_local_daemon"] is None


def test_verify_missing_bucket_is_unmeasured(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path)
    out = iv.verify("nothing-here")
    assert out["status"] == iv.STATUS_UNMEASURED
    assert out["ok"] is False


def test_verify_detects_a_tar_swapped_since_it_was_vendored(tmp_path, monkeypatch, no_docker):
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path)
    bucket = tmp_path / "floci"
    bucket.mkdir()
    tar = bucket / "img.tar"
    digest = make_oci_tar(tar)
    (bucket / "MANIFEST.json").write_text(json.dumps({"images": [
        {"ref": GOOD_PIN, "digest": digest, "tar_name": "img.tar",
         "tar_sha256": "0" * 64}          # recorded hash no longer matches
    ]}), encoding="utf-8")

    out = iv.verify("floci")
    assert out["images"][0]["tar_sha256_matches_manifest"] is False
    assert out["status"] == iv.STATUS_FAILED


# ---------------------------------------------------------------------------
# A pin absent from the LOCAL cache fails the run — nothing is fetched
# ---------------------------------------------------------------------------


def test_save_refuses_when_the_pin_is_absent_from_the_local_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path)
    (tmp_path / "images-floci.txt").write_text(GOOD_PIN + "\n", encoding="utf-8")
    monkeypatch.setattr(iv, "docker_basis", lambda: {"available": True, "server_version": "28.5.1"})
    monkeypatch.setattr(
        iv, "local_image_present",
        lambda ref: {"present": False, "reason": "No such image"},
    )

    def _no_save(args, **kwargs):
        raise AssertionError(f"must not shell out for an absent pin: {args}")

    monkeypatch.setattr(iv, "_docker", _no_save)

    out = iv.save("floci")
    assert out["status"] == iv.STATUS_FAILED
    assert out["ok"] is False
    assert out["absent_from_local_cache"][0]["ref"] == GOOD_PIN
    assert out["saved"] == []


def test_list_bundles_reports_an_empty_root_without_inventing_content(tmp_path, monkeypatch):
    monkeypatch.setattr(iv, "IMAGES_DIR", tmp_path / "does-not-exist")
    out = iv.list_bundles()
    assert out["topics"] == []
    assert out["buckets"] == []
