"""盈透（IBKR）实现，通过本机运行的 IB Gateway 连接。

模拟盘和实盘的区别只在于 Gateway 用哪个账户登录、监听哪个端口：
  IB Gateway: 4002 = 模拟, 4001 = 实盘
  TWS:        7497 = 模拟, 7496 = 实盘
"""
from __future__ import annotations

from .base import Bar, Broker


class IBKRBroker(Broker):
    name = "ibkr"

    def __init__(self, host: str, port: int, client_id: int, what_to_show: str = "TRADES",
                 readonly: bool = False, exchanges: dict[str, str] | None = None):
        from ib_async import IB  # 延迟导入：dryrun 模式不需要安装 ib_async

        self.ib = IB()
        self.host, self.port, self.client_id = host, port, client_id
        self.what_to_show = what_to_show
        self.readonly = readonly  # True = 只读连接，只取数据，不能下单
        # 个别标的指定交易所：如 IEF 后来换了上市交易所，用 SMART 只能取到换所之后的历史，用 ARCA 能取到 2002 年起
        self.exchanges = exchanges or {}
        self._contracts: dict = {}
        self.accounts: list[str] = []

    def connect(self) -> None:
        self.ib.connect(self.host, self.port, clientId=self.client_id, timeout=15,
                        readonly=self.readonly)
        self.accounts = self.ib.managedAccounts()
        # 以 Gateway 实际登录的账户判断：模拟账户号以 D 开头（如 DU1234567），实盘以 U 开头
        self.is_live = any(not a.startswith("D") for a in self.accounts)

    def disconnect(self) -> None:
        self.ib.disconnect()

    def _contract(self, symbol: str):
        from ib_async import Stock

        if symbol not in self._contracts:
            contract = Stock(symbol, self.exchanges.get(symbol, "SMART"), "USD")
            self.ib.qualifyContracts(contract)
            self._contracts[symbol] = contract
        return self._contracts[symbol]

    def cash(self, currency: str) -> float:
        for v in self.ib.accountValues():
            # 多币种账户里按币种的余额以 "$LEDGER-" 前缀的标签返回
            if v.tag in ("CashBalance", "$LEDGER-CashBalance") and v.currency == currency:
                return float(v.value)
        return 0.0

    def net_value(self) -> tuple[float, str]:
        for v in self.ib.accountValues():
            if v.tag == "NetLiquidation":
                return float(v.value), v.currency
        return 0.0, "?"

    def position(self, symbol: str) -> int:
        for p in self.ib.positions():
            if p.contract.symbol == symbol and p.contract.currency == "USD":
                return int(p.position)
        return 0

    def pending(self, symbol: str) -> int:
        self.ib.reqAllOpenOrders()  # 包括本程序之前几次运行留下的挂单
        total = 0
        for t in self.ib.openTrades():
            if t.contract.symbol == symbol and t.contract.secType == "STK":
                sign = 1 if t.order.action == "BUY" else -1
                total += sign * int(t.orderStatus.remaining or t.order.totalQuantity)
        return total

    def daily_bars(self, symbol: str, days: int) -> list[Bar]:
        calendar_days = int(days * 1.5) + 10  # 留出周末和假期
        bars = self.ib.reqHistoricalData(
            self._contract(symbol), endDateTime="", durationStr=f"{calendar_days} D",
            barSizeSetting="1 day", whatToShow=self.what_to_show, useRTH=True,
        )
        return [Bar(str(b.date), b.open, b.high, b.low, b.close, b.volume) for b in bars][-days:]

    def adjusted_history(self, symbol: str, years: int) -> list[Bar]:
        """最近 years 年的复权日线（分红、拆股已调整），用于回测。"""
        bars = self.ib.reqHistoricalData(
            self._contract(symbol), endDateTime="", durationStr=f"{years} Y",
            barSizeSetting="1 day", whatToShow="ADJUSTED_LAST", useRTH=True, timeout=300,
        )
        return [Bar(str(b.date), b.open, b.high, b.low, b.close, b.volume) for b in bars]

    def history(self, symbol: str, days: int) -> list[Bar]:
        years = days // 250 + 1  # 按年请求（盈透超过 1 年的历史只能按年取）
        return self.adjusted_history(symbol, years)[-days:]

    def daily_values(self, symbol: str, years: int, what_to_show: str) -> dict[str, float]:
        """最近 years 年的某种日度指标，返回 {日期: 数值}。
        what_to_show 例如 "HISTORICAL_VOLATILITY"（历史波动率）、"OPTION_IMPLIED_VOLATILITY"（期权隐含波动率）。"""
        bars = self.ib.reqHistoricalData(
            self._contract(symbol), endDateTime="", durationStr=f"{years} Y",
            barSizeSetting="1 day", whatToShow=what_to_show, useRTH=True, timeout=300,
        )
        return {str(b.date): b.close for b in bars}

    def market_order(self, symbol: str, qty: int) -> str:
        from ib_async import MarketOrder

        action = "BUY" if qty > 0 else "SELL"
        trade = self.ib.placeOrder(self._contract(symbol), MarketOrder(action, abs(qty), tif="DAY"))
        self.ib.sleep(3)
        return f"{trade.orderStatus.status} {action} {abs(qty)}"
