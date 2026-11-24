"""Replay a planned trade thousands of times before risking money.

Idea adapted from Jakeschincariol/day-trader-skill `trader-timemachine` (MIT), which bootstraps
the rest of a US session from the stock's own recent 5-minute bars. This version does the same
for Nifty DAILY bars: each replay walks forward up to max_hold sessions, drawing past bars as
ratios to the previous close (open, high, low, close) and rescaling them to the current price.
The stop is checked before the target inside a bar, so a bar touching both counts as the stop.
Gaps through the stop are filled at the open, like the live exit logic.

The replays assume future bars look like a random draw from the recent past. They show the
odds the plan implies under that assumption; they do not predict the market.
"""
from __future__ import annotations

import random

import numpy as np
import pandas as pd

from .config import Config
from .strategy import new_position


def _ratio_pool(df: pd.DataFrame, lookback: int) -> np.ndarray:
    d = df.iloc[-(lookback + 1):]
    prev = d["close"].shift(1)
    pool = pd.concat([d["open"] / prev, d["high"] / prev, d["low"] / prev, d["close"] / prev], axis=1).dropna()
    return pool.to_numpy()


def replay(df: pd.DataFrame, side: int, entry: float, atr: float, cfg: Config,
           replays: int = 10_000, lookback: int = 750, seed: int = 19) -> dict:
    pool = _ratio_pool(df, lookback)
    if len(pool) < 50:
        raise ValueError("Need at least 50 bars of history for a replay")
    rng = random.Random(seed)
    base = new_position(side, entry, atr, cfg)
    risk, stop0, target = base["risk_pts"], base["stop"], base["target"]
    rs, outcomes = [], {"target": 0, "stop": 0, "time": 0, "breakeven": 0}

    for _ in range(replays):
        price, stop, be_done = entry, stop0, False
        r_out, why = None, None
        for bar_i in range(cfg.max_hold):
            o_r, h_r, l_r, c_r = pool[rng.randrange(len(pool))]
            o, h, l, c = price * o_r, price * h_r, price * l_r, price * c_r
            if side == 1:
                if o <= stop:
                    px, why = o, "stop"
                elif l <= stop:
                    px, why = stop, "stop"
                elif h >= target:
                    px, why = target, "target"
                else:
                    px = None
            else:
                if o >= stop:
                    px, why = o, "stop"
                elif h >= stop:
                    px, why = stop, "stop"
                elif l <= target:
                    px, why = target, "target"
                else:
                    px = None
            if px is not None:
                if why == "stop" and be_done and abs(px - entry) < 1e-9:
                    why = "breakeven"
                r_out = side * (px - entry) / risk
                break
            be_level = entry + side * cfg.be_r * risk
            if not be_done and ((side == 1 and h >= be_level) or (side == -1 and l <= be_level)):
                stop, be_done = entry, True
            price = c
        if r_out is None:
            r_out, why = side * (price - entry) / risk, "time"
        outcomes[why] += 1
        rs.append(r_out)

    a = np.array(rs)
    n = float(replays)
    return {
        "replays": replays, "seed": seed, "lookback_bars": len(pool),
        "p_target": round(outcomes["target"] / n, 3), "p_stop": round(outcomes["stop"] / n, 3),
        "p_breakeven": round(outcomes["breakeven"] / n, 3), "p_time_exit": round(outcomes["time"] / n, 3),
        "p_profit": round(float((a > 0).mean()), 3),
        "expected_r": round(float(a.mean()), 3),
        "r_p05_p50_p95": [round(float(np.percentile(a, q)), 2) for q in (5, 50, 95)],
        "caveat": "Random redraws of recent bars; costs and slippage not included.",
    }
