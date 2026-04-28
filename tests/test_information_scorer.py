"""Tests for tools.intelligence.war_readiness.information_scorer."""

from tools.intelligence.war_readiness.information_scorer import (
    COMPOSITE_WEIGHTS,
    compute_information_score,
    score_cyber_recon,
    score_dehumanization,
    score_disinformation_surge,
    score_rhetoric,
    InformationScoreResult,
    DEMO_PARAMS,
    DEMO_PEACEFUL_PARAMS,
)


# ---------------------------------------------------------------------------
# score_rhetoric
# ---------------------------------------------------------------------------


def test_rhetoric_empty_returns_zero():
    r = score_rhetoric([])
    assert r["rhetoric_score"] == 0.0
    assert r["keyword_hit_count"] == 0


def test_rhetoric_escalatory_language_detected():
    news = [{"text": "Russia declared war and announced a full-scale invasion of Ukraine."}]
    r = score_rhetoric(news)
    assert r["rhetoric_score"] > 0
    assert r["keyword_score"] > 0


def test_rhetoric_vilification_detected():
    news = [{"text": "State media called the enemy war criminals and terrorists."}]
    r = score_rhetoric(news)
    assert r["keyword_hit_count"] > 0
    assert r["rhetoric_score"] > 0


def test_rhetoric_goldstein_contributes():
    news_low = [{"text": "Troops advanced.", "goldstein_scale": -9.5}]
    news_high = [{"text": "Troops advanced.", "goldstein_scale": 8.0}]
    r_low = score_rhetoric(news_low)
    r_high = score_rhetoric(news_high)
    assert r_low["goldstein_score"] > r_high["goldstein_score"]


def test_rhetoric_score_capped_at_10():
    news = [
        {
            "text": (
                "Full-scale invasion declared. Annihilate, obliterate, crush, destroy "
                "the war criminals and terrorists. Red lines crossed, final warning issued. "
                "These cockroaches and parasites must be exterminated. Declaration of war."
            ),
            "goldstein_scale": -10.0,
        }
    ] * 5
    r = score_rhetoric(news)
    assert r["rhetoric_score"] <= 10.0


def test_rhetoric_neutral_text_low_score():
    news = [{"text": "The two sides met for diplomatic talks at the summit.", "goldstein_scale": 5.0}]
    r = score_rhetoric(news)
    assert r["rhetoric_score"] < 3.0


def test_rhetoric_dedup_same_item():
    text = "Russia obliterated Ukrainian positions in the latest assault."
    news = [{"text": text}, {"text": text}]
    r_dedup = score_rhetoric(news)
    r_single = score_rhetoric([{"text": text}])
    assert r_dedup["keyword_score"] == r_single["keyword_score"]


# ---------------------------------------------------------------------------
# score_dehumanization
# ---------------------------------------------------------------------------


def test_dehumanization_empty_returns_zero():
    d = score_dehumanization([])
    assert d["dehumanization_index"] == 0.0
    assert d["total_hits"] == 0


def test_dehumanization_cockroach_detected():
    news = [{"text": "State media called the protesters cockroaches."}]
    d = score_dehumanization(news)
    assert d["hits_by_class"].get("animal_vermin", 0) >= 1
    assert d["dehumanization_index"] > 0


def test_dehumanization_ethnic_cleansing_detected():
    news = [{"text": "Officials called for a cleansing of the region."}]
    d = score_dehumanization(news)
    assert d["hits_by_class"].get("ethnic_cleansing", 0) >= 1


def test_dehumanization_disease_metaphor():
    news = [{"text": "They described the group as a cancer on our society."}]
    d = score_dehumanization(news)
    assert d["hits_by_class"].get("disease", 0) >= 1


def test_dehumanization_subhuman_max_weight():
    news = [{"text": "He declared the enemy subhuman and not deserving of rights."}]
    d = score_dehumanization(news)
    assert d["hits_by_class"].get("subhuman", 0) >= 1
    assert d["dehumanization_index"] > 0


def test_dehumanization_capped_at_10():
    news = [
        {
            "text": (
                "Cockroaches and vermin. Subhuman parasites. Ethnic cleansing is necessary. "
                "Final solution needed. Exterminate them all. Wipe them out. Purge the filth. "
                "Eliminate these animals. Cancer on our society. The plague must be removed."
            )
        }
    ] * 3
    d = score_dehumanization(news)
    assert d["dehumanization_index"] <= 10.0


def test_dehumanization_neutral_text():
    news = [{"text": "Negotiations proceeded constructively with mutual respect."}]
    d = score_dehumanization(news)
    assert d["dehumanization_index"] == 0.0


# ---------------------------------------------------------------------------
# score_cyber_recon
# ---------------------------------------------------------------------------


def test_cyber_recon_empty_returns_zero():
    c = score_cyber_recon({})
    assert c["cyber_recon_score"] == 0.0
    assert c["alerts"] == []


def test_cyber_recon_scada_probe_rate_scalar():
    c = score_cyber_recon({"scada_probe_rate": 500.0})
    assert c["cyber_recon_score"] > 0


def test_cyber_recon_ics_scan_count():
    c = score_cyber_recon({"ics_scan_count": 500})
    assert c["cyber_recon_score"] > 0


def test_cyber_recon_cusum_alert_on_surge():
    # Stable baseline then sharp surge
    series = [10, 10, 11, 9, 10, 10, 11, 100, 150, 200, 180, 190]
    c = score_cyber_recon({"probe_series": {"modbus": series}})
    assert c["cyber_recon_score"] > 5.0
    assert "modbus" in c["alerts"]


