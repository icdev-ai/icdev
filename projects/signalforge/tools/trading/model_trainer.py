#!/usr/bin/env python3
"""SignalForge Model Trainer — XGBoost/LightGBM classifier training pipeline.

CUI // SP-CTI

Trains a gradient boosting classifier on labeled feature data with
walk-forward cross-validation and feature importance analysis.

Usage:
    python tools/trading/model_trainer.py --data data/processed/labeled.csv --json
    python tools/trading/model_trainer.py --data data/processed/labeled.csv --output models/best_model.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


def _load_config() -> dict:
    config_path = Path(__file__).parent.parent.parent / "args" / "trading_config.yaml"
    if config_path.exists():
        try:
            import yaml
            with open(config_path) as f:
                return yaml.safe_load(f)
        except ImportError:
            pass
    return {
        "model": {
            "type": "xgboost",
            "max_depth": 6,
            "n_estimators": 500,
            "learning_rate": 0.01,
            "min_child_weight": 5,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "eval_metric": "mlogloss",
            "early_stopping_rounds": 50,
            "cv_folds": 5,
        },
    }


FEATURE_COLS = [
    "close", "high", "low", "volume",
    "sma_20", "sma_50", "sma_200",
    "rsi_14",
    "macd_line", "macd_signal", "macd_hist",
    "bb_upper_dist", "bb_lower_dist",
    "atr_14", "adx_14",
    "stoch_k", "stoch_d",
    "volume_sma_ratio",
    "dist_to_support", "dist_to_resistance",
    "regime",
    "hour_of_day", "day_of_week",
    "close_vs_vwap",
    "bar_range_vs_atr",
]


def _get_model(config: dict):
    """Create model instance based on config."""
    model_type = config.get("model", {}).get("type", "xgboost")
    params = {
        "max_depth": config.get("model", {}).get("max_depth", 6),
        "n_estimators": config.get("model", {}).get("n_estimators", 500),
        "learning_rate": config.get("model", {}).get("learning_rate", 0.01),
        "min_child_weight": config.get("model", {}).get("min_child_weight", 5),
        "subsample": config.get("model", {}).get("subsample", 0.8),
        "colsample_bytree": config.get("model", {}).get("colsample_bytree", 0.8),
        "eval_metric": config.get("model", {}).get("eval_metric", "mlogloss"),
        "random_state": 42,
        "n_jobs": -1,
    }

    if model_type == "lightgbm":
        try:
            import lightgbm as lgb
            return lgb.LGBMClassifier(
                max_depth=params["max_depth"],
                n_estimators=params["n_estimators"],
                learning_rate=params["learning_rate"],
                min_child_weight=params["min_child_weight"],
                subsample=params["subsample"],
                colsample_bytree=params["colsample_bytree"],
                random_state=params["random_state"],
                n_jobs=params["n_jobs"],
                verbose=-1,
            )
        except ImportError:
            print("LightGBM not installed, falling back to XGBoost", file=sys.stderr)

    import xgboost as xgb
    return xgb.XGBClassifier(
        max_depth=params["max_depth"],
        n_estimators=params["n_estimators"],
        learning_rate=params["learning_rate"],
        min_child_weight=params["min_child_weight"],
        subsample=params["subsample"],
        colsample_bytree=params["colsample_bytree"],
        eval_metric=params["eval_metric"],
        random_state=params["random_state"],
        n_jobs=params["n_jobs"],
        use_label_encoder=False,
        objective="multi:softprob",
        num_class=3,
    )


def walk_forward_split(df: pd.DataFrame, config: dict | None = None):
    """Generate walk-forward train/test splits (no data leakage).

    Each split trains on N months, tests on the next M months,
    then slides forward by step_months.

    Yields:
        (train_idx, test_idx) tuples
    """
    if config is None:
        config = _load_config()

    backtest = config.get("backtest", {})
    train_months = backtest.get("walk_forward_train_months", 6)
    test_months = backtest.get("walk_forward_test_months", 1)
    step_months = backtest.get("walk_forward_step_months", 1)

    if "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"])
    else:
        # Use index as proxy — assume sequential bars
        ts = pd.Series(pd.date_range("2024-01-01", periods=len(df), freq="min"))

    min_date = ts.min()
    max_date = ts.max()

    train_start = min_date
    while True:
        train_end = train_start + pd.DateOffset(months=train_months)
        test_end = train_end + pd.DateOffset(months=test_months)

        if train_end >= max_date:
            break

        train_mask = (ts >= train_start) & (ts < train_end)
        test_mask = (ts >= train_end) & (ts < test_end)

        train_idx = df.index[train_mask]
        test_idx = df.index[test_mask]

        if len(train_idx) > 0 and len(test_idx) > 0:
            yield train_idx, test_idx

        train_start += pd.DateOffset(months=step_months)


def train_model(df: pd.DataFrame, config: dict | None = None) -> dict:
    """Train model with walk-forward cross-validation.

    Args:
        df: DataFrame with feature columns and 'label' column
        config: Optional config override

    Returns:
        Dict with model, metrics, feature importance
    """
    if config is None:
        config = _load_config()

    # Identify available features
    available_features = [c for c in FEATURE_COLS if c in df.columns]
    if not available_features:
        raise ValueError(f"No feature columns found. Expected: {FEATURE_COLS}")

    X = df[available_features].copy()
    y = df["label"].copy()

    # Map labels: -1,0,1 -> 0,1,2 for XGBoost
    label_map = {-1: 0, 0: 1, 1: 2}
    label_map_inv = {v: k for k, v in label_map.items()}
    y_mapped = y.map(label_map)

    # Replace NaN/inf
    X = X.replace([np.inf, -np.inf], np.nan).fillna(0)

    # Walk-forward CV
    fold_metrics = []
    best_model = None
    best_accuracy = 0

    splits = list(walk_forward_split(df, config))
    if not splits:
        # Fallback: simple train/test split
        split_point = int(len(df) * 0.8)
        splits = [(df.index[:split_point], df.index[split_point:])]

    for fold_idx, (train_idx, test_idx) in enumerate(splits):
        X_train = X.loc[train_idx]
        X_test = X.loc[test_idx]
        y_train = y_mapped.loc[train_idx]
        y_test = y_mapped.loc[test_idx]

        model = _get_model(config)

        early_stop = config.get("model", {}).get("early_stopping_rounds", 50)

        try:
            model.fit(
                X_train, y_train,
                eval_set=[(X_test, y_test)],
                verbose=False,
            )
        except TypeError:
            # Some versions don't support eval_set
            model.fit(X_train, y_train)

        preds = model.predict(X_test)
        accuracy = (preds == y_test).mean()

        # Per-class accuracy
        from collections import Counter
        pred_counts = Counter(preds)
        actual_counts = Counter(y_test.values)

        fold_metrics.append({
            "fold": fold_idx + 1,
            "train_size": len(train_idx),
            "test_size": len(test_idx),
            "accuracy": round(float(accuracy), 4),
            "pred_distribution": {
                label_map_inv.get(k, k): int(v)
                for k, v in sorted(pred_counts.items())
            },
            "actual_distribution": {
                label_map_inv.get(k, k): int(v)
                for k, v in sorted(actual_counts.items())
            },
        })

        if accuracy > best_accuracy:
            best_accuracy = accuracy
            best_model = model

    # Train final model on all data
    final_model = _get_model(config)
    try:
        final_model.fit(X, y_mapped, verbose=False)
    except TypeError:
        final_model.fit(X, y_mapped)

    # Feature importance
    importances = final_model.feature_importances_
    feature_importance = sorted(
        zip(available_features, importances.tolist()),
        key=lambda x: x[1],
        reverse=True,
    )

    avg_accuracy = np.mean([m["accuracy"] for m in fold_metrics]) if fold_metrics else 0

    return {
        "model": final_model,
        "label_map": label_map,
        "label_map_inv": label_map_inv,
        "features": available_features,
        "fold_metrics": fold_metrics,
        "avg_accuracy": round(float(avg_accuracy), 4),
        "best_fold_accuracy": round(float(best_accuracy), 4),
        "feature_importance": [
            {"feature": name, "importance": round(imp, 4)}
            for name, imp in feature_importance
        ],
    }


def save_model(model, output_path: str | Path, features: list, label_map: dict,
               config: dict | None = None):
    """Save trained model to file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    model_type = type(model).__name__

    if "XGB" in model_type:
        model.save_model(str(output_path))
    elif "LGBM" in model_type:
        import joblib
        joblib.dump(model, str(output_path))
    else:
        import joblib
        joblib.dump(model, str(output_path))

    # Save metadata alongside model
    meta_path = output_path.with_suffix(".meta.json")
    meta = {
        "model_type": model_type,
        "features": features,
        "label_map": {str(k): v for k, v in label_map.items()},
        "config": config or {},
        "saved_at": pd.Timestamp.now().isoformat(),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)


