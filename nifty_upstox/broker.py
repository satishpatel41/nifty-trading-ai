"""Broker adapters. PaperBroker never touches the network; LiveBroker wraps UpstoxClient."""
from __future__ import annotations

import itertools
from pathlib import Path

from .journal import Journal
from .upstox import BASE, UpstoxClient


class PaperBroker:
    live = False

    def __init__(self, state_dir: str | Path):
        self.journal = Journal(state_dir)
        self._ids = itertools.count(1)

    def place(self, key: str, qty: int, side: str, order_type: str = "MARKET",
              price: float = 0.0, trigger: float = 0.0) -> str:
        oid = f"PAPER-{next(self._ids)}"
        self.journal.log("paper_order", id=oid, key=key, qty=qty, side=side,
                         order_type=order_type, price=price, trigger=trigger)
        return oid

    def cancel(self, order_id: str) -> None:
        self.journal.log("paper_cancel", id=order_id)

    def net_qty(self, key: str) -> int | None:
        return None  # unknown in paper mode; executor trusts its own position file


class LiveBroker:
    live = True

    def __init__(self, client: UpstoxClient, state_dir: str | Path):
        self.c = client
        self.journal = Journal(state_dir)

    def place(self, key: str, qty: int, side: str, order_type: str = "MARKET",
              price: float = 0.0, trigger: float = 0.0) -> str:
        oid = self.c.place_order(key, qty, side, order_type, price, trigger)
        self.journal.log("live_order", id=oid, key=key, qty=qty, side=side,
                         order_type=order_type, price=price, trigger=trigger)
        return oid

    def cancel(self, order_id: str) -> None:
        self.c.cancel_order(order_id)
        self.journal.log("live_cancel", id=order_id)

    def net_qty(self, key: str) -> int:
        r = self.c.s.get(f"{BASE}/v2/portfolio/short-term-positions", timeout=15)
        r.raise_for_status()
        for p in r.json().get("data", []):
            if p.get("instrument_token") == key:
                return int(p.get("quantity", 0))
        return 0
