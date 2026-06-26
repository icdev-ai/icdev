"""PNA predictor runner — BGP + Capacity with kanban integration."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[6]))


def run_pna_analysis():
    """Run BGP and Capacity predictors and surface high-risk findings."""
    results = []

    # TODO: Run BGP predictor
    # from tools.network.bgp_predictor import BGPPredictor
    # bgp = BGPPredictor()
    # bgp_result = bgp.predict(as_number=64512, prefix="10.0.0.0/8", lookback_hours=24)
    # results.append(("BGP", bgp_result))

    # TODO: Run Capacity predictor for top-3 links
    # from tools.network.capacity_predictor import CapacityPredictor
    # cap = CapacityPredictor()
    # cap_result = cap.predict_top_n(n=3, lookback_hours=24)
    # results.append(("Capacity", cap_result))

    # TODO: Print risk summary

    # TODO: For high-risk findings, write to kanban backlog

    return results


if __name__ == "__main__":
    run_pna_analysis()
