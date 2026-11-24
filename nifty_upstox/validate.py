"""Is the backtest edge real, or luck / curve-fit? Uses the vendored agiprolabs skills:
walk-forward-validation (purged walk-forward folds, Deflated Sharpe, PBO) and
risk-management (drawdown analysis).
"""
from __future__ import annotations

import itertools
from dataclasses import replace

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, skew

from . import vendor
from .backtest import run_backtest
from .config import Config

TRADING_DAYS = 252


def _daily_returns(equity: pd.Series) -> np.ndarray:
    return equity.pct_change().fillna(0.0).to_numpy()


def _sharpe(r: np.ndarray) -> float:
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0


def param_grid(cfg: Config) -> list[Config]:
    """Small, honest neighbourhood of the chosen parameters. Every variant counts as a trial."""
    out = []
    for stop, rr, hold in itertools.product((1.5, 2.0, 2.5), (2.0, 2.5, 3.0), (8, 12)):
        out.append(replace(cfg, atr_stop_mult=stop, rr_target=rr, max_hold=hold))
    return out


def validate(df: pd.DataFrame, cfg: Config, extra_trials: int = 0, vix: pd.Series | None = None) -> dict:
    ov, wf, dd = vendor.overfit_detector(), vendor.walk_forward(), vendor.drawdown_analyzer()

    base = run_backtest(df, cfg, vix=vix)
    rets = _daily_returns(base.equity)
    report: dict = {"backtest": base.stats}

    # --- Deflated Sharpe: discount the Sharpe for how many variants were tried ---
    grid = param_grid(cfg)
    n_trials = len(grid) + max(0, extra_trials)
    sr = _sharpe(rets)
    dsr = ov.deflated_sharpe_ratio(
        observed_sr=sr, num_trials=n_trials, backtest_length=len(rets),
        skewness=float(skew(rets)), kurtosis=float(kurtosis(rets, fisher=False)),
        annualization=float(np.sqrt(TRADING_DAYS)),
    )
    report["deflated_sharpe"] = {
        "observed_sharpe": round(sr, 2), "trials": n_trials,
        "dsr_probability": round(dsr.dsr_pvalue, 3), "significant": bool(dsr.is_significant),
    }

    # --- PBO across the parameter neighbourhood ---
    matrix = np.column_stack([_daily_returns(run_backtest(df, c, vix=vix).equity) for c in grid])
    keep = matrix.std(axis=0) > 0  # drop variants that never traded
    if keep.sum() >= 2:
        pbo = ov.probability_of_backtest_overfitting(matrix[:, keep])
        report["pbo"] = {"probability": round(pbo.pbo, 3), "overfit": bool(pbo.is_overfit),
                         "variants": int(keep.sum())}
    else:
        report["pbo"] = {"note": "fewer than 2 variants traded; cannot estimate"}

    # --- purged walk-forward: Sharpe in each out-of-sample window ---
    n = len(df)
    wf_cfg = wf.WalkForwardConfig(train_size=min(750, n // 2), test_size=250, step_size=250,
                                  window_type="expanding", purge_size=10, embargo_size=5)
    folds = []
    for fold in wf.WalkForwardValidator(wf_cfg).split(n, df.index):
        # warm-up bars before the test window so indicators are formed
        lo = max(0, int(fold.test_indices[0]) - 250)
        seg = df.iloc[lo: int(fold.test_indices[-1]) + 1]
        res = run_backtest(seg, cfg, vix=vix)
        test_eq = res.equity.iloc[-len(fold.test_indices):]
        folds.append({"test_start": str(df.index[fold.test_indices[0]].date()),
                      "test_end": str(df.index[fold.test_indices[-1]].date()),
                      "oos_sharpe": round(_sharpe(_daily_returns(test_eq)), 2),
                      "oos_return_pct": round(100 * (test_eq.iloc[-1] / test_eq.iloc[0] - 1), 1)})
    report["walk_forward"] = folds
    if folds:
        report["walk_forward_positive_folds"] = f"{sum(f['oos_return_pct'] > 0 for f in folds)}/{len(folds)}"

    # --- drawdown analysis + response ladder ---
    summ = dd.analyze_drawdowns(base.equity.to_numpy())
    level, _, advice = dd.drawdown_response(summ.max_drawdown)
    report["drawdown"] = {"max_drawdown_pct": round(100 * summ.max_drawdown, 1),
                          "longest_underwater_bars": int(summ.longest_underwater),
                          "worst_level": level, "advice_at_that_depth": advice}

    # --- plain verdict ---
    flags = []
    if not report["deflated_sharpe"]["significant"]:
        flags.append("Sharpe not significant after deflating for trials")
    if report["pbo"].get("overfit"):
        flags.append("PBO > 0.5: parameter choice likely overfit")
    pos = report.get("walk_forward_positive_folds", "0/0").split("/")
    if folds and int(pos[0]) * 2 < int(pos[1]):
        flags.append("fewer than half of out-of-sample windows were profitable")
    report["verdict"] = "NO EDGE DEMONSTRATED: " + "; ".join(flags) if flags else \
        "Passes these checks (necessary, not sufficient, for live trading)"
    return report
