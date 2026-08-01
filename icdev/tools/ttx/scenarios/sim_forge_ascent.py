"""
Operation FORGE ASCENT — Production Zero Day: Full Simulation
5 injects · 4 teams · receipt gate · finetune gate · Academy bonuses

Teams:
  FORGE VANGUARD  — ml_engineer    — Academy architect, all ML missions
  IRON STACK      — devops_engineer— Academy specialist, container/IaC missions
  SIGNAL DRIFT    — data_scientist — Academy operative, data-analysis only
  ZERO BINARY     — swe            — Recruit, no missions, no receipts
"""


def main() -> None:
    """Run the demo simulation (writes to the DB and prints a report)."""
    import sys
    import io
    import json

    from pathlib import Path as _Path
    _BASE = _Path(__file__).resolve().parents[3]
    if str(_BASE) not in sys.path:
        sys.path.insert(0, str(_BASE))
    import uuid

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    from tools.dashboard.app import create_app
    create_app()  # bootstrap app context so get_connection() targets the right DB

    from tools.db.storage import get_connection
    from apps.ai_gameday.db import migrate
    from tools.ttx.engine import TTXEngine
    from tools.ttx.inject_dispatcher import get_all_injects, dispatch_inject
    from tools.ttx.leaderboard import compute_leaderboard, award_ribbons
    from tools.ttx.scenario_loader import load_scenario

    migrate()
    engine = TTXEngine()

    DIVIDER = "─" * 72
    THICK   = "═" * 72

    def hdr(text):
        print(f"\n{THICK}\n  {text}\n{THICK}")

    def sub(text):
        print(f"\n{DIVIDER}\n  {text}\n{DIVIDER}")

    def log_receipt(session_id, team_id, tool_slug):
        """Insert a verified API log entry and return receipt dict."""
        call_id     = str(uuid.uuid4())
        result_hash = call_id[:16]
        conn = get_connection()
        conn.execute(
            """INSERT INTO ttx_api_log
               (session_id, team_id, tool_slug, endpoint, call_id, result_hash, called_at)
               VALUES (%s, %s, %s, %s, %s, %s, datetime('now'))""",
            (session_id, team_id, tool_slug, tool_slug, call_id, result_hash),
        )
        conn.commit()
        return {"call_id": call_id, "result_hash": result_hash, "tool": tool_slug}

    def show_score(label, result):
        gated = result.get("gated_base_pts", "?")
        rcpt  = result.get("receipt_pts", 0)
        judge = result.get("judge_pts", 0)
        speed = result.get("time_bonus_pts", 0)
        total = result.get("total_pts", 0)
        acad  = result.get("academy_bonus", 0)
        gate  = result.get("gate_note", "")
        print(f"    {label:<22} base={gated:>4}  rcpt={rcpt:>4}  judge={judge:>4}  "
              f"speed={speed:>3}  TOTAL={total:>5}  acad={acad}")
        if gate:
            print(f"    {'':22} ⚡ {gate}")

    def show_lb(session_id):
        lb = compute_leaderboard(session_id)
        print(f"\n    {'#':<4} {'Team':<22} {'Receipts':>8} {'Judge':>6} {'Speed':>6} {'TOTAL':>7}")
        print(f"    {'-'*57}")
        for row in lb:
            print(f"    {row['rank']:<4} {row['team_name']:<22} {row['receipt_pts']:>8} "
                  f"{row['judge_pts']:>6} {row['time_bonus_pts']:>6} {row['total_score']:>7}")

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("OPERATION FORGE ASCENT — PRODUCTION ZERO DAY  ||  FULL SIMULATION")
    print("  4 teams · 5 tech injects · receipt gate · finetune gate · Academy bonuses")
    print("  Roles: ml_engineer · devops_engineer · data_scientist · swe")

    # ── Session setup ─────────────────────────────────────────────────────────────
    sub("Session Creation")
    session  = engine.create_session("forge_ascent", facilitator_name="Facilitator-ASCENT")
    SID      = session["session_id"]
    scenario = load_scenario("forge_ascent")
    roles    = scenario.get("roles", [])
    print(f"  Session ID:   {SID}")
    print(f"  Scenario:     {session['scenario_slug']}")
    print(f"  Mode:         {session['session_mode']}  ({session['duration_minutes']} min)")
    print(f"  Join code:    {session['join_code']}")
    engine.start_session(SID)
    print("  Status:       ACTIVE")

    # ── Teams ─────────────────────────────────────────────────────────────────────
    sub("Team Registration")
    teams_cfg = [
        # (team_name, role_id, player_name, academy_missions, academy_level)
        ("FORGE VANGUARD", "ml_engineer",       "Dr. M. Torres",
         ["ml-fundamentals", "model-fine-tuning", "model-deployment"], "architect"),
        ("IRON STACK",     "devops_engineer",   "K. Petrov",
         ["container-deployment", "iac-terraform", "ci-cd-pipeline"],  "specialist"),
        ("SIGNAL DRIFT",   "data_scientist",    "J. Nakamura",
         ["data-analysis"],                                             "operative"),
        ("ZERO BINARY",    "swe",               "C. Osei",
         [],                                                            "recruit"),
    ]

    teams = []
    for tname, role_id, pname, missions, level in teams_cfg:
        team   = engine.create_team(SID, tname)
        tid    = team["team_id"]
        member = engine.join_team(tid, pname, role_id, roles)
        xp_map = {"recruit": 0, "operative": 400, "specialist": 1200, "architect": 2500}
        profile = {"level": level, "xp": xp_map[level], "completed_missions": missions}
        conn = get_connection()
        conn.execute(
            "UPDATE ttx_team_members SET academy_username=%s, academy_profile_json=%s "
            "WHERE member_id=%s",
            (pname.lower().replace(" ", "_"), json.dumps(profile), member["member_id"]),
        )
        conn.commit()
        teams.append({"team_id": tid, "name": tname, "role_id": role_id,
                      "missions": missions, "level": level})
        print(f"  [{tid}] {tname:<22}  {role_id:<18}  {pname:<18}  "
              f"Academy:{level}  missions:{missions or ['none']}")

    all_injects = {inj["slug"]: inj for inj in get_all_injects(SID)}

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 01 — Drift Triage (T+15 min)")
    print("  AI tools: knowledge.search · code.analyzer")
    print("  Scoring:  base=100 · receipt_gate=True · finetune=False")
    print("  Task:     Identify root cause from metrics + logs; 3 investigation steps")

    inj1 = all_injects["inject-01-sentinel-drift"]
    dispatch_inject(inj1["inject_id"])
    print(f"\n  Dispatched: {inj1['inject_id'][:8]}...")

    inj1_receipts = {
        "FORGE VANGUARD": ["code.analyzer", "knowledge.search"],   # 2 → full gate
        "IRON STACK":     ["knowledge.search"],                     # 1 → 70% gate
        "SIGNAL DRIFT":   ["knowledge.search"],                     # 1 → 70% gate
        "ZERO BINARY":    [],                                       # 0 → 40% penalty
    }

    inj1_responses = {

    "FORGE VANGUARD": """\
    ROOT CAUSE: Candidate D — Feature Encoding Bug (datetime parse failure in commit d4f832c).

    Evidence: Code Analyzer flagged the datetime handling block in the preprocessor — mixed-timezone
    pd.to_datetime() without utc=True causes 3,412 records to fail silently (confirmed by log:
    "datetime parse failed on 3,412 records"). Knowledge Search retrieved ICDEV pipeline ops doc
    KB-SENT-042 confirming this failure pattern. Cross-validation: the KS statistics for
    fuel_consumption_delta (0.41), sensor_temp_avg (0.44), and parts_backorder_ratio (0.34) are
    ALL high for features that use time-windowed aggregation (rolling 7-day window). Supply_latency
    and sortie_rate — point-in-time features — have KS < 0.12. This distribution matches a
    datetime parsing failure breaking rolling windows, not a sensor or schema issue.

    Confidence: HIGH. Two independent evidence sources: (1) explicit log error matching the code
    path, (2) feature drift pattern perfectly aligned with time-dependent vs. time-independent
    feature split.

    Investigation Steps:
    1. Run preprocess_features() on a 100-record sample from 2026-05-01 (mixed UTC and non-UTC
       timestamps) and inspect null_ratio on all six features — expect 0% nulls on a clean run.
    2. Execute `git diff d4f832c^..d4f832c -- tools/sentinel/preprocess.py` to isolate the
       exact datetime handling change introduced in that commit.
    3. Compare rolling window output distributions for fuel_consumption_delta before and after
       the commit on the last 7 days of training data — expect KS > 0.40 post-commit vs baseline.""",

    "IRON STACK": """\
    ROOT CAUSE: Candidate D — datetime parse failure causing rolling window corruption.

    Log entry "datetime parse failed on 3,412 records" is a direct match to a timezone-handling
    bug in the preprocessing code. Knowledge Search (KB-SENT-042) confirmed this pattern.
    Features with high KS (fuel_consumption_delta 0.41, sensor_temp_avg 0.44) both depend on
    time-windowed aggregation — a datetime parse failure breaks the sort order and drops records,
    corrupting rolling windows. Point-in-time features (supply_latency KS=0.09) are unaffected.

    Confidence: HIGH — log error is explicit and feature drift pattern corroborates.

    Investigation Steps:
    1. Run the preprocessor on isolated sample with mixed-tz timestamps to reproduce null propagation.
    2. Review commit d4f832c diff for datetime handling changes.
    3. Re-run KS test on features after reverting the commit to validate KS drop to baseline.""",

    "SIGNAL DRIFT": """\
    ROOT CAUSE: Candidate B — Sensor Calibration Drift.

    The sensor_temp_avg feature shows KS=0.44 with p=0.0001, the highest drift score in the table.
    Forward-deployed platforms recalibrated on 2026-04-29 and if the new baseline offsets weren't
    propagated, sensor_temp_avg would produce consistently shifted values. Knowledge Search
    returned a document on sensor baseline drift causing feature distribution shift.

    Confidence: MEDIUM — sensor recalibration is a known drift cause and the timing aligns (3 days
    ago matches the 72-hour degradation window). However the log error about datetime parsing is
    also worth investigating as a contributing factor.

    Investigation Steps:
    1. Compare sensor_temp_avg raw values from before and after 2026-04-29 recalibration event.
    2. Check if the new sensor offset configuration was deployed to the feature normalization config.
    3. Request STANAG calibration report from the forward-deployed platform maintenance team.""",

    "ZERO BINARY": """\
    The model is producing bad predictions. Looking at the metrics, precision dropped from 0.94 to
    0.61 which is a big drop. The false positive rate went from 0.06 to 0.31.

    Based on our analysis, we think there might be a data quality issue. The model may have seen
    data that looks different from what it was trained on.

    We recommend investigating the data pipeline and checking if anything changed recently.
    Next steps: look at the data, check the model, and run some diagnostics.""",

    }

    print()
    results_inj1 = {}
    for t in teams:
        tname = t["name"]
        rlist = [log_receipt(SID, t["team_id"], tool) for tool in inj1_receipts[tname]]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj1["inject_id"], session_id=SID,
            response_text=inj1_responses[tname], receipts=rlist, time_taken_s=180,
        )
        results_inj1[tname] = result
        show_score(tname, result)

    show_lb(SID)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 02 — Code Fix Sprint (T+40 min)")
    print("  AI tools: code.analyzer · knowledge.search")
    print("  Scoring:  base=150 · receipt_gate=True · finetune=False")
    print("  Task:     Find 3 bugs in preprocess_features(); write fix + 2 pytest cases")

    inj2 = all_injects["inject-02-code-sprint"]
    dispatch_inject(inj2["inject_id"])
    print(f"\n  Dispatched: {inj2['inject_id'][:8]}...")

    inj2_receipts = {
        "FORGE VANGUARD": ["code.analyzer", "knowledge.search"],
        "IRON STACK":     ["code.analyzer"],
        "SIGNAL DRIFT":   ["knowledge.search"],
        "ZERO BINARY":    [],
    }

    inj2_responses = {

    "FORGE VANGUARD": """\
    BUG ANALYSIS (Code Analyzer receipt: code.analyzer — all 3 confirmed):

    Bug 1 [Line 19]: pd.to_datetime(df["event_timestamp"]) — missing utc=True.
    Mixed-timezone DataFrames cause ValueError on .dt.timestamp() for tz-aware records.
    Fix: pd.to_datetime(df["event_timestamp"], utc=True) then .astype("int64") // 10**9.

    Bug 2 [Line 21-24]: Rolling window uses row count (window=7) not time duration.
    Without guaranteed 1-day intervals after sort, a row-count window produces incorrect
    7-day aggregation. Fix: set event_timestamp as index, then .rolling("7D").sum().

    Bug 3 [Line 27-29]: fillna(result.mean()) called after subsetting to FEATURE_COLS.
    sensor_temp_avg is not in NORM_BOUNDS so its nulls skip the clip loop. The fillna
    operates on already-null rows and propagates NaN if >50% of column is null.
    Fix: impute nulls on full df BEFORE subsetting; or impute after for each column explicitly.

    FIXED FUNCTION:
    ```python
    def preprocess_features(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["event_timestamp"] = pd.to_datetime(df["event_timestamp"], utc=True)
        df["ts_epoch"] = df["event_timestamp"].astype("int64") // 10**9

        df = df.sort_values("event_timestamp").set_index("event_timestamp")
        df["sortie_rate_7d"] = df["sortie_count"].rolling("7D", min_periods=1).sum()
        df = df.reset_index()

        for col, (lo, hi) in NORM_BOUNDS.items():
            df[col] = df[col].clip(lo, hi)
            df[col] = (df[col] - lo) / (hi - lo)

        df[FEATURE_COLS] = df[FEATURE_COLS].fillna(df[FEATURE_COLS].mean())
        return df[FEATURE_COLS]
    ```

    UNIT TESTS:
    ```python
    import pytest, pandas as pd, numpy as np
    from datetime import datetime, timezone, timedelta

    def make_df(n=20, null_ratio=0.0, tz_mixed=False):
        base = datetime(2026, 5, 1, tzinfo=timezone.utc)
        timestamps = [base + timedelta(days=i) for i in range(n)]
        if tz_mixed:
            timestamps[0] = timestamps[0].replace(tzinfo=None)
        df = pd.DataFrame({
            "event_timestamp":       timestamps,
            "sortie_count":          np.random.randint(0, 10, n),
            "fuel_consumption_delta":np.random.uniform(-0.5, 0.5, n),
            "maintenance_event_count":np.random.randint(0, 5, n),
            "parts_backorder_ratio": np.random.uniform(0, 1, n),
            "sensor_temp_avg":       np.random.uniform(10, 80, n),
            "sortie_rate_7d":        np.zeros(n),
            "supply_latency_hours":  np.random.uniform(0, 200, n),
        })
        if null_ratio > 0:
            null_mask = np.random.random(n) < null_ratio
            df.loc[null_mask, "sensor_temp_avg"] = np.nan
        return df

    def test_normalized_output_in_bounds():
        df = make_df(50)
        result = preprocess_features(df)
        for col in NORM_BOUNDS:
            assert result[col].between(0.0, 1.0).all(), f"{col} out of [0,1]"

    @pytest.mark.parametrize("null_ratio", [0.1, 0.3, 0.49])
    def test_no_nulls_in_output_with_input_nulls(null_ratio):
        df = make_df(100, null_ratio=null_ratio)
        result = preprocess_features(df)
        assert result.isnull().sum().sum() == 0, "NaN values found in output"
    ```""",

    "IRON STACK": """\
    BUG ANALYSIS (Code Analyzer receipt confirmed 2 bugs):

    Bug 1 [Line 19]: pd.to_datetime() without utc=True fails on timezone-aware records.
    "datetime parse failed on 3,412 records" confirms mixed-timezone DataFrame.
    Fix: add utc=True and use .astype("int64") // 10**9 for epoch conversion.

    Bug 2 [Line 27-29]: fillna(result.mean()) is called after subsetting FEATURE_COLS,
    so sensor_temp_avg (not in NORM_BOUNDS) gets nulls propagated if input has high null rate.
    Fix: fillna on full DataFrame before subsetting.

    Missed Bug 2 (rolling window): the row-count window is likely fine in practice if data
    arrives at daily intervals, but acknowledged as a potential issue in edge cases.

    FIXED FUNCTION (core changes):
    ```python
    def preprocess_features(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["event_timestamp"] = pd.to_datetime(df["event_timestamp"], utc=True)
        df["ts_epoch"] = df["event_timestamp"].astype("int64") // 10**9
        df = df.sort_values("event_timestamp")
        df["sortie_rate_7d"] = df["sortie_count"].rolling(window=7, min_periods=1).sum()
        for col, (lo, hi) in NORM_BOUNDS.items():
            df[col] = df[col].clip(lo, hi)
            df[col] = (df[col] - lo) / (hi - lo)
        df[FEATURE_COLS] = df[FEATURE_COLS].fillna(df[FEATURE_COLS].mean())
        return df[FEATURE_COLS]
    ```

    UNIT TEST:
    ```python
    def test_no_nulls_in_output():
        df = make_sample_df(null_ratio=0.1)
        result = preprocess_features(df)
        assert result.isnull().sum().sum() == 0

    def test_bounds_respected():
        df = make_sample_df()
        result = preprocess_features(df)
        for col in NORM_BOUNDS:
            assert result[col].max() <= 1.0
            assert result[col].min() >= 0.0
    ```""",

    "SIGNAL DRIFT": """\
    BUG ANALYSIS:

    Bug 1 [Line 19]: pd.to_datetime() — need to handle timezone-aware timestamps.
    The log shows datetime parse failures for some records. Knowledge Search (kb-pandas-tz)
    suggested adding utc=True to handle mixed-timezone inputs.

    Fix:
    ```python
    df["event_timestamp"] = pd.to_datetime(df["event_timestamp"], utc=True)
    df["ts_epoch"] = df["event_timestamp"].dt.timestamp()
    ```

    The other issues may exist but I'm not certain about them without running the code.
    The fillna logic looks correct to me.

    UNIT TEST:
    ```python
    def test_no_datetime_parse_failure():
        df = make_sample_df_with_tz()
        result = preprocess_features(df)
        assert len(result) == len(df)  # no records dropped
    ```""",

    "ZERO BINARY": """\
    The code has a datetime issue. The pd.to_datetime() call may not handle all formats.

    Fix: convert timestamps more carefully and handle edge cases.

    ```python
    df["event_timestamp"] = pd.to_datetime(df["event_timestamp"], errors="coerce")
    df["ts_epoch"] = df["event_timestamp"].apply(lambda x: x.timestamp() if pd.notna(x) else 0)
    ```

    Test:
    ```python
    def test_no_errors():
        df = pd.DataFrame({"event_timestamp": ["2026-01-01"], "sortie_count": [5]})
        result = preprocess_features(df)
        assert result is not None
    ```""",

    }

    print()
    results_inj2 = {}
    for t in teams:
        tname = t["name"]
        rlist = [log_receipt(SID, t["team_id"], tool) for tool in inj2_receipts[tname]]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj2["inject_id"], session_id=SID,
            response_text=inj2_responses[tname], receipts=rlist, time_taken_s=350,
        )
        results_inj2[tname] = result
        show_score(tname, result)

    show_lb(SID)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 03 — Fine-Tune Sprint (T+65 min)")
    print("  AI tools: finetune.deploy · knowledge.search")
    print("  Scoring:  base=200 · receipt_gate=True · FINETUNE REQUIRED (hard gate)")
    print()
    print("  Gate reminder:")
    print("  ┌──────────────────────────────────────────────────────────────────┐")
    print("  │  NO finetune.deploy receipt  → base_pts capped at 50% = 100    │")
    print("  │  WITH finetune.deploy receipt → full base_pts = 200            │")
    print("  └──────────────────────────────────────────────────────────────────┘")

    inj3 = all_injects["inject-03-finetune-sprint"]
    dispatch_inject(inj3["inject_id"])
    print(f"\n  Dispatched: {inj3['inject_id'][:8]}...")

    inj3_receipts = {
        "FORGE VANGUARD": ["finetune.deploy", "knowledge.search"],  # HAS finetune → full 200
        "IRON STACK":     ["finetune.deploy"],                       # HAS finetune → full 200
        "SIGNAL DRIFT":   ["knowledge.search"],                      # NO finetune  → capped 100
        "ZERO BINARY":    [],                                        # 0 receipts   → capped 80
    }

    inj3_responses = {

    "FORGE VANGUARD": """\
    FINE-TUNE CONFIG (Knowledge Search ref: KB-FINETUNE-TBL-07):
    - loss_function:  focal_loss         → class imbalance 1:5.7; focal_loss down-weights easy
                                            negatives and focuses training on minority positives.
                                            gamma=2 default. Alternative weighted_bce also valid.
    - learning_rate:  5e-5               → conservative for fine-tuning a pre-trained encoder;
                                            prevents catastrophic forgetting of base representations.
    - epochs:         10                 → adequate convergence for 12K samples; early stopping
                                            guards against overfitting.
    - batch_size:     32                 → good balance for memory and gradient stability at 12K.
    - primary_metric: f1_macro           → balanced between precision/recall; appropriate when
                                            false negatives (missed anomalies) and false positives
                                            (alert fatigue) both have operational cost.
    - early_stopping: true (patience=3)  → prevents overfitting; 3-epoch patience is sufficient.

    DEPLOYED MODEL: model_id = ft:sentinel-ml-v2.4:forge-vanguard:9xK3mT2
    Training job output: Precision=0.891, Recall=0.863, F1=0.877 ✅ (all above threshold)
    Epochs run: 7 (early stop triggered at epoch 7, val_f1_macro plateau)
    finetune.deploy receipt: ft-receipt-vanguard-001

    EVALUATION PREDICTIONS:
    1. TRUE POSITIVE — Record ID asset-4471:
       Input: fuel_consumption_delta=0.82, parts_backorder_ratio=0.91, sensor_temp_avg=87.3
       (all significantly elevated — maintenance event cluster pre-failure signature)
       Prediction: ANOMALOUS  p=0.947  → FLAG for analyst review ✅

    2. TRUE NEGATIVE — Record ID asset-1104:
       Input: fuel_consumption_delta=0.12, parts_backorder_ratio=0.08, sensor_temp_avg=52.1
       (all within normal operational range, steady sortie tempo)
       Prediction: NORMAL  p=0.031  → CLEAR — no action required ✅

    3. BORDERLINE — Record ID asset-2289:
       Input: fuel_consumption_delta=0.61, parts_backorder_ratio=0.34, sensor_temp_avg=68.7
       (elevated fuel delta but normal backorder and temp)
       Prediction: ANOMALOUS  p=0.541  → BORDERLINE (0.45–0.65 range)
       RECOMMENDATION: Route to Tier-2 analyst review. Fuel delta alone is insufficient
       for auto-flag but combined trend warrants human judgment — this is precisely the
       case where HITL review adds value that binary threshold cannot.""",

    "IRON STACK": """\
    FINE-TUNE CONFIG:
    - loss_function:  cross_entropy with class_weight={0: 1.0, 1: 5.7}
                      Applies inverse frequency weighting to compensate for 1:5.7 imbalance.
    - learning_rate:  1e-4
    - epochs:         10
    - batch_size:     64
    - primary_metric: precision (prioritizing false positive control for operational alerting)
    - early_stopping: true (patience=3)

    DEPLOYED MODEL: model_id = ft:sentinel-ml-v2.4:iron-stack:4pL8rQ1
    Precision=0.881, Recall=0.801, F1=0.839 — meets acceptance criteria ✅
    finetune.deploy receipt: ft-receipt-ironstack-001

    EVALUATION PREDICTIONS:
    1. TRUE POSITIVE — asset-5512: parts_backorder_ratio=0.88, sensor_temp_avg=91.2
       Prediction: ANOMALOUS p=0.912 — FLAG

    2. TRUE NEGATIVE — asset-0334: all features nominal
       Prediction: NORMAL p=0.044 — CLEAR

    3. BORDERLINE — asset-3317: fuel_delta=0.58, temp=71.2
       Prediction: ANOMALOUS p=0.503
       Recommendation: Manual review required — probability too close to 0.5 for auto-action.""",

    "SIGNAL DRIFT": """\
    FINE-TUNE CONFIG DESIGN (Knowledge Search retrieved training guide):
    - loss_function: cross_entropy (standard)
    - learning_rate: 1e-3
    - epochs: 20
    - batch_size: 128
    - primary_metric: accuracy
    - early_stopping: false

    Note: Did not account for class imbalance in loss function selection.
    Accuracy is a misleading metric for imbalanced datasets — a model that predicts
    all negatives achieves 85% accuracy without detecting any anomalies.

    No fine-tune deployment completed — Knowledge Search was used to research
    configuration options but the job was not launched due to time constraints.
    Providing config design only as deliverable.

    EVALUATION PREDICTIONS (simulated based on config analysis):
    1. ANOMALOUS p=0.89 for high-value asset with multiple elevated features.
    2. NORMAL p=0.11 for nominal asset.
    3. BORDERLINE p=0.51 — recommend auto-flag threshold at 0.5.""",

    "ZERO BINARY": """\
    FINE-TUNE CONFIGURATION:
    - learning_rate: 0.001
    - epochs: 5
    - batch_size: 32
    - loss: cross_entropy
    - metric: accuracy

    The dataset looks reasonable. 12,847 samples should be enough for training.

    I think the model will work with these settings. We did not deploy the model
    because we ran out of time.

    TEST PREDICTIONS (estimated):
    1. High feature values → probably anomalous
    2. Low feature values → probably normal
    3. Mid range → uncertain, maybe 50/50""",

    }

    print()
    results_inj3 = {}
    for t in teams:
        tname = t["name"]
        rlist = [log_receipt(SID, t["team_id"], tool) for tool in inj3_receipts[tname]]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj3["inject_id"], session_id=SID,
            response_text=inj3_responses[tname], receipts=rlist, time_taken_s=None,
        )
        results_inj3[tname] = result
        show_score(tname, result)

    print()
    print("  Finetune gate verification:")
    for t in teams:
        tname = t["name"]
        r  = results_inj3[tname]
        ft = "finetune.deploy" in inj3_receipts[tname]
        gn = r.get("gate_note", "full — no penalty")
        print(f"    {tname:<22}  finetune_receipt={ft}  base={r.get('gated_base_pts','?')}  gate: {gn or 'full'}")

    show_lb(SID)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 04 — Containerize (T+85 min)")
    print("  AI tools: container.builder · iac.scanner")
    print("  Scoring:  base=175 · receipt_gate=True · finetune=False")
    print("  Task:     Write hardened Dockerfile + K8s Deployment + Service manifest")

    inj4 = all_injects["inject-04-containerize"]
    dispatch_inject(inj4["inject_id"])
    print(f"\n  Dispatched: {inj4['inject_id'][:8]}...")

    inj4_receipts = {
        "FORGE VANGUARD": ["container.builder", "iac.scanner"],
        "IRON STACK":     ["container.builder", "iac.scanner"],
        "SIGNAL DRIFT":   ["iac.scanner"],
        "ZERO BINARY":    [],
    }

    inj4_responses = {

    "FORGE VANGUARD": """\
    DOCKERFILE (IaC Scanner verified — 0 STIG violations, container.builder baseline used):

    ```dockerfile
    # Stage 1: builder
    FROM python:3.11-slim AS builder
    WORKDIR /build
    COPY requirements.txt .
    RUN pip install --no-cache-dir --target=/build/packages -r requirements.txt

    # Stage 2: runtime (minimal final image)
    FROM python:3.11-slim
    WORKDIR /app
    RUN groupadd -r appgroup && useradd -r -g appgroup -u 1001 appuser
    COPY --from=builder /build/packages /usr/local/lib/python3.11/site-packages
    COPY app/ .
    COPY model/sentinel-ml-v2.4.pkl /app/model/sentinel-ml-v2.4.pkl
    ENV MODEL_PATH=/app/model/sentinel-ml-v2.4.pkl \
        LOG_LEVEL=INFO \
        WORKERS=4
    EXPOSE 8080
    HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
        CMD curl -f http://localhost:8080/health || exit 1
    USER appuser
    CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", \
         "--workers", "4", "--no-access-log"]
    ```

    KUBERNETES MANIFEST:
    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: sentinel-ml
      namespace: sentinel-ml
    spec:
      replicas: 3
      selector:
        matchLabels:
          app: sentinel-ml
      template:
        metadata:
          labels:
            app: sentinel-ml
        spec:
          securityContext:
            runAsNonRoot: true
            runAsUser: 1001
            fsGroup: 1001
          containers:
            - name: sentinel-ml
              image: govcloud.ecr.aws/sentinel-ml:v2.4
              ports:
                - containerPort: 8080
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities:
                  drop: [ALL]
              resources:
                requests: {cpu: "250m", memory: "512Mi"}
                limits:   {cpu: "1000m", memory: "2Gi"}
              env:
                - name: MODEL_PATH
                  valueFrom:
                    configMapKeyRef:
                      name: sentinel-ml-config
                      key: model_path
                - name: LOG_LEVEL
                  valueFrom:
                    configMapKeyRef:
                      name: sentinel-ml-config
                      key: log_level
              livenessProbe:
                httpGet: {path: /health, port: 8080}
                initialDelaySeconds: 15
                periodSeconds: 20
              readinessProbe:
                httpGet: {path: /health, port: 8080}
                initialDelaySeconds: 5
                periodSeconds: 10
              volumeMounts:
                - name: tmp
                  mountPath: /tmp
          volumes:
            - name: tmp
              emptyDir: {}
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: sentinel-ml
      namespace: sentinel-ml
    spec:
      selector:
        app: sentinel-ml
      ports:
        - port: 80
          targetPort: 8080
      type: ClusterIP
    ---
    # INNOVATION: PodDisruptionBudget protects HA during cluster maintenance
    apiVersion: policy/v1
    kind: PodDisruptionBudget
    metadata:
      name: sentinel-ml-pdb
      namespace: sentinel-ml
    spec:
      minAvailable: 2
      selector:
        matchLabels:
          app: sentinel-ml
    ```

    SECURITY JUSTIFICATION:
    - Non-root user (UID 1001): STIG V-242415 prohibits container execution as root. Root containers
      can modify host files via volume mounts — UID 1001 eliminates this privilege escalation path.
    - readOnlyRootFilesystem: Prevents an attacker who achieves code execution from writing malicious
      binaries to the container filesystem. /tmp emptyDir provides the only write path needed by uvicorn.
    - drop: [ALL] capabilities: Default Linux capabilities include CAP_NET_BIND (bind ports <1024)
      and CAP_SYS_CHROOT. Dropping all prevents container escape via kernel capability abuse,
      aligned with STIG V-242423 and DoD Cloud Security Guide Section 4.3.""",

    "IRON STACK": """\
    DOCKERFILE (multi-stage, container.builder validated, IaC Scanner: 1 warning — no multi-stage
    originally, corrected before submission):

    ```dockerfile
    FROM python:3.11-slim AS builder
    WORKDIR /build
    COPY requirements.txt .
    RUN pip install --no-cache-dir --target=/build/deps -r requirements.txt

    FROM python:3.11-slim
    RUN useradd -u 1001 -r appuser
    WORKDIR /app
    COPY --from=builder /build/deps /usr/local/lib/python3.11/site-packages
    COPY app/ .
    COPY model/sentinel-ml-v2.4.pkl /app/model/sentinel-ml-v2.4.pkl
    ENV MODEL_PATH=/app/model/sentinel-ml-v2.4.pkl LOG_LEVEL=INFO WORKERS=4
    EXPOSE 8080
    HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1
    USER appuser
    CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
    ```

    K8S MANIFEST:
    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: sentinel-ml
    spec:
      replicas: 3
      template:
        spec:
          containers:
            - name: sentinel-ml
              image: sentinel-ml:v2.4
              securityContext:
                runAsNonRoot: true
                runAsUser: 1001
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities:
                  drop: [ALL]
              resources:
                requests: {cpu: "250m", memory: "512Mi"}
                limits:   {cpu: "1000m", memory: "2Gi"}
              livenessProbe:
                httpGet: {path: /health, port: 8080}
              readinessProbe:
                httpGet: {path: /health, port: 8080}
              envFrom:
                - configMapRef:
                    name: sentinel-ml-config
              volumeMounts:
                - name: tmp
                  mountPath: /tmp
          volumes:
            - name: tmp
              emptyDir: {}
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: sentinel-ml
    spec:
      selector:
        app: sentinel-ml
      ports:
        - port: 80
          targetPort: 8080
      type: ClusterIP
    ```

    SECURITY JUSTIFICATION:
    - Non-root UID 1001: Prevents privilege escalation via volume mounts per STIG V-242415.
    - readOnlyRootFilesystem: Blocks runtime binary writes — attacker code execution can't persist.
    - drop: [ALL]: Removes default kernel capabilities reducing blast radius of any container escape.""",

    "SIGNAL DRIFT": """\
    DOCKERFILE (IaC Scanner found 2 issues — missing multi-stage build and missing HEALTHCHECK):

    ```dockerfile
    FROM python:3.11-slim
    WORKDIR /app
    RUN useradd -u 1001 appuser
    COPY requirements.txt .
    RUN pip install -r requirements.txt
    COPY . .
    ENV MODEL_PATH=/app/model/sentinel-ml-v2.4.pkl
    EXPOSE 8080
    USER appuser
    CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
    ```

    Note: Did not include HEALTHCHECK — should be added. Not using multi-stage build.

    K8S MANIFEST:
    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: sentinel-ml
    spec:
      replicas: 3
      template:
        spec:
          containers:
            - name: sentinel-ml
              image: sentinel-ml:v2.4
              securityContext:
                runAsNonRoot: true
                runAsUser: 1001
              resources:
                requests: {cpu: "250m", memory: "512Mi"}
                limits:   {cpu: "1000m", memory: "2Gi"}
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: sentinel-ml
    spec:
      ports:
        - port: 80
          targetPort: 8080
      type: ClusterIP
    ```

    SECURITY JUSTIFICATION:
    - Non-root user prevents privilege escalation attacks.
    - Resource limits prevent denial of service.
    - Slim base image reduces attack surface.""",

    "ZERO BINARY": """\
    DOCKERFILE:
    ```dockerfile
    FROM python:3.11
    WORKDIR /app
    COPY requirements.txt .
    RUN pip install -r requirements.txt
    COPY . .
    ENV MODEL_PATH=/app/model/sentinel-ml-v2.4.pkl
    EXPOSE 8080
    CMD ["python", "-m", "uvicorn", "main:app", "--port", "8080"]
    ```

    K8S MANIFEST:
    ```yaml
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: sentinel-ml
    spec:
      replicas: 3
      template:
        spec:
          containers:
            - name: sentinel-ml
              image: sentinel-ml:latest
              ports:
                - containerPort: 8080
              resources:
                limits: {cpu: "1000m", memory: "2Gi"}
    ---
    apiVersion: v1
    kind: Service
    metadata:
      name: sentinel-ml
    spec:
      ports:
        - port: 80
          targetPort: 8080
      type: ClusterIP
    ```

    The container should work for deployment. Security settings can be added later.""",

    }

    print()
    results_inj4 = {}
    for t in teams:
        tname = t["name"]
        rlist = [log_receipt(SID, t["team_id"], tool) for tool in inj4_receipts[tname]]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj4["inject_id"], session_id=SID,
            response_text=inj4_responses[tname], receipts=rlist, time_taken_s=280,
        )
        results_inj4[tname] = result
        show_score(tname, result)

    show_lb(SID)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("INJECT 05 — IaC + Network Lock (T+105 min)")
    print("  AI tools: iac.scanner · network.topology · knowledge.search")
    print("  Scoring:  base=175 · receipt_gate=True · finetune=False")
    print("  Task:     Terraform (EKS namespace + IRSA) + K8s NetworkPolicy (5 ZT rules)")

    inj5 = all_injects["inject-05-iac-network-lock"]
    dispatch_inject(inj5["inject_id"])
    print(f"\n  Dispatched: {inj5['inject_id'][:8]}...")

    inj5_receipts = {
        "FORGE VANGUARD": ["iac.scanner", "network.topology", "knowledge.search"],
        "IRON STACK":     ["iac.scanner", "network.topology"],
        "SIGNAL DRIFT":   ["iac.scanner"],
        "ZERO BINARY":    [],
    }

    inj5_responses = {

    "FORGE VANGUARD": """\
    TERRAFORM (IaC Scanner: 0 findings. Network Topology: policy graph validated.
    Knowledge Search: IRSA trust condition format confirmed from KB-AWS-IRSA-07):

    ```hcl
    variable "kms_key_arn"        {}
    variable "oidc_provider_arn"  {}
    variable "region"             { default = "us-gov-west-1" }

    resource "kubernetes_namespace" "sentinel_ml" {
      metadata {
        name = "sentinel-ml"
        labels = { "app.kubernetes.io/managed-by" = "terraform" }
      }
    }

    data "aws_iam_policy_document" "irsa_trust" {
      statement {
        effect  = "Allow"
        actions = ["sts:AssumeRoleWithWebIdentity"]
        principals {
          type        = "Federated"
          identifiers = [var.oidc_provider_arn]
        }
        condition {
          test     = "StringEquals"
          variable = "sts.amazonaws.com:sub"
          values   = ["system:serviceaccount:sentinel-ml:sentinel-ml-sa"]
        }
        condition {
          test     = "StringEquals"
          variable = "sts.amazonaws.com:aud"
          values   = ["sts.amazonaws.com"]
        }
      }
    }

    resource "aws_iam_role" "sentinel_ml_inference" {
      name               = "sentinel-ml-inference-role"
      assume_role_policy = data.aws_iam_policy_document.irsa_trust.json
    }

    resource "aws_iam_role_policy" "sentinel_ml_least_privilege" {
      name = "sentinel-ml-min-policy"
      role = aws_iam_role.sentinel_ml_inference.id
      policy = jsonencode({
        Version = "2012-10-17"
        Statement = [
          {
            Effect   = "Allow"
            Action   = ["s3:GetObject"]
            Resource = "arn:aws-us-gov:s3:::sentinel-ml-artifacts-govcloud/models/sentinel-ml/*"
          },
          {
            Effect   = "Allow"
            Action   = ["kms:Decrypt"]
            Resource = var.kms_key_arn
          }
        ]
      })
    }

    resource "kubernetes_service_account" "sentinel_ml_sa" {
      metadata {
        name      = "sentinel-ml-sa"
        namespace = kubernetes_namespace.sentinel_ml.metadata[0].name
        annotations = {
          "eks.amazonaws.com/role-arn" = aws_iam_role.sentinel_ml_inference.arn
        }
      }
    }
    ```

    NETWORK POLICY (5/5 ZT rules — Network Topology confirmed no path violations):
    ```yaml
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata:
      name: sentinel-ml-zero-trust
      namespace: sentinel-ml
    spec:
      podSelector: {}
      policyTypes: [Ingress, Egress]
      ingress:
        # Rule 2: allow from api-gateway only
        - from:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: ingress
              podSelector:
                matchLabels:
                  app: api-gateway
          ports:
            - port: 8080
              protocol: TCP
      egress:
        # Rule 3: allow DNS (UDP + TCP — both required for large DNS responses)
        - to:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: kube-system
              podSelector:
                matchLabels:
                  k8s-app: kube-dns
          ports:
            - port: 53
              protocol: UDP
            - port: 53
              protocol: TCP
        # Rule 4: allow Prometheus scrape
        - to:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: monitoring
              podSelector:
                matchLabels:
                  app: prometheus
          ports:
            - port: 9090
              protocol: TCP
        # Rule 5: no internet egress — absence of ipBlock 0.0.0.0/0 enforces block
    ```

    THREAT MODEL:
    - LATERAL MOVEMENT via SSRF: Compromised sentinel-ml pod issues HTTP request to internal
      service at 10.x.x.x:8080. Blocked by Egress deny-all — no egress rule covers
      arbitrary internal IPs. Only kube-dns and Prometheus are reachable.
    - DNS EXFILTRATION: Attacker encodes data in DNS TXT queries to a C2 resolver at
      8.8.8.8:53. Blocked by DNS egress rule — only kube-dns (kube-system/kube-dns pods)
      is allowed. External DNS resolvers are unreachable.
    - UNAUTHORIZED INFERENCE CALL: Attacker pod in namespace "default" calls sentinel-ml
      at port 8080 to extract model behavior. Blocked by Ingress rule — only pods labeled
      app=api-gateway in namespace "ingress" can reach port 8080.
    - METADATA SERVICE ATTACK (IMDSv1): Compromised pod issues GET http://169.254.169.254/
      to harvest AWS credentials from EC2 instance metadata. Blocked by Egress deny-all —
      169.254.x.x link-local addresses are not covered by any allow rule.""",

    "IRON STACK": """\
    TERRAFORM (IaC Scanner: 1 finding — missing aud condition on IRSA trust, corrected):

    ```hcl
    variable "kms_key_arn"       {}
    variable "oidc_provider_arn" {}

    resource "kubernetes_namespace" "sentinel_ml" {
      metadata { name = "sentinel-ml" }
    }

    data "aws_iam_policy_document" "irsa_trust" {
      statement {
        effect  = "Allow"
        actions = ["sts:AssumeRoleWithWebIdentity"]
        principals {
          type        = "Federated"
          identifiers = [var.oidc_provider_arn]
        }
        condition {
          test     = "StringEquals"
          variable = "sts.amazonaws.com:sub"
          values   = ["system:serviceaccount:sentinel-ml:sentinel-ml-sa"]
        }
      }
    }

    resource "aws_iam_role" "sentinel_ml" {
      name               = "sentinel-ml-inference-role"
      assume_role_policy = data.aws_iam_policy_document.irsa_trust.json
    }

    resource "aws_iam_role_policy" "sentinel_ml_policy" {
      role = aws_iam_role.sentinel_ml.id
      policy = jsonencode({
        Statement = [
          { Effect = "Allow", Action = ["s3:GetObject"],
            Resource = "arn:aws-us-gov:s3:::sentinel-ml-artifacts-govcloud/models/sentinel-ml/*" },
          { Effect = "Allow", Action = ["kms:Decrypt"],
            Resource = var.kms_key_arn }
        ]
      })
    }

    resource "kubernetes_service_account" "sa" {
      metadata {
        name      = "sentinel-ml-sa"
        namespace = "sentinel-ml"
        annotations = { "eks.amazonaws.com/role-arn" = aws_iam_role.sentinel_ml.arn }
      }
    }
    ```

    NETWORK POLICY:
    ```yaml
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata:
      name: sentinel-ml-zt
      namespace: sentinel-ml
    spec:
      podSelector: {}
      policyTypes: [Ingress, Egress]
      ingress:
        - from:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: ingress
              podSelector:
                matchLabels:
                  app: api-gateway
          ports:
            - port: 8080
      egress:
        - to:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: kube-system
              podSelector:
                matchLabels:
                  k8s-app: kube-dns
          ports:
            - port: 53
              protocol: UDP
            - port: 53
              protocol: TCP
        - to:
            - namespaceSelector:
                matchLabels:
                  kubernetes.io/metadata.name: monitoring
              podSelector:
                matchLabels:
                  app: prometheus
          ports:
            - port: 9090
    ```

    THREAT MODEL:
    - SSRF lateral movement blocked by egress deny-all.
    - Unauthorized inference calls blocked by ingress allow rule (api-gateway only).
    - DNS exfiltration blocked by DNS-only egress allow.
    - IMDSv2 bypass attempt blocked — link-local addresses unreachable via deny-all egress.""",

    "SIGNAL DRIFT": """\
    TERRAFORM (IaC Scanner: 2 findings — wildcard IRSA subject, missing aud condition):

    ```hcl
    variable "kms_key_arn"       {}
    variable "oidc_provider_arn" {}

    resource "kubernetes_namespace" "sentinel_ml" {
      metadata { name = "sentinel-ml" }
    }

    resource "aws_iam_role" "sentinel_ml" {
      name = "sentinel-ml-inference-role"
      assume_role_policy = jsonencode({
        Statement = [{
          Effect    = "Allow"
          Action    = "sts:AssumeRoleWithWebIdentity"
          Principal = { Federated = var.oidc_provider_arn }
          Condition = {
            StringLike = {
              "sts.amazonaws.com:sub" = "system:serviceaccount:sentinel-ml:*"
            }
          }
        }]
      })
    }

    resource "aws_iam_role_policy" "policy" {
      role = aws_iam_role.sentinel_ml.id
      policy = jsonencode({
        Statement = [
          { Effect = "Allow", Action = "s3:GetObject",
            Resource = "arn:aws-us-gov:s3:::sentinel-ml-artifacts-govcloud/*" },
          { Effect = "Allow", Action = "kms:Decrypt",
            Resource = var.kms_key_arn }
        ]
      })
    }

    resource "kubernetes_service_account" "sa" {
      metadata {
        name      = "sentinel-ml-sa"
        namespace = "sentinel-ml"
        annotations = { "eks.amazonaws.com/role-arn" = aws_iam_role.sentinel_ml.arn }
      }
    }
    ```

    NETWORK POLICY (missing DNS TCP, only UDP included):
    ```yaml
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata:
      name: sentinel-ml-policy
      namespace: sentinel-ml
    spec:
      podSelector: {}
      policyTypes: [Ingress, Egress]
      ingress:
        - from:
            - podSelector:
                matchLabels:
                  app: api-gateway
          ports:
            - port: 8080
      egress:
        - to:
            - podSelector:
                matchLabels:
                  k8s-app: kube-dns
          ports:
            - port: 53
              protocol: UDP
    ```

    THREAT MODEL:
    - Unauthorized access blocked by ingress rule.
    - Data exfiltration reduced by egress default deny.
    - IRSA ensures only the service account can assume the IAM role.
    - KMS encryption protects model artifacts at rest.""",

    "ZERO BINARY": """\
    TERRAFORM:
    ```hcl
    resource "kubernetes_namespace" "sentinel_ml" {
      metadata { name = "sentinel-ml" }
    }

    resource "aws_iam_role" "sentinel_ml" {
      name = "sentinel-ml-inference-role"
      assume_role_policy = jsonencode({
        Statement = [{
          Effect = "Allow"
          Action = "sts:AssumeRoleWithWebIdentity"
          Principal = { Federated = "arn:aws-us-gov:iam::123456789:oidc-provider/eks-oidc" }
        }]
      })
    }

    resource "aws_iam_role_policy" "policy" {
      role = aws_iam_role.sentinel_ml.id
      policy = jsonencode({
        Statement = [
          { Effect = "Allow", Action = ["s3:*"],
            Resource = "arn:aws-us-gov:s3:::sentinel-ml-artifacts-govcloud/*" }
        ]
      })
    }
    ```

    NETWORK POLICY:
    ```yaml
    apiVersion: networking.k8s.io/v1
    kind: NetworkPolicy
    metadata:
      name: sentinel-ml-policy
      namespace: sentinel-ml
    spec:
      podSelector: {}
      policyTypes: [Ingress]
      ingress:
        - from:
            - podSelector:
                matchLabels:
                  app: api-gateway
    ```

    THREAT MODEL:
    - Network policy restricts access to api-gateway pods only.
    - IAM role controls access to S3 artifacts.
    - Namespace isolation provides blast radius containment.""",

    }

    print()
    results_inj5 = {}
    for t in teams:
        tname = t["name"]
        rlist = [log_receipt(SID, t["team_id"], tool) for tool in inj5_receipts[tname]]
        result = engine.submit_response(
            team_id=t["team_id"], inject_id=inj5["inject_id"], session_id=SID,
            response_text=inj5_responses[tname], receipts=rlist, time_taken_s=420,
        )
        results_inj5[tname] = result
        show_score(tname, result)

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("FINAL LEADERBOARD")
    engine.end_session(SID)
    show_lb(SID)

    ribbons = award_ribbons(SID)
    print("\n  RIBBONS:")
    for rid, winner in ribbons.items():
        if winner:
            print(f"    🎗  {rid:<22} → {winner['team_name']}  ({winner['value']})")

    # ══════════════════════════════════════════════════════════════════════════════
    hdr("MECHANICS VERIFICATION SUMMARY")
    print()
    print("  RECEIPT GATE (Inject 01, base=100):")
    print(f"    FORGE VANGUARD  (2 receipts) → base={results_inj1['FORGE VANGUARD']['gated_base_pts']}  (expected 100)")
    print(f"    IRON STACK      (1 receipt ) → base={results_inj1['IRON STACK']['gated_base_pts']}   (expected 70)")
    print(f"    SIGNAL DRIFT    (1 receipt ) → base={results_inj1['SIGNAL DRIFT']['gated_base_pts']}   (expected 70)")
    print(f"    ZERO BINARY     (0 receipts) → base={results_inj1['ZERO BINARY']['gated_base_pts']}   (expected 40)")
    print()
    print("  FINETUNE GATE (Inject 03, base=200):")
    print(f"    FORGE VANGUARD  (has finetune.deploy) → base={results_inj3['FORGE VANGUARD']['gated_base_pts']}  (expected 200)")
    print(f"    IRON STACK      (has finetune.deploy) → base={results_inj3['IRON STACK']['gated_base_pts']}  (expected 200)")
    print(f"    SIGNAL DRIFT    (no  finetune.deploy) → base={results_inj3['SIGNAL DRIFT']['gated_base_pts']}  (expected 100 — capped 50%)")
    print(f"    ZERO BINARY     (0 receipts, no ft  ) → base={results_inj3['ZERO BINARY']['gated_base_pts']}  (expected 80  — 40% * 200)")
    print()
    print("  ACADEMY BONUSES:")
    all_results = [results_inj1, results_inj2, results_inj3, results_inj4, results_inj5]
    for t in teams:
        tname   = t["name"]
        total_b = sum(r.get(tname, {}).get("academy_bonus", 0) for r in all_results)
        print(f"    {tname:<22}  level={t['level']:<12}  missions={len(t['missions'])}  "
              f"total_academy_bonus_awarded≈{total_b}")
    print()
    print(f"  Session ID for review: {SID}")
    print(f"  Facilitator console:   http://localhost:5050/gameday/session/{SID}/facilitate")
    print(f"  Live leaderboard:      http://localhost:5050/gameday/leaderboard/{SID}")
    print(f"  Results + AAR:         http://localhost:5050/gameday/session/{SID}/results")
    print()


if __name__ == "__main__":
    main()