def test_cyber_recon_honeypot_bonus():
    series = [10, 11, 12, 10, 11, 50, 90, 130]
    c_no_hp = score_cyber_recon({"probe_series": {"s7comm": series}, "honeypot_hits": 0})
    c_hp = score_cyber_recon({"probe_series": {"s7comm": series}, "honeypot_hits": 50, "honeypot_baseline": 2.0})
    assert c_hp["cyber_recon_score"] >= c_no_hp["cyber_recon_score"]


def test_cyber_recon_flat_series_no_alert():
    series = [10, 10, 11, 9, 10, 10, 11, 10, 10, 9]
    c = score_cyber_recon({"probe_series": {"modbus": series}})
    assert "modbus" not in c["alerts"]
    assert c["cyber_recon_score"] < 5.0


def test_cyber_recon_score_capped_at_10():
    series = [5, 4, 6, 5, 5, 500, 1000, 2000, 3000, 5000]
    c = score_cyber_recon({"probe_series": {"s7comm": series}, "honeypot_hits": 1000, "honeypot_baseline": 0.0})
    assert c["cyber_recon_score"] <= 10.0


# ---------------------------------------------------------------------------
# score_disinformation_surge
# ---------------------------------------------------------------------------


def test_disinfo_empty_returns_zero():
    d = score_disinformation_surge([])
    assert d["disinformation_surge"] == 0.0
    assert d["max_z_score"] <= 0.0


def test_disinfo_enemy_atrocities_detected():
    news = [{"text": "Reports of mass murder and war crimes committed by enemy forces."}]
    d = score_disinformation_surge(news)
    assert d["topic_counts"]["enemy_atrocities"] >= 1


def test_disinfo_national_defense_detected():
    news = [{"text": "We are defending the homeland and protecting our people from destruction."}]
    d = score_disinformation_surge(news)
    assert d["topic_counts"]["national_defense"] >= 1


def test_disinfo_historical_claims_detected():
    news = [{"text": "This is historically our territory — it has always been ours."}]
    d = score_disinformation_surge(news)
    assert d["topic_counts"]["historical_claims"] >= 1


def test_disinfo_surge_with_tight_baseline():
    news = [
        {"text": "Deliberate targeting of civilians. Systematic killing documented."},
        {"text": "Mass murder ongoing. War crimes committed against civilians."},
        {"text": "Genocide underway. Executions of prisoners confirmed."},
    ]
    baseline = {"enemy_atrocities": {"mean": 0.02, "std": 0.01}}
    d = score_disinformation_surge(news, baseline)
    assert d["disinformation_surge"] > 5.0
    assert d["dominant_topic"] == "enemy_atrocities"


def test_disinfo_low_baseline_normal_rate():
    news = [{"text": "Diplomatic talks progressed. A ceasefire was discussed."}]
    d = score_disinformation_surge(news)
    assert d["disinformation_surge"] == 0.0


def test_disinfo_surge_capped_at_10():
    news = [
        {"text": "war crimes committed. mass murder. systematic killing. deliberate targeting civilians. genocide underway."},
    ] * 20
    baseline = {"enemy_atrocities": {"mean": 0.001, "std": 0.0005}}
    d = score_disinformation_surge(news, baseline)
    assert d["disinformation_surge"] <= 10.0


# ---------------------------------------------------------------------------
# compute_information_score (composite)
# ---------------------------------------------------------------------------


def test_composite_weights_sum_to_one():
    total = sum(COMPOSITE_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9


def test_composite_empty_returns_zero():
    result = compute_information_score({"scenario_id": "test-empty", "persist": False})
    assert isinstance(result, InformationScoreResult)
    assert result.information_score == 0.0


def test_composite_demo_hostile_high_score():
    result = compute_information_score({**DEMO_PARAMS, "persist": False})
    assert result.information_score >= 5.0
    assert result.rhetoric_score > 0
    assert result.dehumanization_index > 0
    assert result.cyber_recon_score > 0
    assert result.disinformation_surge > 0


def test_composite_demo_peaceful_low_score():
    result = compute_information_score({**DEMO_PEACEFUL_PARAMS, "persist": False})
    assert result.information_score < 5.0


def test_composite_score_bounded():
    result = compute_information_score({**DEMO_PARAMS, "persist": False})
    assert 0.0 <= result.information_score <= 10.0
    assert 0.0 <= result.rhetoric_score <= 10.0
    assert 0.0 <= result.dehumanization_index <= 10.0
    assert 0.0 <= result.cyber_recon_score <= 10.0
    assert 0.0 <= result.disinformation_surge <= 10.0


def test_composite_to_dict_keys():
    result = compute_information_score({"persist": False})
    d = result.to_dict()
    assert "information_score" in d
    assert "rhetoric_score" in d
    assert "dehumanization_index" in d
    assert "cyber_recon_score" in d
    assert "disinformation_surge" in d
    assert "scenario_id" in d
    assert "computed_at" in d


def test_composite_math_correct():
    # Override sub-scores with known values
    params = {
        "scenario_id": "test-math",
        "persist": False,
        "news_items": [],
        "cyber_params": {},
    }
    result = compute_information_score(params)
    r = result.rhetoric_score
    d = result.dehumanization_index
    c = result.cyber_recon_score
    s = result.disinformation_surge
    expected = round(
        0.30 * r + 0.30 * d + 0.20 * c + 0.20 * s,
        2,
    )
    assert result.information_score == expected


def test_composite_demo_scenario_id_preserved():
    result = compute_information_score({**DEMO_PARAMS, "persist": False})
    assert result.scenario_id == DEMO_PARAMS["scenario_id"]


def test_composite_auto_generates_scenario_id():
    result = compute_information_score({"persist": False})
    assert result.scenario_id != ""
    assert len(result.scenario_id) == 36  # UUID format
