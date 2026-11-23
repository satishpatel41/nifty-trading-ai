"""Synthetic regime-switching index data for offline demos/tests. NOT real market behaviour."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_data(n: int = 2500, seed: int = 7, start: float = 10000.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift_states = np.array([0.0007, -0.0006, 0.0001])
    vol_states = np.array([0.008, 0.013, 0.007])
    state, rets = 0, []
    for _ in range(n):
        if rng.random() < 0.015:
            state = rng.integers(0, 3)
        rets.append(rng.normal(drift_states[state], vol_states[state]))
    close = start * np.exp(np.cumsum(rets))
    open_ = np.r_[start, close[:-1]] * (1 + rng.normal(0, 0.002, n))
    rng_ = np.abs(rng.normal(0, 0.006, n)) + 0.002
    high = np.maximum(open_, close) * (1 + rng_)
    low = np.minimum(open_, close) * (1 - rng_)
    idx = pd.bdate_range("2014-01-01", periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 0.0}, index=idx)
