"""Option liquidity gate for options_buy mode.

Idea from the CASHPARENCY "Claude AI option trading" post (analysis-only, no disclosed rules):
check liquidity and spread of the chosen option before trading, and be willing to say NO TRADE.
Thresholds here are mine, not the article's. The option-chain field names follow Upstox's
/v2/option/chain response (market_data.bid_price/ask_price/oi/volume) and have not been verified
against a live account: the gate fails closed (blocks) if fields are missing.
"""
from __future__ import annotations

from .config import Config


def chain_market_data(rows: list[dict], instrument_key: str) -> tuple[dict | None, dict | None]:
    for row in rows:
        for leg_name in ("call_options", "put_options"):
            leg = row.get(leg_name) or {}
            if leg.get("instrument_key") == instrument_key:
                return leg.get("market_data") or {}, leg.get("option_greeks") or {}
    return None, None


def liquidity_blocks(md: dict, cfg: Config) -> list[str]:
    blocks: list[str] = []
    bid, ask = md.get("bid_price"), md.get("ask_price")
    if not bid or not ask or bid <= 0 or ask <= 0:
        blocks.append("no two-sided quote")
    else:
        spread = (ask - bid) / ((ask + bid) / 2)
        if spread > cfg.opt_max_spread_pct:
            blocks.append(f"spread {spread:.1%} > {cfg.opt_max_spread_pct:.1%}")
    if (md.get("oi") or 0) < cfg.opt_min_oi:
        blocks.append(f"open interest {md.get('oi') or 0} < {cfg.opt_min_oi}")
    if (md.get("volume") or 0) < cfg.opt_min_volume:
        blocks.append(f"volume {md.get('volume') or 0} < {cfg.opt_min_volume}")
    return blocks
