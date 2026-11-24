"""Scan (after the close) -> plan.json; execute (next morning) -> orders, gated by risk checks."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import Config
from .journal import Journal
from .risk import RiskState
from .sizing import futures_lots, option_lots
from .strategy import add_signals, assess_posture, new_position, step_position
from . import tickets
from .config import NIFTY_KEY
from .liquidity import chain_market_data, liquidity_blocks
from .overlay import overlay
from .timemachine import replay
from .upstox import UpstoxClient, _expiry_date, resolve_future, resolve_option


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def drop_incomplete_bar(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Today's daily candle is partial until ~15:30 IST; never trade off a partial bar."""
    if len(df) and df.index[-1].date() == now.date() and now.time() < datetime.strptime("15:40", "%H:%M").time():
        return df.iloc[:-1]
    return df


def scan(cfg: Config, df: pd.DataFrame, state_dir: str, vix: float | None = None) -> dict:
    sd = Path(state_dir)
    sd.mkdir(parents=True, exist_ok=True)
    j = Journal(sd)
    f = add_signals(df, cfg)
    bar = f.iloc[-1]
    day = str(f.index[-1].date())

    positions = _load(sd / "positions.json", [])
    exits = []
    for p in positions:
        if p["signal_bar"] < day and p.get("last_bar") != day:
            px, why = step_position(p["pos"], bar, cfg)
            p["last_bar"] = day
            if px is not None:
                exits.append({"id": p["id"], "reason": why, "ref_underlying": px})
    (sd / "positions.json").write_text(json.dumps(positions, indent=2, default=str))

    posture = assess_posture(bar, cfg, vix)
    side = 1 if bar["long_sig"] else (-1 if bar["short_sig"] else 0)
    ov = overlay(df)
    entries = []
    if side and (posture.allow_long if side == 1 else posture.allow_short):
        if cfg.overlay_block_ranging and ov["trend"] != "trending":
            j.log("entry_blocked", reason=f"regime overlay: {ov['quadrant']} (not trending)")
        else:
            en = {"side": side, "signal_date": day, "close": float(bar["close"]),
                  "atr": float(bar["atr"]), "size_mult": posture.size_mult}
            try:
                en["timemachine"] = replay(df, side, en["close"], en["atr"], cfg, replays=cfg.replay_runs)
            except ValueError:
                pass  # not enough history for a replay
            entries.append(en)

    plan = {"asof": day, "posture": posture.__dict__, "regime_overlay": ov, "signal_raw": side,
            "entries": entries, "exits": exits}
    (sd / "plan.json").write_text(json.dumps(plan, indent=2))
    j.log("scan", **plan)
    return plan


