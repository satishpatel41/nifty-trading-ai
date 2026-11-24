from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

NIFTY_KEY = "NSE_INDEX|Nifty 50"
VIX_KEY = "NSE_INDEX|India VIX"


@dataclass
class Config:
    # --- account / risk ---
    # NOTE: at ~Rs 5L, a 2xATR Nifty futures stop (~Rs 30k+/lot) exceeds 1% risk, so no lot fits.
    # Use >= ~Rs 25L for futures mode, or instrument_mode="options_buy" for smaller accounts.
    capital: float = 2_500_000.0
    risk_pct: float = 0.01            # risk per trade as fraction of capital
    max_open: int = 1
    allow_short: bool = True

    # --- strategy (daily bars) ---
    adx_min: float = 18.0
    atr_stop_mult: float = 2.0        # initial stop = entry -/+ mult * ATR14
    rr_target: float = 2.5            # target in R multiples
    be_r: float = 1.0                 # move stop to breakeven after +be_r R
    max_hold: int = 12                # time stop in bars
    vix_reduce: float = 22.0          # halve size above this India VIX
    vix_block: float = 30.0           # no new trades above this

    # --- costs (index points, round trip) used by backtest ---
    cost_points: float = 4.0
    slippage_points: float = 2.0

    # --- instrument ---
    instrument_mode: str = "futures"  # "futures" | "options_buy"
    lot_size: int = 65                # overridden from Upstox instrument master when available
    margin_rate: float = 0.12         # rough futures margin as fraction of notional
    max_margin_pct: float = 0.6       # cap on margin used vs capital
    opt_sl_pct: float = 0.35          # option premium stop (fraction of premium)
    opt_min_dte: int = 3              # skip expiries closer than this
    opt_max_spread_pct: float = 0.03  # block if bid-ask spread > 3% of mid
    opt_min_oi: int = 100_000         # block thin contracts
    opt_min_volume: int = 10_000
    overlay_block_ranging: bool = False  # also block entries when regime overlay says not trending
    replay_runs: int = 10_000         # trade replays attached to each planned entry

    # --- circuit breakers (drawdown-circuit-breaker style) ---
    daily_loss_pct: float = 0.02
    weekly_dd_pct: float = 0.05
    max_consec_losses: int = 3
    cooldown_days: int = 3

    @classmethod
    def load(cls, path: str | None) -> "Config":
        if not path:
            return cls()
        data = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {sorted(unknown)}")
        return cls(**data)

    def dump(self, path: str) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2))
