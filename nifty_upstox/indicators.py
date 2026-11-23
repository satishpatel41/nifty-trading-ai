from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def features(df: pd.DataFrame) -> pd.DataFrame:
    """Add indicator columns. Input needs open/high/low/close, ascending date index."""
    f = df.copy()
    f["ema20"] = ema(f["close"], 20)
    f["ema50"] = ema(f["close"], 50)
    f["ema200"] = ema(f["close"], 200)
    f["atr"] = atr(f, 14)
    f["rsi"] = rsi(f["close"], 14)
    f["adx"] = adx(f, 14)
    slope = f["ema50"] - f["ema50"].shift(10)
    f["trend_up"] = (f["close"] > f["ema50"]) & (f["ema50"] > f["ema200"]) & (slope > 0)
    f["trend_dn"] = (f["close"] < f["ema50"]) & (f["ema50"] < f["ema200"]) & (slope < 0)
    return f
