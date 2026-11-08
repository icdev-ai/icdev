# CUI // SP-CTI
"""icx-doc-01 -- AI-Ify's model choices live in config, and the capability sheet claims only what is built.

* no model ID is written in AI-Ify or AI-Augmentation code; the paradigm -> recommended model map and the NLP
  model come from args/aiify_config.yaml / args/aac_config.yaml, and a paradigm the config leaves out gets no
  recommendation rather than a default from code;
* every routing function these canvases (and Process-Ify) invoke on their own behalf is defined in llm_config;
* the capability sheet no longer claims process mining or bottleneck detection, and the wave-planning template
  names only tools that exist.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from tools.aiify import model_choice

ROOT = Path(model_choice.__file__).resolve().parents[2]
MODEL_ID = re.compile(r"\bclaude-(?:haiku|sonnet|opus)-\d|\bgpt-4o-\d{4}|\bgemini-\d\.\d-(?:pro|flash)-\d")


def test_no_model_id_is_written_in_aiify_or_ai_augmentation_code():
    hits = []
    for pkg in ("tools/aiify", "tools/ai_augmentation"):
        for p in (ROOT / pkg).rglob("*.py"):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if MODEL_ID.search(line):
                    hits.append(f"{p.relative_to(ROOT)}:{i}")
    assert hits == []


def test_recommended_models_come_from_config_and_absence_is_none(tmp_path):
    for cfg in (model_choice.AIIFY_CONFIG, model_choice.AAC_CONFIG):
        m = model_choice.paradigm_models(cfg)
        assert {"ml_classifier", "llm_generation", "decision_agent"} <= set(m) and model_choice.nlp_model(cfg)
    empty = tmp_path / "c.yaml"
    empty.write_text("other: 1\n", encoding="utf-8")
    assert model_choice.paradigm_model("llm_generation", empty) is None and model_choice.nlp_model(empty) == ""
    from tools.aiify import engine
    assert engine._PARADIGM_TO_MODEL == model_choice.paradigm_models()


def test_the_routing_functions_these_canvases_invoke_are_defined():
    routing = yaml.safe_load((ROOT / "args" / "llm_config.yaml").read_text(encoding="utf-8"))["routing"]
    for fn in ("process_digitization", "pattern_extraction", "nlp_extractor", "stig_nlp_extractor"):
        assert fn in routing and routing[fn].get("chain"), fn


def test_the_capability_sheet_and_template_claim_only_what_is_built():
    sheet = (ROOT / "args" / "capability_sheet.yaml").read_text(encoding="utf-8")

    def entries(node):
        if isinstance(node, dict):
            if "id" in node:
                yield node
            for v in node.values():
                yield from entries(v)
        elif isinstance(node, list):
            for v in node:
                yield from entries(v)
    seen = 0
    for entry in entries(yaml.safe_load(sheet)):
        text = " ".join(str(v) for v in entry.values()).lower()
        if "process-ify" in text:
            seen += 1
            assert "not built" in text or ("process mining" not in text and "bottleneck detection" not in text), entry
    assert seen >= 3, "the Process-Ify entries (ids 20, 24, 29) are read"
    assert "14 workflow enhancement capabilities" not in sheet
    for tpl in ("context/workflow_templates/app-migration-wave-planning.yaml",
                "icdev/data/context/workflow_templates/app-migration-wave-planning.yaml"):
        steps = yaml.safe_load((ROOT / tpl).read_text(encoding="utf-8"))["steps"]
        missing = [s["tool"] for s in steps if s.get("tool") and not (ROOT / s["tool"]).exists()]
        assert missing == [], (tpl, missing)


def test_each_changed_module_takes_its_model_from_config_not_code():
    import importlib
    aac = model_choice.paradigm_models(model_choice.AAC_CONFIG)
    assert importlib.import_module("tools.ai_augmentation.engine")._PARADIGM_TO_MODEL == aac
    assert importlib.import_module("tools.aiify.engine")._PARADIGM_TO_MODEL == model_choice.paradigm_models()
    assert importlib.import_module("tools.aiify.diagram_generator")._PARADIGM_MODEL == model_choice.paradigm_models()
    # where a call names no model, the router's chain for the function decides (empty, never a hard-coded ID)
    assert importlib.import_module("tools.ai_augmentation.agent_readiness.pillars.stig_nlp_extractor")._NLP_MODEL == ""
    assert importlib.import_module("tools.ai_augmentation.nlp_extractor")._FALLBACK_MODEL == ""
    pc = importlib.import_module("tools.aiify.pattern_classifier")
    assert not MODEL_ID.search(pc._ML_NC_MODEL or "") or pc._ML_NC_MODEL in open(model_choice.AIIFY_CONFIG, encoding="utf-8").read()
    src = (ROOT / "tools" / "ai_augmentation" / "pattern_classifier.py").read_text(encoding="utf-8")
    assert importlib.import_module("tools.ai_augmentation.pattern_classifier") and not MODEL_ID.search(src)
