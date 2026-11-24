from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd

from .backtest import run_backtest, walk_forward
from .config import NIFTY_KEY, VIX_KEY, Config
from .executor import drop_incomplete_bar, execute, scan
from .upstox import UpstoxClient


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["date"], index_col="date").sort_index()
    if "volume" not in df:
        df["volume"] = 0.0
    return df[["open", "high", "low", "close", "volume"]]


def _print_stats(title: str, stats: dict) -> None:
    print(f"\n== {title} ==")
    for k, v in stats.items():
        print(f"  {k:>18}: {v}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nifty_upstox")
    ap.add_argument("--config", help="JSON config overriding defaults")
    ap.add_argument("--state", default="state", help="state directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fetch", help="download Nifty (and India VIX) daily candles from Upstox to CSV")
    p.add_argument("--start", default=(date.today() - timedelta(days=365 * 10)).isoformat())
    p.add_argument("--out", default="data/nifty_daily.csv")

    p = sub.add_parser("backtest", help="backtest on a CSV (date,open,high,low,close)")
    p.add_argument("--csv", required=True)
    p.add_argument("--no-gate", action="store_true", help="disable regime gate (for comparison)")
    p.add_argument("--walk-forward", type=int, default=0, metavar="N")

    p = sub.add_parser("validate", help="deflated Sharpe, PBO, purged walk-forward, drawdown analysis")
    p.add_argument("--csv", help="Nifty daily CSV; omit to use synthetic data")
    p.add_argument("--extra-trials", type=int, default=0,
                   help="other variants you tried before settling on these parameters")

    p = sub.add_parser("tickets", help="show / approve order tickets (required for --live)")
    p.add_argument("action", choices=["show", "approve"])
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--reply", default="")

    sub.add_parser("demo", help="offline demo on synthetic data (no network, results are meaningless)")

    p = sub.add_parser("scan", help="after market close: build plan for next session")
    p.add_argument("--csv")

    p = sub.add_parser("execute", help="next morning: place orders from plan (PAPER unless --live)")
    p.add_argument("--live", action="store_true")

    a = ap.parse_args(argv)
    cfg = Config.load(a.config)

    if a.cmd == "fetch":
        c = UpstoxClient()
        start, end = date.fromisoformat(a.start), date.today()
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        c.daily_candles(NIFTY_KEY, start, end).rename_axis("date").to_csv(a.out)
        vix_out = a.out.replace(".csv", "_vix.csv")
        c.daily_candles(VIX_KEY, start, end).rename_axis("date").to_csv(vix_out)
        print(f"saved {a.out} and {vix_out}")

    elif a.cmd == "backtest":
        df = load_csv(a.csv)
        vix_path = a.csv.replace(".csv", "_vix.csv")
        vix = load_csv(vix_path)["close"] if os.path.exists(vix_path) else None
        res = run_backtest(df, cfg, vix=vix, use_regime_gate=not a.no_gate)
        _print_stats("backtest", res.stats)
        if a.walk_forward:
            print(walk_forward(df, cfg, a.walk_forward, vix=vix, use_regime_gate=not a.no_gate).to_string(index=False))

    elif a.cmd == "validate":
        import json as _json
        from .validate import validate
        if a.csv:
            df = load_csv(a.csv)
            vp = a.csv.replace(".csv", "_vix.csv")
            vix = load_csv(vp)["close"] if os.path.exists(vp) else None
        else:
            from .synthetic import make_data
            df, vix = make_data(), None
            print("(synthetic data: results only prove the code runs)")
        print(_json.dumps(validate(df, cfg, a.extra_trials, vix), indent=2, default=str))

    elif a.cmd == "tickets":
        import json as _json
        from pathlib import Path as _P
        from . import tickets as tk
        plan_p = _P(a.state) / "plan.json"
        entries = _json.loads(plan_p.read_text())["entries"] if plan_p.exists() else []
        if not entries:
            print("No pending entries in plan.json (run `scan` first).")
        elif a.action == "show":
            for i, en in enumerate(entries):
                print(f"[{i}] " + tk.ticket_text(en, cfg.instrument_mode, cfg.capital, cfg.risk_pct))
        else:
            ok, msg = tk.approve(a.state, entries[a.index], cfg.instrument_mode, a.reply)
            print(msg)
            return 0 if ok else 1

    elif a.cmd == "demo":
        from .synthetic import make_data
        df = make_data()
        _print_stats("gated", run_backtest(df, cfg).stats)
        _print_stats("ungated", run_backtest(df, cfg, use_regime_gate=False).stats)
        print("\nSynthetic data only: this checks the code runs, not that the strategy has an edge.")

    elif a.cmd == "scan":
        now = datetime.now()
        vix = None
        if a.csv:
            df = load_csv(a.csv)
        else:
            c = UpstoxClient()
            df = c.daily_candles(NIFTY_KEY, date.today() - timedelta(days=500), date.today())
            try:
                vix = float(c.daily_candles(VIX_KEY, date.today() - timedelta(days=10), date.today())["close"].iloc[-1])
            except Exception as e:  # VIX is optional
                print(f"(no VIX: {e})")
        df = drop_incomplete_bar(df, now)
        plan = scan(cfg, df, a.state, vix)
        print(plan)

    elif a.cmd == "execute":
        from .broker import LiveBroker, PaperBroker
        client = UpstoxClient() if os.environ.get("UPSTOX_ACCESS_TOKEN") else None
        if a.live:
            if os.environ.get("NIFTY_LIVE_CONFIRM") != "YES":
                print("Refusing live trading: set NIFTY_LIVE_CONFIRM=YES to confirm you accept real-money risk.")
                return 2
            if client is None:
                print("Live mode needs UPSTOX_ACCESS_TOKEN.")
                return 2
            broker = LiveBroker(client, a.state)
        else:
            broker = PaperBroker(a.state)
        for m in execute(cfg, a.state, broker, client, datetime.now()):
            print(m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
