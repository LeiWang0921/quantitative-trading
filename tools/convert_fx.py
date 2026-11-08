"""在盈透账户里用加元买美元（或反过来）。

用法：
  python tools/convert_fx.py 14000          用 CAD 买入 14000 USD
  python tools/convert_fx.py -- -5000       卖出 5000 USD 换回 CAD

和 run.py 一样遵守 config.toml 的 mode 与 allow_live 开关。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ib_async import IB, Forex, MarketOrder

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 让 tools/ 下的脚本也能 import autotrader
from autotrader.config import ibkr_port, load_config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("usd", type=float, help="正数=买入美元，负数=卖出美元")
    args = parser.parse_args()

    cfg = load_config()
    mode, ib_cfg = cfg["mode"], cfg["ibkr"]
    port = ibkr_port(cfg)

    ib = IB()
    ib.connect(ib_cfg["host"], port, clientId=ib_cfg["client_id"] + 10, timeout=15)
    try:
        if any(not a.startswith("D") for a in ib.managedAccounts()) and mode != "live":
            sys.exit("连接到的是实盘账户，但 mode 不是 live。已中止。")

        pair = Forex("USDCAD")
        ib.qualifyContracts(pair)
        action = "BUY" if args.usd > 0 else "SELL"
        trade = ib.placeOrder(pair, MarketOrder(action, abs(args.usd), tif="DAY"))
        for _ in range(20):
            ib.sleep(1)
            if trade.isDone():
                break
        print(f"{action} {abs(args.usd):.0f} USD.CAD → {trade.orderStatus.status}, 均价 {trade.orderStatus.avgFillPrice}")
        for entry in trade.log:
            if entry.message:
                print("  ", entry.message)
        ib.sleep(2)
        for v in ib.accountValues():
            if v.tag in ("CashBalance", "$LEDGER-CashBalance") and v.currency in ("USD", "CAD"):
                print(f"  现金 {v.currency}: {float(v.value):,.2f}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
