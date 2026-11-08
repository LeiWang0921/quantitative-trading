"""测试共用：假券商、测试用配置。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autotrader.broker.base import Bar, Broker  # noqa: E402


class FakeBroker(Broker):
    """模拟盈透收盘后的行为：下的单不会立即成交，而是挂着（pending），现金也不变。"""
    name = "fake"

    def __init__(self, prices: dict[str, float], cash: float, positions: dict[str, int] | None = None,
                 pending: dict[str, int] | None = None, is_live: bool = False):
        self.prices = prices
        self._cash = cash
        self.positions = dict(positions or {})
        self.pending_orders = dict(pending or {})
        self.is_live = is_live
        self.orders: list[tuple[str, int]] = []  # 实际下出去的单
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def cash(self, currency: str) -> float:
        return self._cash if currency == "USD" else 0.0

    def net_value(self) -> tuple[float, str]:
        return self._cash + sum(q * self.prices[s] for s, q in self.positions.items()), "USD"

    def position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    def pending(self, symbol: str) -> int:
        return self.pending_orders.get(symbol, 0)

    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        start = date(2020, 1, 1)
        p = self.prices[symbol]
        return [Bar(str(start + timedelta(days=i)), p, p, p, p, 1000) for i in range(days)]

    def market_order(self, symbol: str, qty: int) -> str:
        self.orders.append((symbol, qty))
        self.pending_orders[symbol] = self.pending_orders.get(symbol, 0) + qty
        return f"PreSubmitted {qty:+d}"


def make_cfg(stocks=("AAA", "BBB"), defense=("IEF",), *, band=0.03, drift_only=True, cash_reserve=0.0,
             exit_symbols=(), max_shares=100_000, max_value=10_000_000, mode="paper") -> dict:
    """run.run() 用到的最小配置（目标权重在测试里直接指定，不经过真实策略计算）。"""
    return {
        "mode": mode,
        "ibkr": {"host": "127.0.0.1", "paper_port": 4002, "live_port": 4001, "client_id": 1, "what_to_show": "TRADES"},
        "strategy": {
            "kind": "sleeves", "trend_windows": [100], "trend_band": 0.01, "vol_lookback": 20,
            "cash_reserve": cash_reserve, "exit_symbols": list(exit_symbols),
            "sleeves": {"stocks": list(stocks), "defense": list(defense), "rebalance_band": band,
                        "drift_only": drift_only, "dip_threshold": 0},
        },
        "risk": {"allow_live": False, "max_shares_per_order": max_shares, "max_order_value_usd": max_value},
    }
