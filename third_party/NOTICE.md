# Third-party notices

This project builds on three MIT-licensed open-source skill collections. None of them
covers India or Upstox; they are generic or US/crypto oriented, so they are used as follows.

| Source | Commit | How it is used here |
|---|---|---|
| [agiprolabs/claude-trading-skills](https://github.com/agiprolabs/claude-trading-skills) (c) 2026 AGIPro | `981e1d7` | **Vendored and imported as code** (`third_party/agiprolabs-claude-trading-skills/`): `regime-detection` (ADX/ATR-percentile/Hurst regime overlay), `walk-forward-validation` (walk-forward splitter, Deflated Sharpe Ratio, PBO), `risk-management` (drawdown analysis and response ladder). Unmodified copies. |
| [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (c) 2026 Jake Schincariol | `3d0f4ef` | **Ideas re-implemented** (not copied; the originals are US 5-minute-session, long-only, Alpaca tools): `trader-timemachine` -> `nifty_upstox/timemachine.py` (bootstrap replay of a planned trade, stop checked before target); `trader-broker` -> `nifty_upstox/tickets.py` (explicit per-order "yes" tied to an order fingerprint before any live order). |
| [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) (c) 2026 TraderMonty | `4080042` | **Workflow ideas re-implemented**: exposure-coach -> regime posture (`strategy.py`), position-sizer -> `sizing.py`, drawdown-circuit-breaker and pre-trade-discipline-gate -> `risk.py`, trader-memory-core -> `journal.py`. Its tools were not vendored: the thesis store is USD-only and US-calendar based, so INR index futures would need a currency workaround. |

Full license texts: `third_party/agiprolabs-claude-trading-skills/LICENSE.md` and `third_party/licenses/`.
