"""Second-opinion regime read from the vendored agiprolabs `regime-detection` skill.

Adds volatility percentile, ADX, Hurst exponent and a four-quadrant label to every scan plan.
Informational by default; set Config.overlay_block_ranging=True to also block entries when
the overlay says the market is not trending.
"""
from __future__ import annotations

import math

import pandas as pd

from . import vendor


def overlay(df: pd.DataFrame) -> dict:
    rd = vendor.regime_detection()
    hi, lo, cl = df["high"], df["low"], df["close"]
    vol_pct = float(rd.compute_atr_percentile(hi, lo, cl).iloc[-1])
    adx = float(rd.compute_adx(hi, lo, cl).iloc[-1])
    hurst = float(rd.compute_hurst(cl.iloc[-250:]))
    tdir = int(rd.compute_trend_direction(cl).iloc[-1])
    vol_pct = 0.5 if math.isnan(vol_pct) else vol_pct
    adx = 0.0 if math.isnan(adx) else adx
    out = rd.classify_regime(vol_pct, adx, hurst, tdir)
    out.update({"atr_percentile": round(vol_pct, 2), "adx": round(adx, 1), "hurst": round(hurst, 2),
                "source": "agiprolabs regime-detection (vendored)"})
    return out
