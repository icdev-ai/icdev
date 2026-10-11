# CUI // SP-CTI
"""omx-vllm-05: the vLLM serving recipe -- compose service, air-gap bundle, docs.

No GPU, no docker daemon, no network and no running vLLM: the compose file is
read as YAML, the pin census is called as a library, and the weights bundle is
exercised against a FAKE Hugging Face cache in tmp_path.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "docker-compose.yml"
PIN_FILE = REPO / "vendor" / "images" / "images-vllm.txt"
DOC = REPO / "docs" / "install" / "vllm.md"
OFFLINE_README = REPO / "deploy" / "offline" / "README.md"
BUNDLE_SCRIPT = REPO / "deploy" / "offline" / "vllm_bundle.py"

_DIGEST_RE = re.compile(r"@(sha256:[0-9a-f]{64})")


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _default_image(ref: str) -> str:
    """``${VLLM_IMAGE:-<default>}`` -> ``<default>``; a literal ref passes through."""
    match = re.fullmatch(r"\$\{VLLM_IMAGE:-(.+)\}", ref.strip())
    return match.group(1) if match else ref.strip()


def _pin_digest() -> str:
    lines = [
        ln.strip() for ln in PIN_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert len(lines) == 1, lines
    return _DIGEST_RE.search(lines[0]).group(1)


def _load_bundle_module():
    spec = importlib.util.spec_from_file_location("omx_vllm_bundle", BUNDLE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── compose ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name, profile", [("vllm", "vllm"), ("vllm-embed", "vllm-embed")])
def test_vllm_services_are_opt_in_behind_their_own_profile(name, profile):
    svc = _services()[name]
    assert svc["profiles"] == [profile]
    assert "build" not in svc, "a pulled image, not built here"


@pytest.mark.parametrize("name", ["vllm", "vllm-embed"])
def test_default_image_is_pinned_by_the_vendored_digest(name):
    ref = _default_image(_services()[name]["image"])
    match = _DIGEST_RE.search(ref)
    assert match, f"{name} default image is not digest-pinned: {ref}"
    assert ref.startswith("vllm/vllm-openai")
    assert match.group(1) == _pin_digest(), "compose and vendor/images pin disagree"


def test_pin_census_sees_no_unpinned_vllm_site():
    from tools.ci import pin_census

    sites, problems = pin_census.scan_compose(
        "docker-compose.yml", COMPOSE.read_text(encoding="utf-8"), digested=[]
    )
    assert problems == []
    assert not [s for s in sites if "vllm" in s["subject"]], sites


def test_vllm_requests_a_gpu_and_health_probes_v1_models():
    for name in ("vllm", "vllm-embed"):
        svc = _services()[name]
        devices = svc["deploy"]["resources"]["reservations"]["devices"]
        assert devices[0]["driver"] == "nvidia" and "gpu" in devices[0]["capabilities"]
        assert "/v1/models" in " ".join(svc["healthcheck"]["test"])


def test_default_is_the_low_vram_profile_with_tool_calling():
    cmd = _services()["vllm"]["command"]
    joined = " ".join(cmd)
    assert "--enforce-eager" in cmd
    assert "--enable-auto-tool-choice" in cmd
    assert cmd[cmd.index("--tool-call-parser") + 1] == "${VLLM_TOOL_CALL_PARSER:-hermes}"
    assert "${VLLM_GPU_MEMORY_UTILIZATION:-0.80}" in joined
    assert "${VLLM_MAX_MODEL_LEN:-4096}" in joined
    assert "AWQ" in cmd[cmd.index("--model") + 1]


def test_embed_uses_the_v031_pooling_flag_not_the_removed_task_flag():
    cmd = _services()["vllm-embed"]["command"]
    assert cmd[cmd.index("--runner") + 1] == "pooling"
    assert "--task" not in cmd


def test_ports_are_loopback_by_default():
    for name in ("vllm", "vllm-embed"):
        for port in _services()[name]["ports"]:
            assert port.startswith("${VLLM_BIND_ADDR:-127.0.0.1}:"), port


# ── docs + offline bundle ──────────────────────────────────────────────────


def test_install_doc_covers_arch_wsl_cpu_and_air_gap():
    text = DOC.read_text(encoding="utf-8")
    for needle in ("nvidia-container-toolkit", "WSL2", "CPU-only", "Air gap",
                   "VLLM_BASE_URL", "VLLM_MODEL", "tool-call parser", "max_model_len",
                   _pin_digest().split(":")[1][:8]):
        assert needle in text, needle


def test_offline_readme_carries_the_vllm_image_step():
    text = OFFLINE_README.read_text(encoding="utf-8")
    assert "vllm_bundle.py save" in text
    assert _pin_digest() in text


def _fake_hf_cache(root: Path, model: str) -> None:
    repo = root / "hub" / ("models--" + model.replace("/", "--"))
    (repo / "blobs").mkdir(parents=True)
    (repo / "blobs" / "abc").write_text("weights", encoding="utf-8")
    snap = repo / "snapshots" / "rev1"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}", encoding="utf-8")


def test_weights_bundle_round_trips_and_refuses_tampering(tmp_path):
    mod = _load_bundle_module()
    model = "Org/Tiny-AWQ"
    src, bundle, dest = tmp_path / "src", tmp_path / "bundle", tmp_path / "dest"
    _fake_hf_cache(src, model)

    saved = mod.save_weights(model, src, bundle)
    assert saved["ok"] and saved["saved"]["revisions"] == ["rev1"]
    assert mod.verify_weights(bundle)["ok"]

    loaded = mod.load_weights(bundle, dest)
    assert loaded["ok"]
    assert (dest / "hub" / "models--Org--Tiny-AWQ" / "snapshots" / "rev1" / "config.json").is_file()

    tar = bundle / saved["saved"]["tar_name"]
    tar.write_bytes(tar.read_bytes() + b"tamper")
    assert mod.verify_weights(bundle)["status"] == "failed"
    assert mod.load_weights(bundle, tmp_path / "dest2")["ok"] is False
    assert not (tmp_path / "dest2").exists(), "nothing extracted from a bad bundle"


def test_save_reports_a_model_absent_from_cache_and_fetches_nothing(tmp_path):
    mod = _load_bundle_module()
    out = mod.save_weights("Org/Missing", tmp_path / "empty", tmp_path / "bundle")
    assert out["ok"] is False and out["reason"] == "absent_from_local_cache"


def test_load_refuses_a_member_outside_the_cache(tmp_path):
    mod = _load_bundle_module()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    tar_path = bundle / "evil.tar"
    with tarfile.open(tar_path, "w") as tar:
        info = tarfile.TarInfo("hub/../../escape.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    (bundle / mod.MANIFEST_NAME).write_text(json.dumps({"models": [{
        "model": "Org/Evil", "revisions": [], "tar_name": "evil.tar",
        "tar_sha256": mod._sha256_file(tar_path)}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="outside the cache"):
        mod.load_weights(bundle, tmp_path / "dest")
    assert not (tmp_path / "escape.txt").exists()
