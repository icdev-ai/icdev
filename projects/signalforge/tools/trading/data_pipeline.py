#!/usr/bin/env python3
"""SignalForge Data Pipeline — end-to-end OHLCV processing orchestrator.

CUI // SP-CTI

Orchestrates the full data pipeline:
1. Load raw OHLCV CSV (NT8 export format)
2. Filter to RTH (09:30-16:00 ET)
3. Compute 25 features
4. Generate training labels
5. Output labeled feature dataset

Usage:
    python tools/trading/data_pipeline.py --input data/raw/ES_1min.csv --output data/processed/ --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

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
    return {}


def run_pipeline(
    input_path: str | Path,
    output_dir: str | Path | None = None,
    label_method: str = "tpsl",
    rth_only: bool = True,
    config: dict | None = None,
) -> dict:
    """Run the full data pipeline.

    Args:
        input_path: Path to raw OHLCV CSV
        output_dir: Directory for output files
        label_method: Labeling method ('tpsl' or 'return')
        rth_only: Filter to RTH only
        config: Optional config override

    Returns:
        Pipeline results dict
    """
    if config is None:
        config = _load_config()

    from tools.trading.feature_engineer import load_ohlcv, filter_rth, compute_features
    from tools.trading.label_generator import generate_labels_tpsl, generate_labels_return

    results = {"stages": []}
    start_time = time.time()

    # Stage 1: Load raw data
    t0 = time.time()
    df = load_ohlcv(input_path)
    raw_rows = len(df)
    results["stages"].append({
        "stage": "load",
        "rows": raw_rows,
        "elapsed_sec": round(time.time() - t0, 2),
    })

    # Stage 2: RTH filter
    if rth_only:
        t0 = time.time()
        df = filter_rth(df)
        rth_rows = len(df)
        results["stages"].append({
            "stage": "rth_filter",
            "rows_before": raw_rows,
            "rows_after": rth_rows,
            "rows_removed": raw_rows - rth_rows,
            "elapsed_sec": round(time.time() - t0, 2),
        })
    else:
        rth_rows = raw_rows

    # Stage 3: Compute features
    t0 = time.time()
    features = compute_features(df, config.get("features"))
    features_clean = features.dropna()
    feature_cols = [c for c in features_clean.columns if c != "timestamp"]
    results["stages"].append({
        "stage": "features",
        "rows_before": rth_rows,
        "rows_after": len(features_clean),
        "feature_count": len(feature_cols),
        "warmup_dropped": rth_rows - len(features_clean),
        "elapsed_sec": round(time.time() - t0, 2),
    })

    # Stage 4: Generate labels
    t0 = time.time()
    if label_method == "tpsl":
        labels = generate_labels_tpsl(df, config)
    else:
        labels = generate_labels_return(df, config)

    # Align labels with features (both indexed on df.index)
    features_clean["label"] = labels.loc[features_clean.index]
    features_clean = features_clean.dropna(subset=["label"])
    features_clean["label"] = features_clean["label"].astype(int)

    label_counts = features_clean["label"].value_counts().to_dict()
    total = len(features_clean)
    results["stages"].append({
        "stage": "labels",
        "method": label_method,
        "total_labeled": total,
        "distribution": {
            "LONG": int(label_counts.get(1, 0)),
            "SHORT": int(label_counts.get(-1, 0)),
            "FLAT": int(label_counts.get(0, 0)),
        },
        "distribution_pct": {
            "LONG": round(label_counts.get(1, 0) / max(total, 1) * 100, 1),
            "SHORT": round(label_counts.get(-1, 0) / max(total, 1) * 100, 1),
            "FLAT": round(label_counts.get(0, 0) / max(total, 1) * 100, 1),
        },
        "elapsed_sec": round(time.time() - t0, 2),
    })

    # Stage 5: Save output
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.time()
        features_path = output_dir / "features.csv"
        labeled_path = output_dir / "labeled.csv"

        # Features only (no labels)
        features_only = features_clean.drop(columns=["label"])
        features_only.to_csv(features_path, index=False)

        # Labeled dataset (features + label)
        features_clean.to_csv(labeled_path, index=False)

        # Also save OHLCV alongside for backtesting
        ohlcv_path = output_dir / "ohlcv.csv"
        ohlcv_subset = df.loc[features_clean.index]
        ohlcv_subset.to_csv(ohlcv_path, index=False)

        results["stages"].append({
            "stage": "save",
            "features_path": str(features_path),
            "labeled_path": str(labeled_path),
            "ohlcv_path": str(ohlcv_path),
            "elapsed_sec": round(time.time() - t0, 2),
        })

    total_elapsed = round(time.time() - start_time, 2)

    results["summary"] = {
        "input_file": str(input_path),
        "raw_rows": raw_rows,
        "output_rows": len(features_clean),
        "feature_count": len(feature_cols),
        "label_method": label_method,
        "rth_only": rth_only,
        "total_elapsed_sec": total_elapsed,
    }

    if "timestamp" in features_clean.columns:
        results["summary"]["date_range"] = {
            "start": str(features_clean["timestamp"].iloc[0]),
            "end": str(features_clean["timestamp"].iloc[-1]),
        }

    return results


def main():
    parser = argparse.ArgumentParser(description="SignalForge Data Pipeline")
    parser.add_argument("--input", required=True, help="Path to raw OHLCV CSV")
    parser.add_argument("--output", help="Output directory for processed data")
    parser.add_argument("--method", choices=["tpsl", "return"], default="tpsl",
                        help="Labeling method (default: tpsl)")
    parser.add_argument("--no-rth", action="store_true", help="Skip RTH filtering")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON")
    args = parser.parse_args()

    config = _load_config()
    results = run_pipeline(
        input_path=args.input,
        output_dir=args.output,
        label_method=args.method,
        rth_only=not args.no_rth,
        config=config,
    )

    if args.json:
        print(json.dumps({"status": "success", **results}, indent=2))
    else:
        s = results["summary"]
        print(f"Data Pipeline Complete ({s['total_elapsed_sec']}s)")
        print(f"  Input: {s['input_file']}")
        print(f"  Raw rows: {s['raw_rows']}")
        print(f"  Output rows: {s['output_rows']}")
        print(f"  Features: {s['feature_count']}")
        print(f"  Label method: {s['label_method']}")
        print()
        for stage in results["stages"]:
            print(f"  [{stage['stage']}] {stage.get('elapsed_sec', 0)}s")
            if stage["stage"] == "labels":
                d = stage["distribution"]
                print(f"    LONG: {d['LONG']}  SHORT: {d['SHORT']}  FLAT: {d['FLAT']}")


if __name__ == "__main__":
    main()
