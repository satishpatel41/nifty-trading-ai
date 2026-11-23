"""Daily-bar backtest of the strategy on the Nifty index (futures-style P&L in index points)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .sizing import futures_lots
from .strategy import add_signals, assess_posture, new_position, step_position


@dataclass
class Result:
    trades: pd.DataFrame
    equity: pd.Series
    stats: dict


def run_backtest(df: pd.DataFrame, cfg: Config, vix: pd.Series | None = None,
                 use_regime_gate: bool = True) -> Result:
    f = add_signals(df, cfg)
    idx = f.index
    equity = cfg.capital
    eq_curve = pd.Series(index=idx, dtype=float)
    trades: list[dict] = []
    pos: dict | None = None
    meta: dict = {}
    rt_cost = cfg.cost_points + 2 * cfg.slippage_points

    for i in range(len(f)):
        bar = f.iloc[i]
        date = idx[i]

        # 1) manage open position on this bar
        if pos is not None:
            px, why = step_position(pos, bar, cfg)
            if px is not None:
                pnl_pts = pos["side"] * (px - pos["entry"]) - rt_cost
                pnl = pnl_pts * meta["lots"] * cfg.lot_size
                equity += pnl
                trades.append({
                    "entry_date": meta["date"], "exit_date": date, "side": pos["side"],
                    "entry": pos["entry"], "exit": px, "lots": meta["lots"],
                    "r": pnl_pts / pos["risk_pts"], "pnl": pnl, "reason": why,
                    "bars": pos["bars"],
                })
                pos = None

        # 2) open new position at this bar's open from previous bar's signal
        if pos is None and i > 0 and i > 210:
            prev = f.iloc[i - 1]
            side = 1 if prev["long_sig"] else (-1 if prev["short_sig"] else 0)
            if side:
                v = float(vix.reindex([idx[i - 1]]).iloc[0]) if vix is not None and idx[i - 1] in vix.index else None
                post = assess_posture(prev, cfg, v)
                allowed = (post.allow_long if side == 1 else post.allow_short) or not use_regime_gate
                if allowed:
                    entry = float(bar["open"]) + side * cfg.slippage_points
                    p = new_position(side, entry, float(prev["atr"]), cfg)
                    lots = futures_lots(cfg, entry, p["risk_pts"], post.size_mult if use_regime_gate else 1.0)
                    if lots > 0:
                        pos, meta = p, {"lots": lots, "date": date}
                        # same-bar stop/target check is skipped for the entry bar's open fill;
                        # re-check this bar's range against the stop conservatively
                        px, why = step_position(pos, bar, cfg)
                        if px is not None:
                            pnl_pts = pos["side"] * (px - pos["entry"]) - rt_cost
                            pnl = pnl_pts * lots * cfg.lot_size
                            equity += pnl
                            trades.append({"entry_date": date, "exit_date": date, "side": side,
                                           "entry": pos["entry"], "exit": px, "lots": lots,
                                           "r": pnl_pts / pos["risk_pts"], "pnl": pnl,
                                           "reason": why, "bars": 0})
                            pos = None
        eq_curve.iloc[i] = equity

    tdf = pd.DataFrame(trades)
    return Result(tdf, eq_curve, _stats(tdf, eq_curve, cfg))


def _stats(t: pd.DataFrame, eq: pd.Series, cfg: Config) -> dict:
    if t.empty:
        return {"trades": 0}
    wins, losses = t[t.r > 0], t[t.r <= 0]
    gross_w, gross_l = wins.pnl.sum(), -losses.pnl.sum()
    peak = eq.cummax()
    dd = ((eq - peak) / peak).min()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    return {
        "trades": int(len(t)),
        "win_rate_pct": round(100 * len(wins) / len(t), 1),
        "avg_r": round(float(t.r.mean()), 3),
        "profit_factor": round(float(gross_w / gross_l), 2) if gross_l > 0 else float("inf"),
        "total_return_pct": round(100 * (eq.iloc[-1] / cfg.capital - 1), 1),
        "cagr_pct": round(100 * ((eq.iloc[-1] / cfg.capital) ** (1 / years) - 1), 1),
        "max_drawdown_pct": round(100 * float(dd), 1),
        "longs": int((t.side == 1).sum()),
        "shorts": int((t.side == -1).sum()),
        "avg_bars_held": round(float(t.bars.mean()), 1),
    }


def walk_forward(df: pd.DataFrame, cfg: Config, splits: int = 4, **kw) -> pd.DataFrame:
    """Non-overlapping consecutive windows: a quick check that edge isn't one lucky period."""
    rows = []
    n = len(df)
    size = n // splits
    for k in range(splits):
        chunk = df.iloc[max(0, k * size - 250): (k + 1) * size]  # 250-bar warmup
        res = run_backtest(chunk, cfg, **kw)
        s = res.stats
        s["window"] = f"{df.index[k * size].date()}..{df.index[min((k + 1) * size, n) - 1].date()}"
        rows.append(s)
    return pd.DataFrame(rows)
