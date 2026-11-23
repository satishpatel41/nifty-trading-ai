"""Pre-trade gates and circuit breakers (drawdown-circuit-breaker / pre-trade-discipline-gate style).

State is persisted as JSON so limits survive restarts. A file named KILL in the state dir
blocks all new entries immediately.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import Config


class RiskState:
    def __init__(self, state_dir: str | Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "risk.json"
        self.data = {"pnl_by_day": {}, "consec_losses": 0, "halt_until": None}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2))

    def record_trade(self, day: date, pnl: float, cfg: Config) -> None:
        k = day.isoformat()
        self.data["pnl_by_day"][k] = self.data["pnl_by_day"].get(k, 0.0) + pnl
        if pnl < 0:
            self.data["consec_losses"] += 1
            if self.data["consec_losses"] >= cfg.max_consec_losses:
                self.data["halt_until"] = (day + timedelta(days=cfg.cooldown_days)).isoformat()
        else:
            self.data["consec_losses"] = 0
        self.save()

    def _sum(self, start: date, end: date) -> float:
        return sum(v for k, v in self.data["pnl_by_day"].items()
                   if start <= date.fromisoformat(k) <= end)

    def check_new_entry(self, cfg: Config, now: datetime, open_positions: int) -> list[str]:
        """Return list of blocking reasons; empty list means entry is allowed."""
        blocks: list[str] = []
        today = now.date()
        if (self.dir / "KILL").exists():
            blocks.append("KILL file present")
        if open_positions >= cfg.max_open:
            blocks.append(f"max_open={cfg.max_open} reached")
        halt = self.data.get("halt_until")
        if halt and today <= date.fromisoformat(halt):
            blocks.append(f"cooldown after {cfg.max_consec_losses} losses until {halt}")
        if self._sum(today, today) <= -cfg.daily_loss_pct * cfg.capital:
            blocks.append("daily loss limit hit")
        week_start = today - timedelta(days=today.weekday())
        if self._sum(week_start, today) <= -cfg.weekly_dd_pct * cfg.capital:
            blocks.append("weekly drawdown limit hit")
        if now.weekday() >= 5:
            blocks.append("weekend")
        t = now.time()
        if not (datetime.strptime("09:20", "%H:%M").time() <= t <= datetime.strptime("15:00", "%H:%M").time()):
            blocks.append("outside 09:20-15:00 IST entry window")
        return blocks
