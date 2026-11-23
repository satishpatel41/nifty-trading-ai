import json
from datetime import date, datetime

import pandas as pd
import pytest

from nifty_upstox.backtest import run_backtest
from nifty_upstox.broker import PaperBroker
from nifty_upstox.config import Config
from nifty_upstox.executor import execute, scan
from nifty_upstox.risk import RiskState
from nifty_upstox.sizing import futures_lots, option_lots
from nifty_upstox.strategy import add_signals, new_position, step_position
from nifty_upstox.synthetic import make_data
from nifty_upstox.upstox import parse_candles, resolve_option


def bar(o, h, l, c, ema50=0.0):
    return pd.Series({"open": o, "high": h, "low": l, "close": c, "ema50": ema50})


def test_long_stop_hit_before_target_same_bar():
    cfg = Config()
    pos = new_position(1, 100.0, 5.0, cfg)  # risk 10, stop 90, target 125
    px, why = step_position(pos, bar(100, 130, 85, 100), cfg)
    assert (px, why) == (90.0, "stop")


def test_short_target_and_gap():
    cfg = Config()
    pos = new_position(-1, 100.0, 5.0, cfg)  # stop 110, target 75
    assert step_position(dict(pos), bar(99, 100, 70, 80), cfg) == (75.0, "target")
    assert step_position(dict(pos), bar(115, 120, 110, 112), cfg) == (115.0, "stop_gap")


def test_breakeven_and_time_stop():
    cfg = Config(max_hold=2)
    pos = new_position(1, 100.0, 5.0, cfg)
    assert step_position(pos, bar(100, 112, 99, 105, ema50=90), cfg) == (None, None)
    assert pos["stop"] == 100.0 and pos["be_done"]
    step_position(pos, bar(105, 108, 102, 106, ema50=90), cfg)
    assert pos["pending_exit"] == "time_stop"
    assert step_position(pos, bar(107, 110, 104, 108), cfg) == (107.0, "time_stop")


def test_sizing():
    cfg = Config(capital=500_000, risk_pct=0.01, lot_size=65)
    assert futures_lots(cfg, 24000, 300) == 0       # 5000 risk / (300*65)=0.25 lots
    assert futures_lots(cfg, 24000, 60) == 1         # 5000/(60*65)=1.28 -> 1
    assert option_lots(cfg, 100.0) == 2              # 5000 / (100*.35*65=2275) = 2.19 -> 2
    assert futures_lots(cfg, 24000, 0) == 0


def test_backtest_runs_and_gate_is_not_looser():
    df = make_data()
    cfg = Config()
    gated = run_backtest(df, cfg)
    ungated = run_backtest(df, cfg, use_regime_gate=False)
    assert gated.stats["trades"] > 10
    assert gated.stats["trades"] <= ungated.stats["trades"]
    assert not gated.equity.isna().any()


def test_no_lookahead_signals_only_use_past():
    df = make_data(600)
    full = add_signals(df, Config())
    part = add_signals(df.iloc[:400], Config())
    assert full["long_sig"].iloc[:400].equals(part["long_sig"])
    assert full["short_sig"].iloc[:400].equals(part["short_sig"])


def test_risk_gates(tmp_path):
    cfg = Config(capital=100_000, daily_loss_pct=0.02, max_consec_losses=2)
    r = RiskState(tmp_path)
    ok_time = datetime(2026, 11, 24, 10, 0)  # Tuesday
    assert r.check_new_entry(cfg, ok_time, 0) == []
    assert any("outside" in b for b in r.check_new_entry(cfg, datetime(2026, 11, 24, 8, 0), 0))
    assert any("max_open" in b for b in r.check_new_entry(cfg, ok_time, 1))
    r.record_trade(ok_time.date(), -2500, cfg)
    assert any("daily loss" in b for b in r.check_new_entry(cfg, ok_time, 0))
    r.record_trade(ok_time.date(), -100, cfg)
    assert any("cooldown" in b for b in r.check_new_entry(cfg, datetime(2026, 11, 25, 10, 0), 0))
    (tmp_path / "KILL").write_text("")
    assert any("KILL" in b for b in r.check_new_entry(cfg, datetime(2026, 12, 20, 10, 0), 0))


def test_parse_candles_newest_first():
    payload = {"data": {"candles": [
        ["2026-11-20T00:00:00+05:30", 2, 3, 1, 2.5, 0, 0],
        ["2026-11-19T00:00:00+05:30", 1, 2, 0.5, 1.5, 0, 0]]}}
    df = parse_candles(payload)
    assert list(df.index.day) == [19, 20] and df.close.iloc[-1] == 2.5


def test_resolve_option_picks_atm_nearest_expiry():
    ms = lambda d: int(datetime(*d).timestamp() * 1000)
    inst = [{"segment": "NSE_FO", "underlying_symbol": "NIFTY", "instrument_type": "CE",
             "instrument_key": f"K{s}{e}", "expiry": ms((2026, 12, e)), "strike_price": s, "lot_size": 65}
            for s in (24000, 24050, 24100) for e in (1, 8)]
    got = resolve_option(inst, 24060, 1, date(2026, 11, 23), 3)
    assert got["strike_price"] == 24050 and got["instrument_key"].endswith("1")


def test_scan_execute_paper_roundtrip(tmp_path):
    df = make_data()
    cfg = Config(capital=2_000_000)
    # find a bar that produces a tradable signal, then scan as-of that bar
    f = add_signals(df, cfg)
    sig_idx = next(i for i in range(300, len(f)) if f["long_sig"].iloc[i] and f["trend_up"].iloc[i])
    plan = scan(cfg, df.iloc[: sig_idx + 1], str(tmp_path))
    assert plan["entries"], plan
    msgs = execute(cfg, str(tmp_path), PaperBroker(tmp_path), None, datetime(2026, 11, 24, 10, 0))
    assert msgs[0].startswith("ENTRY"), msgs
    pos = json.loads((tmp_path / "positions.json").read_text())
    assert len(pos) == 1 and pos[0]["pos"]["stop"] < pos[0]["pos"]["entry"]
    # second scan on the next bar steps the position (no double-step on the same bar)
    scan(cfg, df.iloc[: sig_idx + 2], str(tmp_path))
    scan(cfg, df.iloc[: sig_idx + 2], str(tmp_path))
    pos = json.loads((tmp_path / "positions.json").read_text())
    assert pos[0]["pos"]["bars"] <= 1
