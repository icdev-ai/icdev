import math

DECAY_LAMBDA = 0.05  # exponential half-life constant
WEIGHT_FLOOR = 0.01  # minimum weight at extreme age


def compute_signal_decay(age_days: float, decay_lambda: float = DECAY_LAMBDA) -> float:
    if age_days == 0:
        return 1.0
    weight = math.exp(-decay_lambda * age_days)
    return max(weight, WEIGHT_FLOOR)


def batch_decay(signals: list[dict]) -> list[dict]:
    for s in signals:
        s['signal_decay_weight'] = compute_signal_decay(s['age_days'])
    return signals
