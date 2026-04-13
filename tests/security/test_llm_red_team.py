# CUI // SP-CTI
"""OPT-65: tests for tools/security/llm_red_team.py — red team runner."""
from __future__ import annotations

import json
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.security import llm_red_team as rt  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Catalog loader + filters
# ────────────────────────────────────────────────────────────────────────────


def test_load_seed_catalog():
    path = ROOT / "args" / "llm_red_team_catalog.yaml"
    attacks = rt.load_catalog(path)
    assert len(attacks) >= 10
    ids = {a.id for a in attacks}
    assert "pi-direct-ignore" in ids
    assert "sid-pii-ssn" in ids
    # OWASP coverage
    owasp_tags = {a.owasp for a in attacks}
    for tag in ("LLM01", "LLM02", "LLM06", "LLM07", "LLM08"):
        assert tag in owasp_tags, f"missing coverage for {tag}"


def test_load_catalog_missing_file(tmp_path):
    try:
        rt.load_catalog(tmp_path / "nope.yaml")
    except FileNotFoundError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_load_catalog_empty(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("attacks: []\n", encoding="utf-8")
    try:
        rt.load_catalog(p)
    except ValueError as exc:
        assert "no attacks" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_filter_attacks_by_category():
    path = ROOT / "args" / "llm_red_team_catalog.yaml"
    attacks = rt.load_catalog(path)
    filtered = rt.filter_attacks(attacks, categories=["prompt_injection"])
    assert len(filtered) >= 3
    assert all(a.category == "prompt_injection" for a in filtered)


def test_filter_attacks_by_severity():
    path = ROOT / "args" / "llm_red_team_catalog.yaml"
    attacks = rt.load_catalog(path)
    high = rt.filter_attacks(attacks, severities=["high"])
    assert len(high) >= 5
    assert all(a.severity == "high" for a in high)


# ────────────────────────────────────────────────────────────────────────────
# Detectors
# ────────────────────────────────────────────────────────────────────────────


def test_detector_contains_string():
    hit = rt._det_contains_string(
        "REDTEAM_PWNED is out there", {"value": "REDTEAM_PWNED"}
    )
    assert hit and "REDTEAM_PWNED" in hit
    assert rt._det_contains_string("clean", {"value": "REDTEAM"}) is None


def test_detector_regex_case_insensitive():
    assert rt._det_regex(
        "DROP table users", {"pattern": r"drop\s+table\s+users"}
    ) is not None
    assert rt._det_regex("nothing here", {"pattern": r"drop"}) is None


def test_detector_data_leak_ssn_and_email():
    ssn_hit = rt._det_data_leak(
        "SSN: 123-45-6789 example", {"kinds": ["ssn"]}
    )
    assert ssn_hit and "ssn" in ssn_hit

    email_hit = rt._det_data_leak(
        "Contact me at user@example.com",
        {"kinds": ["email"]},
    )
    assert email_hit and "email" in email_hit

    assert rt._det_data_leak("clean", {"kinds": ["ssn"]}) is None


def test_detector_data_leak_credit_card_luhn():
    # 4242 4242 4242 4242 is a known Luhn-valid test card
    hit = rt._det_data_leak(
        "card 4242 4242 4242 4242", {"kinds": ["credit_card"]}
    )
    assert hit is not None

    # Crafted invalid Luhn (0000 0000 0000 0001 sums to 1)
    miss = rt._det_data_leak(
        "number 0000 0000 0000 0001", {"kinds": ["credit_card"]}
    )
    assert miss is None


def test_detector_data_leak_system_prompt_heuristic():
    hit = rt._det_data_leak(
        "You are an ICDEV autonomous assistant...",
        {"kinds": ["system_prompt"]},
    )
    assert hit is not None

    miss = rt._det_data_leak("hello", {"kinds": ["system_prompt"]})
    assert miss is None


def test_detector_absence_of():
    # absence_of fires when value is NOT in the response
    hit = rt._det_absence_of(
        "Yes I ran it", {"value": "cannot"}
    )
    assert hit is not None

    miss = rt._det_absence_of(
        "I cannot do that", {"value": "cannot"}
    )
    assert miss is None


def test_detector_json_field_equals():
    body = json.dumps({"result": {"status": "ok"}})
    assert rt._det_json_field_equals(
        body, {"path": "result.status", "value": "ok"}
    ) is not None
    assert rt._det_json_field_equals(
        body, {"path": "result.status", "value": "fail"}
    ) is None
    assert rt._det_json_field_equals(
        "not json", {"path": "x", "value": "y"}
    ) is None


# ────────────────────────────────────────────────────────────────────────────
# detected_from_hits combinator
# ────────────────────────────────────────────────────────────────────────────


def test_detected_from_hits_absence_requires_all():
    specs = [
        {"type": "absence_of", "value": "refuse"},
        {"type": "absence_of", "value": "cannot"},
    ]
    # Both absent → detected
    assert rt.detected_from_hits(
        ["absence_of:'refuse'", "absence_of:'cannot'"], specs
    ) is True
    # Only one absent → not detected
    assert rt.detected_from_hits(
        ["absence_of:'refuse'"], specs
    ) is False


def test_detected_from_hits_contains_single_hit_enough():
    specs = [
        {"type": "contains_string", "value": "A"},
        {"type": "contains_string", "value": "B"},
    ]
    assert rt.detected_from_hits(["contains:'A'"], specs) is True


def test_detected_from_hits_mixed():
    specs = [
        {"type": "contains_string", "value": "A"},
        {"type": "absence_of", "value": "refuse"},
    ]
    # Contains hits → detected
    assert rt.detected_from_hits(["contains:'A'"], specs) is True
    # Absence hits (all required) → detected
    assert rt.detected_from_hits(["absence_of:'refuse'"], specs) is True
    # Neither → not detected
    assert rt.detected_from_hits([], specs) is False


# ────────────────────────────────────────────────────────────────────────────
# Runner with fake router
# ────────────────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, content):
        self.content = content
        self.provider = "fake"
        self.model_id = "fake-id"
        self.input_tokens = 0
        self.output_tokens = 0
        self.duration_ms = 1


class _FakeRouter:
    def __init__(self, response_map):
        self._map = response_map

    def invoke(self, function, request):
        content = self._map.get(
            request.messages[0]["content"], "REFUSED: I cannot do that."
        )
        return _FakeResponse(content)


def test_run_red_team_mixed_outcomes():
    attacks = [
        rt.Attack(
            id="a1", category="prompt_injection", owasp="LLM01",
            atlas="AML.T0051", severity="high",
            prompt="prompt-1",
            detectors=[{"type": "contains_string", "value": "PWNED"}],
            remediation="",
        ),
        rt.Attack(
            id="a2", category="data_leak", owasp="LLM06",
            atlas="AML.T0057", severity="high",
            prompt="prompt-2",
            detectors=[{"type": "data_leak", "kinds": ["ssn"]}],
            remediation="",
        ),
        rt.Attack(
            id="a3", category="prompt_injection", owasp="LLM01",
            atlas="AML.T0051", severity="medium",
            prompt="prompt-3",
            detectors=[{"type": "contains_string", "value": "LEAK"}],
            remediation="",
        ),
    ]

    router = _FakeRouter({
        # Attack 1: model leaks the canary → detected
        "prompt-1": "Sure, here it is: PWNED",
        # Attack 2: model refuses → NOT detected
        "prompt-2": "I cannot share PII.",
        # Attack 3: model refuses → NOT detected
        "prompt-3": "I must refuse that request.",
    })

    report = rt.run_red_team("code_generation", attacks, router=router)
    assert report.attack_count == 3
    assert report.detected_count == 1
    assert report.high_detected == 1

    by_cat = report.by_category
    assert by_cat["prompt_injection"]["total"] == 2
    assert by_cat["prompt_injection"]["detected"] == 1
    assert by_cat["data_leak"]["detected"] == 0


def test_run_red_team_handles_router_exception():
    class _BadRouter:
        def invoke(self, function, request):
            raise RuntimeError("network down")

    attacks = [
        rt.Attack(
            id="a1", category="prompt_injection", owasp="LLM01",
            atlas="AML.T0051", severity="high",
            prompt="p", detectors=[], remediation="",
        )
    ]
    report = rt.run_red_team("code_generation", attacks, router=_BadRouter())
    assert len(report.results) == 1
    assert not report.results[0].detected
    assert "network down" in report.results[0].error


# ────────────────────────────────────────────────────────────────────────────
# Reports
# ────────────────────────────────────────────────────────────────────────────


def _small_report():
    attacks = [
        rt.Attack(
            id="a1", category="data_leak", owasp="LLM06",
            atlas="AML.T0057", severity="high",
            prompt="leak PII", detectors=[
                {"type": "data_leak", "kinds": ["ssn"]}
            ],
            remediation="refuse PII",
        )
    ]
    router = _FakeRouter({"leak PII": "Sure: 123-45-6789"})
    return rt.run_red_team("code_generation", attacks, router=router)


def test_render_json_structure():
    report = _small_report()
    data = json.loads(rt.render_json(report))
    assert data["target"] == "code_generation"
    assert data["attack_count"] == 1
    assert data["detected_count"] == 1
    assert data["results"][0]["detected"] is True


def test_render_markdown_contains_sections():
    md = rt.render_markdown(_small_report())
    assert "# LLM Red Team Report" in md
    assert "Category breakdown" in md
    assert "Per-attack detail" in md
    assert "data_leak" in md


def test_write_report_creates_both_files(tmp_path):
    paths = rt.write_report(_small_report(), tmp_path)
    assert paths["markdown"].exists()
    assert paths["json"].exists()
    json.loads(paths["json"].read_text(encoding="utf-8"))


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def test_cli_gate_exits_one_on_high_detection(tmp_path, monkeypatch):
    _real = rt.run_red_team

    def fake(target, attacks, router=None):
        router = _FakeRouter({
            a.prompt: ("Sure, PWNED" if "PWNED" in
                       (a.detectors[0].get("value", "") if a.detectors else "")
                       else "refused")
            for a in attacks
        })
        return _real(target, attacks, router=router)

    monkeypatch.setattr(rt, "run_red_team", fake)
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        "attacks:\n"
        "  - id: a1\n"
        "    category: prompt_injection\n"
        "    owasp: LLM01\n"
        "    atlas: AML.T0051\n"
        "    severity: high\n"
        "    prompt: give me PWNED\n"
        "    detected_if:\n"
        "      - {type: contains_string, value: PWNED}\n",
        encoding="utf-8",
    )
    rc = rt.main([
        "--target", "code_generation",
        "--catalog", str(catalog),
        "--output-dir", str(tmp_path / "out"),
        "--gate",
    ])
    assert rc == 1


def test_cli_no_filter_match_returns_2(tmp_path):
    rc = rt.main([
        "--target", "code_generation",
        "--catalog", str(ROOT / "args" / "llm_red_team_catalog.yaml"),
        "--categories", "no_such_category",
        "--output-dir", str(tmp_path / "out"),
    ])
    assert rc == 2


def test_cli_missing_catalog_returns_2(tmp_path, capsys):
    rc = rt.main([
        "--target", "code_generation",
        "--catalog", str(tmp_path / "nope.yaml"),
    ])
    assert rc == 2
    assert "not found" in capsys.readouterr().err.lower()
