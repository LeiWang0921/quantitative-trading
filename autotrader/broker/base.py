"""券商接口：策略和运行脚本只和这个抽象类打交道，换券商只需新增一个实现。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class Broker(ABC):
    name = "base"
    is_live = False  # 只有确认连上了实盘账户才为 True

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def cash(self, currency: str) -> float:
        """某币种的现金余额。"""

    @abstractmethod
    def net_value(self) -> tuple[float, str]:
        """账户净值及其币种。"""

    @abstractmethod
    def position(self, symbol: str) -> int:
        """当前持股数量，无持仓返回 0。"""

    def pending(self, symbol: str) -> int:
        """已提交但未成交的订单净股数（买为正、卖为负）。立即成交的券商返回 0。"""
        return 0

    @abstractmethod
    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        """最近 days 个交易日的日线，按时间升序。"""

    def history(self, symbol: str, days: int) -> list[Bar]:
        """给策略算信号用的复权日线（分红、拆股已调整），最近 days 个交易日。默认就是 daily_bars。"""
        return self.daily_bars(symbol, days)

    @abstractmethod
    def market_order(self, symbol: str, qty: int) -> str:
        """qty>0 买入，qty<0 卖出。返回订单状态描述。"""
