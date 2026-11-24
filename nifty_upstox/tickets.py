"""Explicit human approval before any LIVE order.

Idea adapted from Jakeschincariol/day-trader-skill `trader-broker` (MIT): a plain-English
ticket per order, and an approval that is tied to that exact order by a fingerprint, so a stale or
generic "yes" cannot authorize a different trade. Paper mode does not need approval.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path


def fingerprint(entry: dict, mode: str) -> str:
    core = {k: entry[k] for k in ("side", "signal_date", "close", "atr")} | {"mode": mode}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]


def ticket_text(entry: dict, mode: str, capital: float, risk_pct: float) -> str:
    side = "BUY (long)" if entry["side"] == 1 else "SELL (short)"
    instr = ("Nifty futures" if mode == "futures"
             else f"ATM Nifty {'CALL' if entry['side'] == 1 else 'PUT'} (buying options)")
    lines = [
        f"Ticket {fingerprint(entry, mode)}: {side} via {instr}",
        f"  Signal date {entry['signal_date']}, reference close {entry['close']:.1f}, ATR {entry['atr']:.1f}",
        f"  Risk budget: {risk_pct:.1%} of capital ({capital * risk_pct:,.0f}) x size {entry['size_mult']:.1f}",
    ]
    tm = entry.get("timemachine")
    if tm:
        lines.append(f"  Replay odds: target {tm['p_target']:.0%}, stop {tm['p_stop']:.0%}, "
                     f"expected {tm['expected_r']:+.2f}R ({tm['replays']:,} replays; random redraws, not a forecast)")
    lines.append("  Approve with:  python -m nifty_upstox tickets approve --index N --reply \"yes\"")
    return "\n".join(lines)


def _path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "tickets.json"


def _load(state_dir) -> dict:
    p = _path(state_dir)
    return json.loads(p.read_text()) if p.exists() else {"approved": {}}


def approve(state_dir, entry: dict, mode: str, reply: str) -> tuple[bool, str]:
    r = reply.strip().lower()
    if not re.match(r"^yes\b", r) or re.search(r"\ball\b|everything|\bany\b", r):
        return False, 'Refused: reply must be an explicit "yes" for this one ticket (not "ok", "sure", or "yes to all").'
    fp = fingerprint(entry, mode)
    data = _load(state_dir)
    data["approved"][fp] = {"reply": reply, "at": datetime.now().isoformat(timespec="seconds")}
    _path(state_dir).parent.mkdir(parents=True, exist_ok=True)
    _path(state_dir).write_text(json.dumps(data, indent=2))
    return True, f"Approved ticket {fp}"


def is_approved(state_dir, entry: dict, mode: str) -> bool:
    return fingerprint(entry, mode) in _load(state_dir)["approved"]