def load_model(model_path: str | Path):
    """Load a trained model and its metadata."""
    model_path = Path(model_path)
    meta_path = model_path.with_suffix(".meta.json")

    meta = {}
    if meta_path.exists():
        with open(meta_path) as f:
            meta = json.load(f)

    model_type = meta.get("model_type", "XGBClassifier")

    if "XGB" in model_type:
        import xgboost as xgb
        model = xgb.XGBClassifier()
        model.load_model(str(model_path))
    else:
        import joblib
        model = joblib.load(str(model_path))

    return model, meta


def main():
    parser = argparse.ArgumentParser(description="SignalForge Model Trainer")
    parser.add_argument("--data", required=True, help="Path to labeled features CSV")
    parser.add_argument("--output", help="Path to save model")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON")
    args = parser.parse_args()

    df = pd.read_csv(args.data)

    if "label" not in df.columns:
        print("Error: 'label' column not found in data", file=sys.stderr)
        sys.exit(1)

    config = _load_config()
    start_time = time.time()

    result = train_model(df, config)
    elapsed = round(time.time() - start_time, 2)

    if args.output:
        save_model(
            result["model"], args.output,
            result["features"], result["label_map"], config,
        )

    if args.json:
        output = {
            "status": "success",
            "elapsed_sec": elapsed,
            "features_used": len(result["features"]),
            "avg_accuracy": result["avg_accuracy"],
            "best_fold_accuracy": result["best_fold_accuracy"],
            "num_folds": len(result["fold_metrics"]),
            "fold_metrics": result["fold_metrics"],
            "top_features": result["feature_importance"][:10],
        }
        if args.output:
            output["model_path"] = args.output
        print(json.dumps(output, indent=2))
    else:
        print(f"Model trained in {elapsed}s")
        print(f"  Features: {len(result['features'])}")
        print(f"  Avg accuracy: {result['avg_accuracy']:.1%}")
        print(f"  Best fold accuracy: {result['best_fold_accuracy']:.1%}")
        print(f"  Walk-forward folds: {len(result['fold_metrics'])}")
        print(f"\nTop 5 features:")
        for fi in result["feature_importance"][:5]:
            print(f"  {fi['feature']}: {fi['importance']:.4f}")
        if args.output:
            print(f"\nModel saved to: {args.output}")


if __name__ == "__main__":
    main()
