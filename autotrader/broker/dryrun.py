"""纯本地的假券商：不需要账户、不联网，用随机游走生成价格。

开户审核期间就能把整套流程跑通。状态保存在 JSON 文件里，
每运行一次相当于“过了一个交易日”，持仓和现金会延续到下次。
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

from .base import Bar, Broker

HISTORY_DAYS = 1000  # 终选策略最长用 300 日均线，再加预热


class DryRunBroker(Broker):
    name = "dryrun"

    def __init__(self, state_file: Path, starting_cash: float = 100_000.0, seed: int = 42):
        self.state_file = state_file
        self.starting_cash = starting_cash
        self.seed = seed
        self.state: dict = {}

    def connect(self) -> None:
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
        else:
            self.state = {"cash_usd": self.starting_cash, "positions": {}, "day": 0}

    def disconnect(self) -> None:
        self.state["day"] += 1
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2), encoding="utf-8")

    def _closes(self, symbol: str) -> list[float]:
        rng = random.Random(f"{self.seed}-{symbol}")
        price, out = 100.0, []
        for _ in range(HISTORY_DAYS + self.state["day"]):
            price *= math.exp(rng.gauss(0.0003, 0.015))
            out.append(round(price, 2))
        return out

    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        closes = self._closes(symbol)[-days:]
        start = date(2020, 1, 1) + timedelta(days=self.state["day"])
        return [Bar(str(start + timedelta(days=i)), c, c, c, c, 0) for i, c in enumerate(closes)]

    def _last(self, symbol: str) -> float:
        return self._closes(symbol)[-1]

    def cash(self, currency: str) -> float:
        return self.state["cash_usd"] if currency == "USD" else 0.0

    def net_value(self) -> tuple[float, str]:
        held = sum(q * self._last(s) for s, q in self.state["positions"].items())
        return round(self.state["cash_usd"] + held, 2), "USD"

    def position(self, symbol: str) -> int:
        return self.state["positions"].get(symbol, 0)

    def market_order(self, symbol: str, qty: int) -> str:
        price = self._last(symbol)
        self.state["cash_usd"] = round(self.state["cash_usd"] - qty * price, 2)
        new_qty = self.position(symbol) + qty
        if new_qty:
            self.state["positions"][symbol] = new_qty
        else:
            self.state["positions"].pop(symbol, None)
        return f"Filled {qty:+d} @ {price:.2f}"
