"""窗口程序从盈透读取的数据。只读连接（readonly=True，技术上不能下单），每次取完就断开。

连接编号用 client_id + 40，不和 run.py（+0）、convert_fx.py（+10）、fetch_data.py（+20）冲突。
这些函数会阻塞几秒，只在后台线程里调用。
"""
from __future__ import annotations

import json
from datetime import date

from ..config import DATA_DIR, ibkr_port

CLIENT_OFFSET = 40
NAMES_FILE = DATA_DIR / "symbol_names.json"  # 代码 → 公司名的缓存，查过一次就不再问盈透


def _connect(cfg: dict):
    from ib_async import IB

    ib = IB()
    ib.connect(cfg["ibkr"]["host"], ibkr_port(cfg), clientId=cfg["ibkr"]["client_id"] + CLIENT_OFFSET,
               timeout=10, readonly=True)
    return ib


def _load_names() -> dict[str, str]:
    try:
        return json.loads(NAMES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _lookup_names(ib, symbols: list[str]) -> dict[str, str]:
    from ib_async import Stock

    names = _load_names()
    new = [s for s in symbols if s not in names]
    for s in new:
        details = ib.reqContractDetails(Stock(s, "SMART", "USD"))
        if details:
            names[s] = details[0].longName
    if new:
        DATA_DIR.mkdir(exist_ok=True)
        NAMES_FILE.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
    return names


def snapshot(cfg: dict, symbols: list[str], nav_start: str | None) -> dict:
    """账户、持仓、挂单、公司名，以及从 nav_start 起的 SPY 收盘价（净值曲线对比用）。"""
    from ib_async import Stock

    ib = _connect(cfg)
    try:
        accounts = ib.managedAccounts()
        values = ib.accountValues()
        nav, ccy = 0.0, ""
        for v in values:
            if v.tag == "NetLiquidation" and v.currency:
                nav, ccy = float(v.value), v.currency
        cash = {v.currency: float(v.value) for v in values
                if v.tag in ("CashBalance", "$LEDGER-CashBalance") and v.currency not in ("", "BASE")}
        portfolio = [{"symbol": p.contract.symbol, "sec_type": p.contract.secType, "currency": p.contract.currency,
                      "qty": float(p.position), "price": p.marketPrice, "value": p.marketValue,
                      "avg_cost": p.averageCost, "unrealized": p.unrealizedPNL}
                     for p in ib.portfolio() if p.position]
        orders = []
        try:
            ib.reqAllOpenOrders()
            for t in ib.openTrades():
                orders.append({"symbol": t.contract.symbol, "action": t.order.action,
                               "qty": float(t.orderStatus.remaining or t.order.totalQuantity),
                               "status": t.orderStatus.status})
        except Exception:
            pass  # 只读连接下个别版本不允许查挂单：不影响其他信息
        names = _lookup_names(ib, symbols)
        spy = {}
        if nav_start:
            days = (date.today() - date.fromisoformat(nav_start)).days + 10
            bars = ib.reqHistoricalData(Stock("SPY", "SMART", "USD"), endDateTime="", durationStr=f"{days} D",
                                        barSizeSetting="1 day", whatToShow="ADJUSTED_LAST", useRTH=True)
            spy = {str(b.date): b.close for b in bars}
        return {"accounts": accounts, "is_live": any(not a.startswith("D") for a in accounts), "nav": nav,
                "currency": ccy, "cash": cash, "portfolio": portfolio, "orders": orders, "names": names, "spy": spy}
    finally:
        ib.disconnect()


def validate_symbol(cfg: dict, symbol: str, need_bars: int) -> dict:
    """检查一个美股代码能不能加入候选池：盈透查得到、是股票或 ETF、历史日线够长。"""
    from ib_async import Stock

    ib = _connect(cfg)
    try:
        details = ib.reqContractDetails(Stock(symbol, "SMART", "USD"))
        if not details:
            return {"ok": False, "reason": f"盈透找不到美股代码 {symbol}"}
        d = details[0]
        bars = ib.reqHistoricalData(d.contract, endDateTime="", durationStr=f"{need_bars // 250 + 2} Y",
                                    barSizeSetting="1 day", whatToShow="ADJUSTED_LAST", useRTH=True, timeout=120)
        n = len(bars)
        info = {"symbol": symbol, "name": d.longName, "exchange": d.contract.primaryExchange,
                "type": d.stockType, "bars": n, "first": str(bars[0].date) if bars else "", "need": need_bars}
        if n < need_bars:
            return info | {"ok": False, "reason": (
                f"只有 {n} 根日线（从 {info['first']} 起），至少需要 {need_bars} 根。"
                "run.py 会按所有标的共同的日期对齐历史，历史太短的股票会把其他股票的历史也截短，改变它们的信号")}
        names = _load_names()
        names[symbol] = d.longName
        NAMES_FILE.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
        return info | {"ok": True}
    finally:
        ib.disconnect()
