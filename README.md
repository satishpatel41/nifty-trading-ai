# Nifty 50 swing strategy + Upstox (paper trading by default)

A daily-bar **trend-pullback** strategy for Nifty 50 with buy/sell signals, a regime gate,
risk-based sizing, circuit breakers, a trade journal, a backtester and an Upstox adapter.

Built on three MIT-licensed skill collections (details and commits in `third_party/NOTICE.md`).
None covers India or Upstox, so they are used like this:

| Source | What we took | How |
|---|---|---|
| [agiprolabs/claude-trading-skills](https://github.com/agiprolabs/claude-trading-skills) | regime detection (ADX / ATR percentile / Hurst), walk-forward splitter, Deflated Sharpe, PBO, drawdown analysis | **vendored, imported as code** (`third_party/`) |
| [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) | trade "time machine" replay, per-order approval ticket | ideas re-implemented for Nifty daily bars |
| [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) | regime posture, position sizing, circuit breakers, journal | ideas re-implemented (its tools are USD/US-calendar based) |

The [CASHPARENCY post](https://www.cashparency.org/post/claude-ai-option-trading) is a sales page for a paid,
analysis-only Nifty options skill. It claims no results and discloses no rules; the only thing used
is its general idea of an option liquidity check and an explicit "no trade" outcome (`liquidity.py`, thresholds are ours).

**Signals and gates are for human review; nothing trades live unless you explicitly enable it.**

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
python -m pytest -q                    # 18 tests
python -m nifty_upstox demo            # offline, synthetic data (checks code, not edge)

export UPSTOX_ACCESS_TOKEN=...         # daily token from Upstox login flow
python -m nifty_upstox fetch --start 2016-01-01 --out data/nifty_daily.csv
python -m nifty_upstox backtest --csv data/nifty_daily.csv --walk-forward 4

python -m nifty_upstox validate --csv data/nifty_daily.csv   # is the edge real? (see below)

# daily routine (paper)
python -m nifty_upstox scan            # ~16:00 IST: builds state/plan.json
python -m nifty_upstox execute         # ~09:20 IST next day: PAPER orders + journal
```

Live (real money) requires `--live`, `NIFTY_LIVE_CONFIRM=YES`, **and** an explicit approval per order:

```bash
python -m nifty_upstox tickets show                       # plain-English ticket with replay odds
python -m nifty_upstox tickets approve --index 0 --reply "yes"   # "ok"/"sure"/"yes to all" are refused
```

Approval is tied to a fingerprint of that exact order, so it does not carry over to another day's signal.

## Extra checks added from the skill repos

- **`validate`**: Deflated Sharpe (discounts for the 18 parameter variants tried), PBO across that
  neighbourhood, purged walk-forward out-of-sample windows, drawdown analysis. Prints a plain verdict.
  Run it on real Nifty data and treat "NO EDGE DEMONSTRATED" as a real answer.
- **Regime overlay**: every scan plan carries a second regime read (volatility percentile, ADX, Hurst).
  Informational unless `overlay_block_ranging` is true.
- **Time machine**: each planned entry gets 10,000 replays (random redraws of recent daily bars) giving
  odds of target / stop / time exit. Not a forecast; costs not included.
- **Option liquidity gate** (`options_buy` only): blocks wide spreads, thin open interest/volume, missing quotes,
  and fails closed if the option chain call fails. Thresholds are defaults to tune.

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
- The option-chain field names used by the liquidity gate are unverified against a live account.
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
  validate.py   DSR / PBO / walk-forward   overlay.py     regime overlay (vendored skill)
  timemachine.py trade replay              tickets.py     per-order live approval
  liquidity.py  option liquidity gate      vendor.py      loads third_party scripts
third_party/    vendored agiprolabs skills + licenses + NOTICE.md
tests/test_core.py  tests/test_integrations.py
```
