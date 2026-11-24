import json
from datetime import datetime

from nifty_upstox import tickets
from nifty_upstox.broker import PaperBroker
from nifty_upstox.config import Config
from nifty_upstox.executor import execute, scan
from nifty_upstox.liquidity import chain_market_data, liquidity_blocks
from nifty_upstox.overlay import overlay
from nifty_upstox.strategy import add_signals
from nifty_upstox.synthetic import make_data
from nifty_upstox.timemachine import replay
from nifty_upstox.validate import validate


def test_vendored_regime_overlay_runs():
    ov = overlay(make_data(600))
    assert ov["quadrant"] in {"quiet_trend", "volatile_trend", "quiet_range", "volatile_range", "uncertain"}
    assert 0.0 <= ov["hurst"] <= 1.0


def test_replay_deterministic_and_probabilities_sum():
    df, cfg = make_data(800), Config()
    a = replay(df, 1, 10000.0, 150.0, cfg, replays=2000, seed=5)
    b = replay(df, 1, 10000.0, 150.0, cfg, replays=2000, seed=5)
    assert a == b
    total = a["p_target"] + a["p_stop"] + a["p_breakeven"] + a["p_time_exit"]
    assert abs(total - 1.0) < 0.01


def test_replay_wider_target_hits_less_often():
    df = make_data(800)
    near = replay(df, 1, 10000.0, 150.0, Config(rr_target=1.0), replays=3000)
    far = replay(df, 1, 10000.0, 150.0, Config(rr_target=4.0), replays=3000)
    assert near["p_target"] > far["p_target"]


def test_ticket_approval_rules(tmp_path):
    en = {"side": 1, "signal_date": "2026-11-20", "close": 24000.0, "atr": 180.0, "size_mult": 1.0}
    for bad in ("ok", "sure", "yes to all", "y", ""):
        ok, _ = tickets.approve(tmp_path, en, "futures", bad)
        assert not ok, bad
    assert not tickets.is_approved(tmp_path, en, "futures")
    ok, _ = tickets.approve(tmp_path, en, "futures", "yes")
    assert ok and tickets.is_approved(tmp_path, en, "futures")
    # approval does not carry over to a different order
    assert not tickets.is_approved(tmp_path, {**en, "signal_date": "2026-11-21"}, "futures")
    assert not tickets.is_approved(tmp_path, en, "options_buy")


def test_liquidity_gate():
    cfg = Config()
    good = {"bid_price": 100.0, "ask_price": 101.0, "oi": 500000, "volume": 90000}
    assert liquidity_blocks(good, cfg) == []
    wide = {**good, "ask_price": 110.0}
    assert any("spread" in b for b in liquidity_blocks(wide, cfg))
    assert any("two-sided" in b for b in liquidity_blocks({"oi": 1, "volume": 1}, cfg))
    rows = [{"call_options": {"instrument_key": "K1", "market_data": good}, "put_options": {"instrument_key": "K2"}}]
    assert chain_market_data(rows, "K1")[0] == good
    assert chain_market_data(rows, "NOPE") == (None, None)


def test_scan_attaches_overlay_and_replay(tmp_path):
    df, cfg = make_data(), Config(capital=2_000_000, replay_runs=500)
    f = add_signals(df, cfg)
    i = next(i for i in range(300, len(f)) if f["long_sig"].iloc[i] and f["trend_up"].iloc[i])
    plan = scan(cfg, df.iloc[: i + 1], str(tmp_path))
    assert "regime_overlay" in plan and plan["entries"][0]["timemachine"]["replays"] == 500


def test_live_path_requires_approval(tmp_path):
    class FakeLive(PaperBroker):
        live = True

    df, cfg = make_data(), Config(capital=2_000_000, replay_runs=200)
    f = add_signals(df, cfg)
    i = next(i for i in range(300, len(f)) if f["long_sig"].iloc[i] and f["trend_up"].iloc[i])
    plan = scan(cfg, df.iloc[: i + 1], str(tmp_path))
    now = datetime(2026, 11, 24, 10, 0)
    msgs = execute(cfg, str(tmp_path), FakeLive(tmp_path), None, now)
    assert msgs[0].startswith("NOT APPROVED")
    assert json.loads((tmp_path / "positions.json").read_text()) == [] if (tmp_path / "positions.json").exists() else True


def test_validate_report_shape():
    rep = validate(make_data(2000), Config(capital=2_500_000))
    for k in ("backtest", "deflated_sharpe", "pbo", "walk_forward", "drawdown", "verdict"):
        assert k in rep
    assert rep["deflated_sharpe"]["trials"] >= 18


def test_auth_helpers(tmp_path, monkeypatch):
    import pytest
    from nifty_upstox import auth
    assert auth.code_from_redirect("https://x.test/cb?code=abc123&state=1") == "abc123"
    with pytest.raises(ValueError):
        auth.code_from_redirect("https://x.test/cb?error=denied")
    assert "client_id=K" in auth.login_url("K", "https://x.test/cb")
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    assert auth.load_token(tmp_path) is None
    p = auth.save_token(tmp_path, {"access_token": "TOK"})
    assert oct(p.stat().st_mode)[-3:] == "600" and auth.load_token(tmp_path) == "TOK"
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "ENV")
    assert auth.load_token(tmp_path) == "ENV"