def execute(cfg: Config, state_dir: str, broker, client: UpstoxClient | None, now: datetime) -> list[str]:
    sd = Path(state_dir)
    plan = _load(sd / "plan.json", None)
    if plan is None:
        return ["no plan.json: run scan first"]
    j = Journal(sd)
    risk = RiskState(sd)
    positions = _load(sd / "positions.json", [])
    msgs: list[str] = []

    def price_of(key: str, fallback: float | None) -> float:
        if client is not None and client.token:
            return client.ltp(key)
        if fallback is None:
            raise RuntimeError(f"Need UPSTOX_ACCESS_TOKEN for a live price on {key}")
        return fallback

    # ---- exits first (never blocked by risk gates) ----
    for ex in plan["exits"]:
        p = next((x for x in positions if x["id"] == ex["id"]), None)
        if p is None:
            continue
        have = broker.net_qty(p["key"])
        if have is not None and have == 0:
            msgs.append(f"{p['id']}: broker shows flat (stop likely filled); marking closed")
            exit_px = p["pos"]["stop"] if p["mode"] == "futures" else p["entry_px"] * (1 - cfg.opt_sl_pct)
        else:
            if p.get("sl_order"):
                broker.cancel(p["sl_order"])
            side = "SELL" if (p["mode"] == "options_buy" or p["pos"]["side"] == 1) else "BUY"
            broker.place(p["key"], p["qty"], side)
            exit_px = price_of(p["key"], ex["ref_underlying"] if p["mode"] == "futures" else None)
        mult = p["pos"]["side"] if p["mode"] == "futures" else 1
        pnl = mult * (exit_px - p["entry_px"]) * p["qty"]
        risk.record_trade(now.date(), pnl, cfg)
        j.log("exit", id=p["id"], reason=ex["reason"], exit_px=exit_px, pnl=pnl)
        positions.remove(p)
        msgs.append(f"EXIT {p['id']} {ex['reason']} pnl={pnl:,.0f}")

    # ---- entries (risk-gated) ----
    for en in plan["entries"]:
        blocks = risk.check_new_entry(cfg, now, len(positions))
        if blocks:
            j.log("entry_blocked", entry=en, reasons=blocks)
            msgs.append("ENTRY BLOCKED: " + "; ".join(blocks))
            continue
        if broker.live and not tickets.is_approved(sd, en, cfg.instrument_mode):
            msgs.append("NOT APPROVED: run `tickets show` then `tickets approve`; live orders need an explicit yes")
            j.log("entry_unapproved", entry=en)
            continue
        side = en["side"]
        instruments = client.instruments() if (client and client.token) else None
        if cfg.instrument_mode == "futures":
            inst = resolve_future(instruments, now.date()) if instruments else \
                {"instrument_key": "PAPER|NIFTY_FUT", "trading_symbol": "NIFTY-FUT", "lot_size": cfg.lot_size}
            lot = int(inst.get("lot_size") or cfg.lot_size)
            px = price_of(inst["instrument_key"], en["close"])
            pos = new_position(side, px, en["atr"], cfg)
            lots = futures_lots(cfg, px, pos["risk_pts"], en["size_mult"], lot)
            order_side, sl_side, sl_trigger = ("BUY" if side == 1 else "SELL"), ("SELL" if side == 1 else "BUY"), pos["stop"]
        else:
            if not instruments:
                msgs.append("options_buy needs UPSTOX_ACCESS_TOKEN for the instrument master/premium")
                continue
            und = price_of("NSE_INDEX|Nifty 50", en["close"])
            inst = resolve_option(instruments, und, side, now.date(), cfg.opt_min_dte)
            try:
                rows = client.option_chain(NIFTY_KEY, _expiry_date(inst["expiry"]))
                md, _ = chain_market_data(rows, inst["instrument_key"])
                lq = liquidity_blocks(md, cfg) if md is not None else ["contract not found in option chain"]
            except Exception as e:  # fail closed: no data, no trade
                lq = [f"option chain unavailable ({e})"]
            if lq:
                j.log("entry_blocked", entry=en, reasons=lq)
                msgs.append("NO TRADE (option liquidity): " + "; ".join(lq))
                continue
            lot = int(inst.get("lot_size") or cfg.lot_size)
            px = price_of(inst["instrument_key"], None)
            pos = new_position(side, und, en["atr"], cfg)  # underlying levels drive exits
            lots = option_lots(cfg, px, en["size_mult"], lot)
            order_side, sl_side, sl_trigger = "BUY", "SELL", round(px * (1 - cfg.opt_sl_pct), 1)
        if lots < 1:
            j.log("entry_skipped", reason="size<1 lot", entry=en)
            msgs.append("SKIPPED: risk budget below 1 lot")
            continue
        qty = lots * lot
        broker.place(inst["instrument_key"], qty, order_side)
        sl_id = broker.place(inst["instrument_key"], qty, sl_side, "SL-M", trigger=sl_trigger)
        rec = {"id": uuid.uuid4().hex[:8], "mode": cfg.instrument_mode, "key": inst["instrument_key"],
               "symbol": inst.get("trading_symbol"), "qty": qty, "entry_px": px, "sl_order": sl_id,
               "signal_bar": en["signal_date"], "pos": pos}
        positions.append(rec)
        j.log("entry", **rec)
        msgs.append(f"ENTRY {rec['symbol']} {order_side} {qty} @~{px:.1f} SL {sl_trigger:.1f}")

    (sd / "positions.json").write_text(json.dumps(positions, indent=2, default=str))
    (sd / "plan.json").write_text(json.dumps({**plan, "entries": [], "exits": []}, indent=2))  # consume plan
    return msgs or ["nothing to do"]
