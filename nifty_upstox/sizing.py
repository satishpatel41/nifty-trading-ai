"""Risk-based position sizing in lots (position-sizer style: risk first, size second)."""
from __future__ import annotations

import math

from .config import Config


def futures_lots(cfg: Config, entry: float, stop_points: float, size_mult: float = 1.0,
                 lot_size: int | None = None) -> int:
    lot = lot_size or cfg.lot_size
    if stop_points <= 0:
        return 0
    risk_amt = cfg.capital * cfg.risk_pct * size_mult
    by_risk = math.floor(risk_amt / (stop_points * lot))
    margin_per_lot = entry * lot * cfg.margin_rate
    by_margin = math.floor(cfg.capital * cfg.max_margin_pct / margin_per_lot) if margin_per_lot else 0
    return max(0, min(by_risk, by_margin))


def option_lots(cfg: Config, premium: float, size_mult: float = 1.0,
                lot_size: int | None = None) -> int:
    """Buying options: worst case loss is the premium stop, not the full premium."""
    lot = lot_size or cfg.lot_size
    loss_per_lot = premium * cfg.opt_sl_pct * lot
    if loss_per_lot <= 0:
        return 0
    by_risk = math.floor(cfg.capital * cfg.risk_pct * size_mult / loss_per_lot)
    by_cash = math.floor(cfg.capital * cfg.max_margin_pct / (premium * lot))
    return max(0, min(by_risk, by_cash))
