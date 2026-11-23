"""Thin Upstox REST client: historical candles, instrument master, orders.

Endpoints follow Upstox's public API (v3 historical candles and v3 order placement).
Order/instrument-master paths have NOT been exercised against a live account from this
codebase: test in paper mode and with 1 lot before trusting them, and compare against
https://upstox.com/developer/api-documentation .
"""
from __future__ import annotations

import gzip
import io
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

BASE = "https://api.upstox.com"
HFT = "https://api-hft.upstox.com"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"


def parse_candles(payload: dict) -> pd.DataFrame:
    rows = payload["data"]["candles"]  # [ts, o, h, l, c, v, oi], newest first
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "oi"][: len(rows[0])])
    idx = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None).dt.normalize()
    df.index = idx
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df[~df.index.duplicated()].sort_index()


class UpstoxClient:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("UPSTOX_ACCESS_TOKEN")
        self.s = requests.Session()
        self.s.headers.update({"Accept": "application/json"})
        if self.token:
            self.s.headers["Authorization"] = f"Bearer {self.token}"
        extra = os.environ.get("UPSTOX_ORDER_EXTRA_HEADERS")  # JSON, e.g. algo registration headers
        self.order_headers = json.loads(extra) if extra else {}

    def _require_token(self) -> None:
        if not self.token:
            raise RuntimeError("Set UPSTOX_ACCESS_TOKEN (daily token from the Upstox login flow).")

    # ---------- data ----------
    def daily_candles(self, instrument_key: str, start: date, end: date, chunk_years: int = 3) -> pd.DataFrame:
        self._require_token()
        k = quote(instrument_key, safe="")
        frames, cur = [], start
        while cur <= end:
            to = min(date(cur.year + chunk_years, cur.month, min(cur.day, 28)) - timedelta(days=1), end)
            url = f"{BASE}/v3/historical-candle/{k}/days/1/{to.isoformat()}/{cur.isoformat()}"
            r = self.s.get(url, timeout=30)
            r.raise_for_status()
            frames.append(parse_candles(r.json()))
            cur = to + timedelta(days=1)
        out = pd.concat(frames) if frames else pd.DataFrame()
        return out[~out.index.duplicated()].sort_index()

    def ltp(self, instrument_key: str) -> float:
        self._require_token()
        r = self.s.get(f"{BASE}/v2/market-quote/ltp", params={"instrument_key": instrument_key}, timeout=15)
        r.raise_for_status()
        data = r.json()["data"]
        return float(next(iter(data.values()))["last_price"])

    def instruments(self, cache: str | Path = "state/NSE_instruments.json") -> list[dict]:
        cache = Path(cache)
        if cache.exists() and datetime.fromtimestamp(cache.stat().st_mtime).date() == date.today():
            return json.loads(cache.read_text())
        r = requests.get(INSTRUMENTS_URL, timeout=60)
        r.raise_for_status()
        data = json.load(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(r.content)), encoding="utf-8"))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
        return data

    # ---------- orders ----------
    def place_order(self, instrument_key: str, qty: int, side: str, order_type: str = "MARKET",
                    price: float = 0.0, trigger: float = 0.0, tag: str = "nifty-bot") -> str:
        self._require_token()
        body = {
            "quantity": int(qty), "product": "D", "validity": "DAY", "price": price, "tag": tag,
            "instrument_token": instrument_key, "order_type": order_type,
            "transaction_type": side, "disclosed_quantity": 0, "trigger_price": trigger,
            "is_amo": False, "slice": False,
        }
        r = self.s.post(f"{HFT}/v3/order/place", json=body, headers=self.order_headers, timeout=15)
        r.raise_for_status()
        ids = r.json()["data"]["order_ids"]
        return ids[0]

    def cancel_order(self, order_id: str) -> None:
        self._require_token()
        r = self.s.delete(f"{HFT}/v3/order/cancel", params={"order_id": order_id},
                          headers=self.order_headers, timeout=15)
        r.raise_for_status()


# ---------- instrument resolution ----------
def _expiry_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000).date()


def nifty_derivatives(instruments: list[dict]) -> list[dict]:
    return [i for i in instruments
            if i.get("segment") == "NSE_FO"
            and (i.get("underlying_symbol") or i.get("name")) == "NIFTY"
            and i.get("instrument_type") in ("FUT", "CE", "PE")]


def resolve_future(instruments: list[dict], today: date, min_dte: int = 2) -> dict:
    futs = [i for i in nifty_derivatives(instruments) if i["instrument_type"] == "FUT"
            and (_expiry_date(i["expiry"]) - today).days >= min_dte]
    if not futs:
        raise LookupError("No NIFTY future found in instrument master")
    return min(futs, key=lambda i: i["expiry"])


def resolve_option(instruments: list[dict], spot: float, side: int, today: date, min_dte: int) -> dict:
    kind = "CE" if side == 1 else "PE"
    opts = [i for i in nifty_derivatives(instruments) if i["instrument_type"] == kind
            and (_expiry_date(i["expiry"]) - today).days >= min_dte]
    if not opts:
        raise LookupError(f"No NIFTY {kind} with >= {min_dte} DTE in instrument master")
    expiry = min(i["expiry"] for i in opts)
    same = [i for i in opts if i["expiry"] == expiry]
    return min(same, key=lambda i: abs(float(i["strike_price"]) - spot))  # ATM
