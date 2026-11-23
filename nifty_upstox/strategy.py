"""Trend-pullback strategy for Nifty 50 daily bars, gated by a market-regime filter.

Idea (long side; short is the mirror image):
  1. Regime: close > EMA50 > EMA200 and EMA50 rising (uptrend), ADX >= adx_min.
  2. Setup: price pulled back below EMA20 for >= 2 of the last 5 closes.
  3. Trigger: close back above EMA20 (pullback resumes the trend).
  4. Entry next session open; stop = entry - atr_stop_mult * ATR; target = rr_target R;
     stop to breakeven after be_r R; exit on time stop or when close loses EMA50.

The regime posture is modelled on the exposure-coach / market-regime idea from
tradermonty/claude-trading-skills: a gate that decides whether new risk is allowed at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .indicators import features


@dataclass
class Posture:
    state: str                 # UPTREND | DOWNTREND | CHOP | HIGH_VOL
    allow_long: bool
    allow_short: bool
    size_mult: float
    reasons: list[str] = field(default_factory=list)


def assess_posture(row: pd.Series, cfg: Config, vix: float | None = None) -> Posture:
    reasons: list[str] = []
    if bool(row["trend_up"]):
        state, long_ok, short_ok = "UPTREND", True, False
        reasons.append("close>EMA50>EMA200 and EMA50 rising")
    elif bool(row["trend_dn"]):
        state, long_ok, short_ok = "DOWNTREND", False, cfg.allow_short
        reasons.append("close<EMA50<EMA200 and EMA50 falling")
    else:
        state, long_ok, short_ok = "CHOP", False, False
        reasons.append("no clean trend: new entries blocked")

    if not np.isnan(row["adx"]) and row["adx"] < cfg.adx_min and state != "CHOP":
        long_ok = short_ok = False
        reasons.append(f"ADX {row['adx']:.1f} < {cfg.adx_min}: trend too weak")

    mult = 1.0
    if vix is not None:
        if vix >= cfg.vix_block:
            long_ok = short_ok = False
            state = "HIGH_VOL"
            reasons.append(f"India VIX {vix:.1f} >= {cfg.vix_block}: blocked")
        elif vix >= cfg.vix_reduce:
            mult = 0.5
            reasons.append(f"India VIX {vix:.1f} >= {cfg.vix_reduce}: half size")
    return Posture(state, long_ok, short_ok, mult, reasons)


def add_signals(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    f = features(df)
    below = f["close"] < f["ema20"]
    above = f["close"] > f["ema20"]
    pulled_dn = below.shift(1).astype(float).rolling(5).sum() >= 2
    pulled_up = above.shift(1).astype(float).rolling(5).sum() >= 2
    cross_up = above & below.shift(1, fill_value=False)
    cross_dn = below & above.shift(1, fill_value=False)
    adx_ok = f["adx"] >= cfg.adx_min
    f["long_sig"] = f["trend_up"] & pulled_dn & cross_up & adx_ok
    f["short_sig"] = (f["trend_dn"] & pulled_up & cross_dn & adx_ok) if cfg.allow_short else False
    return f


def new_position(side: int, entry: float, atr_val: float, cfg: Config) -> dict:
    risk = cfg.atr_stop_mult * atr_val
    return {
        "side": side,
        "entry": entry,
        "stop": entry - side * risk,
        "target": entry + side * cfg.rr_target * risk,
        "risk_pts": risk,
        "bars": 0,
        "be_done": False,
        "pending_exit": None,
    }


def step_position(pos: dict, bar: pd.Series, cfg: Config) -> tuple[float | None, str | None]:
    """Advance one daily bar. Mutates pos. Returns (exit_price, reason) if closed this bar.

    Conservative ordering: stop is checked before target on the same bar.
    Time/regime exits are flagged on the close and executed at the next open.
    """
    s = pos["side"]
    if pos.get("pending_exit"):
        return float(bar["open"]), pos["pending_exit"]

    o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
    stop, target = pos["stop"], pos["target"]
    if s == 1:
        if o <= stop:
            return o, "stop_gap"
        if l <= stop:
            return stop, "stop"
        if h >= target:
            return target, "target"
    else:
        if o >= stop:
            return o, "stop_gap"
        if h >= stop:
            return stop, "stop"
        if l <= target:
            return target, "target"

    pos["bars"] += 1
    be_level = pos["entry"] + s * cfg.be_r * pos["risk_pts"]
    if not pos["be_done"] and ((s == 1 and h >= be_level) or (s == -1 and l <= be_level)):
        pos["stop"] = pos["entry"]
        pos["be_done"] = True
    if pos["bars"] >= cfg.max_hold:
        pos["pending_exit"] = "time_stop"
    elif (s == 1 and c < bar["ema50"]) or (s == -1 and c > bar["ema50"]):
        pos["pending_exit"] = "lost_ema50"
    return None, None
