# Nifty 50 swing strategy + Upstox (paper trading by default)

A daily-bar **trend-pullback** strategy for Nifty 50 with buy/sell signals, a regime gate,
risk-based sizing, circuit breakers, a trade journal, a backtester and an Upstox adapter.

Built by porting the *workflow ideas* from
[tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills)
(market-regime gate, position sizer, drawdown circuit breaker, pre-trade discipline gate,
trader-memory journal, backtest-before-trade). That repo is US-equity oriented, has no Upstox
or Indian-market support, and says it is "not a broker execution platform", so none of its
code is used directly. It is a process toolkit, and this project follows the same stance:
**signals and gates are for human review; nothing trades live unless you explicitly enable it.**

## Strategy (`strategy.py`)

| Piece | Rule |
|---|---|
| Regime | Up: close > EMA50 > EMA200, EMA50 rising. Down: mirror. Else CHOP = no new trades. ADX >= 18. India VIX >= 22 halves size, >= 30 blocks entries. |
| Long setup | In uptrend, >= 2 of last 5 closes below EMA20, then close back above EMA20. |
| Short setup | Mirror image in downtrend. |
| Entry | Next session open (after-close scan, morning execution). |
| Stop / target | Stop = 2 x ATR14. Target = 2.5R. Stop to breakeven after +1R. |
| Other exits | 12-bar time stop; close loses EMA50. |
| Sizing | Risk 1% of capital per trade, whole lots, margin cap 60%. |
| Instruments | `futures` (long and short) or `options_buy` (ATM CE for longs, ATM PE for shorts, premium stop 35%). |
| Circuit breakers | Daily loss 2%, weekly drawdown 5%, 3 consecutive losses -> 3-day cooldown, `state/KILL` file, entry window 09:20-15:00 IST. |

Defaults live in `config.py`; override with `--config my.json`.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                    # 10 tests
python -m nifty_upstox demo            # offline, synthetic data (checks code, not edge)

export UPSTOX_ACCESS_TOKEN=...         # daily token from Upstox login flow
python -m nifty_upstox fetch --start 2016-01-01 --out data/nifty_daily.csv
python -m nifty_upstox backtest --csv data/nifty_daily.csv --walk-forward 4

# daily routine (paper)
python -m nifty_upstox scan            # ~16:00 IST: builds state/plan.json
python -m nifty_upstox execute         # ~09:20 IST next day: PAPER orders + journal
```

Live (real money) requires **both** `--live` and `NIFTY_LIVE_CONFIRM=YES`.

## Read before going live

- **Capital vs lot size.** At ~Rs 5L, a 2 x ATR Nifty futures stop does not fit 1% risk in even one
  lot, so the bot will skip every signal. Use roughly Rs 25L+ for futures, or `options_buy`.
  Lot size is read from Upstox's instrument master when a token is set; the config value is only a fallback.
- **Backtest limits.** Uses index points as a futures proxy; it does not model option premiums,
  theta, expiry rolls, STT/brokerage exactly, or intraday gaps beyond the open. `cost_points` and
  `slippage_points` are rough placeholders. Always run on your own fetched data and walk-forward.
- **The synthetic demo means nothing** about profitability.
- **Live order code is not verified against a real account.** Endpoints follow Upstox's public v3
  API (order placement via `api-hft.upstox.com`), but check them against the current docs, run in
  paper mode first, then 1 lot. SEBI's retail-algo rules may require algo registration/identifiers on
  orders; extra headers can be supplied as JSON in `UPSTOX_ORDER_EXTRA_HEADERS`.
- Stop-loss moves (breakeven) are tracked in `positions.json` but the broker-side SL order is not
  modified automatically; the bot exits via the next scan if the stop is hit.
- Upstox access tokens expire daily; automate the login flow yourself.
- Not financial advice. Derivatives can lose more than your margin in gap events.

## Layout

```
nifty_upstox/
  config.py     all parameters          indicators.py  EMA/ATR/RSI/ADX
  strategy.py   regime + signals + exits   sizing.py     lots from risk budget
  backtest.py   backtest + walk-forward    risk.py       circuit breakers / gates
  executor.py   scan -> plan -> execute    journal.py    JSONL trade journal
  upstox.py     candles, instruments, orders   broker.py  paper / live adapters
  synthetic.py  offline test data          cli.py        commands
tests/test_core.py
```
